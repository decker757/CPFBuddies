# The rail, as one container: FastAPI, both orchestrators, every atomic service
# and the settlement worker on the app's lifespan.
#
# Two stages, and the first one is not optional. `load_abi` reads the contract
# ABI out of Hardhat's build output at `onchain/artifacts/`, which is gitignored
# -- it is compiler output, not source. A clean checkout therefore has no ABI,
# and an image built without this stage starts, serves `/health`, and then fails
# the first time anything touches the chain. Compiling here means the ABI in the
# image always matches the contract source in the image.

# --- stage 1: contract artifacts -------------------------------------------
FROM node:22-slim AS contracts

WORKDIR /onchain
# Manifests first so the install layer caches on dependency changes only, not
# on every edit to a .sol file.
COPY onchain/package.json onchain/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY onchain/ ./
RUN npx hardhat compile


# --- stage 2: runtime -------------------------------------------------------
FROM python:3.13-slim AS runtime

# PYTHONUNBUFFERED so CloudWatch sees a log line when it is written rather than
# when the buffer happens to flush, which matters when the thing you are
# debugging is why the process stopped.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    # The source tree must shadow the installed copies in site-packages, and
    # this is not a style preference. Both packages locate their runtime data
    # relative to their own file -- `REPO_ROOT = Path(__file__).parents[N]` --
    # so an import resolved from site-packages looks for `config/verifier.toml`
    # and `onchain/deployments/` under /usr/local/lib, where they are not. From
    # /srv/src and /srv/backend those same expressions land on /srv, where the
    # COPY lines below put them. Without this the container starts and then dies
    # with FileNotFoundError on the verifier config.
    PYTHONPATH=/srv/src:/srv/backend

WORKDIR /srv

# Dependency manifests before source, same caching reason as above. `src/` has
# to come along because pyproject points setuptools at it and pip needs the
# package to exist to install it.
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir ".[aws]" "uvicorn>=0.34"

COPY backend/ ./backend/
RUN pip install --no-cache-dir ./backend

# Runtime data, not code. Deployment addresses come from
# `onchain/deployments/<network>.json` and never from Python config, and the
# EIP-712 domain comes from `config/verifier.toml` -- a mismatch there is
# invisible at runtime, so the file ships with the image rather than being
# mounted and forgotten.
COPY config/ ./config/
COPY onchain/deployments/ ./onchain/deployments/
COPY --from=contracts /onchain/artifacts/contracts/ ./onchain/artifacts/contracts/

# Not root. The process needs to read its own files and nothing else.
RUN useradd --system --create-home --uid 10001 rail \
    && chown -R rail:rail /srv
USER rail

EXPOSE 8000

# `/health` is unauthenticated and touches no store, so a failing health check
# means the process is gone rather than that a dependency is slow.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/health', timeout=4).status==200 else 1)"

# `chain_app`, not `demo_app`: the latter never settles. It refuses to start on
# a wrong role or a mismatched signing domain, which is the behaviour you want
# from a container that would otherwise come up healthy and record digests no
# contract shares.
CMD ["sh", "-c", "exec uvicorn --factory app.rail:chain_app --host 0.0.0.0 --port ${PORT}"]
