"""Front's presentation role: recorded speech in, character dialogue out.

`argue` p2 step 2. The discussion — a Front Desk conversation, an argue —
runs without any character in it. What the screen shows as a graphic-novel
exchange is a **re-voicing made afterwards**, by a run that does nothing
else: its input is a snapshot (the posts to render, the context before them,
who each speaker is, one pinned settings revision), its output is one
`ag-dialogue` block, and it has neither `agentchat` nor any way to write
into a discussion. Extreme lore therefore changes how a conversation *looks*
and never what anybody in it was told.

This module is the pure half: who a speaker is, what the snapshot is, what
the prompt carries, and the check between the run's output and the record
that is posted. `agfront.render` is the other half — when a rendering is
made, the durable job behind it, and the memo it is saved to.

**Speakers.** A post's speaker is its account, except where one account
speaks as several logical participants (`agag.argue.speaker_of`: archsage's
`**[sage:<name>]**` header), and then it is that participant: `archsage` and
`sage:arxiv` are different speakers on one account. A speaker's character is
the settings revision's — by roster agent name (`agents`), or for a logical
participant by its label (`senders`). A speaker without a character is not
an error: its posts are shown as written, under the speaker's label
(`plain` turns, added here and never by the model).

**The check.** `agfront.dialogue.parse_block` validates shape, characters and
sizes against the pinned revision. On top of it: every turn cites at least
one post, every cited post is one of this job's, the turn's character is the
cited speaker's character (nobody is given somebody else's words), and every
post that has a character got at least one turn. An output failing any of
it is an error for the job to retry, never a partial record.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from agag.agent import is_ack
from agag.argue import speaker_of
from agag.selfnote import is_speech
from agag.topics import guide as shared_guide, prompt_with_guide

from .dialogue import DialogueError, Dialogue, parse_block, split_reply
from .settings import CharacterSettings

PRESENT_ROLE = "present"
#: Bumped when what a rendering *means* changes (the guide's contract, the
#: check): a result names the renderer it was made by, and a new version is a
#: new interpretation rather than a silent replacement.
RENDERER_VERSION = "agfront.present/1"
RECORD_SCHEMA = "ag.memo-dialogue.v1"
#: How much earlier conversation a rendering is shown, newest last.
CONTEXT_POSTS = 12
CONTEXT_POST_CHARS = 1500
#: Zulip truncates silently past 10000; one record stays well under it.
MAX_RECORD_CHARS = 8500

_HANDOFF = re.compile(r"^\s*@\*\*[^*\n]+\*\*\s*\n+")
_SPEAKER_HEADER = re.compile(r"^\*\*\[[a-z][\w-]*:[\w.-]+\]\*\*[ \t]*\n")

__all__ = [
    "PRESENT_ROLE", "RECORD_SCHEMA", "RENDERER_VERSION", "PresentError", "SourcePost", "Speaker",
    "check_dialogue", "context_markdown", "fingerprint", "is_renderable", "plain_content", "present_prompt",
    "records_for", "sources_markdown", "speaker_for", "write_snapshot",
]


class PresentError(ValueError):
    """The run's output cannot be saved as it is; the job may be retried."""


@dataclass(frozen=True)
class Speaker:
    """Who said a post: the label the view shows, the roster agent behind it
    (None for a human or an unintroduced account), and its character id in
    the pinned revision (None: shown as written)."""

    label: str
    agent: str | None = None
    character: str | None = None
    human: bool = False


@dataclass(frozen=True)
class SourcePost:
    message_id: int
    sender_id: int
    speaker: Speaker
    content: str
    timestamp: int = 0


def plain_content(content: str) -> str:
    """A post without its transport: the hand-off mention `serve_topic`
    prefixes and the logical-speaker header."""
    text = _HANDOFF.sub("", str(content or ""), count=1)
    return _SPEAKER_HEADER.sub("", text.lstrip(), count=1).strip()


