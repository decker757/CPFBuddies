#!/usr/bin/env bash
#
# Push the rail image to ECR and run it on App Runner.
#
#   ./scripts/deploy_apprunner.sh --dry-run   # print every command, run none
#   ./scripts/deploy_apprunner.sh             # build, push, create or update
#
# Idempotent in the same way scripts/provision_aws.py is: the repository, the
# role and the service are each created only if absent, and a second run just
# ships a new image to the service that already exists.
#
# Run scripts/provision_aws.py FIRST. This script gives the service permission
# to reach DynamoDB, SQS, KMS and Bedrock, but it does not create them, and a
# service that boots against missing tables fails on its first mandate rather
# than at startup.
#
# **Architecture matters here.** App Runner is amd64 and a Mac builds arm64 by
# default. An image built without --platform starts, fails to exec, and reports
# a health check timeout that says nothing about why. That is what the explicit
# --platform below is for.

set -euo pipefail

REGION="${AWS_REGION:-ap-southeast-1}"
REPOSITORY="trustrail"
SERVICE="trustrail-rail"
IMAGE_TAG="${IMAGE_TAG:-latest}"
PLATFORM="linux/amd64"

# App Runner needs a role it can assume to pull from ECR, and a second role the
# running task uses to reach DynamoDB, SQS, KMS and Bedrock. They are separate
# because pulling an image and spending money are different authorities.
ACCESS_ROLE="TrustRailAppRunnerECRAccess"
INSTANCE_ROLE="TrustRailAppRunnerInstance"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '  would run: %s\n' "$*"
  else
    "$@"
  fi
}

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${REPOSITORY}:${IMAGE_TAG}"

echo "account   ${ACCOUNT_ID}"
echo "region    ${REGION}"
echo "image     ${IMAGE}"
[[ $DRY_RUN -eq 1 ]] && echo "mode      DRY RUN"
echo

# --- ECR --------------------------------------------------------------------
echo "ecr"
if aws ecr describe-repositories --repository-names "$REPOSITORY" --region "$REGION" >/dev/null 2>&1; then
  echo "  exists  ${REPOSITORY}"
else
  echo "  create  ${REPOSITORY}"
  # Scanning on push because this image carries the settlement path. It costs
  # nothing and the finding you want is the one you get before the demo.
  run aws ecr create-repository \
    --repository-name "$REPOSITORY" \
    --region "$REGION" \
    --image-scanning-configuration scanOnPush=true
fi

echo "  login"
if [[ $DRY_RUN -eq 0 ]]; then
  aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$REGISTRY"
else
  echo "  would run: aws ecr get-login-password | docker login ..."
fi

echo "  build (${PLATFORM})"
run docker build --platform "$PLATFORM" -t "$IMAGE" .
echo "  push"
run docker push "$IMAGE"

# --- IAM --------------------------------------------------------------------
# Deliberately not in provision_aws.py: the trust policy names App Runner, so
# these roles only make sense once something is being deployed to it.
echo
echo "iam"
for role in "$ACCESS_ROLE" "$INSTANCE_ROLE"; do
  if aws iam get-role --role-name "$role" >/dev/null 2>&1; then
    echo "  exists  ${role}"
  else
    echo "  create  ${role}"
    principal="build.apprunner.amazonaws.com"
    [[ "$role" == "$INSTANCE_ROLE" ]] && principal="tasks.apprunner.amazonaws.com"
    run aws iam create-role --role-name "$role" --assume-role-policy-document "{
      \"Version\": \"2012-10-17\",
      \"Statement\": [{
        \"Effect\": \"Allow\",
        \"Principal\": {\"Service\": \"${principal}\"},
        \"Action\": \"sts:AssumeRole\"
      }]
    }"
  fi
done

echo "  attach  ECR pull -> ${ACCESS_ROLE}"
run aws iam attach-role-policy --role-name "$ACCESS_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess

