"""agfront's chat entrance: serve `front-*` topics, ask forge in `#general`.

The mechanics are pyagag's, shared with the other agents' listeners:
`agag.zulip.sweep_serve` finds every unresolved `front-*` topic whose last
poster is not this bot, and `agag.topics.serve_topic` serves each one — ack,
numbered generation workspace, `chatlog.md`, the run, always reply, then
re-check for posts that arrived during the run.

What is agfront's own is small on purpose: one role, and one command file.
The guide gives Front three branches — answer it, describe the wanted asset
in `create.md`, or refuse — and only the middle one leaves the topic. When
`create.md` exists, *this* handler posts it into a fresh `create-` topic, the
way agautolab acts on `new_mission.md`.

Neither the channel nor the topic is Front's to choose. The channel is
`#general` because that is the one channel Front is subscribed to besides its
own entrance, and subscription is the routing decision; the topic is derived
from the conversation that asked for it, so a request can be read back to its
cause. `create-` is agforge's request prefix, so forge picks the topic up.

Nothing comes back. Forge answers inside the `create-` topic, not here; the
reply into the `front-*` topic says what was sent and where, and says that
much honestly.
"""

from __future__ import annotations

import os
from pathlib import Path

from agag.topics import (
    TopicResult,
    chatlog_placement,
    chatlog_path,
    format_chatlog,
    generation_dir as shared_generation_dir,
    guide as shared_guide,
    next_generation,
    next_record_path as shared_next_record_path,
    prompt_with_guide,
    serve_topic,
    topic_workspace as shared_topic_workspace,
)
from agag.zulip import ZulipClient, log, sweep_serve, topic_write

from .role_run import AGFRONT_ROOT, run_role

ZULIP_ENV = AGFRONT_ROOT / ".local" / "zulip.env"
TOPICS_ROOT = AGFRONT_ROOT / ".local" / "topics"
GUIDES = AGFRONT_ROOT / "agent" / "guides"
RECORDS_ROOT = AGFRONT_ROOT / ".local" / "agent"

FRONT_TOPIC_PREFIX = "front-"
# Front's one outbound channel this phase. It is also the only channel Front
# is subscribed to besides its own entrance, so widening this constant alone
# would not widen what Front can reach. agforge is subscribed to it too, which
# is what makes a `create-` topic here reach the agent that serves it.
OUTBOUND_CHANNEL = "general"
CREATE_FILE = "create.md"
# agforge's request prefix (`agforge.zulip_listener.REQUEST_TOPIC_PREFIX`).
CREATE_TOPIC_PREFIX = "create-"

ACK_TEXT = "Message received. Please wait for the reply."
EMPTY_REPLY = "There is nothing in this topic to answer yet."

# Front reads a conversation and writes one file. It never generates, builds
# or runs anything, so it gets agforge's front budget, not its generator's.
FRONT_TIMEOUT_SECONDS = 360

__all__ = [
    "CREATE_FILE",
    "CREATE_TOPIC_PREFIX",
    "OUTBOUND_CHANNEL",
    "ZULIP_ENV",
    "ListenerError",
    "create_topic_name",
    "front_prompt",
    "generation_dir",
    "guide",
    "handle_create",
    "handle_topic",
    "main",
    "observe_topic",
    "run_front",
    "serve",
    "topic_workspace",
]


class ListenerError(RuntimeError):
    """One front-topic workflow could not complete."""


def topic_workspace(channel: str, topic: str) -> Path:
    """`.local/topics/<channel>/<topic>/` — the conversation's own directory."""
    return shared_topic_workspace(TOPICS_ROOT, channel, topic)


def generation_dir(channel: str, topic: str, number: int, role: str) -> Path:
    """`.local/topics/<channel>/<topic>/<N>/<role>/`.

    Generations are never deleted. Cutting a new one is what stops a previous
    generation's `create.md` from being posted a second time.
    """
    return shared_generation_dir(TOPICS_ROOT, channel, topic, number, role)


def guide(*parts: str) -> str:
    return shared_guide(GUIDES, *parts)


def next_record_path(directory: Path) -> Path:
    return shared_next_record_path(directory)


def is_ack(content: str) -> bool:
    """Our own transport noise, which is not conversation."""
    return content == ACK_TEXT


