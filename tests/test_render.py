"""Front's presentation role and its rendering jobs (`argue` p2 step 2).

Over a mirrored fake realm: what is rendered and what is not, what the
presentation run is shown, what is saved to the memo, and that failure,
retry, restart and a second settings revision behave as the plan says —
without a discussion handler ever being involved.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from agag.memo import parse_record
from agag.mirror import Mirror
from agag.mirror.testing import FakeRealm

from agfront import present, render
from agfront.present import PresentError, SourcePost, Speaker
from agfront.settings import pin

FRONT, AUTOLAB, SAGE, DEV, NOTIFIER = 15, 11, 24, 8, 21
ACK = "Message received. Please wait for the reply."
GUIDES = Path(__file__).resolve().parents[1] / "agent" / "guides"
REV_A, REV_B = "aaaa1111", "bbbb2222"

MANIFEST = """schema = "ag.settings-manifest.v1"

[characters.front]
name = "Front"
lore = "characters/front/lore.md"
face = "characters/front/face.jpg"
agents = ["front"]

[characters.autolab]
name = "Autolab"
lore = "characters/autolab/lore.md"
face = "characters/autolab/face.jpg"
agents = ["autolab"]

[characters.archsage]
name = "Archsage"
lore = "characters/archsage/lore.md"
face = "characters/archsage/face.png"
agents = ["archsage"]
"""


def roster(instance, agent, bot, bot_id):
    return (f"hello\n```agag-roster\nschema: ag.agent-roster.v1\ninstance: {instance}\nagent: {agent}\n"
            f"bot: {bot}\nbot_id: {bot_id}\nchannel: {instance}\nprefixes: x-\n```")


def settings_tree(tmp_path, revisions=((REV_A, "LORE-A"),), active=REV_A) -> Path:
    root = tmp_path / "settings"
    for revision, lore in revisions:
        snapshot = root / "revisions" / revision
        for cid in ("front", "autolab", "archsage"):
            (snapshot / "characters" / cid).mkdir(parents=True, exist_ok=True)
            (snapshot / "characters" / cid / "lore.md").write_text(f"{lore} of {cid}", encoding="utf-8")
        (snapshot / "manifest.toml").write_text(MANIFEST, encoding="utf-8")
    (root / "active.json").write_text(json.dumps({"revision": active}), encoding="utf-8")
    return root


class Poster:
    """The renderer's Zulip client over the fake realm: it posts as Front."""

    def __init__(self, realm: FakeRealm):
        self.realm = realm
        self.reads = 0
        self.fail_posts = 0

    def ensure_subscribed(self, channel):
        return True

    def send_to_channel(self, channel, topic, content):
        if self.fail_posts:
            self.fail_posts -= 1
            raise RuntimeError("zulip is down")
        return self.realm.post(channel, topic, content, sender_id=FRONT, sender_name="Front")

    def topic_history(self, channel, topic, num_before=50):
        self.reads += 1
        return sorted((dict(m) for m in self.realm.messages.values()
                       if m["display_recipient"] == channel and m["subject"] == topic), key=lambda m: m["id"])


def voice(posts_by_character):
    """A presentation run that re-voices each cited post as `<character>: <id>`."""

    def run(prompt, workspace, job):
        run.prompts.append(prompt)
        turns = [{"character": character, "text": f"{character} says #{message_id}",
                  "sources": [{"message_id": message_id}]}
                 for message_id, character in posts_by_character(job)]
        return "```ag-dialogue\n" + json.dumps({"schema": "ag.frontdesk-dialogue.v1", "turns": turns}) + "\n```"

    run.prompts = []
    return run


