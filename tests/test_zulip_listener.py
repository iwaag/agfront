"""agfront's part of serving a front topic: two files, one run, one reply.

The serving *discipline* — ack first, always answer, name the failed step,
re-serve when a human spoke during the run, the empty-topic guard, workspace
numbering, chatlog formatting — lives in `agag.topics` and is tested there.

Since p2 agfront decides almost nothing else. What is pinned here is exactly
that: the run gets the conversation and the freshly harvested board, the run's
answer is the reply, and **no outbound route exists in agfront** — a request
to another agent is something Front does with `agentchat`, not something this
handler posts on its behalf.

Since p8 there is one more thing to pin: a mention elsewhere serves the
`front-*` conversation the **topic's own root note** says it belongs to, and
answers **at home** — never into the topic that called.

Same rule as the sibling suites: nothing asserts what an agent said.
"""

import json
import shutil
from dataclasses import replace

import pytest
from agag import topics
from agag.topics import GuideError

from agag import intro as agents_md
from agag.selfnote import rootchat_note, Conversation
from agag.zulip import ZulipError

from agfront import settings as agfront_settings
from agfront import zulip_listener

BOT_ID = 15
HUMAN_ID = 8
CHANNEL = "front"
TOPIC = "front-20260817-120000"
REQUEST = "I want a title image for the game."

INTRO_TOPIC = "intro-agforge-agstudio1"
INTRO_BODY = "# agforge\n\nOpen an `assetplan-…` topic in `agforge-agstudio1`."


def message(sender_id=HUMAN_ID, name="Developer", content=REQUEST, id=1):
    return {
        "id": id,
        "type": "stream",
        "sender_id": sender_id,
        "sender_full_name": name,
        "display_recipient": CHANNEL,
        "subject": TOPIC,
        "content": content,
    }


class Client:
    email = "front-bot@example.invalid"

    def __init__(self, calls, history=None, board=None):
        self.calls = calls
        self.history = [message()] if history is None else history
        #: The `#agents` board the harvest reads. Its reads stay out of
        #: `calls`: it is one fixed step of every serving, pinned on its own
        #: below, and threading it through every call-order assertion would
        #: only make those assertions about the harvest.
        self.board = {INTRO_TOPIC: INTRO_BODY} if board is None else board

    def whoami(self):
        self.calls.append(("whoami",))
        return {"user_id": BOT_ID, "full_name": "Front"}

    def stream_id(self, name):
        return 30

    def channel_topics(self, stream_id):
        return list(self.board)

    def topic_history(self, channel, topic, num_before):
        if channel == agents_md.AGENTS_CHANNEL:
            return [message(sender_id=13, name="Forge", content=self.board[topic], id=99)]
        self.calls.append(("history", channel, topic, num_before))
        return self.history

    def own_rootchat_notes(self, num_before=200):
        """The `sender:me search:rootchat` narrow. Front has anchored nothing
        in this fixture, so it is party to no other conversation."""
        return []

    def send_to_channel(self, channel, topic, content):
        self.calls.append(("post", channel, topic, content))
        return 900


def wire(monkeypatch, tmp_path, calls, *, answer="on it", run=None):
    monkeypatch.setattr(zulip_listener, "TOPICS_ROOT", tmp_path / "topics")
    monkeypatch.setattr(zulip_listener, "RECORDS_ROOT", tmp_path / "records")
    monkeypatch.setattr(
        topics,
        "topic_write",
        lambda topic, text, **kwargs: (
            calls.append(("reply", kwargs.get("channel"), topic, text)) or "success"
        ),
    )

    def front_run(prompt, cwd, home, role="front", *, extra_meta=None, selection=None):
        calls.append(("front", prompt, cwd, home, role, extra_meta, selection))
        if run is not None:
            run(cwd)
        return answer

    monkeypatch.setattr(zulip_listener, "run_front", front_run)
    guides = tmp_path / "guides"
    (guides / "front").mkdir(parents=True)
    (guides / "front" / "guide.md").write_text("FRONT GUIDE")
    (guides / "character_talk").mkdir(parents=True)
    (guides / "character_talk" / "guide.md").write_text("CHARACTER GUIDE")
    monkeypatch.setattr(zulip_listener, "GUIDES", guides)
    # No character settings unless a test places some (`settings_at`).
    monkeypatch.setattr(agfront_settings, "settings_root", lambda: tmp_path / "no-settings")


def gen_dir(tmp_path, number, role="front"):
    return tmp_path / "topics" / CHANNEL / TOPIC / str(number) / role


def replies(calls):
    """Just the message bodies, in the order they were posted."""
    return [call[3] for call in calls if call[0] == "reply"]


# --- one serving ------------------------------------------------------------


def test_the_run_s_answer_is_the_reply_and_nothing_is_posted_elsewhere(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, answer="Forge can do this. May I ask it?")

    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)

    # The second `whoami` is Front's execution menu, addressed by the name a
    # mention matches (`ag.exec-options.v1`); a real client answers it from
    # cache. The topic is read **before** the ack — a configuration-only post
    # must buy neither an ack nor a run — and that same read is the serving's
    # chatlog, so the contract costs no extra Zulip call. The last history
    # read is the post-run re-check.
    assert [call[0] for call in calls] == [
        "whoami", "whoami", "history", "reply", "front",
        # the handoff lookup, the reply, then the post-run re-check
        "history", "reply", "history",
    ]
    assert {call[1] for call in calls if call[0] == "reply"} == {CHANNEL}
    assert replies(calls)[-1] == "@**Developer**\n\nForge can do this. May I ask it?"


