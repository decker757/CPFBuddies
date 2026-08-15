"""The decision dashboard's feed.

The dashboard is the demo screen, so these tests are about the two things that
would ruin it: entries arriving out of order or not at all, and a frame that a
browser's `EventSource` cannot parse. The second is the sharper risk, because
merchant-supplied text reaches the wire and SSE is newline-delimited.

**The stream is driven directly rather than through `TestClient`.** Starlette's
test client cannot read an endpoint that streams indefinitely -- it blocks
before the response headers arrive and never yields a chunk -- so a test written
against it would hang rather than fail. Calling the generator is also the more
precise test: framing, ordering and the cursor are this module's, while getting
bytes onto a socket is Starlette's and is already covered by Starlette.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from trustrail.app import create_app
from trustrail.audit.api import _entries_from, _starting_cursor
from trustrail.contracts.keys import ISSUER_PRIVATE_KEY
from trustrail.contracts.scenarios import demo_config
from trustrail.mandate.service import MandateService
from trustrail.models.audit import AuditEntry, AuditEventType
from trustrail.signing.local import LocalSigner
from trustrail.stores.memory import (
    InMemoryAuditLog,
    InMemoryKillSwitchStore,
    InMemoryMandateStore,
)
from trustrail.verifier.service import VerifierService

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
MANDATE_ID = "0x" + "ab" * 32


class StubRequest:
    """A client that hangs up after `polls` checks.

    The real generator runs until the client disconnects, so a test needs a
    client that eventually does. `on_poll` is the seam for asking what happens
    when an entry is written *while* the stream is open.
    """

    def __init__(self, polls: int = 1, on_poll=None) -> None:
        self._remaining = polls
        self._on_poll = on_poll
        self.polls = 0

    async def is_disconnected(self) -> bool:
        if self._remaining <= 0:
            return True
        self._remaining -= 1
        self.polls += 1
        if self._on_poll is not None:
            self._on_poll(self.polls)
        return False


def _entry(index: int, *, summary: str = "something happened") -> AuditEntry:
    return AuditEntry(
        event_id="0x" + f"{index:02x}" * 32,
        mandate_id=MANDATE_ID,
        event_type=AuditEventType.CANDIDATE_SELECTED,
        occurred_at=NOW + timedelta(seconds=index),
        actor="browser-1",
        summary=summary,
    )


def _drain(audit, *, cursor: int = 0, polls: int = 1, on_poll=None) -> list[str]:
    """Run the stream to completion and return its raw frames."""

    async def collect() -> list[str]:
        request = StubRequest(polls=polls, on_poll=on_poll)
        return [frame async for frame in _entries_from(audit, request, cursor)]

    return asyncio.run(collect())


def _parse(frame: str) -> dict[str, str]:
    fields = {}
    for line in frame.splitlines():
        if not line or line.startswith(":"):
            continue
        name, _, value = line.partition(": ")
        fields[name] = value
    return fields


@pytest.fixture
def audit() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def client(audit: InMemoryAuditLog) -> TestClient:
    mandates = MandateService(
        signer=LocalSigner(ISSUER_PRIVATE_KEY),
        store=InMemoryMandateStore(),
        kill_switch=InMemoryKillSwitchStore(),
        audit=audit,
    )
    return TestClient(
        create_app(
            mandates=mandates,
            verifier=VerifierService(demo_config()),
            audit=audit,
        )
    )


class TestSnapshot:
    """`GET /audit` -- what a dashboard paints before it opens the stream."""

    def test_the_feed_is_empty_before_anything_happens(self, client):
        assert client.get("/audit").json() == []

    def test_entries_come_back_oldest_first(self, client, audit):
        for i in range(3):
            audit.record(_entry(i))

        body = client.get("/audit").json()

        assert [e["event_id"] for e in body] == [_entry(i).event_id for i in range(3)]

    def test_since_and_limit_page_through_the_trail(self, client, audit):
        for i in range(5):
            audit.record(_entry(i))

        body = client.get("/audit", params={"since": 1, "limit": 2}).json()

        assert [e["event_id"] for e in body] == [_entry(1).event_id, _entry(2).event_id]

    def test_a_negative_cursor_is_rejected_rather_than_wrapping(self, client):
        """Python would read -1 as "the last entry"; the API must not."""
        assert client.get("/audit", params={"since": -1}).status_code == 422

    def test_the_stream_route_is_mounted(self, client):
        """Cheap proof the router is wired, without reading the stream itself."""
        paths = client.get("/openapi.json").json()["paths"]
        assert {"/audit", "/audit/stream"} <= paths.keys()


class TestStreamFrames:
    def test_existing_entries_are_replayed_on_connect(self, audit):
        for i in range(2):
            audit.record(_entry(i))

        frames = [_parse(f) for f in _drain(audit)]

        assert [f["event"] for f in frames] == ["CANDIDATE_SELECTED"] * 2
        assert [json.loads(f["data"])["event_id"] for f in frames] == [
            _entry(0).event_id,
            _entry(1).event_id,
        ]

    def test_the_event_id_is_the_cursor_to_resume_from(self, audit):
        for i in range(3):
            audit.record(_entry(i))

        frames = [_parse(f) for f in _drain(audit)]

        # Ids point *past* each entry, so resuming asks for what comes next.
        assert [f["id"] for f in frames] == ["1", "2", "3"]

    def test_a_cursor_skips_what_the_client_already_has(self, audit):
        for i in range(3):
            audit.record(_entry(i))

        frames = [_parse(f) for f in _drain(audit, cursor=2)]

        assert [json.loads(f["data"])["event_id"] for f in frames] == [
            _entry(2).event_id
        ]

    def test_entries_written_while_connected_are_pushed(self, audit):
        """The whole point: a verdict must land without the client asking again."""
        audit.record(_entry(0))

        frames = [
            _parse(f)
            for f in _drain(
                audit, polls=2, on_poll=lambda n: audit.record(_entry(1)) if n == 1 else None
            )
        ]

        assert [json.loads(f["data"])["event_id"] for f in frames] == [
            _entry(0).event_id,
            _entry(1).event_id,
        ]

    def test_a_cursor_past_the_end_replays_instead_of_stalling(self, audit):
        """An in-memory log restarts empty; a stale client must not hang forever."""
        audit.record(_entry(0))

        frames = [_parse(f) for f in _drain(audit, cursor=99)]

        assert [json.loads(f["data"])["event_id"] for f in frames] == [
            _entry(0).event_id
        ]

    def test_untrusted_text_cannot_split_a_frame(self, audit):
        """A blank line in merchant text would end the frame early if unescaped.

        This one would break the dashboard rather than merely look wrong:
        `EventSource` delimits on a blank line, so a listing title containing
        one would arrive as two malformed events.
        """
        audit.record(_entry(0, summary="line one\n\nline two: injected"))

        [frame] = _drain(audit)

        assert frame.count("\n\n") == 1, "the blank line must only be the terminator"
        assert (
            json.loads(_parse(frame)["data"])["summary"]
            == "line one\n\nline two: injected"
        )


class TestStartingCursor:
    def test_a_fresh_connection_starts_at_the_beginning(self):
        assert _starting_cursor(since=None, last_event_id=None) == 0

    def test_a_reconnecting_client_resumes_from_its_header(self):
        assert _starting_cursor(since=None, last_event_id="7") == 7

    def test_an_explicit_since_beats_the_header(self):
        """A dashboard that painted from GET /audit knows better than the browser."""
        assert _starting_cursor(since=2, last_event_id="7") == 2

    @pytest.mark.parametrize("junk", ["not-a-number", "", "1.5", "-3"])
    def test_a_junk_header_replays_rather_than_failing(self, junk):
        """`Last-Event-ID` is whatever the client sends. It must never 500."""
        assert _starting_cursor(since=None, last_event_id=junk) == 0
