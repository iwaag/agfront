"""agfront's part of serving a front topic: one run, then one dispatch.

The serving *discipline* — ack first, always answer, name the failed step,
re-serve when a human spoke during the run, the empty-topic guard, workspace
numbering, chatlog formatting — lives in `agag.topics` and is tested there.
What is pinned here is only what agfront decides: that the run's `dispatch.md`
becomes exactly one post in `#general`, that its absence is not a failure,
and that the reply says where the message went.

Same rule as the sibling suites: nothing asserts what an agent said.
"""

import pytest
from agag import topics
from agag.topics import GuideError

from agfront import zulip_listener

BOT_ID = 15
HUMAN_ID = 8
CHANNEL = "front"
TOPIC = "front-20260817-120000"


def message(sender_id=HUMAN_ID, name="Developer", content="please advance the work", id=1):
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

    def __init__(self, calls, history=None):
        self.calls = calls
        self.history = [message()] if history is None else history

    def whoami(self):
        self.calls.append(("whoami",))
        return {"user_id": BOT_ID, "full_name": "Front"}

    def topic_history(self, channel, topic, num_before):
        self.calls.append(("history", channel, topic, num_before))
        return self.history


def wire(monkeypatch, tmp_path, calls, *, answer="on it", dispatch=None):
    monkeypatch.setattr(zulip_listener, "TOPICS_ROOT", tmp_path / "topics")
    monkeypatch.setattr(zulip_listener, "RECORDS_ROOT", tmp_path / "records")
    # The reply into the front topic goes through the shared skeleton; the
    # dispatch is posted by agfront itself. Catching both separately is what
    # makes "one post into #general" checkable.
    monkeypatch.setattr(
        topics,
        "topic_write",
        lambda topic, text, **kwargs: calls.append(("reply", topic, text)) or "success",
    )
    monkeypatch.setattr(
        zulip_listener,
        "topic_write",
        lambda topic, text, **kwargs: (
            calls.append(("dispatch", kwargs.get("channel"), topic, text)) or "success"
        ),
    )

    def front_run(prompt, cwd):
        calls.append(("front", prompt, cwd))
        if dispatch is not None:
            (cwd / zulip_listener.DISPATCH_FILE).write_text(dispatch)
        return answer

    monkeypatch.setattr(zulip_listener, "run_front", front_run)
    guides = tmp_path / "guides"
    (guides / "front").mkdir(parents=True)
    (guides / "front" / "guide.md").write_text("FRONT GUIDE")
    monkeypatch.setattr(zulip_listener, "GUIDES", guides)


def gen_dir(tmp_path, number, role="front"):
    return tmp_path / "topics" / CHANNEL / TOPIC / str(number) / role


# --- (a) a request that dispatches -----------------------------------------


def test_a_dispatch_file_becomes_one_post_in_general(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, dispatch="run-20260817-1\n\nPlease advance the work.\n")

    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)

    assert [call[0] for call in calls] == [
        "whoami", "reply", "history", "front", "dispatch", "reply", "history",
    ]
    assert calls[4][1:] == (
        zulip_listener.DISPATCH_CHANNEL, "run-20260817-1", "Please advance the work.",
    )


def test_the_reply_names_the_topic_the_work_went_to(monkeypatch, tmp_path):
    """Nothing comes back to the front topic, so where it went is the only
    thing the Developer can follow."""
    calls = []
    wire(monkeypatch, tmp_path, calls, dispatch="run-20260817-1\n\nadvance it\n")
    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert calls[-2][2] == (
        "on it\n\ndispatched to #general > run-20260817-1; the reply will appear there"
    )


def test_the_chatlog_and_the_prompt_are_the_run_s_whole_input(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)
    prompt, cwd = next((call[1], call[2]) for call in calls if call[0] == "front")
    assert prompt == (
        "The chatlog is placed in the working directory. "
        "You are 'Front' in the chatlog.\n\nFRONT GUIDE"
    )
    assert cwd == gen_dir(tmp_path, 1)
    assert (cwd / "chatlog.md").read_text() == "[Developer] please advance the work\n"


# --- (b) a request that cannot be dispatched -------------------------------


def test_no_dispatch_file_means_no_post_and_no_failure(monkeypatch, tmp_path):
    """Front refusing, or merely answering, is the normal case."""
    calls = []
    wire(monkeypatch, tmp_path, calls, answer="I cannot do that.")

    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)

    assert not any(call[0] == "dispatch" for call in calls)
    assert calls[-2][2] == "I cannot do that."


# --- (c) a malformed command file ------------------------------------------


@pytest.mark.parametrize("body", ["", "\n\n", "run-20260817-1\n", "\nmessage only\n"])
def test_an_unpostable_dispatch_is_reported_not_posted(monkeypatch, tmp_path, body):
    calls = []
    wire(monkeypatch, tmp_path, calls, dispatch=body)

    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)

    assert not any(call[0] == "dispatch" for call in calls)
    assert calls[-1][2].startswith("failed during dispatch: dispatch.md ")


def test_parse_dispatch_keeps_the_message_as_written():
    topic, body = zulip_listener.parse_dispatch(
        "run-x\n\n# Advance\n\nTwo paragraphs.\n"
    )
    assert topic == "run-x"
    assert body == "# Advance\n\nTwo paragraphs."


# --- failures and generations ----------------------------------------------


def test_a_front_failure_names_its_step(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)

    def explode(prompt, cwd):
        raise zulip_listener.ListenerError("claude_code timed out")

    monkeypatch.setattr(zulip_listener, "run_front", explode)
    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert calls[-1][2] == "failed during front: claude_code timed out"


def test_generation_increments_and_the_old_dispatch_is_not_resent(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, dispatch="run-20260817-1\n\nadvance it\n")
    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)
    monkeypatch.setattr(zulip_listener, "run_front", lambda prompt, cwd: "nothing to do")

    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)

    assert (gen_dir(tmp_path, 1) / zulip_listener.DISPATCH_FILE).is_file()
    assert not (gen_dir(tmp_path, 2) / zulip_listener.DISPATCH_FILE).exists()
    assert len([call for call in calls if call[0] == "dispatch"]) == 1


def test_an_empty_topic_costs_no_agent_run(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    zulip_listener.handle_topic(Client(calls, history=[]), CHANNEL, TOPIC)
    assert not any(call[0] == "front" for call in calls)
    assert calls[-1][2] == zulip_listener.EMPTY_REPLY


def test_our_acks_are_dropped_from_the_chatlog(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    history = [
        message(),
        message(sender_id=BOT_ID, name="Front", content=zulip_listener.ACK_TEXT, id=2),
        message(sender_id=BOT_ID, name="Front", content="dispatched", id=3),
    ]
    zulip_listener.handle_topic(Client(calls, history=history), CHANNEL, TOPIC)
    assert (gen_dir(tmp_path, 1) / "chatlog.md").read_text() == (
        "[Developer] please advance the work\n[Front (you)] dispatched\n"
    )


def test_guide_refuses_to_start_without_the_file(monkeypatch, tmp_path):
    monkeypatch.setattr(zulip_listener, "GUIDES", tmp_path)
    with pytest.raises(GuideError):
        zulip_listener.guide("front", "guide.md")