def test_the_chatlog_and_the_prompt_are_the_run_s_whole_input(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)
    prompt, cwd, home = next(
        (call[1], call[2], call[3]) for call in calls if call[0] == "front"
    )
    assert prompt == (
        "The chatlog is placed in the working directory. "
        "You are 'Front' in the chatlog.\n"
        "\n"
        + topics.conversation_context(f"[Developer] {REQUEST}\n")
        + "\n\nFRONT GUIDE"
    )
    assert cwd == gen_dir(tmp_path, 1)
    # The same bytes are in the file and in the prompt: one rendering, one
    # snapshot, so a run cannot be shown two versions of one conversation
    # (`routine_tests` p2 ex1 step 2).
    assert (cwd / "chatlog.md").read_text() == f"[Developer] {REQUEST}\n"
    assert REQUEST in prompt and "Developer" in prompt
    # The run posts as a participant of this conversation, so an answer to
    # whatever it says elsewhere comes back here.
    assert home == (CHANNEL, TOPIC)


def test_a_run_that_writes_nothing_is_the_normal_case(monkeypatch, tmp_path):
    """Front answering in text, refusing, or asking permission all leave the
    workspace as it was. There is no command file to look for any more."""
    calls = []
    wire(monkeypatch, tmp_path, calls, answer="I cannot do that.")
    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert replies(calls)[-1] == "@**Developer**\n\nI cannot do that."
    assert sorted(p.name for p in gen_dir(tmp_path, 1).iterdir()) == ["chatlog.md", "tools"]


# --- the intro harvest ------------------------------------------------------


def test_the_intro_harvest_lands_in_tools_before_the_run(monkeypatch, tmp_path):
    """The guide tells Front to read `tools/`; this is what puts the other
    agents' own introductions there, freshly, for every run."""
    calls = []
    seen = {}
    wire(
        monkeypatch, tmp_path, calls,
        run=lambda cwd: seen.update(text=(cwd / "tools" / "agents.md").read_text()),
    )
    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert INTRO_BODY in seen["text"]


def test_no_schedule_tool_is_placed_in_tools(monkeypatch, tmp_path):
    """The schedule and its CLI are gone (`refine_routine` p1): a run's
    `tools/` holds the board and nothing about firing routines."""
    calls = []
    seen = {}
    wire(
        monkeypatch, tmp_path, calls,
        run=lambda cwd: seen.update(files=sorted(f.name for f in (cwd / "tools").iterdir())),
    )
    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert seen["files"] == ["agents.md"]


