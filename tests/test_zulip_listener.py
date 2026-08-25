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

import pytest
from agag import topics
from agag.topics import GuideError

from agag import intro as agents_md
from agag.selfnote import rootchat_note, Conversation

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

    def front_run(prompt, cwd, home):
        calls.append(("front", prompt, cwd, home))
        if run is not None:
            run(cwd)
        return answer

    monkeypatch.setattr(zulip_listener, "run_front", front_run)
    guides = tmp_path / "guides"
    (guides / "front").mkdir(parents=True)
    (guides / "front" / "guide.md").write_text("FRONT GUIDE")
    monkeypatch.setattr(zulip_listener, "GUIDES", guides)


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

    assert [call[0] for call in calls] == [
        "whoami", "reply", "history", "front",
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
        "You are 'Front' in the chatlog.\n\nFRONT GUIDE"
    )
    assert cwd == gen_dir(tmp_path, 1)
    assert (cwd / "chatlog.md").read_text() == f"[Developer] {REQUEST}\n"
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


def test_schedule_usage_lands_in_tools_before_the_run(monkeypatch, tmp_path):
    calls = []
    seen = {}
    wire(
        monkeypatch, tmp_path, calls,
        run=lambda cwd: seen.update(text=(cwd / "tools" / "schedule.md").read_text()),
    )
    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert "rtschedule add-decide" in seen["text"]


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

    def explode(prompt, cwd, home):
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


# --- the listener entry ------------------------------------------------------


def test_the_listener_is_the_skeleton_with_one_route_and_the_mention_route(monkeypatch):
    from agfront import listener

    handed = {}
    monkeypatch.setattr(
        listener, "listener_main",
        lambda spec, routes, **kw: handed.update(spec=spec, routes=routes, **kw),
    )
    listener.main()
    assert handed["spec"] is zulip_listener.SPEC
    assert handed["routes"] == {"front-": zulip_listener.handle_topic}
    assert handed["on_mention"] is zulip_listener.handle_mention
    # `front-` is the only prefix swept: Front never answers the topics it
    # opens elsewhere, by filter and not by luck.
    assert zulip_listener.SPEC.sweep_prefixes == ("front-",)