# Least privilege, one statement per thing the rail actually touches. Note KMS
# is Sign and GetPublicKey only: the service must never be able to export,
# schedule deletion of, or re-encrypt with the signing keys.
echo "  attach  runtime policy -> ${INSTANCE_ROLE}"
run aws iam put-role-policy --role-name "$INSTANCE_ROLE" \
  --policy-name TrustRailRuntime --policy-document "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [
    {
      \"Effect\": \"Allow\",
      \"Action\": [\"dynamodb:GetItem\", \"dynamodb:PutItem\", \"dynamodb:Query\",
                   \"dynamodb:Scan\", \"dynamodb:UpdateItem\", \"dynamodb:TransactWriteItems\"],
      \"Resource\": [
        \"arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/trustrail-*\",
        \"arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/trustrail-*/index/*\"
      ]
    },
    {
      \"Effect\": \"Allow\",
      \"Action\": [\"sqs:SendMessage\", \"sqs:ReceiveMessage\", \"sqs:DeleteMessage\",
                   \"sqs:ChangeMessageVisibility\", \"sqs:GetQueueAttributes\"],
      \"Resource\": \"arn:aws:sqs:${REGION}:${ACCOUNT_ID}:trustrail-*\"
    },
    {
      \"Effect\": \"Allow\",
      \"Action\": [\"kms:Sign\", \"kms:GetPublicKey\"],
      \"Resource\": \"arn:aws:kms:${REGION}:${ACCOUNT_ID}:key/*\"
    },
    {
      \"Effect\": \"Allow\",
      \"Action\": [\"bedrock:InvokeModel\"],
      \"Resource\": \"*\"
    }
  ]
}"

# --- App Runner -------------------------------------------------------------
echo
echo "apprunner"
SERVICE_ARN="$(aws apprunner list-services --region "$REGION" \
  --query "ServiceSummaryList[?ServiceName=='${SERVICE}'].ServiceArn" --output text 2>/dev/null || true)"

if [[ -n "$SERVICE_ARN" && "$SERVICE_ARN" != "None" ]]; then
  echo "  exists  ${SERVICE} -> redeploying the new image"
  run aws apprunner start-deployment --service-arn "$SERVICE_ARN" --region "$REGION"
else
  echo "  create  ${SERVICE}"
  echo
  echo "  Set the runtime environment on the service. TRUSTRAIL_REGISTRAR_KEY and"
  echo "  TRUSTRAIL_SETTLER_KEY are private keys — put them in Parameter Store as"
  echo "  SecureString and reference them, or move to KMS and drop them entirely."
  echo "  CLAUDE.md is explicit that no private key material belongs anywhere else."
  echo
  cat <<EOF
  aws apprunner create-service --region ${REGION} \\
    --service-name ${SERVICE} \\
    --source-configuration '{
      "ImageRepository": {
        "ImageIdentifier": "${IMAGE}",
        "ImageRepositoryType": "ECR",
        "ImageConfiguration": {
          "Port": "8000",
          "RuntimeEnvironmentVariables": {
            "TRUSTRAIL_PERSISTENCE": "aws",
            "TRUSTRAIL_NETWORK": "avalanche",
            "TRUSTRAIL_RPC_URL": "https://api.avax.network/ext/bc/C/rpc",
            "TRUSTRAIL_QUEUE_URL": "<from provision_aws.py>",
            "TRUSTRAIL_EVALUATOR_MODEL": "bedrock",
            "AWS_REGION": "${REGION}"
          }
        }
      },
      "AutoDeploymentsEnabled": false,
      "AuthenticationConfiguration": {
        "AccessRoleArn": "arn:aws:iam::${ACCOUNT_ID}:role/${ACCESS_ROLE}"
      }
    }' \\
    --instance-configuration '{
      "Cpu": "1 vCPU", "Memory": "2 GB",
      "InstanceRoleArn": "arn:aws:iam::${ACCOUNT_ID}:role/${INSTANCE_ROLE}"
    }' \\
    --health-check-configuration '{
      "Protocol": "HTTP", "Path": "/health",
      "Interval": 10, "Timeout": 5, "HealthyThreshold": 1, "UnhealthyThreshold": 5
    }'
EOF
  echo
  echo "  Printed rather than run: creating the service starts billing, and the"
  echo "  environment above still has a placeholder in it."
fi

echo
echo "done."
