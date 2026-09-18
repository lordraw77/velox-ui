# ADR-0024: A turn belongs to the conversation, not to the connection reading it

**Status:** accepted
**Date:** 2026-09-18

## Context
Until now a turn *was* its request. `POST /api/chats/{id}/completions` handed
`PreparedTurn.stream()` straight to the response, so the generator lived and died with
the socket: when the reader went away the turn was cancelled, what the model had
produced was persisted, and generation stopped (ADR-0005).

That is right for a cancelled request and wrong for a reader who merely looked
somewhere else. In use it meant three things, all reported as "the chat stops working":

- opening another conversation aborted the reply, because the client calls `stop()` on
  the way out;
- reloading the page, or a laptop suspending, ended the turn mid-answer;
- a dropped connection was indistinguishable from "stop", so a network blip lost the
  rest of a long local generation that the machine had already paid for.

## Decision

### The turn runs as its own task; the response reads it
`services/turns.py` holds a `TurnBroker`: `POST .../completions` starts
`PreparedTurn.stream()` as a background task (`AppState.spawn`) that writes its frames
into an `ActiveTurn` buffer, and the response is a *reader* of that buffer. A reader
going away means nothing to the producer. In-process, single-worker state, like the
approval gate (ADR-0020) and model jobs (ADR-0017), for the same reason: a turn in
flight is meaningless after the restart that killed the generation behind it.

### Coming back replays the turn
`GET /api/chats/{id}/stream` attaches to a running turn, replaying from the first
frame — which is enough, because the event protocol is self-describing and starts with
the `start` frame carrying both message ids. `from=<index>` resumes where a reader left
off; `from=now` follows only what happens next, which is what a client watching a turn
it navigated away from needs, since it wants the end and not the text.

A finished turn stays readable for five minutes so a reader arriving just after the end
still gets the answer, after which the persisted message is the record.

### Stopping is asked for
`POST /api/chats/{id}/stop` cancels the running turn; what the model produced is kept,
as before. The stop button now says so to the server instead of dropping a socket. The
route answers with msgspec rather than a declared response model: it sits inside the
chat prefix the hot-path boundary test guards (ADR-0001).

### One turn per conversation
Starting a second turn in a conversation that already has one is a 409. Two turns
writing one conversation would interleave their messages, and the client has no reason
to: it either reads the running one or stops it.

### Telling the reader it finished
Two channels, because one is not always available. The in-app notice and the tab title
work everywhere. A system notification needs a secure context — HTTPS or localhost — so
on a plain `http://192.168.x.x` instance the browser offers no Notification API at all;
`lib/notify.ts` adds it when the browser allows, asks for permission at the moment a
reply is about to be produced rather than on load, and putting TLS in front of velox-ui
later turns it on with no other change.

## Consequences
- A reply now costs what it costs: leaving a conversation, or closing the laptop lid,
  no longer stops a model that is mid-answer on hardware already busy generating it.
- Heartbeats moved from the turn to the reader. They were a property of a connection
  all along, and buffering them would have replayed a turn's worth of pings.
- Memory: a turn's frames are held until it ends plus five minutes. A safety valve
  stops a turn whose frames pass 4 MB, which is roughly a million tokens of text —
  a runaway, not a conversation.
- Multiple workers would split the broker, as they already split the approval gate and
  the job registries: a reader could reach a worker that does not have the turn. One
  worker stays the supported shape (`settings.workers`).
- An abandoned turn now runs to completion rather than stopping early, so a client that
  starts turns and walks away costs the backend more than it used to. The 409 keeps one
  conversation to one turn, but a script could still start turns in many conversations;
  this is a single-tenant home-server product, and that trade is documented rather than
  guarded.
- ADR-0005 still holds where it matters: persistence stays off the hot path and what
  the model produced is kept when a turn is stopped. What changed is that an abandoned
  *reader* is no longer read as an abandoned *turn*.
