"""Assemble `tools/agents.md` — who else exists, as of this moment.

Front is told by its guide to read `tools/` to learn what other agents can
do. This is what puts the knowledge there, and it is deliberately not an
agent step: the `#agents` board is fetched and the intros are concatenated by
string operations, so what reaches the run is what the other agents actually
said about themselves, not a summary of it.

It is regenerated immediately before every run rather than committed or
cached. An intro topic is append-only — the newest post carries the current
revision stamp — so "the latest post of each `intro-*` topic" is the current
state of the board, and taking it per run is what lets an agent that changed
its entrance this morning be reachable this afternoon with no deploy.

Nothing here is agfront's knowledge about agforge: this file names no agent
and no channel. That is the point of p2 — the routing Front uses has to
arrive by reading, or the demonstration proves nothing.

Marked for a later rethink (pull vs. push, caching, per-agent files). This is
the first cut.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agag.zulip import RESOLVED_TOPIC_PREFIX

AGENTS_CHANNEL = "agents"
INTRO_TOPIC_PREFIX = "intro-"
TOOLS_DIRNAME = "tools"
AGENTS_FILENAME = "agents.md"

HEADING = "# Other agents"
PREAMBLE = (
    "Each section below is one agent's own introduction, copied verbatim from\n"
    "the shared `#agents` board in Zulip. Talk to them with `agentchat`."
)
NO_AGENTS = (
    "No agent has introduced itself on the `#agents` board, so there is "
    "nobody to ask."
)

__all__ = [
    "AGENTS_CHANNEL",
    "AGENTS_FILENAME",
    "INTRO_TOPIC_PREFIX",
    "TOOLS_DIRNAME",
    "agents_file_path",
    "harvest_intros",
    "render_agents_md",
    "write_agents_md",
]


def _agent_name(topic: str) -> str:
    return topic[len(INTRO_TOPIC_PREFIX):].strip() or topic


def harvest_intros(client) -> list[tuple[str, str]]:
    """`(agent name, latest intro body)` for every live `intro-*` topic.

    Resolved (`✔`) topics are skipped — retiring an agent's introduction is
    how it leaves the board — as is anything that is not an introduction.
    Sorted by agent name so two harvests of the same board are the same file.
    """
    entries: list[tuple[str, str]] = []
    for topic in client.channel_topics(client.stream_id(AGENTS_CHANNEL)):
        if topic.startswith(RESOLVED_TOPIC_PREFIX) or not topic.startswith(INTRO_TOPIC_PREFIX):
            continue
        history = client.topic_history(AGENTS_CHANNEL, topic, num_before=1)
        if not history:
            continue
        body = str(history[-1].get("content", "")).strip()
        if not body:
            continue
        entries.append((_agent_name(topic), body))
    return sorted(entries)


def render_agents_md(entries: list[tuple[str, str]], generated_at: datetime | None = None) -> str:
    """The whole file, by string operations. No model call on this path."""
    stamp = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    parts = [HEADING, "", PREAMBLE, "", f"Generated: {stamp.isoformat(timespec='seconds')}", ""]
    if not entries:
        # An empty board is a fact, not a crash: Front can read this and say
        # so, which is a better answer than a failed run.
        parts.append(NO_AGENTS)
    else:
        for name, body in entries:
            parts.extend([f"## {name}", "", body, ""])
    return "\n".join(parts).rstrip() + "\n"


def agents_file_path(front_dir: Path) -> Path:
    return front_dir / TOOLS_DIRNAME / AGENTS_FILENAME


def write_agents_md(client, front_dir: Path, generated_at: datetime | None = None) -> Path:
    """Harvest the board and drop it in this generation's `tools/`."""
    path = agents_file_path(front_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_agents_md(harvest_intros(client), generated_at), encoding="utf-8"
    )
    return path
