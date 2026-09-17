"""Rendering jobs: when a re-voicing is made, kept, retried and saved.

`argue` p2 step 2. `agfront.present` says what one rendering is. This module
is everything around it, and its first rule is that **rendering never stands
in the way of the discussion**: it is a worker thread beside Front's
listener, following the same mirror with a checkpoint of its own, with its
own durable store (`.local/render/render.sqlite`). A rendering that is slow,
failing or switched off changes nothing about how a conversation is served,
and nothing here ever calls a discussion handler.

**Sources.** A renderable source is a Front Desk conversation
(`#front › front-desk-…`) or an argue (`#argue › argue-…`). Its identity is
a message id that no rename touches: the argue's `[selfnote][argue]` note,
or a desk conversation's first post.

**The trigger is new agent speech in a source** — Front's replies, and every
specialist's contribution in an argue — seen on the mirror's change feed.
Human posts are never re-voiced (they are shown verbatim), and acks,
selfnotes and Zulip's notices are not speech. New speech marks the source
*dirty*; a job is planned once the conversation has been quiet for
`QUIET_SECONDS` (longer while a serving's ack is the newest post, because a
reply is about to land), so a burst is one job of up to `MAX_JOB_MESSAGES`
posts. The very first start adopts the present as the past: nothing already
in the realm is rendered unless somebody asks.

**A job is its content.** Its id is a digest of the source anchor, the
message ids, the fingerprint of their content, the settings revision and the
renderer version. Planning the same thing twice is one row. Its result is
the memo record carrying that id, so *the memo is the truth*: before any
run, the memo topic is searched for the job's record and an existing one
finishes the job — which is how a crash between the post and the local
"done" is healed without a second paid run. A validated result is also kept
beside the job (`result.json`) before it is posted, so a crash between two
parts re-posts and does not re-run.

**Failure is bounded.** A job is tried `MAX_ATTEMPTS` times with a backoff,
then marked failed and said so once in the memo (a `failed` record), and
left. Neither a restart nor a reader re-arms it; an explicit request does.

**Another interpretation is explicit.** `[selfnote][render] <settings
revision>` written into the source by a human account (the relay writes it
as the Developer) asks for the whole conversation at that revision: every
agent post with no result there becomes jobs, failed ones are re-armed, and
earlier results are left exactly where they are. A selfnote buys nobody a
run and is invisible to every chatlog, so asking costs the discussion
nothing. Requests are remembered by id and bounded by
`MAX_REQUEST_MESSAGES`.

**A changed source is identifiable, not chased.** The record carries the
fingerprint of what was rendered; a reader comparing it with the source as
it stands sees an edit or a deletion. Nothing regenerates by itself: a new
request renders the changed posts (their fingerprint, hence their job id,
is new).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from agag.agent import is_ack, run_role
from agag.argue import ARGUE_TAG, is_argue_topic
from agag.memo import (
    MEMO_CHANNEL, RENDER_TAG, SOURCE_TAG, memo_topic, parse_record, render_record, render_request_note, source_note,
)
from agag.mirror import Mirror, bare_topic
from agag.selfnote import is_speech, parse_note
from agag.topics import next_record_path
from agag.zulip import ZulipClient, log as default_log

from .instance import SPEC
from .present import (
    PRESENT_ROLE,
    RECORD_SCHEMA,
    RENDERER_VERSION,
    PresentError,
    SourcePost,
    check_dialogue,
    fingerprint,
    is_renderable,
    present_prompt,
    records_for,
    speaker_for,
    write_snapshot,
)
from .settings import CharacterSettings, SettingsUnavailable, pin

FRONT_CHANNEL = "front"
FRONT_DESK_PREFIX = "front-desk-"
QUIET_SECONDS = 45.0
#: While a serving's ack is the newest post a reply is on its way; waited for
#: this long at most, so a run that died after its ack does not hold a
#: rendering for ever.
ACK_WAIT_SECONDS = 600.0
MAX_JOB_MESSAGES = 6
MAX_REQUEST_MESSAGES = 60
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 60.0
PRESENT_TIMEOUT_SECONDS = 300
CONTEXT_MESSAGES = 12
TICK_SECONDS = 5.0

__all__ = [
    "ACK_WAIT_SECONDS", "MAX_ATTEMPTS", "MAX_JOB_MESSAGES", "QUIET_SECONDS", "RENDER_TAG", "Jobs", "Renderer",
    "is_source", "job_id", "memo_results", "render_request_note", "source_anchor", "start",
]


def is_source(channel: str, topic: str) -> bool:
    bare = bare_topic(topic)
    return is_argue_topic(channel, bare) or (channel == FRONT_CHANNEL and bare.startswith(FRONT_DESK_PREFIX))


def source_anchor(mirror: Mirror, channel: str, topic: str) -> int | None:
    """The message id that *is* this source: an argue's anchor note, a desk
    conversation's first post. None while there is neither."""
    bare = bare_topic(topic)
    if is_argue_topic(channel, bare):
        notes = [n for n in mirror.notes(tag=ARGUE_TAG, channel=channel) if bare_topic(n.topic) == bare]
        return min((n.message_id for n in notes), default=None)
    messages = mirror.messages(channel, bare)
    return messages[0].id if messages else None


