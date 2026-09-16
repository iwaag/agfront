"""Front as the owner of an argue (`argue` p1 step 1).

Pinned: an argue serving runs the `argue` role over the conversation with
its message ids and the board; the reply carries **no** hand-off mention;
a desire named by the run is recorded only when it is a human's own post
in this conversation, and a refused designation is said out loud so the
next serving asks again; a hand-opened argue gets its anchor note; an
`argue-` topic outside `#argue` is left alone. Nothing here asserts what
the run said.
"""

from __future__ import annotations

import pytest
from agag import topics
from agag import intro as agents_md
from agag.argue import argue_note, desire_note
from agag.selfnote import Conversation

from agfront import argue as argue_module
from agfront import zulip_listener

BOT_ID = 15
HUMAN_ID = 8
OTHER_BOT = 14
CHANNEL = "argue"
TOPIC = "argue-aquarium"


def message(*, sender_id=HUMAN_ID, name="Developer", content="I want something grand", id=100, is_bot=False):
    return {"id": id, "sender_id": sender_id, "sender_full_name": name, "content": content,
            "timestamp": 1758000000, "subject": TOPIC, "display_recipient": CHANNEL}


class Client:
    email = "front-bot@example.invalid"

    def __init__(self, calls, history):
        self.calls = calls
        self.history = list(history)

    def whoami(self):
        return {"user_id": BOT_ID, "full_name": "Front"}

    def stream_id(self, name):
        return 30

    def channel_topics(self, stream_id):
        return ["intro-cagent"]

    def topic_history(self, channel, topic, num_before):
        if channel == agents_md.AGENTS_CHANNEL:
            return [message(sender_id=OTHER_BOT, name="cagent", content="# cagent\n\nI explain the cluster.", id=1)]
        self.calls.append(("history", channel, topic))
        return list(self.history)

    def users(self):
        return [{"user_id": HUMAN_ID, "is_bot": False}, {"user_id": BOT_ID, "is_bot": True},
                {"user_id": OTHER_BOT, "is_bot": True}]

    def send_to_channel(self, channel, topic, content):
        self.calls.append(("post", channel, topic, content))
        self.history.append(message(sender_id=BOT_ID, name="Front", content=content, id=900 + len(self.calls)))
        return 900 + len(self.calls)


def wire(monkeypatch, tmp_path, calls, *, answer="on it"):
    monkeypatch.setattr(zulip_listener, "TOPICS_ROOT", tmp_path / "topics")
    monkeypatch.setattr(zulip_listener, "RECORDS_ROOT", tmp_path / "records")
    monkeypatch.setattr(
        topics, "topic_write",
        lambda topic, text, **kwargs: (calls.append(("reply", kwargs.get("channel"), topic, text)) or "success"),
    )

    def front_run(prompt, cwd, home, role="front", *, extra_meta=None, selection=None):
        calls.append(("run", prompt, cwd, home, role, extra_meta))
        return answer

    monkeypatch.setattr(zulip_listener, "run_front", front_run)
    guides = tmp_path / "guides"
    (guides / "argue").mkdir(parents=True)
    (guides / "argue" / "guide.md").write_text("ARGUE GUIDE")
    monkeypatch.setattr(zulip_listener, "GUIDES", guides)


def replies(calls):
    return [c for c in calls if c[0] == "reply"]


def posts(calls):
    return [c for c in calls if c[0] == "post"]


def runs(calls):
    return [c for c in calls if c[0] == "run"]


def test_an_argue_serving_runs_the_argue_role_without_a_handoff_mention(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, answer="Tell me more about what you imagine.")
    history = [message(sender_id=BOT_ID, name="Front", content=argue_note(Conversation("front", "front-a")), id=90),
               message(sender_id=BOT_ID, name="Front", content="Welcome. What do you want?", id=91),
               message(content="Something with aquariums and 3D printing.", id=92)]
    argue_module.handle_argue(Client(calls, history), CHANNEL, TOPIC)
    (run,) = runs(calls)
    _, prompt, cwd, home, role, meta = run
    assert role == "argue" and home == (CHANNEL, TOPIC) and meta == {"argue": 90}
    assert "This is argue 90, opened from front/front-a." in prompt
    assert "has not been recorded yet" in prompt
    assert "ARGUE GUIDE" in prompt
    assert "[Developer #92]" in prompt  # message ids reach the run
    assert (cwd / "tools" / "agents.md").is_file() and cwd.name == "argue"
    # The ack, then the reply exactly as written: no `@**Developer**` in front of it.
    assert [r[3] for r in replies(calls)] == [zulip_listener.ACK_TEXT, "Tell me more about what you imagine."]
    assert posts(calls) == []


def test_a_desire_named_by_the_run_is_recorded_when_it_is_a_human_post(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, answer="Got it.\n\n```ag-argue\ndesire: 92\n```\n")
    history = [message(sender_id=BOT_ID, name="Front", content=argue_note(None), id=90),
               message(content="I want a self-running aquarium factory.", id=92)]
    argue_module.handle_argue(Client(calls, history), CHANNEL, TOPIC)
    assert posts(calls) == [("post", CHANNEL, TOPIC, desire_note(92, HUMAN_ID))]
    reply = replies(calls)[-1][3]
    assert reply.startswith("Got it.") and "on record as message 92" in reply and "```" not in reply


@pytest.mark.parametrize("named, why", [
    (91, "your own post"),      # Front's draft
    (93, "by a bot"),           # another agent adopting the draft
    (555, "not in this conversation"),
])
def test_a_designation_that_is_not_a_human_post_is_refused_out_loud(monkeypatch, tmp_path, named, why):
    calls = []
    wire(monkeypatch, tmp_path, calls, answer=f"Recorded.\n```ag-argue\ndesire: {named}\n```")
    history = [message(sender_id=BOT_ID, name="Front", content=argue_note(None), id=90),
               message(sender_id=BOT_ID, name="Front", content="Draft: an aquarium factory?", id=91),
               message(content="hmm", id=92),
               message(sender_id=OTHER_BOT, name="cagent", content="I adopt the draft", id=93)]
    argue_module.handle_argue(Client(calls, history), CHANNEL, TOPIC)
    assert posts(calls) == []
    reply = replies(calls)[-1][3]
    assert "not recorded as the desire" in reply and why in reply


def test_a_recorded_desire_is_not_recorded_twice(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, answer="Again.\n```ag-argue\ndesire: 94\n```")
    history = [message(sender_id=BOT_ID, name="Front", content=argue_note(None), id=90),
               message(content="the desire", id=92),
               message(sender_id=BOT_ID, name="Front", content=desire_note(92, HUMAN_ID), id=93),
               message(content="and more", id=94)]
    argue_module.handle_argue(Client(calls, history), CHANNEL, TOPIC)
    assert posts(calls) == []
    assert "already on record as message 92" in replies(calls)[-1][3]
    assert "desire on record is message 92" in runs(calls)[0][1]


def test_a_hand_opened_argue_is_anchored_by_its_first_serving(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    argue_module.handle_argue(Client(calls, [message(content="hello, I have an idea", id=92)]), CHANNEL, TOPIC)
    assert posts(calls) == [("post", CHANNEL, TOPIC, argue_note(None))]
    assert "no anchor note yet" in runs(calls)[0][1]


def test_an_argue_prefix_outside_the_argue_channel_is_left_alone(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    argue_module.handle_argue(Client(calls, [message(id=92)]), "general", TOPIC)
    assert calls == []