def test_a_run_sees_the_board_as_it_was_at_that_moment(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    zulip_listener.handle_topic(Client(calls, board={}), CHANNEL, TOPIC)
    first = (gen_dir(tmp_path, 1) / "tools" / "agents.md").read_text()
    zulip_listener.handle_topic(Client(calls, board={"intro-new": "hello"}), CHANNEL, TOPIC)
    second = (gen_dir(tmp_path, 2) / "tools" / "agents.md").read_text()
    assert agents_md.NO_AGENTS in first
    assert "hello" in second


def test_an_empty_board_does_not_stop_the_run(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, answer="nobody to ask")
    zulip_listener.handle_topic(Client(calls, board={}), CHANNEL, TOPIC)
    assert any(call[0] == "front" for call in calls)
    assert replies(calls)[-1] == "@**Developer**\n\nnobody to ask"


# --- attributability --------------------------------------------------------


def test_agfront_knows_no_other_agent_s_channel():
    """p2's third success criterion, as a test rather than only a grep: the
    channel Front posts into must come from the harvested board. An agfront
    that hardcoded it would pass every other test here."""
    import pathlib

    root = pathlib.Path(zulip_listener.__file__).resolve().parents[2]
    searched = [*(root / "src").rglob("*.py"), *(root / "agent" / "guides").rglob("*.md")]
    assert searched
    for path in searched:
        assert "agforge-agstudio1" not in path.read_text(encoding="utf-8"), path


# --- failures and generations ----------------------------------------------


def test_a_front_failure_names_its_step(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)

    def explode(prompt, cwd, home, role="front", **_):
        raise zulip_listener.ListenerError("claude_code timed out")

    monkeypatch.setattr(zulip_listener, "run_front", explode)
    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert replies(calls)[-1] == (
        "@**Developer**\n\nfailed during front: claude_code timed out"
    )


def test_each_serving_gets_its_own_generation(monkeypatch, tmp_path):
    """The Developer's permission is simply the next serving, so a
    conversation has several of them and each keeps its own evidence."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)
    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert (gen_dir(tmp_path, 1) / "chatlog.md").is_file()
    assert (gen_dir(tmp_path, 2) / "chatlog.md").is_file()


def test_an_empty_topic_costs_no_agent_run(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    zulip_listener.handle_topic(Client(calls, history=[]), CHANNEL, TOPIC)
    assert not any(call[0] == "front" for call in calls)
    assert calls[-1][3] == zulip_listener.EMPTY_REPLY


def test_our_acks_are_dropped_from_the_chatlog(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    history = [
        message(),
        message(sender_id=BOT_ID, name="Front", content=zulip_listener.ACK_TEXT, id=2),
        message(sender_id=BOT_ID, name="Front", content="asked forge", id=3),
    ]
    zulip_listener.handle_topic(Client(calls, history=history), CHANNEL, TOPIC)
    assert (gen_dir(tmp_path, 1) / "chatlog.md").read_text() == (
        f"[Developer] {REQUEST}\n[Front (you)] asked forge\n"
    )


def test_guide_refuses_to_start_without_the_file(monkeypatch, tmp_path):
    monkeypatch.setattr(zulip_listener, "GUIDES", tmp_path)
    with pytest.raises(GuideError):
        zulip_listener.guide("front", "guide.md")


# --- the callback: served because somebody named Front ---------------------


REMOTE_CHANNEL = "work-s2-10"
REMOTE_TOPIC = "workrun-task1-s2-10"


class Board(Client):
    """A client that holds more than one conversation."""

    def __init__(self, calls, histories, board=None):
        super().__init__(calls, board=board)
        self.histories = dict(histories)

    def topic_history(self, channel, topic, num_before):
        if channel == agents_md.AGENTS_CHANNEL:
            return [message(sender_id=13, name="Forge", content=self.board[topic], id=99)]
        self.calls.append(("history", channel, topic, num_before))
        return list(self.histories.get((channel, topic), []))

    def own_rootchat_notes(self, num_before=200):
        """The `sender:me search:rootchat` narrow, over these fixtures."""
        found = []
        for (channel, topic), history in self.histories.items():
            for row in history:
                if row.get("sender_id") != BOT_ID:
                    continue
                if str(row.get("content", "")).startswith("[selfnote][rootchat]"):
                    found.append({**row, "type": "stream",
                                  "display_recipient": channel, "subject": topic})
        return found


def remote_message(content="task 1 is blocked, what now?", sender_id=11, name="Autolab"):
    return {
        "id": 7,
        "type": "stream",
        "sender_id": sender_id,
        "sender_full_name": name,
        "display_recipient": REMOTE_CHANNEL,
        "subject": REMOTE_TOPIC,
        "content": content,
    }


def root_note(id=5):
    """Front's own anchor in the remote topic — hidden everywhere, and the
    only thing that says which conversation Front is there for."""
    return {
        "id": id,
        "type": "stream",
        "sender_id": BOT_ID,
        "sender_full_name": "Front",
        "display_recipient": REMOTE_CHANNEL,
        "subject": REMOTE_TOPIC,
        "content": rootchat_note(Conversation(CHANNEL, TOPIC)),
    }


def test_a_mention_serves_the_front_topic_it_was_sent_on_behalf_of(monkeypatch, tmp_path):
    """p8 for Front: the request it is supervising is the run's subject, and
    the answer goes **home**, to the developer — not into the topic that
    called, which is where p7's loop was fed from."""
    calls = []
    wire(monkeypatch, tmp_path, calls, answer="yes, that is done")
    client = Board(calls, {
        (CHANNEL, TOPIC): [message()],
        (REMOTE_CHANNEL, REMOTE_TOPIC): [root_note(), remote_message()],
    })

    zulip_listener.handle_mention(client, REMOTE_CHANNEL, REMOTE_TOPIC)

    # The chatlog is the front conversation; the remote is a thread beside it.
    _, cwd, home = next((c[1], c[2], c[3]) for c in calls if c[0] == "front")
    assert home == (CHANNEL, TOPIC)
    assert (cwd / "chatlog.md").read_text() == f"[Developer] {REQUEST}\n"
    thread = (cwd / "threads" / REMOTE_CHANNEL / f"{REMOTE_TOPIC}.md").read_text()
    assert "task 1 is blocked" in thread
    # The note that carried the wiring is not part of the conversation.
    assert "selfnote" not in thread
    # Everything posted went home. Nothing was said in the calling topic.
    assert {c[1] for c in calls if c[0] == "reply"} == {CHANNEL}
    assert replies(calls)[-1] == "@**Developer**\n\nyes, that is done"
    # ...including the mark that says this callback is answered (p9).
    assert [c for c in calls if c[0] == "post"] == [
        ("post", CHANNEL, TOPIC,
         f"[selfnote][served] {REMOTE_CHANNEL}/{REMOTE_TOPIC} 7"),
    ]


def test_a_mention_front_never_anchored_leaves_no_mark(monkeypatch, tmp_path):
    """Nothing was served, so there is nothing to say has been served."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Board(calls, {("general", "somebody-elses-topic"): [remote_message()]})
    zulip_listener.handle_mention(client, "general", "somebody-elses-topic")
    assert [c for c in calls if c[0] == "post"] == []


def test_the_threads_are_named_in_the_prompt(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Board(calls, {
        (CHANNEL, TOPIC): [message()],
        (REMOTE_CHANNEL, REMOTE_TOPIC): [root_note(), remote_message()],
    })
    zulip_listener.handle_topic(client, CHANNEL, TOPIC)
    prompt = next(c[1] for c in calls if c[0] == "front")
    assert f'"threads/{REMOTE_CHANNEL}/{REMOTE_TOPIC}.md"' in prompt


def test_a_first_request_carries_no_sentence_about_threads(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)
    prompt = next(c[1] for c in calls if c[0] == "front")
    assert "threads" not in prompt


def test_a_mention_in_a_topic_front_never_anchored_costs_no_run(monkeypatch, tmp_path):
    """Front's entrance is `#front`. Being named somewhere it never wrote is
    not a request to it, and there is no root note of its own to say
    otherwise."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Board(calls, {("general", "somebody-elses-topic"): [remote_message()]})
    zulip_listener.handle_mention(client, "general", "somebody-elses-topic")
    assert [call[0] for call in calls] == ["whoami", "history"]


# --- the Front Desk: a conversation chooses its role (front_desk p1) ---------


DESK_TOPIC = "front-desk-20260908-1530"


def desk_message(content="やっほー", id=1):
    return {**message(content=content, id=id), "subject": DESK_TOPIC}


def role_calls(calls):
    return [(call[3], call[4]) for call in calls if call[0] == "front"]


def test_a_front_desk_topic_is_served_by_character_talk_with_its_own_guide(monkeypatch, tmp_path):
    """The voice is the guide, not the role's name: a `front-desk-` serving
    runs `character_talk` and its prompt carries that guide and no other."""
    calls = []
    wire(monkeypatch, tmp_path, calls, answer="やっほー✨")
    zulip_listener.handle_topic(Client(calls, history=[desk_message()]), CHANNEL, DESK_TOPIC)
    assert role_calls(calls) == [((CHANNEL, DESK_TOPIC), "character_talk")]
    prompt = next(c[1] for c in calls if c[0] == "front")
    assert "CHARACTER GUIDE" in prompt and "FRONT GUIDE" not in prompt
    # Its workspace is filed under the role, like its run record.
    cwd = next(c[2] for c in calls if c[0] == "front")
    assert cwd == tmp_path / "topics" / CHANNEL / DESK_TOPIC / "1" / "character_talk"
    # Since p2 the desk chatlog keeps the ids (`agfront.evidence`).
    assert "[Developer #1]" in (cwd / "chatlog.md").read_text()
    assert replies(calls)[-1] == "@**Developer**\n\nやっほー✨"


def test_an_ordinary_front_topic_is_still_served_by_front(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert role_calls(calls) == [((CHANNEL, TOPIC), "front")]
    prompt = next(c[1] for c in calls if c[0] == "front")
    assert "FRONT GUIDE" in prompt and "CHARACTER GUIDE" not in prompt


def test_the_role_is_the_prefix_and_nothing_else():
    assert zulip_listener.role_for("front", "front-desk-abc") == "character_talk"
    assert zulip_listener.role_for("front", "front-desk-") == "character_talk"
    assert zulip_listener.role_for("front", "front-20260817-p2-chat") == "front"
    assert zulip_listener.role_for("front", "front-routine-ghtrends-2026-09-07T07:00Z") == "front"
    # A remote topic never decides the role; only the home does.
    assert zulip_listener.role_for("work-s2-10", "workrun-task1-s2-10") == "front"


def test_a_callback_into_a_front_desk_conversation_keeps_its_voice(monkeypatch, tmp_path):
    """The role is chosen from the **home** the root note names, so autolab
    naming Front in its own topic brings back the Front Desk voice when that
    is where the request came from — and answers there, at home."""
    calls = []
    wire(monkeypatch, tmp_path, calls, answer="できたよ🎉")
    note = {**root_note(), "content": rootchat_note(Conversation(CHANNEL, DESK_TOPIC))}
    client = Board(calls, {
        (CHANNEL, DESK_TOPIC): [desk_message("ghtrends をお願い")],
        (REMOTE_CHANNEL, REMOTE_TOPIC): [note, remote_message("done: repo x, 123 stars")],
    })
    zulip_listener.handle_mention(client, REMOTE_CHANNEL, REMOTE_TOPIC)
    assert role_calls(calls) == [((CHANNEL, DESK_TOPIC), "character_talk")]
    prompt, cwd = next((c[1], c[2]) for c in calls if c[0] == "front")
    assert "CHARACTER GUIDE" in prompt
    assert "done: repo x" in (cwd / "threads" / REMOTE_CHANNEL / f"{REMOTE_TOPIC}.md").read_text()
    assert {c[1] for c in calls if c[0] == "reply"} == {CHANNEL}
    assert [c for c in calls if c[0] == "reply"][-1][2] == DESK_TOPIC
    assert [c for c in calls if c[0] == "post"] == [
        ("post", CHANNEL, DESK_TOPIC, f"[selfnote][served] {REMOTE_CHANNEL}/{REMOTE_TOPIC} 7"),
    ]


def test_a_callback_into_an_ordinary_conversation_keeps_the_front_voice(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Board(calls, {
        (CHANNEL, TOPIC): [message()],
        (REMOTE_CHANNEL, REMOTE_TOPIC): [root_note(), remote_message()],
    })
    zulip_listener.handle_mention(client, REMOTE_CHANNEL, REMOTE_TOPIC)
    assert role_calls(calls) == [((CHANNEL, TOPIC), "front")]


def test_a_front_desk_failure_names_its_role(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)

    def explode(prompt, cwd, home, role="front", **_):
        raise zulip_listener.ListenerError(f"{role} run exited 1")

    monkeypatch.setattr(zulip_listener, "run_front", explode)
    zulip_listener.handle_topic(Client(calls, history=[desk_message()]), CHANNEL, DESK_TOPIC)
    assert replies(calls)[-1] == "@**Developer**\n\nfailed during character_talk: character_talk run exited 1"


def test_the_character_guide_exists_and_names_both_voices():
    """The guide is read from disk per run; a missing one is a run with no
    instruction. It has to define the two voices and the event-driven turn,
    and must not carry the old `agentchat wait` advice."""
    text = zulip_listener.guide("character_talk", "guide.md")
    # Since p2 the character is the settings' lore, not a description here.
    assert "characters.md" in text and "ギャル" not in text
    assert "agentchat send" in text
    assert "agentchat wait" not in text
    assert "read --since" in text or "--since" in text


# --- the listener entry ------------------------------------------------------


def test_the_listener_is_the_skeleton_with_one_route_and_the_mention_route(monkeypatch):
    from agfront import listener

    handed = {}
    monkeypatch.setattr(
        listener, "listener_main",
        lambda spec, routes, **kw: handed.update(spec=spec, routes=routes, **kw),
    )
    monkeypatch.setattr(listener, "recover_runs", lambda client: handed.update(recovered=True))
    listener.main()
    assert handed["spec"] is zulip_listener.SPEC
    assert handed["routes"] == {"front-": zulip_listener.handle_topic,
                                "routinerun-": zulip_listener.handle_topic}
    assert handed["on_mention"] is zulip_listener.handle_mention
    assert handed["recovered"] is True
    # `front-` and its own `routinerun-` are the only prefixes swept: Front
    # never answers the topics it opens in other agents' channels, by filter
    # and not by luck.
    assert zulip_listener.SPEC.sweep_prefixes == ("front-", "routinerun-")


# --- the Front Desk: characters and evidence (front_desk p2 step 2) ---------


MANIFEST = """schema = "ag.settings-manifest.v1"

[characters.front]
name = "Front"
nickname = "姐さん"
lore = "characters/front/lore.md"
face = "characters/front/face.jpg"
agents = ["front"]

[characters.autolab]
name = "Autolab"
nickname = "親方"
lore = "characters/autolab/lore.md"
face = "characters/autolab/face.jpg"
agents = ["autolab"]
"""


def settings_at(monkeypatch, tmp_path, revision="aaaa1111", front_lore="皆からは「姐さん」と呼ばれている。",
                autolab_lore="みんなからは「親方」と呼ばれている。"):
    """A synced settings tree, the way `agentroom-settings sync` leaves it."""
    root = tmp_path / "settings"
    snapshot = root / "revisions" / revision
    for cid, lore in (("front", front_lore), ("autolab", autolab_lore)):
        (snapshot / "characters" / cid).mkdir(parents=True, exist_ok=True)
        (snapshot / "characters" / cid / "lore.md").write_text(lore, encoding="utf-8")
        (snapshot / "characters" / cid / "face.jpg").write_bytes(b"jpg")
    (snapshot / "manifest.toml").write_text(MANIFEST, encoding="utf-8")
    (root / "active.json").write_text(json.dumps({"revision": revision}), encoding="utf-8")
    monkeypatch.setattr(agfront_settings, "settings_root", lambda: root)
    return root


def the_run(calls):
    return next(c for c in calls if c[0] == "front")


def test_a_front_desk_run_is_given_the_characters_of_one_revision(monkeypatch, tmp_path):
    """The lore is the character: whole, from the pinned revision, with the
    section that is Front marked — and the revision stamped into the record."""
    calls = []
    wire(monkeypatch, tmp_path, calls, answer="やっほー✨")
    settings_at(monkeypatch, tmp_path)
    zulip_listener.handle_topic(Client(calls, history=[desk_message()]), CHANNEL, DESK_TOPIC)
    prompt, cwd, extra = the_run(calls)[1], the_run(calls)[2], the_run(calls)[5]
    characters = (cwd / "characters.md").read_text(encoding="utf-8")
    assert "settings revision aaaa1111" in characters
    assert "## front — **this is you**" in characters and "「姐さん」" in characters
    assert "## autolab" in characters and "「親方」" in characters and "agents: autolab" in characters
    assert "this is you" not in characters.split("## autolab")[1]
    assert json.loads((cwd / "settings.json").read_text())["revision"] == "aaaa1111"
    assert 'placed beside it in "characters.md" (settings revision aaaa1111)' in prompt
    assert extra == {"settings_revision": "aaaa1111"}


def test_the_desk_chatlog_names_the_conversation_and_keeps_every_id(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    settings_at(monkeypatch, tmp_path)
    history = [desk_message("お願い", id=5171),
               {**desk_message("Message received. Please wait for the reply.", id=5172), "sender_id": BOT_ID, "sender_full_name": "Front"},
               {**desk_message("[selfnote][served] a/b 1", id=5173), "sender_id": BOT_ID, "sender_full_name": "Front"},
               {**desk_message("了解〜", id=5174), "sender_id": BOT_ID, "sender_full_name": "Front"}]
    zulip_listener.handle_topic(Client(calls, history=history), CHANNEL, DESK_TOPIC)
    chatlog = (the_run(calls)[2] / "chatlog.md").read_text(encoding="utf-8")
    assert chatlog.startswith(f"# #{CHANNEL} › {DESK_TOPIC}\n")
    assert "[Developer #5171] sender 8" in chatlog and "[Front (you) #5174]" in chatlog
    assert "#5172" not in chatlog and "selfnote" not in chatlog  # the ack and the note are not conversation
    assert "2 posts" in chatlog


def test_a_callback_s_thread_carries_ids_and_says_when_it_is_resolved(monkeypatch, tmp_path):
    """The completion report is very often the post that resolves the topic:
    the thread is read under its ✔ name and the file says the conversation
    is finished, with the id of the report to cite."""
    calls = []
    wire(monkeypatch, tmp_path, calls, answer="できたよ🎉")
    settings_at(monkeypatch, tmp_path)
    note = {**root_note(), "content": rootchat_note(Conversation(CHANNEL, DESK_TOPIC)), "subject": f"✔ {REMOTE_TOPIC}"}
    done = {**remote_message("done: commit a99625f, 32 lines", sender_id=11, name="Autolab"), "id": 5203,
            "subject": f"✔ {REMOTE_TOPIC}", "timestamp": 1788872596}
    client = Board(calls, {
        (CHANNEL, DESK_TOPIC): [desk_message("ghtrends をお願い")],
        (REMOTE_CHANNEL, f"✔ {REMOTE_TOPIC}"): [note, done],
    })
    zulip_listener.handle_mention(client, REMOTE_CHANNEL, REMOTE_TOPIC)
    assert role_calls(calls) == [((CHANNEL, DESK_TOPIC), "character_talk")]
    prompt, cwd = the_run(calls)[1], the_run(calls)[2]
    thread = (cwd / "threads" / REMOTE_CHANNEL / f"{REMOTE_TOPIC}.md").read_text(encoding="utf-8")
    assert thread.startswith(f"# #{REMOTE_CHANNEL} › {REMOTE_TOPIC}\n")
    assert "resolved (✔)" in thread and f"Now named `✔ {REMOTE_TOPIC}`" in thread
    assert "[Autolab #5203] sender 11" in thread and "commit a99625f" in thread
    assert "selfnote" not in thread
    assert f'"threads/{REMOTE_CHANNEL}/{REMOTE_TOPIC}.md"' in prompt
    assert "characters.md" in prompt and (cwd / "characters.md").exists()


def test_a_thread_that_cannot_be_read_is_written_as_such(monkeypatch, tmp_path):
    """Skipping it silently would read as "no news" to the run."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    settings_at(monkeypatch, tmp_path)

    class Flaky(Board):
        def topic_history(self, channel, topic, num_before):
            if channel == REMOTE_CHANNEL:
                raise ZulipError("realm down")
            return super().topic_history(channel, topic, num_before)

    client = Flaky(calls, {
        (CHANNEL, DESK_TOPIC): [desk_message("進捗は？")],
        (REMOTE_CHANNEL, REMOTE_TOPIC): [{**root_note(), "content": rootchat_note(Conversation(CHANNEL, DESK_TOPIC))}],
    })
    zulip_listener.handle_topic(client, CHANNEL, DESK_TOPIC)
    cwd = the_run(calls)[2]
    thread = (cwd / "threads" / REMOTE_CHANNEL / f"{REMOTE_TOPIC}.md").read_text(encoding="utf-8")
    assert "could not be read: ZulipError: realm down" in thread
    assert f'"threads/{REMOTE_CHANNEL}/{REMOTE_TOPIC}.md"' in the_run(calls)[1]


def test_a_bounded_history_says_so(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    settings_at(monkeypatch, tmp_path)
    monkeypatch.setattr(zulip_listener, "HISTORY_MESSAGES", 3)
    history = [desk_message(f"m{i}", id=i) for i in range(1, 4)]
    zulip_listener.handle_topic(Client(calls, history=history), CHANNEL, DESK_TOPIC)
    chatlog = (the_run(calls)[2] / "chatlog.md").read_text(encoding="utf-8")
    assert "Only the newest 3 messages were fetched" in chatlog and "--since" in chatlog


def test_without_settings_the_run_still_happens_and_is_told_why(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)  # settings_root → an absent directory
    zulip_listener.handle_topic(Client(calls, history=[desk_message()]), CHANNEL, DESK_TOPIC)
    prompt, cwd, extra = the_run(calls)[1], the_run(calls)[2], the_run(calls)[5]
    assert "No character settings are available for this run" in prompt
    assert "agentroom-settings sync" in prompt
    assert not (cwd / "characters.md").exists() and extra is None
    assert replies(calls)[-1].endswith("on it")


def test_a_settings_update_reaches_the_next_run_not_the_one_in_progress(monkeypatch, tmp_path):
    """Pinned at the start of the serving and copied: what the run reads is
    the file in its own workspace, which a later sync does not touch."""
    calls = []
    root = settings_at(monkeypatch, tmp_path, revision="aaaa1111", front_lore="first lore")

    def sync_during_the_run(cwd):
        # A new revision lands while the run is reading its files.
        snapshot = root / "revisions" / "bbbb2222"
        shutil.copytree(root / "revisions" / "aaaa1111", snapshot)
        (snapshot / "characters" / "front" / "lore.md").write_text("second lore", encoding="utf-8")
        (root / "active.json").write_text(json.dumps({"revision": "bbbb2222"}), encoding="utf-8")
        assert "first lore" in (cwd / "characters.md").read_text(encoding="utf-8")

    wire(monkeypatch, tmp_path, calls, run=sync_during_the_run)
    monkeypatch.setattr(agfront_settings, "settings_root", lambda: root)
    zulip_listener.handle_topic(Client(calls, history=[desk_message()]), CHANNEL, DESK_TOPIC)
    assert the_run(calls)[5] == {"settings_revision": "aaaa1111"}
    calls.clear()
    zulip_listener.handle_topic(Client(calls, history=[desk_message()]), CHANNEL, DESK_TOPIC)
    assert the_run(calls)[5] == {"settings_revision": "bbbb2222"}
    assert "second lore" in (the_run(calls)[2] / "characters.md").read_text(encoding="utf-8")


def test_an_ordinary_front_run_is_untouched_by_the_desk_s_files(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    settings_at(monkeypatch, tmp_path)
    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)
    prompt, cwd, extra = the_run(calls)[1], the_run(calls)[2], the_run(calls)[5]
    assert (cwd / "chatlog.md").read_text() == f"[Developer] {REQUEST}\n"
    assert not (cwd / "characters.md").exists() and "characters" not in prompt and extra is None


def test_the_settings_root_is_configuration_of_this_instance(monkeypatch, tmp_path):
    monkeypatch.delenv(agfront_settings.SETTINGS_ROOT_VARIABLE, raising=False)
    monkeypatch.setattr(agfront_settings, "SPEC", replace(agfront_settings.SPEC, root=tmp_path))
    assert agfront_settings.settings_root() == agfront_settings.DEFAULT_SETTINGS_ROOT
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local" / "instance.toml").write_text('name = "front-x1"\nsettings_root = "elsewhere/settings"\n')
    assert agfront_settings.settings_root() == (agfront_settings.AGFRONT_ROOT / "elsewhere/settings").resolve()
    monkeypatch.setenv(agfront_settings.SETTINGS_ROOT_VARIABLE, str(tmp_path / "env"))
    assert agfront_settings.settings_root() == tmp_path / "env"


# --- the Front Desk: the dialogue block in the post (front_desk p2 step 3) --


def test_a_desk_reply_with_a_dialogue_is_posted_as_reply_plus_canonical_block(monkeypatch, tmp_path):
    calls = []
    scene = ('```ag-dialogue\n{"schema": "ag.frontdesk-dialogue.v1", "settings_revision": "wrong", "turns": ['
             '{"character": "front", "text": "親方、どう？"}, '
             '{"character": "autolab", "text": "終わった。", "sources": [{"channel": "work-g-13", "topic": "workrun-task1-g-13", "message_id": 5203}]}]}\n```')
    wire(monkeypatch, tmp_path, calls, answer=f"できたよ🎉\n\n{scene}")
    settings_at(monkeypatch, tmp_path)
    zulip_listener.handle_topic(Client(calls, history=[desk_message("どうなった？")]), CHANNEL, DESK_TOPIC)
    posted = replies(calls)[-1]
    assert posted.startswith("@**Developer**\n\nできたよ🎉\n\n```ag-dialogue\n")
    body = json.loads(posted.split("```ag-dialogue\n", 1)[1].rsplit("\n```", 1)[0])
    assert body["settings_revision"] == "aaaa1111"  # the pinned one, not the run's
    assert [t["character"] for t in body["turns"]] == ["front", "autolab"]
    assert "wrong" not in posted


def test_a_desk_reply_with_a_broken_block_is_posted_with_the_error_fence(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, answer="できたよ🎉\n\n```ag-dialogue\n{oops\n```")
    settings_at(monkeypatch, tmp_path)
    zulip_listener.handle_topic(Client(calls, history=[desk_message("どうなった？")]), CHANNEL, DESK_TOPIC)
    posted = replies(calls)[-1]
    assert posted.startswith("@**Developer**\n\nできたよ🎉\n\n```ag-dialogue-error\n")
    assert (the_run(calls)[2] / "dialogue-error.txt").exists()


def test_an_ordinary_front_reply_is_never_parsed_for_a_block(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, answer="ok\n\n```ag-dialogue\n{oops\n```")
    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert replies(calls)[-1].endswith("ok\n\n```ag-dialogue\n{oops\n```")


# --- execution options (ag.exec-options.v1, runtime-profile step4) ---------

from agag import execopt  # noqa: E402
from agag.execopt import Option, Selection  # noqa: E402

from agfront import instance as front_instance  # noqa: E402


def exec_command(option, bot="Front"):
    return f"@**{bot}** use {option}"


def test_front_publishes_only_profiles_it_actually_has(tmp_path):
    config = tmp_path / "agents.toml"
    config.write_text(
        'schema = "ag.agent-config.v2"\n'
        '[models."antigravity/g"]\n'
        '[profiles.agy]\nharness = "agy"\nmodel = "antigravity/g"\n',
        encoding="utf-8",
    )
    assert [option.name for option in front_instance.exec_options(config)] == ["default", "agy"]


def test_fronts_options_are_about_fronts_own_conversations():
    # Asking Front to run on agy and asking Front to have autolab run on agy
    # are different requests; the menu says which one this is.
    for option in front_instance.SPEC.exec_options:
        assert "my own conversations" in option.covers


def test_a_selection_reaches_fronts_own_run(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    zulip_listener.handle_topic(
        Client(calls, history=[message(content=exec_command("agy"), id=5),
                               message(content=REQUEST, id=6)]),
        CHANNEL, TOPIC,
    )
    selection = next(call for call in calls if call[0] == "front")[6]
    assert selection.option == "agy" and selection.message_id == 5


def test_a_configuration_only_post_in_a_front_topic_starts_no_run(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    zulip_listener.handle_topic(
        Client(calls, history=[message(sender_id=BOT_ID, name="Front", content="answered", id=4),
                               message(content=exec_command("agy"), id=5)]),
        CHANNEL, TOPIC,
    )
    assert not [call for call in calls if call[0] == "front"]
    assert "Execution option set to `agy`" in replies(calls)[-1]


def test_an_unpublished_option_is_refused_and_names_the_menu(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    zulip_listener.handle_topic(
        Client(calls, history=[message(sender_id=BOT_ID, name="Front", content="answered", id=4),
                               message(content=exec_command("opus"), id=5)]),
        CHANNEL, TOPIC,
    )
    assert not [call for call in calls if call[0] == "front"]
    refusal = replies(calls)[-1]
    assert refusal.startswith("@**Developer**")
    assert "`opus`" in refusal and "`agy`" in refusal


def test_a_callback_answers_home_under_homes_selection(monkeypatch, tmp_path):
    """A callback's remote topic is not Front's execution context."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    remote = ("agforge-agstudio1", "assetplan-x")

    class Callback(Client):
        def topic_history(self, channel, topic, num_before):
            if (channel, topic) == remote:
                self.calls.append(("history", channel, topic, num_before))
                return [
                    message(sender_id=BOT_ID, name="Front",
                            content=rootchat_note(Conversation(CHANNEL, TOPIC))),
                    message(sender_id=13, name="Forge",
                            content=exec_command("codex", bot="Forge"), id=70),
                    message(sender_id=13, name="Forge",
                            content="@**Front** here it is", id=71),
                ]
            return super().topic_history(channel, topic, num_before)

    client = Callback(calls, history=[message(content=exec_command("agy"), id=5),
                                      message(content=REQUEST, id=6)])
    zulip_listener.handle_mention(client, *remote)
    assert next(call for call in calls if call[0] == "front")[6].option == "agy"



# --- derived usage pools (agag.execpool, refactor p3 ex1 step 3) -----------

import tomllib as _tomllib  # noqa: E402

from agag import execpool  # noqa: E402
from agag.agent_config import load_config as _load_config  # noqa: E402


def _real_config():
    return _load_config(
        front_instance.SPEC.agents_config, front_instance.SPEC.agents_local_config
    )


def test_the_named_options_resolve_to_the_pools_they_declare():
    """A declaration is an assertion, and this is that assertion checked.

    The named options depend only on the committed `agents.toml` — an overlay
    moves *roles*, not the option-to-profile mapping — so this is
    deterministic on any machine that can read the config.
    """
    declared = {o.name: o.pool for o in front_instance.SPEC.exec_options_with_default()}
    published = {o.name: o.pool
                 for o in front_instance.SPEC.published_options("Front").options}
    assert set(declared) == set(published)
    for name, pool in declared.items():
        if name != "default":
            assert published[name] == pool, name


def test_the_default_is_priced_from_the_roles_this_machine_will_run():
    assert front_instance.SPEC.published_options("Front").get("default").pool not in ("", "-")


def test_every_covered_role_is_a_role_front_has_configured():
    """`exec_roles` is what the pool is derived from, so a name that is not a
    role would silently price the menu from nothing."""
    config, _ = _real_config()
    for role in front_instance.SPEC.exec_roles:
        assert role in config["roles"], role


def test_the_covered_roles_are_the_three_the_sentence_names():
    # `COVERS` says "this entrance, the Front Desk and routine runs", and the
    # pool is derived from exactly those. A sentence and a pool about
    # different work is the failure this pairing exists to prevent.
    assert front_instance.SPEC.exec_roles == ("front", "character_talk", "routine_run")


def test_a_role_moved_in_the_overlay_moves_the_derived_default():
    """The failure the derivation exists for, on Front's own roles.

    The Front Desk voice has its own profile precisely so it can be moved
    without touching the ordinary entrance — and that is exactly when a
    hand-written `pool: anthropic` becomes a lie.
    """
    config, _ = _real_config()
    overlay = _tomllib.loads(
        'schema = "ag.agent-config.v2"\n[roles.character_talk]\nprofile = "agy"\n'
    )
    found = execpool.derive(
        None, front_instance.SPEC.exec_roles, config, overlay,
        front_instance.SPEC.profile_for,
    )
    assert execpool.JOIN in found.pool and "antigravity" in found.pool
    declared = front_instance.SPEC.exec_options_with_default()[0]
    lines = execpool.diagnose([declared], [found])
    assert len(lines) == 1 and "character_talk -> agy/agy (antigravity)" in lines[0]


def test_an_unavailable_harness_is_not_reported_as_a_wrong_declaration(monkeypatch):
    """Availability is a runtime fact. A CLI that is not installed makes that
    one option fail when it runs; it must not read as a broken contract, and
    it must not take an unrelated conversation down."""
    real = execpool.resolve_role

    def flaky(config, overlay, role, *, profile_override=None, check_available=True):
        if check_available:
            raise execpool.AgentConfigError("E_UNAVAILABLE", "nothing is installed")
        return real(config, overlay, role, profile_override=profile_override,
                    check_available=False)

    monkeypatch.setattr(execpool, "resolve_role", flaky)
    published = front_instance.SPEC.published_options("Front")
    assert published.get("default").pool not in ("", "-")
    assert front_instance.SPEC.pool_diagnostics() == ()