def speaker_for(message: dict, agents: dict[int, str], settings: CharacterSettings | None) -> Speaker:
    """`agents` is `{bot user id: roster agent name}` from the introductions.
    An account without an introduction is a human as far as rendering goes:
    its words are never re-voiced."""
    sender_id = int(message.get("sender_id") or 0)
    name = str(message.get("sender_full_name") or f"user{sender_id}")
    agent = agents.get(sender_id)
    if agent is None:
        return Speaker(label=name, human=True)
    logical = speaker_of(_HANDOFF.sub("", str(message.get("content") or ""), count=1))
    if logical:
        found = settings.for_sender(logical) if settings is not None else None
        return Speaker(label=logical, agent=agent, character=found.id if found else None)
    found = settings.for_agent(agent) if settings is not None else None
    return Speaker(label=name, agent=agent, character=found.id if found else None)


def is_renderable(message: dict, agents: dict[int, str]) -> bool:
    """Whether a post is an agent's *speech*: not a note, not Zulip's own
    notice, not a transport ack, not empty, and by an introduced agent."""
    if not is_speech(message) or int(message.get("sender_id") or 0) not in agents:
        return False
    text = plain_content(str(message.get("content") or ""))
    return bool(text) and not is_ack(text)


def fingerprint(posts: Iterable[SourcePost | dict]) -> str:
    """What was rendered, so an edit or a deletion afterwards is visible:
    a digest over each post's id and its content as it stood."""
    digest = hashlib.sha256()
    rows = []
    for post in posts:
        if isinstance(post, SourcePost):
            rows.append((post.message_id, post.content))
        else:
            rows.append((int(post["id"]), str(post.get("content") or "")))
    for message_id, content in sorted(rows):
        digest.update(f"{message_id}\0{content}\0".encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


# --- the snapshot a run reads -------------------------------------------------


def sources_markdown(posts: list[SourcePost], channel: str, topic: str) -> str:
    lines = [f"# Posts to re-voice — #{channel} › {topic}", "",
             f"{len(posts)} post{'s' if len(posts) != 1 else ''}, oldest first.", ""]
    for post in posts:
        who = (f"character `{post.speaker.character}`" if post.speaker.character
               else "no character — skip this post, it is shown as written")
        lines.append(f"[{post.speaker.label} #{post.message_id}] {who}")
        lines.append(plain_content(post.content))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def context_markdown(posts: list[SourcePost]) -> str:
    lines = ["# What was said before (context only — never re-voice these)", ""]
    if not posts:
        lines.append("Nothing: the posts to re-voice open the conversation.")
    for post in posts[-CONTEXT_POSTS:]:
        text = plain_content(post.content)
        if len(text) > CONTEXT_POST_CHARS:
            text = text[:CONTEXT_POST_CHARS].rstrip() + " […]"
        lines.append(f"[{post.speaker.label} #{post.message_id}]")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def characters_markdown(settings: CharacterSettings, only: Iterable[str] | None = None) -> str:
    """The lore of the characters this job needs — nobody is "you" here."""
    wanted = None if only is None else set(only)
    lines = [f"# Characters — settings revision {settings.revision}", ""]
    for character in settings.characters.values():
        if wanted is not None and character.id not in wanted:
            continue
        lines.append(f"## {character.id}")
        lines.append("")
        lines.append(f"- name: {character.name}")
        if character.nickname:
            lines.append(f"- nickname: {character.nickname}")
        lines.append("")
        lines.append(character.lore)
        lines.append("")
    return "\n".join(lines)


def write_snapshot(workspace: Path, posts: list[SourcePost], context: list[SourcePost],
                   settings: CharacterSettings, channel: str, topic: str) -> dict[str, str]:
    """The three files of one job, written once; returns their text."""
    workspace.mkdir(parents=True, exist_ok=True)
    needed = {p.speaker.character for p in posts if p.speaker.character}
    files = {
        "sources.md": sources_markdown(posts, channel, topic),
        "context.md": context_markdown(context),
        "characters.md": characters_markdown(settings, needed),
    }
    for name, text in files.items():
        (workspace / name).write_text(text, encoding="utf-8")
    return files


def present_prompt(files: dict[str, str], guides: Path) -> str:
    """Everything in the prompt: the role has files beside it as the
    complete copy, and needs to open none of them."""
    lines = ["The snapshot is placed beside you as \"sources.md\", \"context.md\" and \"characters.md\"; "
             "the same text follows.", ""]
    for name in ("characters.md", "context.md", "sources.md"):
        lines += [f"----- {name} -----", files[name].rstrip("\n"), ""]
    return prompt_with_guide(lines, shared_guide(guides, PRESENT_ROLE, "guide.md"))


# --- from the run's output to what is posted -----------------------------------


def check_dialogue(output: str, posts: list[SourcePost], settings: CharacterSettings,
                   channel: str, topic: str) -> Dialogue:
    """The validated dialogue, or `PresentError` saying what was wrong."""
    _, body = split_reply(output or "")
    if body is None:
        raise PresentError("the output carries no ag-dialogue block")
    try:
        dialogue = parse_block(body, settings, source_default=(channel, topic))
    except DialogueError as error:
        raise PresentError(str(error)) from error
    by_id = {post.message_id: post for post in posts}
    covered: set[int] = set()
    for position, turn in enumerate(dialogue.turns, 1):
        if not turn.sources:
            raise PresentError(f"turn {position} cites no post")
        for source in turn.sources:
            post = by_id.get(source.message_id or 0)
            if post is None:
                raise PresentError(f"turn {position} cites #{source.message_id}, which is not one of the posts to re-voice")
            if post.speaker.character != turn.character:
                raise PresentError(f"turn {position} gives {turn.character!r} the words of "
                                   f"{post.speaker.label} (#{post.message_id})")
            covered.add(post.message_id)
    missing = [p.message_id for p in posts if p.speaker.character and p.message_id not in covered]
    if missing:
        raise PresentError("no turn re-voices " + ", ".join(f"#{i}" for i in missing))
    return dialogue


def _turns(dialogue: Dialogue | None, posts: list[SourcePost], channel: str, topic: str) -> list[dict]:
    """The model's turns plus one `plain` turn per post without a character,
    in the order the posts were made."""
    rows: list[tuple[int, int, dict]] = []
    labels = {p.message_id: p.speaker.label for p in posts}
    for order, turn in enumerate(dialogue.turns if dialogue else ()):
        first = min(s.message_id or 0 for s in turn.sources)
        payload = turn.payload()
        payload["speaker"] = labels.get(first, turn.character)
        rows.append((first, order, payload))
    for post in posts:
        if post.speaker.character is None:
            rows.append((post.message_id, -1, {
                "character": None, "speaker": post.speaker.label, "plain": True,
                "sources": [{"channel": channel, "topic": topic, "message_id": post.message_id}],
            }))
    return [payload for _, _, payload in sorted(rows, key=lambda row: (row[0], row[1]))]


def records_for(job: dict, dialogue: Dialogue | None, posts: list[SourcePost]) -> list[dict]:
    """The memo records of one result: one, or several when the turns would
    not fit one Zulip post. Each part carries the whole identity of the job,
    so any of them says which interpretation it belongs to."""
    import json

    channel, topic = job["channel"], job["topic"]
    turns = _turns(dialogue, posts, channel, topic)
    head = {
        "schema": RECORD_SCHEMA, "kind": "result", "job": job["job"],
        "source": {"anchor": job["anchor"], "channel": channel, "topic": topic,
                   "messages": [p.message_id for p in posts], "fingerprint": job["fingerprint"]},
        "settings_revision": job["settings_revision"], "renderer": job["renderer"],
    }
    parts: list[list[dict]] = [[]]
    for turn in turns:
        trial = parts[-1] + [turn]
        if parts[-1] and len(json.dumps({**head, "turns": trial}, ensure_ascii=False)) > MAX_RECORD_CHARS:
            parts.append([turn])
        else:
            parts[-1] = trial
    return [{**head, "part": n, "parts": len(parts), "turns": chunk} for n, chunk in enumerate(parts, 1)]