class World:
    def __init__(self, tmp_path, *, run=None, revisions=((REV_A, "LORE-A"),), active=REV_A):
        self.tmp = tmp_path
        self.realm = FakeRealm()
        for stream_id, name in ((35, "agents"), (24, "front"), (169, "argue"), (70, "memo")):
            self.realm.add_channel(stream_id, name)
        for instance, agent, bot, bot_id in (("front-x1", "front", "Front", FRONT), ("autolab-x1", "autolab", "autolab-x1", AUTOLAB),
                                             ("archsage-x1", "archsage", "archsage", SAGE)):
            self.realm.post("agents", f"intro-{instance}", roster(instance, agent, bot, bot_id), sender_id=bot_id,
                            sender_name=bot, quiet=True)
        self.settings_root = settings_tree(tmp_path, revisions, active)
        self.now = [10_000.0]
        self.client = Poster(self.realm)
        self.characters = {FRONT: "front", AUTOLAB: "autolab", SAGE: "archsage"}
        self.run = run or voice(self.cited)
        self.mirror = Mirror.open(tmp_path / "zulip.env", tmp_path / "mirror", client_factory=self.realm.facet,
                                  log=lambda line: None, start=True, resync_backoff=0.05)
        deadline = time.time() + 5
        while not self.mirror.live and time.time() < deadline:
            time.sleep(0.02)
        self.log: list[str] = []
        self.renderer = self.make()

    def cited(self, job):
        found = []
        for message_id in job["message_ids"]:
            message = self.realm.messages[message_id]
            if message["content"].lstrip().startswith("**[sage:"):
                continue  # a logical speaker with no character: left to the plain fallback
            found.append((message_id, self.characters[message["sender_id"]]))
        return found

    def make(self):
        return render.Renderer(self.mirror, self.client, root=self.tmp / "render", run=self.run,
                               settings_pin=lambda revision=None: pin(self.settings_root, revision), guides=GUIDES,
                               clock=lambda: self.now[0], log=self.log.append, quiet_seconds=30, ack_wait_seconds=300,
                               backoff_seconds=10)

    def sync(self):
        """Wait until the mirror holds everything the realm was told."""
        newest = max(self.realm.messages)
        deadline = time.time() + 5
        while self.mirror.message(newest) is None and time.time() < deadline:
            time.sleep(0.02)
        assert self.mirror.message(newest) is not None

    def say(self, channel, topic, content, sender_id, name):
        ident = self.realm.post(channel, topic, content, sender_id=sender_id, sender_name=name)
        self.sync()
        return ident

    def tick(self, advance=0.0):
        """The worker ticks every few seconds: what was posted is taken in at
        the present clock, then `advance` passes and it ticks again."""
        self.sync()
        self.renderer.tick()
        self.sync()
        if advance:
            self.now[0] += advance
            self.renderer.tick()
            self.sync()

    def memo_records(self, kind="result"):
        rows = sorted((m for m in self.realm.messages.values() if m["display_recipient"] == "memo"), key=lambda m: m["id"])
        found = [parse_record(m["content"]) for m in rows]
        return [r for r in found if r and r.get("kind") == kind]

    def stop(self):
        self.renderer.stop()
        self.mirror.stop()


@pytest.fixture
def world(tmp_path):
    found = World(tmp_path)
    yield found
    found.stop()


def open_argue(world, stem="argue-far"):
    world.renderer.tick()  # the first start: the present becomes the past
    anchor = world.say("argue", stem, "[selfnote][argue] from front/front-desk-1", FRONT, "Front")
    world.say("argue", stem, "I want machines that learn from their failures.", DEV, "Developer")
    return anchor


# --- who speaks, and the check -------------------------------------------------


def test_one_account_is_two_speakers_and_a_missing_character_is_plain(tmp_path):
    settings = pin(settings_tree(tmp_path), None)
    agents = {FRONT: "front", SAGE: "archsage"}
    council = {"sender_id": SAGE, "sender_full_name": "archsage", "content": "The council's view."}
    sage = {"sender_id": SAGE, "sender_full_name": "archsage", "content": "**[sage:arxiv]**\nThree papers bear on this."}
    human = {"sender_id": DEV, "sender_full_name": "Developer", "content": "hello"}
    assert present.speaker_for(council, agents, settings) == Speaker("archsage", "archsage", "archsage")
    assert present.speaker_for(sage, agents, settings) == Speaker("sage:arxiv", "archsage", None)
    assert present.speaker_for(human, agents, settings) == Speaker("Developer", None, None, human=True)
    assert present.plain_content("@**Developer**\n\n**[sage:arxiv]**\nThree papers.") == "Three papers."
    assert not present.is_renderable({**council, "content": ACK}, agents)
    assert not present.is_renderable({**council, "content": "[selfnote][served] a/b 1"}, agents)
    assert not present.is_renderable(human, agents)


