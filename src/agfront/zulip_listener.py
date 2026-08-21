"""agfront's chat entrance: serve `front-*` topics, and let Front do the rest.

The mechanics are pyagag's, shared with the other agents' listeners:
`agag.zulip.sweep_serve` finds every unresolved `front-*` topic whose last
poster is not this bot, and `agag.topics.serve_topic` serves each one — ack,
numbered generation workspace, `chatlog.md`, the run, always reply, then
re-check for posts that arrived during the run.

What is agfront's own is now two files and no routing at all: `chatlog.md`,
the conversation, and `tools/agents.md`, the other agents' own introductions
harvested from `#agents` for this run. Front reads those, decides whether
anybody can serve the request, asks the Developer, and — if permitted —
speaks to that agent itself with `agentchat`.

**No outbound route lives here any more.** Until p2 the run wrote `create.md`
and *this* handler posted it into an asset-request topic in `#general` whose
name it derived itself, because Front was not allowed to name a channel. That was a shackle around an agent
that could not read the board; now it can. Which agent, which channel and
which topic prefix are things Front learns by reading, which is precisely the
capability this phase exists to demonstrate — so grepping this file for
another agent's channel must find nothing.

Multi-turn needs no code: the guide asks permission before acting, and
`serve_topic` re-checks the topic after each run, so the Developer's answer
is simply the next serving.

**Since `agent_standardize` p7 a supervision is not a long run — it is
several short ones.** Front posts into another agent's topic and finishes;
`agentchat send` records that participation, and when that agent's reply
names Front, `sweep_serve`'s mention route serves this `front-*` conversation
again with the remote thread beside its chatlog, and Front answers there.
Nothing blocks, nothing is backgrounded, and a run that ends is not a
supervision that stopped.
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
    threads_placement,
    topic_workspace as shared_topic_workspace,
    write_threads,
)
from agag.intro import write_agents_md
from agag.participation import home_for, remotes_for_home
from agag.zulip import ZulipClient, log, sweep_serve

from .role_run import AGENTCHAT_LEDGER, AGFRONT_ROOT, ZULIP_ENV, run_role

TOPICS_ROOT = AGFRONT_ROOT / ".local" / "topics"
GUIDES = AGFRONT_ROOT / "agent" / "guides"
RECORDS_ROOT = AGFRONT_ROOT / ".local" / "agent"

FRONT_TOPIC_PREFIX = "front-"

ACK_TEXT = "Message received. Please wait for the reply."
EMPTY_REPLY = "There is nothing in this topic to answer yet."

# Front reads a conversation, reads the board, and posts a message or two. It
# generates, builds and runs nothing, and it no longer waits for anybody:
# 360 s, the pre-p5 ceiling, restored in `agent_standardize` p7.
#
# p5 raised this to 3600 so a supervising run could block on `agentchat wait`.
# p6 showed the ceiling was never the binding constraint — the run that
# failed used 242 s of it and ended itself. Supervision is now several short
# servings, each brought back by a mention, so there is nothing left for an
# hour-long budget to buy. What it did cost was real: this listener is
# serial, so a supervising run was also how long the Developer's next post
# waited to be answered.
FRONT_TIMEOUT_SECONDS = 360

__all__ = [
    "ZULIP_ENV",
    "ListenerError",
    "front_prompt",
    "generation_dir",
    "guide",
    "handle_mention",
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

    Generations are never deleted, so each turn of a conversation keeps the
    board it actually saw beside the chatlog it actually read.
    """
    return shared_generation_dir(TOPICS_ROOT, channel, topic, number, role)


def guide(*parts: str) -> str:
    return shared_guide(GUIDES, *parts)


def next_record_path(directory: Path) -> Path:
    return shared_next_record_path(directory)


def is_ack(content: str) -> bool:
    """Our own transport noise, which is not conversation."""
    return content == ACK_TEXT


def front_prompt(bot_name: str, threads=(), root: Path | None = None) -> str:
    """The placement lines, then the guide.

    Placement says where the files are; the guide says what to produce. The
    threads line only appears when there are threads, so a first request
    never carries a sentence about files that are not there.
    """
    lines = [chatlog_placement(bot_name)]
    if placement := threads_placement(threads, root or Path(".")):
        lines.append(placement)
    return prompt_with_guide(lines, guide("front", "guide.md"))


