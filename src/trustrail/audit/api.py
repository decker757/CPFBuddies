"""The decision dashboard's feed, as a snapshot and as a stream.

CLAUDE.md is specific that the dashboard must stream rather than poll on a
button press, because a row flipping to FAIL in real time is the moment the demo
turns on. `GET /audit/stream` is that: one long-lived Server-Sent Events
connection, entries pushed as they are written.

**Why SSE and not a websocket.** The feed is one-directional -- the dashboard
never sends anything back -- and SSE is plain HTTP, so it survives the API
Gateway and WAF in front of it without a protocol upgrade to configure.
`EventSource` also reconnects on its own and replays `Last-Event-ID`, which is
the whole of our resume story for free.

**Why the server polls its own log.** `AuditLog.record` is synchronous and is
called from synchronous code deep inside the Mandate Service. Pushing from there
into an async subscriber means thread-safe hand-off between a sync writer and an
event loop, which is a real source of dropped or duplicated events. Reading a
cursor over an append-only list, on a short timer, has neither problem. The
client still holds one open connection and still learns within a tick, which is
the requirement -- the polling is an implementation detail on one side of it,
not something the browser does.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from trustrail.models.audit import AuditEntry
from trustrail.ports import AuditLog

#: How often the stream looks for new entries. Short enough that a verdict lands
#: while the demo is still saying the sentence, long enough not to spin a core.
POLL_SECONDS = 0.25

#: Sent when nothing has happened, to keep proxies from reaping an idle
#: connection. An SSE comment: clients ignore it, intermediaries see traffic.
_HEARTBEAT = ": keep-alive\n\n"

#: Roughly a minute of silence between heartbeats' worth of ticks.
_TICKS_PER_HEARTBEAT = int(15 / POLL_SECONDS)

_STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Nginx and several proxies buffer responses by default, which turns a
    # stream into one delivery at the end -- exactly the failure this endpoint
    # exists to avoid, and invisible in local testing where there is no proxy.
    "X-Accel-Buffering": "no",
}


def build_router(audit: AuditLog) -> APIRouter:
    router = APIRouter(tags=["audit"])

    @router.get("/audit")
    def feed(
        since: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> list[AuditEntry]:
        """A snapshot of the trail, for a dashboard's first paint.

        `since` is a position in the feed, not a timestamp -- the same cursor
        the stream reports as an event id, so a client can page through history
        and then pick the stream up exactly where it stopped.
        """
        return audit.all_entries()[since : since + limit]

    @router.get("/audit/stream")
    async def stream(
        request: Request,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
        since: Annotated[int | None, Query(ge=0)] = None,
    ) -> StreamingResponse:
        """Every entry from `since`, then every entry as it is written.

        A reconnecting `EventSource` sends `Last-Event-ID` by itself, so a
        dropped connection resumes without losing a row. An explicit `since`
        wins over it, which is what lets a dashboard paint from `GET /audit`
        first and then stream only what it does not already have.
        """
        cursor = _starting_cursor(since=since, last_event_id=last_event_id)
        return StreamingResponse(
            _entries_from(audit, request, cursor),
            media_type="text/event-stream",
            headers=_STREAM_HEADERS,
        )

    return router


async def _entries_from(
    audit: AuditLog, request: Request, cursor: int
) -> AsyncIterator[str]:
    """Yield SSE frames until the client goes away.

    The disconnect check is what stops this generator: without it a closed tab
    leaves a task polling the audit log forever, and a demo that has been
    reloaded a few times is quietly running a dozen of them.
    """
    quiet_ticks = 0
    while True:
        if await request.is_disconnected():
            return

        entries = audit.all_entries()
        if cursor > len(entries):
            # The log is shorter than the client's cursor: a restart, since
            # nothing here ever deletes. Resume from the start rather than
            # stalling forever waiting for a position that will not come back.
            cursor = 0
        for entry in entries[cursor:]:
            cursor += 1
            yield _frame(cursor, entry)
            quiet_ticks = 0

        quiet_ticks += 1
        if quiet_ticks >= _TICKS_PER_HEARTBEAT:
            quiet_ticks = 0
            yield _HEARTBEAT

        await asyncio.sleep(POLL_SECONDS)


def _frame(cursor: int, entry: AuditEntry) -> str:
    """One SSE frame.

    The id is the cursor *after* this entry, so a client resuming from it asks
    for what comes next rather than replaying the row it already drew.

    `model_dump_json` cannot emit a bare newline inside a JSON string -- it
    escapes them -- which matters more than it looks: a raw newline in the data
    would terminate the frame early and split one entry into two malformed ones.
    Merchant-supplied text reaches this line, so that is a property worth
    relying on deliberately rather than by luck.
    """
    return f"id: {cursor}\nevent: {entry.event_type.value}\ndata: {entry.model_dump_json()}\n\n"


def _starting_cursor(*, since: int | None, last_event_id: str | None) -> int:
    """Where this connection begins reading.

    An explicit `since` wins: a dashboard that has already painted from
    `GET /audit` knows its own position better than a header the browser
    resends automatically.

    `Last-Event-ID` is otherwise whatever the client chose to send, so it is
    untrusted input. A value that is not a position starts the feed from the
    beginning, which is the harmless answer -- the alternative is a 500 on a
    reconnect nobody triggered on purpose.
    """
    if since is not None:
        return since
    if last_event_id is None:
        return 0
    try:
        return max(0, int(last_event_id))
    except ValueError:
        return 0
