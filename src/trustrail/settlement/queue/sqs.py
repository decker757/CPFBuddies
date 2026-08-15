"""SQS-backed queue.

WRITTEN BUT UNWIRED. Workstream D owns provisioning the queue and its dead-letter queue;
switching to it should be configuration only.

Redrive is intentionally not implemented here. SQS applies the redrive policy configured on the
queue itself, so ``nack`` simply makes the message visible again and lets AWS decide when it
has been retried enough. Implementing a second redrive policy in code would fight the first.
"""

from __future__ import annotations

from typing import Any

from trustrail.settlement.models import SettlementRequest
from trustrail.settlement.queue.base import QueueMessage

RECEIVE_COUNT_ATTRIBUTE = "ApproximateReceiveCount"


class SqsQueue:
    """Thin adapter over the SQS API, matching :class:`SettlementQueue`."""

    def __init__(self, queue_url: str, client: Any | None = None, wait_seconds: int = 10) -> None:
        self._queue_url = queue_url
        self._client = client if client is not None else _default_client()
        self._wait_seconds = wait_seconds

    def publish(self, request: SettlementRequest) -> str:
        response = self._client.send_message(
            QueueUrl=self._queue_url,
            MessageBody=request.model_dump_json(),
        )
        return response["MessageId"]

    def receive(self, limit: int = 1) -> list[QueueMessage]:
        response = self._client.receive_message(
            QueueUrl=self._queue_url,
            MaxNumberOfMessages=min(limit, 10),
            WaitTimeSeconds=self._wait_seconds,
            AttributeNames=[RECEIVE_COUNT_ATTRIBUTE],
        )
        return [self._to_message(raw) for raw in response.get("Messages", [])]

    def ack(self, message: QueueMessage) -> None:
        self._client.delete_message(
            QueueUrl=self._queue_url, ReceiptHandle=message.receipt_handle
        )

    def nack(self, message: QueueMessage) -> None:
        # Zero visibility timeout returns it to the queue immediately; the queue's own redrive
        # policy dead-letters it once the receive count is exhausted.
        self._client.change_message_visibility(
            QueueUrl=self._queue_url,
            ReceiptHandle=message.receipt_handle,
            VisibilityTimeout=0,
        )

    @staticmethod
    def _to_message(raw: dict) -> QueueMessage:
        attributes = raw.get("Attributes", {})
        return QueueMessage(
            message_id=raw["MessageId"],
            request=SettlementRequest.model_validate_json(raw["Body"]),
            receive_count=int(attributes.get(RECEIVE_COUNT_ATTRIBUTE, 1)),
            receipt_handle=raw["ReceiptHandle"],
        )


def _default_client() -> Any:
    import boto3

    return boto3.client("sqs")