def test_the_check_refuses_borrowed_words_foreign_posts_and_gaps(tmp_path):
    settings = pin(settings_tree(tmp_path), None)
    posts = [SourcePost(10, FRONT, Speaker("Front", "front", "front"), "a"),
             SourcePost(11, AUTOLAB, Speaker("autolab-x1", "autolab", "autolab"), "b")]

    def block(turns):
        return "```ag-dialogue\n" + json.dumps({"schema": "ag.frontdesk-dialogue.v1", "turns": turns}) + "\n```"

    good = [{"character": "front", "text": "x", "sources": [{"message_id": 10}]},
            {"character": "autolab", "text": "y", "sources": [{"message_id": 11}]}]
    assert len(present.check_dialogue(block(good), posts, settings, "argue", "argue-x").turns) == 2
    for turns, reason in (
        ([{**good[0], "sources": []}, good[1]], "cites no post"),
        ([{**good[0], "sources": [{"message_id": 99}]}, good[1]], "not one of the posts"),
        ([{**good[0], "sources": [{"message_id": 11}]}, good[1]], "the words of autolab-x1"),
        ([good[0]], "no turn re-voices #11"),
    ):
        with pytest.raises(PresentError, match=reason):
            present.check_dialogue(block(turns), posts, settings, "argue", "argue-x")
    with pytest.raises(PresentError, match="no ag-dialogue block"):
        present.check_dialogue("just prose", posts, settings, "argue", "argue-x")


# --- what is rendered ------------------------------------------------------------


def test_nothing_already_in_the_realm_is_rendered_unasked(world):
    world.say("front", "front-desk-old", "old question", DEV, "Developer")
    world.say("front", "front-desk-old", "old answer", FRONT, "Front")
    world.tick()
    world.tick(advance=1000)
    assert world.renderer.jobs.all() == [] and world.renderer.runs == 0
    assert any("nothing older is rendered unasked" in line for line in world.log)


def test_a_burst_of_agent_speech_is_one_job_with_the_intended_sources(world):
    anchor = open_argue(world)
    ack = world.say("argue", "argue-far", ACK, FRONT, "Front")
    world.tick()
    reply = world.say("argue", "argue-far", "What would failure look like? I will ask autolab.", FRONT, "Front")
    world.say("argue", "argue-far", "[selfnote][desire] 1 by 8", FRONT, "Front")
    built = world.say("argue", "argue-far", "I can build a harness; commit a99625f shows how.", AUTOLAB, "autolab-x1")
    sage = world.say("argue", "argue-far", "**[sage:arxiv]**\nThree papers bear on this.", SAGE, "archsage")
    world.tick()
    assert world.renderer.jobs.all() == [], "not quiet yet: nothing is planned"
    world.tick(advance=31)
    (job,) = world.renderer.jobs.all()
    assert job["state"] == "done" and job["message_ids"] == [reply, built, sage] and job["anchor"] == anchor
    assert world.renderer.runs == 1
    prompt = world.run.prompts[0]
    assert "LORE-A of front" in prompt and "LORE-A of autolab" in prompt and "LORE-A of archsage" not in prompt
    assert "commit a99625f" in prompt and "machines that learn from their failures" in prompt
    assert f"#{ack}" not in prompt and "selfnote" not in prompt
    (record,) = world.memo_records()
    assert record["source"] == {"anchor": anchor, "channel": "argue", "topic": "argue-far",
                                "messages": [reply, built, sage], "fingerprint": job["fingerprint"]}
    assert record["settings_revision"] == REV_A and record["renderer"] == present.RENDERER_VERSION
    assert record["job"] == job["job"] and (record["part"], record["parts"]) == (1, 1)
    assert [(t["character"], t["speaker"], t.get("plain", False)) for t in record["turns"]] == [
        ("front", "Front", False), ("autolab", "autolab-x1", False), (None, "sage:arxiv", True)]
    memo = sorted((m for m in world.realm.messages.values() if m["display_recipient"] == "memo"), key=lambda m: m["id"])
    assert memo[0]["content"] == f"[selfnote][memosource] {anchor}" and memo[0]["subject"] == f"argue-far-s{anchor}"
    # The human's words are never re-voiced, and nothing was written into the discussion.
    assert all(m["sender_id"] != FRONT or m["id"] in (anchor, ack, reply, reply + 1)
               for m in world.realm.messages.values() if m["display_recipient"] == "argue")


