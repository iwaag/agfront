"""The character settings, pinned to one revision for one run.

`front_desk` p2 step 2. The characters — who Front is on the Front Desk,
who the other agents are when they appear in its dialogue — live in the
settings repository agdevworld syncs (`agentroom-settings sync`, agdevworld
`agentroom/README.md` › `/settings`). That sync keeps every revision it has
used under `<settings>/revisions/<sha>/` and names the active one in
`<settings>/active.json`; this module reads those files and nothing else —
never Git, never the relay — so a run costs no fetch.

**One revision per run.** `pin()` reads the active revision once, at the
start of a serving, and everything the run is given comes from that
snapshot: a sync that lands mid-run changes the next run, not this one. The
revision is written into the run record and, from step 3, into the dialogue
the run produces, so a saved dialogue names the faces it was drawn for.

**Where the settings are** is configuration of this instance, because it is
a local path: `AGFRONT_SETTINGS_ROOT`, else `settings_root` in
`.local/instance.toml`, else the sibling checkout's default
(`../agdevworld/.local/settings`, which is where `pj-agdev` puts it). Any
other agent workspace on the host can read the same tree the same way — the
`current/` symlink there is the active revision as plain files.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .instance import AGFRONT_ROOT, SPEC

SETTINGS_ROOT_VARIABLE = "AGFRONT_SETTINGS_ROOT"
DEFAULT_SETTINGS_ROOT = AGFRONT_ROOT.parent / "agdevworld" / ".local" / "settings"
MANIFEST_FILE = "manifest.toml"
MANIFEST_SCHEMA = "ag.settings-manifest.v1"
#: The one character Front speaks as, by manifest id; `agents` in the
#: manifest maps every other agent name onto its character.
FRONT_AGENT = "front"

__all__ = [
    "DEFAULT_SETTINGS_ROOT", "FRONT_AGENT", "SETTINGS_ROOT_VARIABLE", "Character",
    "CharacterSettings", "SettingsUnavailable", "characters_markdown", "pin", "settings_root",
]


class SettingsUnavailable(RuntimeError):
    """No usable settings revision: the run still happens, and the guide is
    told the characters are not available rather than being given none."""


def settings_root() -> Path:
    found = os.environ.get(SETTINGS_ROOT_VARIABLE, "").strip()
    if found:
        return Path(found).expanduser()
    try:
        data = tomllib.loads(SPEC.instance_toml.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        data = {}
    configured = str(data.get("settings_root") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else (AGFRONT_ROOT / path).resolve()
    return DEFAULT_SETTINGS_ROOT


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    nickname: str | None
    lore: str
    agents: tuple[str, ...]
    senders: tuple[str, ...]
    face_path: str


@dataclass(frozen=True)
class CharacterSettings:
    revision: str
    root: Path
    characters: dict[str, Character] = field(default_factory=dict)

    @property
    def front(self) -> Character | None:
        return self.for_agent(FRONT_AGENT)

    def for_agent(self, agent: str) -> Character | None:
        return next((c for c in self.characters.values() if agent in c.agents), None)

    def for_sender(self, sender: str) -> Character | None:
        return next((c for c in self.characters.values() if sender in c.senders), None)


def _read_manifest(root: Path, revision: str) -> dict[str, Character]:
    try:
        data = tomllib.loads((root / MANIFEST_FILE).read_text(encoding="utf-8"))
    except OSError as error:
        raise SettingsUnavailable(f"revision {revision[:12]} has no readable {MANIFEST_FILE}: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise SettingsUnavailable(f"{MANIFEST_FILE} at {revision[:12]} is not valid TOML: {error}") from error
    if data.get("schema") != MANIFEST_SCHEMA:
        raise SettingsUnavailable(f"{MANIFEST_FILE} at {revision[:12]} is not {MANIFEST_SCHEMA}")
    characters: dict[str, Character] = {}
    for cid, entry in (data.get("characters") or {}).items():
        if not isinstance(entry, dict):
            continue
        lore_path = str(entry.get("lore") or "")
        try:
            lore = (root / lore_path).read_text(encoding="utf-8").strip() if lore_path else ""
        except OSError:
            lore = ""
        if not lore:
            raise SettingsUnavailable(f"characters.{cid} at {revision[:12]} has no readable lore ({lore_path!r})")
        characters[cid] = Character(
            id=str(cid), name=str(entry.get("name") or cid).strip() or str(cid),
            nickname=(str(entry.get("nickname")).strip() or None) if entry.get("nickname") else None,
            lore=lore,
            agents=tuple(str(a) for a in (entry.get("agents") or [])),
            senders=tuple(str(s) for s in (entry.get("senders") or [])),
            face_path=str(entry.get("face") or ""),
        )
    if not characters:
        raise SettingsUnavailable(f"{MANIFEST_FILE} at {revision[:12]} names no characters")
    return characters


def pin(root: Path | None = None, revision: str | None = None) -> CharacterSettings:
    """The active revision (or a named one) as an object, read once.

    Raises `SettingsUnavailable` with the reason when there is no active
    revision, the snapshot is gone, or the manifest is unusable — the same
    checks the sync made, repeated cheaply because the snapshot is a plain
    directory that anybody could have removed.
    """
    root = settings_root() if root is None else root
    if revision is None:
        try:
            active = json.loads((root / "active.json").read_text(encoding="utf-8"))
        except OSError as error:
            raise SettingsUnavailable(
                f"no active settings revision under {root} ({error.strerror or error}); "
                "run `agentroom-settings sync` in agdevworld/agentroom"
            ) from error
        except ValueError as error:
            raise SettingsUnavailable(f"{root / 'active.json'} is unreadable: {error}") from error
        revision = str((active or {}).get("revision") or "")
        if not revision:
            raise SettingsUnavailable(f"{root / 'active.json'} names no revision")
    snapshot = root / "revisions" / revision
    if not snapshot.is_dir():
        raise SettingsUnavailable(f"settings revision {revision[:12]} is not retained under {root}")
    return CharacterSettings(revision=revision, root=snapshot, characters=_read_manifest(snapshot, revision))


def characters_markdown(settings: CharacterSettings, *, you: str = FRONT_AGENT) -> str:
    """`characters.md` for a run: every character's whole lore, who speaks
    as whom, and which one is Front. Copied into the workspace so the run
    reads a file that a later sync cannot change."""
    lines = [
        f"# Characters — settings revision {settings.revision}",
        "",
        f"Snapshot: `{settings.root}` (`manifest.toml`, `characters/<id>/lore.md`, portraits).",
        "Each section is one character: its id, display name, nickname, which agents",
        "speak as it (the `agent` name from their `#agents` introduction), and its",
        "complete lore. The lore is the whole definition of the character; there is",
        "no other description anywhere.",
        "",
    ]
    for character in settings.characters.values():
        role = " — **this is you**" if you in character.agents else ""
        lines.append(f"## {character.id}{role}")
        lines.append("")
        lines.append(f"- name: {character.name}")
        if character.nickname:
            lines.append(f"- nickname: {character.nickname}")
        lines.append(f"- agents: {', '.join(character.agents) or '(none)'}")
        if character.senders:
            lines.append(f"- also appears as: {', '.join(character.senders)}")
        lines.append("")
        lines.append(character.lore)
        lines.append("")
    return "\n".join(lines)
