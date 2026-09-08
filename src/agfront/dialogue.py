"""The Front Desk dialogue: a short exchange between characters, as data.

`front_desk` p2 step 3. A Front Desk run answers the developer in Front's
voice — that reply is the post, as before — and may add a **dialogue**: a
few ordered turns, each spoken by one character of the pinned settings
revision, that the screen plays back as a graphic-novel exchange and shows
in its history with portraits. The other characters' lines are Front's
rendering of what those agents actually said, in their lore's voice, with
the results, names, numbers and links kept faithful and the source posts
cited.

**The format is one fenced block at the end of the reply**:

    <the reply, in character>

    ```ag-dialogue
    {"schema": "ag.frontdesk-dialogue.v1", "turns": [
      {"character": "front", "text": "…"},
      {"character": "autolab", "text": "…",
       "sources": [{"channel": "work-g-13", "topic": "workrun-task1-g-13", "message_id": 5203}]}
    ]}
    ```

The run writes it; **this module validates and re-serializes it** before
anything is posted, so what reaches Zulip is canonical: the schema, the
pinned `settings_revision` (stamped here — the run's own value, if any, is
ignored), turns with a character id the revision knows, non-empty text,
well-formed sources, no live mentions (a `@**name**` inside a turn is
reduced to the name; a fence already keeps it from firing, and this keeps
it from firing when quoted outside one), and a length that stays under what
this realm truncates silently. A reply with no block is a Front-only reply,
which is the normal case for small talk.

**An unusable block is not a failed run.** The reply is posted without it,
followed by an `ag-dialogue-error` fence naming what was wrong, so the
screen shows the reply and the history records the formatting issue, and
nobody re-runs a delegation to repair presentation. The reason is logged
and written beside the run's files as `dialogue-error.txt`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .settings import CharacterSettings

SCHEMA = "ag.frontdesk-dialogue.v1"
FENCE = "ag-dialogue"
ERROR_FENCE = "ag-dialogue-error"
#: A few short turns is the target; more than this is a script, not a scene.
MAX_TURNS = 12
MAX_TURN_CHARS = 1200
MAX_SOURCES = 8
#: This realm's `max_message_length` is 10000 and it truncates silently; the
#: chat door sends 4000. A reply plus its block stays well under the limit.
MAX_POST_CHARS = 9000

_BLOCK = re.compile(r"```[ \t]*" + re.escape(FENCE) + r"[ \t]*\n(.*?)\n[ \t]*```[ \t]*", re.DOTALL)
_MENTION = re.compile(r"@\*\*([^*\n]+)\*\*")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

__all__ = [
    "ERROR_FENCE", "FENCE", "MAX_TURNS", "SCHEMA", "Dialogue", "DialogueError", "Turn",
    "finish_reply", "parse_block", "render", "split_reply", "strip_preface",
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


def _source(entry, index: int, position: int) -> Source:
    if not isinstance(entry, dict):
        raise DialogueError(f"turn {position}: source {index} must be an object")
    channel = str(entry.get("channel") or "").strip()
    topic = str(entry.get("topic") or "").strip()
    if not channel or not topic:
        raise DialogueError(f"turn {position}: source {index} needs channel and topic")
    message_id = entry.get("message_id")
    if message_id is not None:
        try:
            message_id = int(message_id)
        except (TypeError, ValueError) as error:
            raise DialogueError(f"turn {position}: source {index} message_id is not an integer") from error
    return Source(channel=channel, topic=topic, message_id=message_id)


def parse_block(body: str, settings: CharacterSettings | None) -> Dialogue:
    """Validate the block's JSON against the pinned revision, canonically."""
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
    if len(turns) > MAX_TURNS:
        raise DialogueError(f"{len(turns)} turns is more than the {MAX_TURNS} a scene holds")
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
        sources = tuple(_source(s, i, position) for i, s in enumerate(raw_sources, 1))
        parsed.append(Turn(character=character, text=text, sources=sources))
    return Dialogue(settings_revision=settings.revision, turns=tuple(parsed),
                    characters=tuple(dict.fromkeys(t.character for t in parsed)))


def render(reply: str, dialogue: Dialogue | None, error: str | None = None) -> str:
    """The post: the reply, then the canonical block or the error fence."""
    parts = [reply.strip()]
    if dialogue is not None:
        parts.append(f"```{FENCE}\n{dialogue.serialize()}\n```")
    elif error is not None:
        record = json.dumps({"schema": f"{SCHEMA}-error", "error": error}, ensure_ascii=False)
        parts.append(f"```{ERROR_FENCE}\n{record}\n```")
    return "\n\n".join(part for part in parts if part)


_JAPANESE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


def strip_preface(reply: str) -> tuple[str, str | None]:
    """Drop a leading paragraph with no Japanese in it when a later one has some.

    Three live runs in a row began with an English sentence about the message
    ("This is just small talk…", "Now it reads correctly…") before the reply,
    with the guide saying twice not to. The screen posts the output verbatim,
    so the developer read the preface as dialogue. This is the smallest rule
    that removes exactly that: a first paragraph the developer's language
    does not appear in, ahead of one it does. The dropped text is returned
    so it can be logged, and nothing else is rewritten.
    """
    parts = reply.strip().split("\n\n")
    if len(parts) < 2:
        return reply, None
    first = parts[0]
    if _JAPANESE.search(first) or not any(_JAPANESE.search(rest) for rest in parts[1:]):
        return reply, None
    return "\n\n".join(parts[1:]).strip(), first


def finish_reply(
    output: str, settings: CharacterSettings | None, *, workspace: Path | None = None, log=None,
) -> tuple[str, Dialogue | None, str | None]:
    """From the run's whole output to the post: `(text, dialogue, error)`.

    No block → the reply as it is. A usable block → the reply and the
    canonical block. An unusable one → the reply, the error fence, and the
    reason logged and written beside the run's files.
    """
    reply, body = split_reply(output)
    reply, preface = strip_preface(reply)
    if preface is not None and log is not None:
        log(f"dropped a preface before the reply: {preface[:120]!r}")
    if body is None:
        return (reply if reply else output.strip()), None, None
    dialogue: Dialogue | None = None
    error: str | None = None
    try:
        dialogue = parse_block(body, settings)
    except DialogueError as found:
        error = str(found)
    else:
        if len(render(reply, dialogue)) > MAX_POST_CHARS:
            dialogue, error = None, (f"the reply and its dialogue together are longer than {MAX_POST_CHARS} "
                                     "characters, which this realm would truncate")
    if error is not None:
        if log is not None:
            log(f"dialogue block unusable: {error}")
        if workspace is not None:
            try:
                (workspace / "dialogue-error.txt").write_text(f"{error}\n\n{body}\n", encoding="utf-8")
            except OSError:
                pass
    if not reply:
        # A block with no reply around it: the developer still gets words.
        reply = "\n".join(t.text for t in dialogue.turns if t.character == "front") if dialogue else output.strip()
    return render(reply, dialogue, error), dialogue, error