def run_front(prompt: str, cwd: Path, home: tuple[str, str]) -> str:
    """One front run in the topic workspace, with its `ag.agent-run.v1` record.

    `home` is the `front-*` conversation being served. Anything this run
    posts elsewhere is recorded against it, so the answer comes back here.
    """
    record = next_record_path(RECORDS_ROOT / "front")
    output, _, exit_code = run_role(
        "front",
        prompt,
        cwd=cwd,
        timeout=FRONT_TIMEOUT_SECONDS,
        record=record,
        home=home,
    )
    if exit_code != 0:
        raise ListenerError(f"front run exited {exit_code}: {output.strip()[:500]}")
    return output.strip()


def serve(context) -> TopicResult:
    """agfront's part of one serving: give the run its files, then run.

    Three kinds of file now: the conversation being served (`chatlog.md`),
    the conversations Front has taken part in elsewhere (`threads/`), and the
    board (`tools/agents.md`). One of the threads is usually why this run is
    happening at all.
    """
    number = next_generation(topic_workspace(context.channel, context.topic))
    front_dir = generation_dir(context.channel, context.topic, number, "front")
    chatlog_path(front_dir).write_text(
        format_chatlog(context.history, context.self_id, drop=is_ack), encoding="utf-8"
    )

    context.step = "threads"
    threads = write_threads(
        context.client,
        front_dir,
        [
            conversation.as_pair()
            for conversation in remotes_for_home(
                AGENTCHAT_LEDGER, context.channel, context.topic
            )
        ],
        context.self_id,
        drop=is_ack,
    )

    context.step = "harvest"
    write_agents_md(context.client, front_dir)

    context.step = "front"
    return TopicResult([
        run_front(
            front_prompt(context.bot_name, threads, front_dir),
            front_dir,
            (context.channel, context.topic),
        )
    ])


def handle_topic(client: ZulipClient, channel: str, topic: str) -> None:
    """Serve one awaiting front topic through the shared skeleton."""
    log(f"front topic {channel!r}/{topic!r}")
    serve_topic(client, channel, topic, serve, ack_text=ACK_TEXT, empty_reply=EMPTY_REPLY)


def handle_mention(client: ZulipClient, channel: str, topic: str) -> None:
    """Front was named somewhere it does not own: serve the request it came from.

    The ledger says which `front-*` conversation this remote topic was opened
    for. That conversation is what the run works on — its chatlog, its
    workspace, its generation — and the answer goes back into the topic that
    named Front, because that is where the question was asked. What Front
    wants to tell the Developer, it tells with `agentchat`.

    A mention in a topic no participation covers is not Front's business: it
    is logged and dropped. Front's own entrance is `#front`, and nothing
    else opens a request to it.
    """
    home = home_for(AGENTCHAT_LEDGER, channel, topic)
    if home is None:
        log(f"mention in {channel!r}/{topic!r} matches no participation; ignoring")
        return
    log(f"mention in {channel!r}/{topic!r} serves {home}")
    serve_topic(
        client, home.channel, home.topic, serve,
        ack_text=ACK_TEXT,
        empty_reply=EMPTY_REPLY,
        reply_to=(channel, topic),
    )


def observe_topic(channel: str, topic: str) -> None:
    """Passive handler (`AGFRONT_ZULIP_LOG_ONLY=1`): log sweep matches, never act."""
    log(f"observed sweep match {channel!r}/{topic!r}")


def main() -> None:
    client = ZulipClient.from_env(ZULIP_ENV)
    if os.environ.get("AGFRONT_ZULIP_LOG_ONLY") == "1":
        handler = observe_topic
        mention_handler = observe_topic
    else:
        def handler(channel: str, topic: str) -> None:
            handle_topic(client, channel, topic)

        def mention_handler(channel: str, topic: str) -> None:
            handle_mention(client, channel, topic)

    # The filter is `front-` only, which is what keeps Front from answering
    # the topics it opens elsewhere: no bot loop, by filter and not by luck.
    # The other side of the same asymmetry is agforge, which sweeps its own
    # `assetplan-` topics and never `front-`.
    log(
        "agfront zulip listener starting "
        f"(pull sweep, prefix {FRONT_TOPIC_PREFIX!r}, plus mentions)"
    )
    try:
        sweep_serve(
            client, handler,
            topic_filter=(FRONT_TOPIC_PREFIX,),
            on_mention=mention_handler,
        )
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