def test_while_an_ack_is_the_newest_post_the_reply_is_waited_for(world):
    world.renderer.tick()
    world.say("front", "front-desk-1", "please", DEV, "Developer")
    first = world.say("front", "front-desk-1", "Proposal: ask forge.", FRONT, "Front")
    world.say("front", "front-desk-1", "go ahead", DEV, "Developer")
    world.say("front", "front-desk-1", ACK, FRONT, "Front")
    world.tick()
    world.tick(advance=60)
    assert world.renderer.jobs.all() == [], "a reply is on its way; the burst is not cut in two"
    second = world.say("front", "front-desk-1", "Sent. It is in #agforge-x1.", FRONT, "Front")
    world.tick()
    world.tick(advance=31)
    (job,) = world.renderer.jobs.all()
    assert job["message_ids"] == [first, second]


# --- failure, retry, restart ------------------------------------------------------------


def test_a_failing_renderer_is_bounded_and_touches_nothing_but_the_memo(tmp_path):
    def broken(prompt, workspace, job):
        raise RuntimeError("the model is unavailable")

    world = World(tmp_path, run=broken)
    try:
        open_argue(world)
        world.say("argue", "argue-far", "What would failure look like?", FRONT, "Front")
        before = {i for i, m in world.realm.messages.items() if m["display_recipient"] != "memo"}
        world.tick(advance=31)
        (job,) = world.renderer.jobs.all()
        assert (job["state"], job["attempts"]) == ("pending", 1) and "unavailable" in job["error"]
        world.tick(advance=1)
        assert world.renderer.jobs.all()[0]["attempts"] == 1, "the backoff holds the retry"
        world.tick(advance=10)
        world.tick(advance=20)
        (job,) = world.renderer.jobs.all()
        assert (job["state"], job["attempts"]) == ("failed", 3)
        world.tick(advance=10_000)
        assert world.renderer.jobs.all()[0]["attempts"] == 3, "a failed job is left alone"
        (failed,) = world.memo_records("failed")
        assert failed["job"] == job["job"] and failed["attempts"] == 3 and world.memo_records() == []
        assert {i for i, m in world.realm.messages.items() if m["display_recipient"] != "memo"} == before
    finally:
        world.stop()


def test_a_crash_between_the_post_and_the_local_done_is_healed_without_a_second_run(world):
    open_argue(world)
    world.say("argue", "argue-far", "What would failure look like?", FRONT, "Front")
    world.tick(advance=31)
    (job,) = world.renderer.jobs.all()
    assert job["state"] == "done" and world.renderer.runs == 1
    # The crash: the memo holds the result, the store still says `running`,
    # and the job's own files are gone with the machine's scratch space.
    world.renderer.jobs.update(job["job"], world.now[0], state="running", result_ids=[])
    (world.renderer.workspace(job) / "result.json").unlink()
    world.renderer = world.make()
    world.renderer.resume()
    world.tick()
    (again,) = world.renderer.jobs.all()
    assert again["state"] == "done" and again["result_ids"] == job["result_ids"]
    assert world.renderer.runs == 0 and len(world.memo_records()) == 1
    assert world.client.reads >= 1, "a job that was running is checked against Zulip itself"
    assert any("recognized, not re-run" in line for line in world.log)