def job_id(anchor: int, message_ids, fingerprint_: str, revision: str, renderer: str) -> str:
    text = f"{anchor}|{','.join(str(i) for i in sorted(message_ids))}|{fingerprint_}|{revision}|{renderer}"
    return "j" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def memo_topic_of(mirror: Mirror, anchor: int) -> str | None:
    """The memo topic presenting this source, as it is named now."""
    for found in mirror.notes(tag=SOURCE_TAG, channel=MEMO_CHANNEL, include_memos=True):
        if found.value.split()[:1] == [str(anchor)]:
            return found.topic
    return None


def memo_results(messages) -> dict[str, dict[int, dict]]:
    """`{job id: {part: record}}` for the result records among these posts."""
    found: dict[str, dict[int, dict]] = {}
    for message in messages:
        content = message.content if hasattr(message, "content") else message.get("content")
        record = parse_record(content)
        if not record or record.get("schema") != RECORD_SCHEMA or record.get("kind") != "result":
            continue
        found.setdefault(str(record.get("job")), {})[int(record.get("part") or 1)] = record
    return found


def _complete(parts: dict[int, dict]) -> bool:
    total = max((int(r.get("parts") or 1) for r in parts.values()), default=0)
    return total > 0 and all(n in parts for n in range(1, total + 1))


# --- the durable store ----------------------------------------------------------