def front_prompt(bot_name: str) -> str:
    return prompt_with_guide([chatlog_placement(bot_name)], guide("front", "guide.md"))


def run_front(prompt: str, cwd: Path) -> str:
    """One front run in the topic workspace, with its `ag.agent-run.v1` record."""
    record = next_record_path(RECORDS_ROOT / "front")
    output, _, exit_code = run_role(
        "front",
        prompt,
        cwd=cwd,
        timeout=FRONT_TIMEOUT_SECONDS,
        record=record,
    )
    if exit_code != 0:
        raise ListenerError(f"front run exited {exit_code}: {output.strip()[:500]}")
    return output.strip()


def create_topic_name(topic: str, number: int) -> str:
    """The `create-` topic one request gets, derived from its conversation.

    `front-20260817-advance` generation 1 → `create-20260817-advance-1`. The
    generation number is what keeps a second request in the same conversation
    from landing in the first one's topic, and the stem is what lets anyone
    reading `#general` find the conversation that caused the request.
    """
    stem = topic[len(FRONT_TOPIC_PREFIX):] if topic.startswith(FRONT_TOPIC_PREFIX) else topic
    stem = stem.strip() or "request"
    return f"{CREATE_TOPIC_PREFIX}{stem}-{number}"


def handle_create(client: ZulipClient, front_dir: Path, topic: str, number: int) -> list[str]:
    """Post what the run asked to have created, and report where it went.

    What the front *wrote* drives this; its answer is relayed verbatim and
    never parsed. No `create.md` means no request — a Front that refused, or
    that only answered a question, is the normal case, not a failure. An
    empty one is an error: a blank post into a channel other agents watch
    would start a forge run over nothing.
    """
    path = front_dir / CREATE_FILE
    if not path.is_file():
        return []
    body = path.read_text(encoding="utf-8").strip()
    if not body:
        raise ListenerError(f"{CREATE_FILE} is empty")
    create_topic = create_topic_name(topic, number)
    topic_write(create_topic, body, channel=OUTBOUND_CHANNEL, client=client)
    return [
        f"asked forge in #{OUTBOUND_CHANNEL} > {create_topic}; "
        "the reply will appear there"
    ]


def serve(context) -> TopicResult:
    """agfront's part of one serving: the front run, then its create request."""
    number = next_generation(topic_workspace(context.channel, context.topic))
    front_dir = generation_dir(context.channel, context.topic, number, "front")
    chatlog_path(front_dir).write_text(
        format_chatlog(context.history, context.self_id, drop=is_ack), encoding="utf-8"
    )

    context.step = "front"
    sections = [run_front(front_prompt(context.bot_name), front_dir)]

    context.step = "create"
    sections.extend(handle_create(context.client, front_dir, context.topic, number))
    return TopicResult(sections)


def handle_topic(client: ZulipClient, channel: str, topic: str) -> None:
    """Serve one awaiting front topic through the shared skeleton."""
    log(f"front topic {channel!r}/{topic!r}")
    serve_topic(client, channel, topic, serve, ack_text=ACK_TEXT, empty_reply=EMPTY_REPLY)


def observe_topic(channel: str, topic: str) -> None:
    """Passive handler (`AGFRONT_ZULIP_LOG_ONLY=1`): log sweep matches, never act."""
    log(f"observed sweep match {channel!r}/{topic!r}")


def main() -> None:
    client = ZulipClient.from_env(ZULIP_ENV)
    if os.environ.get("AGFRONT_ZULIP_LOG_ONLY") == "1":
        handler = observe_topic
    else:
        def handler(channel: str, topic: str) -> None:
            handle_topic(client, channel, topic)

    # The filter is `front-` only. Front is subscribed to #general so it can
    # post there, and this is what keeps it from also answering the `create-`
    # topics it opens: no bot loop, by filter and not by luck. The other side
    # of the same asymmetry is agforge, which sweeps `create-` and never
    # `front-`.
    log(f"agfront zulip listener starting (pull sweep, prefix {FRONT_TOPIC_PREFIX!r})")
    try:
        sweep_serve(client, handler, topic_filter=(FRONT_TOPIC_PREFIX,))
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
