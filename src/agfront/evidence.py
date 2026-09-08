"""Conversations as evidence: the same files, with their message ids kept.

`front_desk` p2 step 2. A Front Desk run turns real exchanges into a short
dialogue between characters, and every line it gives another character has
to be traceable to a post somebody made. `agag.topics.format_chatlog` renders
`[name] body` and drops the ids; this renders the same conversation with the
id, the sender's user id and the topic named at the top, so a run can cite
`#work-g-13 › workrun-task1-g-13 #5203` without a second read.

Two more things the shared renderer does not say, and a run has to know:

- **a history that is a window.** A topic read at `HISTORY_MESSAGES` comes
  back full when it is longer than that, and the file says so, with the
  command that reads further (`agentchat read --since`).
- **a thread that could not be read.** The shared `write_threads` skips it
  and logs; here the file is written anyway, naming the failure, because a
  run that is not told about a missing thread reads "no news" where there
  was news it could not see. The `✔ ` rename is followed the same way as
  everywhere else (`topic_history_across_resolve`).

Selfnotes never appear (`agag.selfnote`), and this bot's own transport
noise (`drop`) is filtered as in the shared renderer.
"""

from __future__ import annotations

import time
from pathlib import Path

from agag.selfnote import is_selfnote
from agag.topics import HISTORY_MESSAGES, threads_dir
from agag.zulip import RESOLVED_TOPIC_PREFIX, ZulipClient, ZulipError, log as default_log

__all__ = ["format_evidence", "write_evidence_threads"]


def _stamp(timestamp) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(timestamp)))
    except (TypeError, ValueError, OverflowError):
        return "?"


def format_evidence(
    messages: list[dict], self_id: int, *, channel: str, topic: str, drop=None,
    live_topic: str | None = None, bounded: bool = False, history_messages: int = HISTORY_MESSAGES,
    unavailable: str | None = None,
) -> str:
    """One conversation as a file: a header naming it, then one entry per
    real post — `[name #id]` (`(you)` on our own), the time, the body."""
    live = live_topic or topic
    lines = [f"# #{channel} › {topic}"]
    if live != topic:
        lines.append(f"Now named `{live}`" + (" — resolved (✔): this conversation is finished." if live.startswith(RESOLVED_TOPIC_PREFIX) else "."))
    if unavailable:
        lines.append("")
        lines.append(f"**This conversation could not be read: {unavailable}.** Nothing below is "
                     f"its content; read it with `agentchat read {channel} \"{topic}\"` if it matters.")
        return "\n".join(lines) + "\n"
    shown = []
    for message in messages:
        content = str(message.get("content", "")).strip()
        if is_selfnote(content):
            continue
        own = message.get("sender_id") == self_id
        if own and drop is not None and drop(content):
            continue
        shown.append((message, content, own))
    lines.append(f"{len(shown)} post{'s' if len(shown) != 1 else ''}, oldest first, each cited as "
                 f"`#{channel} › {topic} #<id>`. Sender ids are Zulip user ids.")
    if bounded:
        lines.append(f"**Only the newest {history_messages} messages were fetched**; older posts exist. "
                     f"`agentchat read {channel} \"{topic}\" --since <id>` reads further.")
    lines.append("")
    for message, content, own in shown:
        speaker = message.get("sender_full_name") or f"user{message.get('sender_id')}"
        who = f"{speaker} (you)" if own else speaker
        lines.append(f"[{who} #{message.get('id')}] sender {message.get('sender_id')} · {_stamp(message.get('timestamp'))}")
        lines.append(content)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def write_evidence_threads(
    client: ZulipClient, directory: Path, conversations, self_id: int, *,
    history_messages: int = HISTORY_MESSAGES, drop=None, log=default_log,
) -> list[Path]:
    """`threads/<channel>/<topic>.md` per conversation, ids kept, ✔ followed,
    an unreadable one written as such. Returns the paths written."""
    written: list[Path] = []
    for channel, topic in conversations:
        if not channel or not topic or "/" in channel or "/" in topic or topic in {".", ".."}:
            log(f"skipping thread with an unusable name: {channel!r}/{topic!r}")
            continue
        path = threads_dir(directory) / channel / f"{topic}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        live = topic
        unavailable = None
        messages: list[dict] = []
        try:
            messages = client.topic_history(channel, topic, num_before=history_messages)
            if not messages and not topic.startswith(RESOLVED_TOPIC_PREFIX):
                live = f"{RESOLVED_TOPIC_PREFIX}{topic}"
                messages = client.topic_history(channel, live, num_before=history_messages)
                if not messages:
                    live = topic
        except (ZulipError, ValueError, OSError) as error:
            unavailable = f"{type(error).__name__}: {error}"
            log(f"could not read thread {channel!r}/{topic!r}: {error!r}")
        path.write_text(
            format_evidence(
                messages, self_id, channel=channel, topic=topic, drop=drop, live_topic=live,
                bounded=len(messages) >= history_messages, history_messages=history_messages,
                unavailable=unavailable,
            ),
            encoding="utf-8",
        )
        written.append(path)
    return written
