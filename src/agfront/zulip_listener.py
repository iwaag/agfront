"""agfront's chat entrance: serve `front-*` topics, dispatch to `#general`.

The mechanics are pyagag's, shared with the other agents' listeners:
`agag.zulip.sweep_serve` finds every unresolved `front-*` topic whose last
poster is not this bot, and `agag.topics.serve_topic` serves each one — ack,
numbered generation workspace, `chatlog.md`, the run, always reply, then
re-check for posts that arrived during the run.

What is agfront's own is small on purpose: one role, and one command file.
The run writes `dispatch.md` — first line the topic, the rest the message —
and *this* handler posts it, the way agautolab acts on `new_mission.md`. The
channel is not in the file: Front reaches `#general` because that is the one
channel it is subscribed to, and subscription is the routing decision.

Nothing comes back. Whoever picks the dispatched topic up answers inside that
topic, not here; the reply into the `front-*` topic says what was sent and
where, and says that much honestly.
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
# would not widen what Front can reach.
DISPATCH_CHANNEL = "general"
DISPATCH_FILE = "dispatch.md"

ACK_TEXT = "Message received. Please wait for the reply."
EMPTY_REPLY = "There is nothing in this topic to answer yet."

# Front reads a conversation and writes one file. It never generates, builds
# or runs anything, so it gets agforge's front budget, not its generator's.
FRONT_TIMEOUT_SECONDS = 360

__all__ = [
    "DISPATCH_CHANNEL",
    "DISPATCH_FILE",
    "ZULIP_ENV",
    "ListenerError",
    "front_prompt",
    "generation_dir",
    "guide",
    "handle_dispatch",
    "handle_topic",
    "main",
    "observe_topic",
    "parse_dispatch",
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
    generation's `dispatch.md` from being posted a second time.
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


def parse_dispatch(text: str) -> tuple[str, str]:
    """Split a `dispatch.md` into its topic and its message.

    First line is the topic, the rest is the message. Both are required: a
    dispatch with no topic has no destination, and one with no body would
    post an empty message into a channel other agents are watching.
    """
    topic, _, body = text.partition("\n")
    topic, body = topic.strip(), body.strip()
    if not topic:
        raise ListenerError(f"{DISPATCH_FILE} has no topic on its first line")
    if not body:
        raise ListenerError(f"{DISPATCH_FILE} has a topic but no message under it")
    return topic, body


def handle_dispatch(client: ZulipClient, front_dir: Path) -> list[str]:
    """Post what the run asked to have posted, and report where it went.

    What the front *wrote* drives this; its answer is relayed verbatim and
    never parsed. No `dispatch.md` means no dispatch — a Front that refused,
    or that only answered a question, is the normal case, not a failure.
    """
    path = front_dir / DISPATCH_FILE
    if not path.is_file():
        return []
    topic, body = parse_dispatch(path.read_text(encoding="utf-8"))
    topic_write(topic, body, channel=DISPATCH_CHANNEL, client=client)
    return [f"dispatched to #{DISPATCH_CHANNEL} > {topic}; the reply will appear there"]


def serve(context) -> TopicResult:
    """agfront's part of one serving: the front run, then its dispatch."""
    number = next_generation(topic_workspace(context.channel, context.topic))
    front_dir = generation_dir(context.channel, context.topic, number, "front")
    chatlog_path(front_dir).write_text(
        format_chatlog(context.history, context.self_id, drop=is_ack), encoding="utf-8"
    )

    context.step = "front"
    sections = [run_front(front_prompt(context.bot_name), front_dir)]

    context.step = "dispatch"
    sections.extend(handle_dispatch(context.client, front_dir))
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
    # post there, and this is what keeps it from also answering the `run-`
    # topics it creates: no bot loop, by filter and not by luck.
    log(f"agfront zulip listener starting (pull sweep, prefix {FRONT_TOPIC_PREFIX!r})")
    try:
        sweep_serve(client, handler, topic_filter=(FRONT_TOPIC_PREFIX,))
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
