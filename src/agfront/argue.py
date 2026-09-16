"""Front as the owner of an argue (`argue` p1).

An argue is `#argue › argue-<stem>`: a conversation in which a human develops
a desire with every agent (`agag.argue` is the contract every participant
shares). Front is the one agent served **automatically** there — it owns the
`argue-` prefix — and it facilitates: it asks for the desire until a human
has stated it, invites other agents by naming them, and keeps the thread
moving. Everybody else speaks only when named.

What is Front's own here, and pinned by `tests/test_argue.py`:

- **No automatic hand-off mention.** Every other Front serving names the
  last other speaker so that the next turn happens; in an argue that would
  pull whoever spoke last back in for a run each time. The reply is posted
  as written, and an invitation is a mention Front writes on purpose.
- **The desire is recorded by this listener, never by the run's say-so.**
  The run may end its reply with an `ag-argue` block naming the message it
  takes as the human's desire; the message must be in this conversation and
  a human's, or the reply says why not and the next serving is asked again.
- **A hand-opened argue gets its anchor here.** `agentchat argue open`
  writes `[selfnote][argue]` first; a human who simply posts under a new
  `argue-` name gets the note written by the first serving, so the argue has
  an identity either way.
"""

from __future__ import annotations

from pathlib import Path

from agag.agent import SWEEP_ACK as ACK_TEXT, exec_options_for, is_ack
from agag.argue import (
    ARGUE_CHANNEL,
    Anchor,
    Desire,
    anchor as argue_anchor,
    argue_note,
    desire_note,
    desire_placement,
    is_argue_topic,
    recorded_desire,
    split_block,
    validate_desire,
)
from agag.entrance import EMPTY_REPLY
from agag.intro import write_agents_md
from agag.topics import (
    HISTORY_MESSAGES,
    TopicResult,
    chatlog_placement,
    chatlog_path,
    conversation_context,
    generation_dir,
    next_generation,
    prompt_with_guide,
    serve_topic,
    topic_workspace,
)
from agag.zulip import ZulipClient, log

from . import zulip_listener as front
from .evidence import format_evidence

ARGUE_ROLE = "argue"

__all__ = ["ARGUE_ROLE", "anchor_placement", "argue_prompt", "handle_argue", "humans_of", "serve_argue"]


def anchor_placement(anchor: Anchor | None) -> str:
    if anchor is None:
        return "This argue has no anchor note yet; one is written when this serving ends."
    origin = f"opened from {anchor.origin}" if anchor.origin is not None else "opened by hand"
    return f"This is {anchor.label}, {origin}."


def argue_prompt(bot_name: str, conversation: str, anchor: Anchor | None, desire: Desire | None,
                 history: list[dict]) -> str:
    lines = [chatlog_placement(bot_name), anchor_placement(anchor), desire_placement(desire, history), "", conversation]
    return prompt_with_guide(lines, front.guide(ARGUE_ROLE, "guide.md"))


def humans_of(client: ZulipClient) -> set[int]:
    """The realm's human user ids — Zulip's `is_bot`, read once per serving."""
    return {int(u["user_id"]) for u in client.users() if not u.get("is_bot") and u.get("user_id") is not None}


def serve_argue(context) -> TopicResult:
    """One Front serving of an argue: the files, the run, the notes."""
    context.step = "anchor"
    anchor = argue_anchor(context.history)
    if anchor is None:
        context.client.send_to_channel(context.channel, context.topic, argue_note(None))
        log(f"anchored hand-opened argue {context.channel!r}/{context.topic!r}")
    desire = recorded_desire(context.history)

    context.step = "chatlog"
    number = next_generation(topic_workspace(front.TOPICS_ROOT, context.channel, context.topic))
    workspace = generation_dir(front.TOPICS_ROOT, context.channel, context.topic, number, ARGUE_ROLE)
    chatlog = format_evidence(context.history, context.self_id, channel=context.channel, topic=context.topic,
                              drop=is_ack, bounded=len(context.history) >= HISTORY_MESSAGES,
                              history_messages=HISTORY_MESSAGES)
    chatlog_path(workspace).write_text(chatlog, encoding="utf-8")
    context.step = "harvest"
    write_agents_md(context.client, workspace)

    context.step = ARGUE_ROLE
    output = front.run_front(
        argue_prompt(context.bot_name, conversation_context(chatlog), anchor, desire, context.history),
        workspace, (context.channel, context.topic), ARGUE_ROLE,
        extra_meta={"argue": anchor.message_id} if anchor else None,
        selection=context.selection,
    )

    context.step = "block"
    text, fields, error = split_block(output)
    notes: list[str] = []
    if error:
        notes.append(f"(your {'ag-argue'} block was not readable: {error})")
    if fields and fields.get("desire"):
        notes.append(record_desire(context, fields["desire"], desire))
    if fields and fields.get("complete", "").lower() == "true":
        log(f"argue {context.channel!r}/{context.topic!r} says it is complete; completion is outside step 1")
    body = "\n\n".join(part for part in [text, *notes] if part)
    return TopicResult([body or EMPTY_REPLY])


def record_desire(context, value: str, existing: Desire | None) -> str:
    """Write the desire note, or say why the designation was refused."""
    try:
        message_id = int(value)
    except ValueError:
        return f"(the desire must be named by message id, not {value!r})"
    if existing is not None:
        return f"(the desire is already on record as message {existing.message_id})"
    humans = humans_of(context.client)
    desire, why = validate_desire(context.history, message_id, self_id=context.self_id,
                                  is_human=lambda user_id: user_id in humans)
    if desire is None:
        log(f"refused desire designation {message_id} in {context.channel!r}/{context.topic!r}: {why}")
        return f"(not recorded as the desire: {why})"
    context.client.send_to_channel(context.channel, context.topic, desire_note(desire.message_id, desire.user_id))
    log(f"recorded the desire of {context.channel!r}/{context.topic!r}: message {desire.message_id}")
    return f"— the desire is on record as message {desire.message_id}."


def handle_argue(client: ZulipClient, channel: str, topic: str) -> None:
    """Serve one argue Front owns. An `argue-` topic outside `#argue` is
    not one, and is left alone rather than answered."""
    if not is_argue_topic(channel, topic):
        log(f"ignoring {channel!r}/{topic!r}: argues live in #{ARGUE_CHANNEL}")
        return
    log(f"argue {channel!r}/{topic!r}")
    serve_topic(
        client, channel, topic, serve_argue, ack_text=ACK_TEXT, empty_reply=EMPTY_REPLY,
        handoff=False, exec_options=exec_options_for(front.SPEC, client),
    )