class Jobs:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS jobs (
        job TEXT PRIMARY KEY, anchor INTEGER NOT NULL, channel TEXT NOT NULL, topic TEXT NOT NULL,
        message_ids TEXT NOT NULL, fingerprint TEXT NOT NULL, settings_revision TEXT NOT NULL,
        renderer TEXT NOT NULL, origin TEXT NOT NULL DEFAULT 'auto', state TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0, error TEXT, result_ids TEXT NOT NULL DEFAULT '[]',
        not_before REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS dirty (
        anchor INTEGER PRIMARY KEY, channel TEXT NOT NULL, topic TEXT NOT NULL,
        since_id INTEGER NOT NULL, last_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS requests (message_id INTEGER PRIMARY KEY, handled_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(self.SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    # -- dirty sources ---------------------------------------------------------

    def mark_dirty(self, anchor: int, channel: str, topic: str, message_id: int, at: float) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO dirty (anchor, channel, topic, since_id, last_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(anchor) DO UPDATE SET channel = excluded.channel, topic = excluded.topic,"
                " since_id = MIN(since_id, excluded.since_id), last_at = MAX(last_at, excluded.last_at)",
                (int(anchor), channel, topic, int(message_id), float(at)))

    def dirty(self) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._db.execute("SELECT * FROM dirty ORDER BY last_at")]

    def clean(self, anchor: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM dirty WHERE anchor = ?", (int(anchor),))

    # -- requests ----------------------------------------------------------------

    def request_seen(self, message_id: int, at: float) -> bool:
        """Record a request; False when it was already handled."""
        with self._lock:
            if self._db.execute("SELECT 1 FROM requests WHERE message_id = ?", (int(message_id),)).fetchone():
                return False
            self._db.execute("INSERT INTO requests (message_id, handled_at) VALUES (?, ?)", (int(message_id), at))
            return True

    # -- jobs ------------------------------------------------------------------------

    @staticmethod
    def _job(row) -> dict:
        found = dict(row)
        found["message_ids"] = json.loads(found["message_ids"])
        found["result_ids"] = json.loads(found["result_ids"])
        return found

    def add(self, job: dict, at: float, *, rearm: bool = False) -> bool:
        """Insert a job; True when it is new (or, with `rearm`, was failed
        and is pending again). The same content is the same row."""
        with self._lock:
            row = self._db.execute("SELECT state FROM jobs WHERE job = ?", (job["job"],)).fetchone()
            if row is not None:
                if rearm and row["state"] == "failed":
                    self._db.execute("UPDATE jobs SET state = 'pending', attempts = 0, error = NULL,"
                                     " not_before = 0, updated_at = ? WHERE job = ?", (at, job["job"]))
                    return True
                return False
            self._db.execute(
                "INSERT INTO jobs (job, anchor, channel, topic, message_ids, fingerprint, settings_revision,"
                " renderer, origin, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job["job"], int(job["anchor"]), job["channel"], job["topic"], json.dumps(job["message_ids"]),
                 job["fingerprint"], job["settings_revision"], job["renderer"], job.get("origin", "auto"), at, at))
            return True

    def get(self, job: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE job = ?", (job,)).fetchone()
        return self._job(row) if row else None

    def all(self, state: str | None = None) -> list[dict]:
        with self._lock:
            if state is None:
                rows = self._db.execute("SELECT * FROM jobs ORDER BY created_at, job").fetchall()
            else:
                rows = self._db.execute("SELECT * FROM jobs WHERE state = ? ORDER BY created_at, job", (state,)).fetchall()
        return [self._job(row) for row in rows]

    def take(self, at: float) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE state = 'pending' AND not_before <= ?"
                                   " ORDER BY created_at, job LIMIT 1", (at,)).fetchone()
            if row is None:
                return None
            self._db.execute("UPDATE jobs SET state = 'running', attempts = attempts + 1, updated_at = ?"
                             " WHERE job = ?", (at, row["job"]))
            return self.get(row["job"])

    def update(self, job: str, at: float, **fields) -> None:
        if "result_ids" in fields:
            fields["result_ids"] = json.dumps(fields["result_ids"])
        keys = sorted(fields)
        with self._lock:
            self._db.execute(f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in keys)}, updated_at = ? WHERE job = ?",
                             (*(fields[k] for k in keys), at, job))

    def covered(self, anchor: int, revision: str, renderer: str) -> set[int]:
        """Message ids of this source that a job — pending, running, done or
        failed — already stands for at this interpretation."""
        found: set[int] = set()
        for job in self.all():
            if job["anchor"] == anchor and job["settings_revision"] == revision and job["renderer"] == renderer:
                found.update(job["message_ids"])
        return found


# --- the worker ------------------------------------------------------------------------


class Renderer:
    def __init__(self, mirror: Mirror, client: ZulipClient, *, root: Path | None = None,
                 run: Callable[[str, Path, dict], str] | None = None,
                 settings_pin: Callable[..., CharacterSettings] = pin, guides: Path | None = None,
                 clock: Callable[[], float] = time.time, log=default_log,
                 quiet_seconds: float = QUIET_SECONDS, ack_wait_seconds: float = ACK_WAIT_SECONDS,
                 backoff_seconds: float = BACKOFF_SECONDS):
        self.mirror = mirror
        self.client = client
        self.root = Path(root) if root is not None else SPEC.local / "render"
        self.jobs = Jobs(self.root / "render.sqlite")
        self._run = run
        self.pin = settings_pin
        self.guides = guides or SPEC.guides
        self.clock = clock
        self.log = log
        self.quiet_seconds = float(quiet_seconds)
        self.ack_wait_seconds = float(ack_wait_seconds)
        self.backoff_seconds = float(backoff_seconds)
        self._stop = threading.Event()
        self._recovering: set[str] = set()
        self.runs = 0

    # -- who speaks ---------------------------------------------------------------------

    def agents(self) -> dict[int, str]:
        """`{bot user id: roster agent name}` from every introduction, a
        retired one included: what it said is still what it said."""
        found: dict[int, str] = {}
        for intro in self.mirror.intros().values():
            roster = intro.roster
            if roster is not None and roster.bot_id is not None:
                found[int(roster.bot_id)] = roster.agent
        return found

    # -- intake ---------------------------------------------------------------------------

    def intake(self) -> int:
        """Follow the change feed from this worker's own checkpoint."""
        checkpoint = self.jobs.meta("revision")
        if checkpoint is None:
            # The first start on this host: the present becomes the past.
            self.jobs.set_meta("revision", str(self.mirror.revision()))
            self.log(f"rendering starts from revision {self.mirror.revision()}; nothing older is rendered unasked")
            return 0
        revision = int(checkpoint)
        changes = self.mirror.changes(revision)
        if changes is None:
            self.jobs.set_meta("revision", str(self.mirror.revision()))
            self.log("the change feed forgot the rendering checkpoint; resuming from now")
            return 0
        agents = None
        seen = 0
        for change in changes:
            revision = change.revision
            if change.kind != "message" or change.message_id is None or not is_source(change.channel, change.topic):
                continue
            message = self.mirror.message(change.message_id)
            if message is None:
                continue
            row = message.as_zulip()
            requested = parse_note(message.content, RENDER_TAG)
            if requested is not None:
                self._request(message, requested.strip())
                continue
            if agents is None:
                agents = self.agents()
            if not is_renderable(row, agents):
                continue
            anchor = source_anchor(self.mirror, message.channel, message.topic)
            if anchor is None:
                continue
            self.jobs.mark_dirty(anchor, message.channel, bare_topic(message.topic), message.id, self.clock())
            seen += 1
        if changes:
            self.jobs.set_meta("revision", str(revision))
        return seen

    # -- planning -----------------------------------------------------------------------

    def _posts(self, channel: str, topic: str, settings: CharacterSettings | None, agents: dict[int, str]):
        rows = [m.as_zulip() for m in self.mirror.messages(channel, topic)]
        speech = [m for m in rows if is_speech(m) and not is_ack(str(m.get("content") or "").strip())]
        return [SourcePost(int(m["id"]), int(m.get("sender_id") or 0), speaker_for(m, agents, settings),
                           str(m.get("content") or ""), int(m.get("timestamp") or 0)) for m in speech], rows

    def _plan(self, anchor: int, channel: str, topic: str, posts: list[SourcePost], settings: CharacterSettings,
              *, origin: str, rearm: bool = False) -> int:
        added = 0
        for start_at in range(0, len(posts), MAX_JOB_MESSAGES):
            chunk = posts[start_at:start_at + MAX_JOB_MESSAGES]
            digest = fingerprint(chunk)
            ids = [p.message_id for p in chunk]
            job = {"job": job_id(anchor, ids, digest, settings.revision, RENDERER_VERSION), "anchor": anchor,
                   "channel": channel, "topic": topic, "message_ids": ids, "fingerprint": digest,
                   "settings_revision": settings.revision, "renderer": RENDERER_VERSION, "origin": origin}
            if self.jobs.add(job, self.clock(), rearm=rearm):
                added += 1
                self.log(f"render job {job['job']} planned ({origin}): #{channel} › {topic} "
                         f"{ids} at settings {settings.revision[:12]}")
        return added

    def plan(self) -> int:
        """Turn quiet dirty sources into jobs at the active settings revision."""
        now = self.clock()
        planned = 0
        for entry in self.jobs.dirty():
            rows = self.mirror.messages(entry["channel"], entry["topic"])
            last = next((m for m in reversed(rows) if is_speech(m.as_zulip())), None)
            waiting_for_reply = last is not None and is_ack(last.content.strip())
            quiet_for = now - entry["last_at"]
            if quiet_for < self.quiet_seconds or (waiting_for_reply and quiet_for < self.ack_wait_seconds):
                continue
            try:
                settings = self.pin()
            except SettingsUnavailable as error:
                self.log(f"rendering waits: {error}")
                return planned
            agents = self.agents()
            posts, _ = self._posts(entry["channel"], entry["topic"], settings, agents)
            covered = self.jobs.covered(entry["anchor"], settings.revision, RENDERER_VERSION)
            fresh = [p for p in posts if p.message_id >= entry["since_id"] and p.message_id not in covered
                     and not p.speaker.human]
            planned += self._plan(entry["anchor"], entry["channel"], entry["topic"], fresh, settings, origin="auto")
            self.jobs.clean(entry["anchor"])
        return planned

    def _request(self, message, revision: str) -> None:
        """An explicit interpretation of one whole source at `revision`."""
        if not self.jobs.request_seen(message.id, self.clock()):
            return
        agents = self.agents()
        if message.sender_id in agents:
            self.log(f"render request #{message.id} ignored: written by an agent account")
            return
        channel, topic = message.channel, bare_topic(message.topic)
        anchor = source_anchor(self.mirror, channel, topic)
        if anchor is None:
            self.log(f"render request #{message.id} ignored: #{channel} › {topic} has no anchor")
            return
        try:
            settings = self.pin(revision=revision or None)
        except SettingsUnavailable as error:
            self.log(f"render request #{message.id} refused: {error}")
            self._post_record(anchor, channel, topic, {
                "schema": RECORD_SCHEMA, "kind": "refused", "request": message.id,
                "source": {"anchor": anchor, "channel": channel, "topic": topic},
                "settings_revision": revision, "renderer": RENDERER_VERSION, "error": str(error)})
            return
        posts, _ = self._posts(channel, topic, settings, agents)
        done = self._done_ids(anchor, settings.revision, posts)
        wanted = [p for p in posts if not p.speaker.human and p.message_id not in done][-MAX_REQUEST_MESSAGES:]
        planned = self._plan(anchor, channel, topic, wanted, settings, origin=f"request:{message.id}", rearm=True)
        self.log(f"render request #{message.id}: {planned} job(s) for #{channel} › {topic} "
                 f"at settings {settings.revision[:12]}")

    def _done_ids(self, anchor: int, revision: str, posts: list[SourcePost]) -> set[int]:
        """Posts that already have a complete result at this interpretation
        *for the content they have now* — read from the memo, the truth."""
        topic = memo_topic_of(self.mirror, anchor)
        if topic is None:
            return set()
        current = {p.message_id: p for p in posts}
        done: set[int] = set()
        for parts in memo_results(self.mirror.messages(MEMO_CHANNEL, topic)).values():
            first = next(iter(parts.values()))
            if first.get("settings_revision") != revision or first.get("renderer") != RENDERER_VERSION:
                continue
            if not _complete(parts):
                continue
            ids = [int(i) for i in (first.get("source") or {}).get("messages") or []]
            if all(i in current for i in ids) and fingerprint([current[i] for i in ids]) == (first.get("source") or {}).get("fingerprint"):
                done.update(ids)
        return done

    # -- the memo ---------------------------------------------------------------------------

    def _memo_topic(self, anchor: int, label: str) -> str:
        found = memo_topic_of(self.mirror, anchor)
        if found is not None:
            return found
        topic = memo_topic(anchor, label)
        # Asked of Zulip once as well: the mirror may be a moment behind a
        # topic this very process opened.
        for message in self.client.topic_history(MEMO_CHANNEL, topic, num_before=5):
            if parse_note(message.get("content"), SOURCE_TAG) is not None:
                return topic
        self.client.ensure_subscribed(MEMO_CHANNEL)
        self.client.send_to_channel(MEMO_CHANNEL, topic, source_note(anchor))
        return topic

    def _post_record(self, anchor: int, channel: str, topic: str, record: dict) -> int:
        del channel
        return self.client.send_to_channel(MEMO_CHANNEL, self._memo_topic(anchor, topic), render_record(record))

    def _existing(self, job: dict, *, ask_zulip: bool) -> tuple[dict[int, int], int]:
        """`({part: memo message id}, parts)` of this job's result as the
        memo holds it. `ask_zulip` reads the topic itself as well — for a job
        that was running at a crash, where the mirror may be a post behind."""
        topic = memo_topic_of(self.mirror, job["anchor"])
        if topic is None:
            return {}, 0
        messages = [m.as_zulip() for m in self.mirror.messages(MEMO_CHANNEL, topic)]
        if ask_zulip:
            try:
                messages = self.client.topic_history(MEMO_CHANNEL, topic, num_before=1000) or messages
            except Exception as error:  # noqa: BLE001 - the mirror's copy is still an answer
                self.log(f"could not read #{MEMO_CHANNEL} › {topic} directly: {error!r}")
        found: dict[int, int] = {}
        parts = 0
        for message in messages:
            record = parse_record(message.get("content"))
            if record and record.get("kind") == "result" and record.get("job") == job["job"]:
                found[int(record.get("part") or 1)] = int(message["id"])
                parts = max(parts, int(record.get("parts") or 1))
        return found, parts

    # -- one job --------------------------------------------------------------------------------

    def workspace(self, job: dict) -> Path:
        return self.root / "jobs" / job["job"]

    def _render(self, job: dict) -> list[dict]:
        """The job's records: from `result.json` when a run already made
        them, else one presentation run over a fresh snapshot."""
        workspace = self.workspace(job)
        saved = workspace / "result.json"
        if saved.is_file():
            return json.loads(saved.read_text(encoding="utf-8"))
        settings = self.pin(revision=job["settings_revision"])
        agents = self.agents()
        posts, _ = self._posts(job["channel"], job["topic"], settings, agents)
        by_id = {p.message_id: p for p in posts}
        missing = [i for i in job["message_ids"] if i not in by_id]
        if missing:
            raise PresentError("the source changed before it was rendered: " + ", ".join(f"#{i}" for i in missing) + " is gone")
        chosen = [by_id[i] for i in job["message_ids"]]
        if fingerprint(chosen) != job["fingerprint"]:
            raise PresentError("the source changed before it was rendered: its content is not what was planned")
        dialogue = None
        if any(p.speaker.character for p in chosen):
            context = [p for p in posts if p.message_id < chosen[0].message_id][-CONTEXT_MESSAGES:]
            files = write_snapshot(workspace, chosen, context, settings, job["channel"], job["topic"])
            prompt = present_prompt(files, self.guides)
            self.runs += 1
            output = self._run(prompt, workspace, job) if self._run is not None else self._run_role(prompt, workspace, job)
            (workspace / "output.txt").write_text(output or "", encoding="utf-8")
            dialogue = check_dialogue(output, chosen, settings, job["channel"], job["topic"])
        else:
            workspace.mkdir(parents=True, exist_ok=True)
        records = records_for(job, dialogue, chosen)
        saved.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
        return records

    def _run_role(self, prompt: str, workspace: Path, job: dict) -> str:
        output, _, exit_code = run_role(
            SPEC, PRESENT_ROLE, prompt, cwd=workspace, timeout=PRESENT_TIMEOUT_SECONDS,
            record=next_record_path(SPEC.records_root / PRESENT_ROLE),
            extra_meta={"render_job": job["job"], "source_anchor": job["anchor"],
                        "source": f"{job['channel']}/{job['topic']}", "source_messages": job["message_ids"],
                        "settings_revision": job["settings_revision"], "renderer": job["renderer"]},
        )
        if exit_code != 0:
            raise RuntimeError(f"{PRESENT_ROLE} run exited {exit_code}: {output.strip()[:300]}")
        return output

    def work(self, *, recovering: bool = False) -> dict | None:
        """Take one due job and finish it, or fail it one attempt further."""
        job = self.jobs.take(self.clock())
        if job is None:
            return None
        try:
            posted, parts = self._existing(job, ask_zulip=recovering or job["attempts"] > 1)
            if parts and all(n in posted for n in range(1, parts + 1)):
                self.log(f"render job {job['job']}: its result is already in the memo; recognized, not re-run")
                self.jobs.update(job["job"], self.clock(), state="done", error=None,
                                 result_ids=[posted[n] for n in sorted(posted)])
                return self.jobs.get(job["job"])
            records = self._render(job)
            for record in records:
                if int(record["part"]) not in posted:
                    posted[int(record["part"])] = self._post_record(job["anchor"], job["channel"], job["topic"], record)
            self.jobs.update(job["job"], self.clock(), state="done", error=None,
                             result_ids=[posted[n] for n in sorted(posted)])
            self.log(f"render job {job['job']} done: {len(records)} record(s) in #{MEMO_CHANNEL}")
        except Exception as error:  # noqa: BLE001 - a rendering failure is a job state, never a crash
            final = job["attempts"] >= MAX_ATTEMPTS
            self.log(f"render job {job['job']} failed (attempt {job['attempts']}/{MAX_ATTEMPTS}): {error!r}")
            self.jobs.update(job["job"], self.clock(), state="failed" if final else "pending", error=str(error)[:500],
                             not_before=self.clock() + self.backoff_seconds * (2 ** (job["attempts"] - 1)))
            if final:
                try:
                    self._post_record(job["anchor"], job["channel"], job["topic"], {
                        "schema": RECORD_SCHEMA, "kind": "failed", "job": job["job"],
                        "source": {"anchor": job["anchor"], "channel": job["channel"], "topic": job["topic"],
                                   "messages": job["message_ids"], "fingerprint": job["fingerprint"]},
                        "settings_revision": job["settings_revision"], "renderer": job["renderer"],
                        "attempts": job["attempts"], "error": str(error)[:500]})
                except Exception as post_error:  # noqa: BLE001
                    self.log(f"could not record the failure of {job['job']} in the memo: {post_error!r}")
        return self.jobs.get(job["job"])

    # -- lifecycle -----------------------------------------------------------------------------------

    def resume(self) -> None:
        """Jobs a crash left `running` are pending again — with their attempt
        counted, and the memo asked first when they are taken."""
        for job in self.jobs.all("running"):
            self.log(f"render job {job['job']} was running at the restart; checked against the memo before any run")
            self.jobs.update(job["job"], self.clock(), state="pending", not_before=0)
            self._recovering.add(job["job"])

    def tick(self) -> None:
        self.intake()
        self.plan()
        while not self._stop.is_set():
            due = self.jobs.all("pending")
            recovering = any(j["job"] in self._recovering for j in due)
            done = self.work(recovering=recovering)
            if done is None:
                break
            self._recovering.discard(done["job"])

    def run(self) -> None:
        while not self.mirror.live and not self._stop.is_set():
            self._stop.wait(2.0)
        self.resume()
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as error:  # noqa: BLE001 - the worker outlives one bad tick
                self.log(f"render tick failed: {error!r}")
            self._stop.wait(TICK_SECONDS)

    def stop(self) -> None:
        self._stop.set()


def start(mirror: Mirror, client: ZulipClient, **kwargs) -> Renderer:
    """Start the rendering worker beside the listener, on its mirror."""
    renderer = Renderer(mirror, client, **kwargs)
    threading.Thread(target=renderer.run, name="front-renderer", daemon=True).start()
    return renderer