def test_a_failed_post_reuses_the_validated_result_instead_of_running_again(world):
    open_argue(world)
    world.say("argue", "argue-far", "What would failure look like?", FRONT, "Front")
    world.sync()
    world.renderer.intake()
    world.now[0] += 31
    world.renderer.plan()
    # The memo topic opens, then Zulip fails on the record itself.
    original = world.client.send_to_channel

    def flaky(channel, topic, content):
        if "ag-memo" in content and not flaky.failed:
            flaky.failed = True
            raise RuntimeError("zulip is down")
        return original(channel, topic, content)

    flaky.failed = False
    world.client.send_to_channel = flaky
    world.tick()
    assert world.renderer.jobs.all()[0]["state"] == "pending" and world.renderer.runs == 1
    world.tick(advance=11)
    assert world.renderer.jobs.all()[0]["state"] == "done"
    assert world.renderer.runs == 1 and len(world.memo_records()) == 1


def test_repeated_processing_and_a_replayed_feed_plan_nothing_twice(world):
    open_argue(world)
    world.say("argue", "argue-far", "What would failure look like?", FRONT, "Front")
    world.tick(advance=31)
    assert world.renderer.runs == 1
    # The same feed again, as after a lost checkpoint write.
    world.renderer.jobs.set_meta("revision", "0")
    world.tick(advance=31)
    world.tick(advance=31)
    assert len(world.renderer.jobs.all()) == 1 and world.renderer.runs == 1 and len(world.memo_records()) == 1


# --- another interpretation ---------------------------------------------------------------


def test_another_settings_revision_is_a_distinct_interpretation_of_the_same_sources(tmp_path):
    world = World(tmp_path, revisions=((REV_A, "LORE-A"), (REV_B, "LORE-B")), active=REV_A)
    try:
        open_argue(world)
        reply = world.say("argue", "argue-far", "What would failure look like?", FRONT, "Front")
        world.tick(advance=31)
        assert [r["settings_revision"] for r in world.memo_records()] == [REV_A]
        # A request by an agent account is not a request.
        world.say("argue", "argue-far", f"[selfnote][render] {REV_B}", AUTOLAB, "autolab-x1")
        world.tick()
        assert len(world.renderer.jobs.all()) == 1
        request = world.say("argue", "argue-far", f"[selfnote][render] {REV_B}", DEV, "Developer")
        world.tick()
        records = world.memo_records()
        assert [r["settings_revision"] for r in records] == [REV_A, REV_B], "the earlier result is retained"
        assert records[0]["source"]["messages"] == records[1]["source"]["messages"] == [reply]
        assert records[0]["job"] != records[1]["job"]
        assert "LORE-B of front" in world.run.prompts[-1] and "LORE-A" not in world.run.prompts[-1]
        origins = {j["settings_revision"]: j["origin"] for j in world.renderer.jobs.all()}
        assert origins == {REV_A: "auto", REV_B: f"request:{request}"}
        # Asking again, restarting, or replaying the feed buys nothing more.
        world.say("argue", "argue-far", f"[selfnote][render] {REV_B}", DEV, "Developer")
        world.renderer = world.make()
        world.renderer.jobs.set_meta("revision", "0")
        world.tick(advance=100)
        assert len(world.memo_records()) == 2 and world.renderer.runs == 0
        # A revision that is not retained is refused in the memo, with no run.
        world.say("argue", "argue-far", "[selfnote][render] cccc3333", DEV, "Developer")
        world.tick()
        (refused,) = world.memo_records("refused")
        assert refused["settings_revision"] == "cccc3333" and "not retained" in refused["error"]
    finally:
        world.stop()


def test_an_edited_source_is_identifiable_and_rendered_again_only_on_request(world):
    open_argue(world)
    reply = world.say("argue", "argue-far", "We need 3 studies.", FRONT, "Front")
    world.tick(advance=31)
    (first,) = world.memo_records()
    world.realm.edit(reply, "We need 5 studies.")
    deadline = time.time() + 5
    while world.mirror.message(reply).content != "We need 5 studies." and time.time() < deadline:
        time.sleep(0.02)
    world.tick(advance=1000)
    assert len(world.memo_records()) == 1, "an edit alone renders nothing"
    current = present.fingerprint([{"id": reply, "content": "We need 5 studies."}])
    assert first["source"]["fingerprint"] != current, "the saved result is identifiable as stale"
    world.say("argue", "argue-far", f"[selfnote][render] {REV_A}", DEV, "Developer")
    world.tick()
    records = world.memo_records()
    assert len(records) == 2 and records[1]["source"]["fingerprint"] == current
