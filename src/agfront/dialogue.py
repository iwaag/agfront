"""Character dialogue as data: ordered turns, each by one character, each cited.

`front_desk` p2 step 3 introduced the shape — a fenced `ag-dialogue` JSON
block of turns, each spoken by one character of a pinned settings revision
and citing the posts it renders. Until `argue` p2 the *substantive* Front
Desk run wrote that block at the end of its own reply, so character lore sat
in the context of the run that also judged and delegated. That coupling is
gone: the block is now the whole output of Front's **presentation** role
(`agfront.present`), which re-voices recorded speech and does nothing else,
and the result is saved to a memo topic, never into the conversation.

What stays here is the validator: **what a run writes is validated and
re-serialized before anything is posted**, so what reaches Zulip is
canonical — the schema, the pinned `settings_revision` (stamped here; the
run's own value, if any, is ignored), turns with a character id the revision
knows, non-empty text, well-formed sources, no live mentions (a `@**name**`
inside a turn is reduced to the name), and bounded sizes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .settings import CharacterSettings

SCHEMA = "ag.frontdesk-dialogue.v1"
FENCE = "ag-dialogue"
# Turn count follows the source discussion; memo records split by transport size.
MAX_TURN_CHARS = 1200
MAX_SOURCES = 8

_BLOCK = re.compile(r"```[ \t]*" + re.escape(FENCE) + r"[ \t]*\n(.*?)\n[ \t]*```[ \t]*", re.DOTALL)
_MENTION = re.compile(r"@\*\*([^*\n]+)\*\*")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

__all__ = [
    "FENCE", "SCHEMA", "Dialogue", "DialogueError", "Source", "Turn",
    "parse_block", "split_reply",
]


class DialogueError(ValueError):
    """The block is present and cannot be used; the reply still can."""


@dataclass(frozen=True)
class Source:
    channel: str
    topic: str
    message_id: int | None = None

    def payload(self) -> dict:
        found: dict = {"channel": self.channel, "topic": self.topic}
        if self.message_id is not None:
            found["message_id"] = self.message_id
        return found


@dataclass(frozen=True)
class Turn:
    character: str
    text: str
    sources: tuple[Source, ...] = ()

    def payload(self) -> dict:
        found: dict = {"character": self.character, "text": self.text}
        if self.sources:
            found["sources"] = [s.payload() for s in self.sources]
        return found


@dataclass(frozen=True)
class Dialogue:
    settings_revision: str
    turns: tuple[Turn, ...]
    characters: tuple[str, ...] = field(default=())

    def payload(self) -> dict:
        return {"schema": SCHEMA, "settings_revision": self.settings_revision,
                "turns": [t.payload() for t in self.turns]}

    def serialize(self) -> str:
        return json.dumps(self.payload(), ensure_ascii=False, indent=1)


def split_reply(output: str) -> tuple[str, str | None]:
    """The reply without its block, and the block's body (None when absent).

    The block is the *last* fence of its kind; an earlier one — quoted, say
    — is left in the reply as prose.
    """
    matches = list(_BLOCK.finditer(output))
    if not matches:
        return output.strip(), None
    last = matches[-1]
    reply = (output[:last.start()] + output[last.end():]).strip()
    return reply, last.group(1).strip()


def _source(entry, index: int, position: int, default: tuple[str, str] | None = None) -> Source:
    if not isinstance(entry, dict):
        raise DialogueError(f"turn {position}: source {index} must be an object")
    channel = str(entry.get("channel") or (default[0] if default else "")).strip()
    topic = str(entry.get("topic") or (default[1] if default else "")).strip()
    if not channel or not topic:
        raise DialogueError(f"turn {position}: source {index} needs channel and topic")
    message_id = entry.get("message_id")
    if message_id is not None:
        try:
            message_id = int(message_id)
        except (TypeError, ValueError) as error:
            raise DialogueError(f"turn {position}: source {index} message_id is not an integer") from error
    return Source(channel=channel, topic=topic, message_id=message_id)


def parse_block(body: str, settings: CharacterSettings | None, *,
                source_default: tuple[str, str] | None = None) -> Dialogue:
    """Validate the block's JSON against the pinned revision, canonically.

    `source_default` is the conversation a source belongs to when the run
    named only a message id — the presentation role renders one conversation
    and is never asked to spell its name."""
    try:
        data = json.loads(body)
    except ValueError as error:
        raise DialogueError(f"the {FENCE} block is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise DialogueError(f"the {FENCE} block must be a JSON object")
    if data.get("schema") != SCHEMA:
        raise DialogueError(f"the {FENCE} block declares schema {data.get('schema')!r}, not {SCHEMA!r}")
    if settings is None:
        raise DialogueError("no character settings were available to this run, so no dialogue can name a character")
    turns = data.get("turns")
    if not isinstance(turns, list) or not turns:
        raise DialogueError("turns must be a non-empty list")
    known = set(settings.characters)
    parsed: list[Turn] = []
    for position, entry in enumerate(turns, 1):
        if not isinstance(entry, dict):
            raise DialogueError(f"turn {position} must be an object")
        character = str(entry.get("character") or "").strip()
        if not _ID.match(character):
            raise DialogueError(f"turn {position}: character id {character!r} is not an id")
        if character not in known:
            raise DialogueError(f"turn {position}: character {character!r} is not in settings revision "
                                f"{settings.revision[:12]} (known: {', '.join(sorted(known))})")
        text = _MENTION.sub(r"\1", str(entry.get("text") or "")).strip()
        if not text:
            raise DialogueError(f"turn {position}: text is empty")
        if len(text) > MAX_TURN_CHARS:
            raise DialogueError(f"turn {position}: {len(text)} characters is longer than a turn may be ({MAX_TURN_CHARS})")
        raw_sources = entry.get("sources") or []
        if not isinstance(raw_sources, list):
            raise DialogueError(f"turn {position}: sources must be a list")
        if len(raw_sources) > MAX_SOURCES:
            raise DialogueError(f"turn {position}: {len(raw_sources)} sources is more than {MAX_SOURCES}")
        sources = tuple(_source(s, i, position, source_default) for i, s in enumerate(raw_sources, 1))
        parsed.append(Turn(character=character, text=text, sources=sources))
    return Dialogue(settings_revision=settings.revision, turns=tuple(parsed),
                    characters=tuple(dict.fromkeys(t.character for t in parsed)))
