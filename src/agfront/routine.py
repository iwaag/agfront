"""A routine run: a `routinerun-` topic Front owns, opens, drives and closes.

`refine_routine` p1 step 2. A routine is a guide in Zulip (channel folder
`routine`, one channel per routine, its `guide` topic). Asked to run one,
Front opens a `routinerun-<id>` topic in that channel with **one opening
post** — the request verbatim, the conditions as Front read them, the guide
post it read, and where the request came from — and that topic is then a
conversation of Front's own: served by the ordinary owner route, resumed by
the ordinary callback route, and answered at home like any `front-*` topic,
but by the `routine_run` role with its own guide.

Three things are Front's own here and are pinned in this module.

**Starting.** The owner sweep serves a topic when somebody *else* spoke last,
so a topic Front opened alone is never served by it, and a run would sit
unstarted forever. `unstarted` says which of Front's own run topics has not
been served yet — nothing but Front's own speech and no serving ack in it —
and the listener starts those right after the serving that opened them
(`start_opened_runs`), and again at startup for one that was opened just
before a crash (`recover_unstarted_runs`). A run opened by a human is served
by the owner route like any other topic, so a hand-written opening post
starts a run too.

**Origin.** `agentchat send` anchors the topic it posts into with a
`[selfnote][rootchat] <home>` note. In a topic Front owns that note has a
different meaning from the one it has in autolab's topic: it says which
conversation *opened* this run — the requester — and never redirects a
callback, because a topic Front owns is served as itself. `origin_of` reads
it; a run with no such note was opened by hand and reports where it is.

**Finishing.** A run ends when the `routine_run` role says so, in one fenced
`ag-routinerun` block at the end of its reply (`FinishReport`). The
listener, not the run, delivers the report into the origin conversation and
resolves the run topic, so the origin never carries a root note pointing at
the run and a resolved run is read as finished by everyone
(`agentchat send` refuses it, the sweeps skip it).

**Continuing.** The delivery is Front's own post, so it leaves the origin
with Front as its last speaker — and the owner sweep skips exactly that
(`sweep_topics`, and the event path's re-check with it). A request that asked
for one routine is then finished, but a request that asked for something
*after* the routine has nobody left to notice: Front is never served again,
and the next stage is never started. `routine_tests` p1 step 1 found that by
inspection, before the trial.

So the delivery is followed by a **delivered note** in the origin:

    [selfnote][delivered] <run channel>/<run topic>

A note buys nobody a run (`agag.selfnote`), so this changes nothing about who
the sweeps serve. What it does is make "a report landed here and nothing has
served this conversation since" a question the *chat* can answer —
`awaiting_continuation` — which is what the listener's `continue_deliveries`
asks after every run serving and again at startup. The serving it triggers is
an ordinary one: the conversation, its chatlog, its own role and guide. **What
happens next is decided there, in the conversation, by Front** — this module
knows only that somebody should look, never what any particular request is
made of.

The note is cleared by speech, and an ack is not speech enough: `is_ack` is
skipped, so a crash between a serving's ack and its reply leaves the handoff
still owed rather than silently spent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from agag.agent import SWEEP_ACK, is_ack
from agag.selfnote import (
    Conversation,
    is_speech,
    note,
    own_rootchat,
    parse_conversation,
    parse_note,
)
from agag.zulip import (
    LAST_SPEAKER_LOOKBACK,
    RESOLVED_TOPIC_PREFIX,
    ZulipClient,
    live_topic_name,
    remotes_for_home,
    rootchat_notes,
)

#: The topic prefix of a run, in the routine's own channel.
RUN_PREFIX = "routinerun-"
#: The role that serves a run topic (`agents.toml`, `agent/guides/routine_run/`).
ROUTINE_ROLE = "routine_run"

#: The tag of the note that says a run's report landed in this conversation
#: and nothing has served it since. Front's own; see the module docstring.
DELIVERED_TAG = "delivered"

SCHEMA = "ag.routinerun-finish.v1"
FENCE = "ag-routinerun"
ERROR_FENCE = "ag-routinerun-error"
#: This realm truncates a post silently past 10000 characters.
MAX_REPORT_CHARS = 8000

_BLOCK = re.compile(r"```[ \t]*" + re.escape(FENCE) + r"[ \t]*\n(.*?)\n[ \t]*```[ \t]*", re.DOTALL)
_MENTION = re.compile(r"@\*\*([^*\n]+)\*\*")

__all__ = [
    "DELIVERED_TAG",
    "ERROR_FENCE",
    "FENCE",
    "ROUTINE_ROLE",
    "RUN_PREFIX",
    "SCHEMA",
    "FinishError",
    "FinishReport",
    "awaiting_continuation",
    "delivered_note",
    "delivery_text",
    "late_answer_text",
    "parse_delivered",
    "pending_continuations",
    "is_run_topic",
    "opened_runs",
    "origin_of",
    "record_text",
    "recover_unstarted_runs",
    "split_finish",
    "unstarted",
]


def is_run_topic(topic: str) -> bool:
    return topic.startswith(RUN_PREFIX)


# --- starting ----------------------------------------------------------------


def unstarted(history: list[dict], self_id: int) -> bool:
    """Whether this run topic has been opened and never served.

    True when somebody has spoken, every speaker is Front itself, and none of
    Front's posts is the serving ack. The ack is what a serving posts first,
    so its presence — even before a failed run's "failed during …" reply — is
    what says "started", and a run is never started twice by this rule. A
    progress record Front posts from a serving is Front's own speech too, and
    it follows an ack, so it never re-starts anything either.
    """
    spoke = [m for m in history if is_speech(m)]
    if not spoke:
        return False
    if any(m.get("sender_id") != self_id for m in spoke):
        return False
    return not any(
        m.get("sender_id") == self_id and str(m.get("content", "")).strip() == SWEEP_ACK
        for m in history
    )


def opened_runs(client: ZulipClient, home: tuple[str, str]) -> list[Conversation]:
    """The run topics the conversation `home` has opened, oldest first.

    Read from the chat: a run topic carries Front's root note naming the
    conversation it was opened from, exactly as a delegate's topic does.
    """
    return [
        remote for remote in remotes_for_home(client, home[0], home[1])
        if is_run_topic(remote.topic)
    ]


def recover_unstarted_runs(client: ZulipClient, self_id: int, *, lookback: int = 50) -> list[Conversation]:
    """Every run topic Front opened, from any conversation, that was never served.

    Startup recovery: the serving that opened a run starts it, and a crash
    between the two leaves a run nobody will start — the owner sweep skips a
    topic whose last speaker is Front. Asked of the chat, as everything else
    is: Front's own root notes name every topic it opened.
    """
    found: list[Conversation] = []
    for (channel, topic), _home in rootchat_notes(client):
        if not is_run_topic(topic):
            continue
        history = client.topic_history(channel, topic, num_before=lookback)
        if unstarted(history, self_id):
            found.append(Conversation(channel, topic))
    return found


# --- origin ------------------------------------------------------------------


def origin_of(history: list[dict], self_id: int) -> Conversation | None:
    """The conversation this run was opened from, or None for a hand-opened run."""
    return own_rootchat(history, self_id)


# --- finishing ---------------------------------------------------------------


class FinishError(ValueError):
    """The finish block is present and cannot be used; the reply still can."""


@dataclass(frozen=True)
class FinishReport:
    """What the run says when it ends: whether the routine's goal was reached,
    why the run ends, and the report for whoever asked."""

    achieved: bool
    reason: str
    report: str

    def payload(self) -> dict:
        return {"schema": SCHEMA, "achieved": self.achieved, "reason": self.reason,
                "report": self.report}

    def serialize(self) -> str:
        return json.dumps(self.payload(), ensure_ascii=False, indent=1)


def _plain(text: str) -> str:
    """No live mention: the report is posted where a `@**name**` would summon
    somebody into the requester's conversation."""
    return _MENTION.sub(r"\1", text)


def parse_finish(body: str) -> FinishReport:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as error:
        raise FinishError(f"the {FENCE} block is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise FinishError(f"the {FENCE} block must be a JSON object")
    if data.get("schema") != SCHEMA:
        raise FinishError(f"the {FENCE} block declares schema {data.get('schema')!r}, not {SCHEMA!r}")
    achieved = data.get("achieved")
    if not isinstance(achieved, bool):
        raise FinishError("`achieved` must be true or false: whether the routine's goal was reached")
    reason = str(data.get("reason") or "").strip()
    report = str(data.get("report") or "").strip()
    if not reason:
        raise FinishError("`reason` must say why the run ends")
    if not report:
        raise FinishError("`report` must carry the report for the requester")
    if len(report) > MAX_REPORT_CHARS:
        raise FinishError(f"`report` is longer than {MAX_REPORT_CHARS} characters, which this realm would truncate")
    return FinishReport(achieved=achieved, reason=_plain(reason), report=_plain(report))


def split_finish(output: str) -> tuple[str, FinishReport | None, str | None]:
    """`(reply, finish, error)` from the run's whole output.

    The block is the *last* fence of its kind. No block: the run goes on and
    the reply is its record. A usable block: the run ends with it. An
    unusable one: the reply is posted with the error fence after it and the
    run stays open — presentation never ends a run.
    """
    matches = list(_BLOCK.finditer(output))
    if not matches:
        return output.strip(), None, None
    last = matches[-1]
    reply = (output[:last.start()] + output[last.end():]).strip()
    body = last.group(1).strip()
    try:
        return reply, parse_finish(body), None
    except FinishError as error:
        return reply, None, str(error)


def record_text(reply: str, finish: FinishReport | None, error: str | None) -> str:
    """The post into the run topic: the reply, then the canonical block or
    the error fence."""
    parts = [reply] if reply else []
    if finish is not None:
        parts.append(f"```{FENCE}\n{finish.serialize()}\n```")
    elif error is not None:
        parts.append(f"```{ERROR_FENCE}\n{error}\n```")
    return "\n\n".join(parts) if parts else (reply or "")


def late_answer_text(run: Conversation, remote: Conversation) -> str:
    """Told to the requester when a finished run's delegate answers anyway.

    A run that has ended is not reopened — it is resolved, and a resolved
    conversation is finished for everybody. But the answer is real work, and
    the request that asked for the run may still be live, so it is named here
    rather than dropped: the conversation decides whether it needs another run.
    """
    return (f"**A finished routine run was answered.** #{run.channel} › `{run.topic}` "
            f"has already ended and is resolved, and an answer arrived afterwards in "
            f"#{remote.channel} › `{remote.topic}`.\n\n"
            f"Nothing has been done with it. Read that topic and decide whether the work "
            f"this request is waiting for still needs a run.")


def delivery_text(finish: FinishReport, run: Conversation) -> str:
    """The report as it lands in the requester's conversation."""
    verdict = ("the routine's goal was reached" if finish.achieved
               else "the run ended without reaching the routine's goal")
    return (f"**Routine run finished** — {verdict}. Reason: {finish.reason}\n\n"
            f"{finish.report}\n\n"
            f"Run: #{run.channel} › `{run.topic}` (resolved).")


# --- continuing --------------------------------------------------------------


def delivered_note(run: Conversation) -> str:
    """The note written into the requester's conversation after a run's report.

    It names the run whose report just landed. Nothing reads it for the run's
    sake — the run is over — only to answer "has anybody looked at this
    conversation since?".
    """
    return note(DELIVERED_TAG, str(run))


def parse_delivered(content) -> Conversation | None:
    """The run a delivered note names, or None if this is not one."""
    return parse_conversation(parse_note(content, DELIVERED_TAG))


def awaiting_continuation(history: list[dict], self_id: int) -> Conversation | None:
    """The run whose report landed here and that nothing has served since.

    Reading forward: our own delivered note arms the handoff, and speech
    disarms it — whoever spoke, because the developer's own next post is
    served by the ordinary owner route and Front's reply is the serving this
    exists to buy. A serving **ack** is not that reply, so it does not
    disarm: a crash between the ack and the reply leaves the handoff owed,
    and startup recovery finds it.
    """
    pending: Conversation | None = None
    for message in history:
        content = str(message.get("content", ""))
        if is_speech(message):
            if not is_ack(content.strip()):
                pending = None
            continue
        if message.get("sender_id") != self_id:
            continue
        run = parse_delivered(content)
        if run is not None:
            pending = run
    return pending


def pending_continuations(
    client: ZulipClient, self_id: int, *, lookback: int = LAST_SPEAKER_LOOKBACK
) -> list[Conversation]:
    """Every conversation of Front's that is holding an unserved run report.

    Asked of the chat, like everything else here: Front's own root notes name
    each run topic and the conversation it was opened from, so the requesters
    are found without a ledger. A requester is looked at once however many
    runs it opened, under its live name, and a resolved one is skipped — a
    conversation somebody has closed is not one to reopen with a report it has
    already seen.
    """
    seen: set[tuple[str, str]] = set()
    found: list[Conversation] = []
    for (_channel, topic), home in rootchat_notes(client, include_resolved=True):
        if not is_run_topic(topic) or home.as_pair() in seen:
            continue
        seen.add(home.as_pair())
        name = live_topic_name(client, home.channel, home.topic)
        if name.startswith(RESOLVED_TOPIC_PREFIX):
            continue
        history = client.topic_history(home.channel, name, num_before=lookback)
        if awaiting_continuation(history, self_id) is not None:
            found.append(Conversation(home.channel, name))
    return found
