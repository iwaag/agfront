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

    def own_rootchat_notes(self, num_before=200):
        return []

    def own_moved_notes(self, num_before=200):
        return []

    def resolve_topic(self, message_id, topic):
        self.calls.append(("resolve", message_id, topic))

    def send_to_channel(self, channel, topic, content):
        self.calls.append(("post", channel, topic, content))
        self.history.append(message(sender_id=BOT_ID, name="Front", content=content, id=900 + len(self.calls)))
        return 900 + len(self.calls)


def marked(answer: str) -> str:
    """A stub run's output under the reply contract (`agag.reply`): the
    answer inside an `ag-reply` mark, after a line of the run's own, unless
    the test wrote the marks itself."""
    if "```ag-reply" in answer:
        return answer
    return f"thinking about it first.\n\n```ag-reply\n{answer}\n```"


def wire(monkeypatch, tmp_path, calls, *, answer="on it"):
    monkeypatch.setattr(zulip_listener, "TOPICS_ROOT", tmp_path / "topics")
    monkeypatch.setattr(zulip_listener, "RECORDS_ROOT", tmp_path / "records")
    monkeypatch.setattr(
        topics, "deliver",
        lambda client, channel, topic, text, **kwargs: (calls.append(("reply", channel, topic, text)) or 900),
    )
    monkeypatch.setattr(
        topics, "topic_write",
        lambda topic, text, **kwargs: (calls.append(("reply", kwargs.get("channel"), topic, text)) or "success"),
    )

    def front_run(prompt, cwd, home, role="front", *, extra_meta=None, selection=None, journal=None):
        calls.append(("run", prompt, cwd, home, role, extra_meta))
        return marked(answer)

    monkeypatch.setattr(zulip_listener, "run_front", front_run)
    guides = tmp_path / "guides"
    (guides / "argue").mkdir(parents=True, exist_ok=True)
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
    # No character reaches a discussion run (`argue` p2): no file, no placement.
    assert not (cwd / "characters.md").exists() and "characters" not in prompt and "settings revision" not in prompt
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


# --- completion (step 3) --------------------------------------------------------------


class Realm(Client):
    """A client whose realm holds a project channel in a chosen state."""

    def __init__(self, calls, history, *, channels=(), topics=(), setup_answered=False, goal=True):
        super().__init__(calls, history)
        self.names = list(channels)
        self.topic_names = list(topics)
        self.setup_answered = setup_answered
        self.goal = goal

    def channels(self, include_archived=False):
        return [{"name": n, "stream_id": 400 + i} for i, n in enumerate(self.names)]

    def channel_topics(self, stream_id):
        return list(self.topic_names)

    def topic_last_id(self, channel, topic):
        return 1 if (topic == "goal" and self.goal) else 0

    def topic_history(self, channel, topic, num_before):
        if channel == agents_md.AGENTS_CHANNEL:
            return super().topic_history(channel, topic, num_before)
        if topic.startswith("workplan-setup-"):
            rows = [message(sender_id=BOT_ID, name="Front", content="please set up", id=10)]
            if self.setup_answered:
                rows.append(message(sender_id=OTHER_BOT, name="autolab", content="done: main/ exists", id=11))
            return rows
        return super().topic_history(channel, topic, num_before)


def argue_history():
    return [message(sender_id=BOT_ID, name="Front", content=argue_note(Conversation("front", "front-a")), id=90),
            message(content="I want a self-running aquarium factory.", id=92),
            message(sender_id=BOT_ID, name="Front", content=desire_note(92, HUMAN_ID), id=93),
            message(content="go ahead with the project", id=94)]


def outcome_answer(kind, target="pj-aquafactory"):
    return f"Outcome: …\n\n```ag-argue\noutcome: {kind}\ntarget: {target}\ncomplete: true\n```"


def test_a_project_completes_only_when_channel_goal_and_workspace_answer_exist(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, answer=outcome_answer("project"))
    client = Realm(calls, argue_history(), channels=("pj-aquafactory",), setup_answered=True)
    argue_module.handle_argue(client, CHANNEL, TOPIC)
    assert ("post", CHANNEL, TOPIC, "[selfnote][outcome] project pj-aquafactory") in posts(calls)
    origin = [p for p in posts(calls) if p[1] == "front"]
    assert origin and "has finished: project in #pj-aquafactory" in origin[0][3]
    assert any(c[0] == "resolve" for c in calls)  # resolved by serve_topic after the reply
    reply = replies(calls)[-1][3]
    assert "is complete (project in #pj-aquafactory)" in reply and "```" not in reply


def test_a_project_without_the_workspace_answer_is_not_complete(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, answer=outcome_answer("project"))
    client = Realm(calls, argue_history(), channels=("pj-aquafactory",), setup_answered=False)
    argue_module.handle_argue(client, CHANNEL, TOPIC)
    assert not any("[selfnote][outcome]" in p[3] for p in posts(calls))
    assert "not complete" in replies(calls)[-1][3] and "no answer yet" in replies(calls)[-1][3]


def test_a_missing_channel_or_document_is_named(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, answer=outcome_answer("plan", "pj-studyrealworld"))
    argue_module.handle_argue(Realm(calls, argue_history()), CHANNEL, TOPIC)
    assert "does not exist" in replies(calls)[-1][3]
    calls = []
    wire(monkeypatch, tmp_path, calls, answer=outcome_answer("plan", "pj-studyrealworld"))
    argue_module.handle_argue(Realm(calls, argue_history(), channels=("pj-studyrealworld",), topics=("guide",)), CHANNEL, TOPIC)
    assert "researchplan-" in replies(calls)[-1][3]


def test_a_plan_in_an_existing_study_completes_without_a_workspace_answer(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, answer=outcome_answer("plan", "pj-studyrealworld"))
    client = Realm(calls, argue_history(), channels=("pj-studyrealworld",), topics=("researchplan-closed-loop", "guide"))
    argue_module.handle_argue(client, CHANNEL, TOPIC)
    assert ("post", CHANNEL, TOPIC, "[selfnote][outcome] plan pj-studyrealworld") in posts(calls)


def test_a_new_study_needs_its_plan_topic_and_the_workspace_answer(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, answer=outcome_answer("study", "pj-aquaculture"))
    client = Realm(calls, argue_history(), channels=("pj-aquaculture",), topics=("researchplan-aquaculture",), setup_answered=True)
    argue_module.handle_argue(client, CHANNEL, TOPIC)
    assert ("post", CHANNEL, TOPIC, "[selfnote][outcome] study pj-aquaculture") in posts(calls)


def test_completion_needs_a_recorded_desire(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, answer=outcome_answer("project"))
    history = [message(sender_id=BOT_ID, name="Front", content=argue_note(None), id=90), message(content="hi", id=92)]
    argue_module.handle_argue(Realm(calls, history, channels=("pj-aquafactory",), setup_answered=True), CHANNEL, TOPIC)
    assert "no desire is on record" in replies(calls)[-1][3]
    assert not any("[selfnote][outcome]" in p[3] for p in posts(calls))


def test_a_callback_into_an_argue_home_is_served_by_the_argue_role(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, answer="autolab says the workspace exists.")
    client = Realm(calls, argue_history())
    monkeypatch.setattr(zulip_listener, "rootchat_home", lambda c, ch, t, s: Conversation(CHANNEL, TOPIC))
    monkeypatch.setattr(zulip_listener, "live_topic_name", lambda c, ch, t: t)
    monkeypatch.setattr(zulip_listener, "note_served", lambda *a, **k: 11)
    monkeypatch.setattr(zulip_listener, "start_opened_runs", lambda *a, **k: None)
    zulip_listener.handle_mention(client, "pj-aquafactory", "workplan-setup-aquafactory")
    (run,) = runs(calls)
    assert run[4] == "argue" and run[3] == (CHANNEL, TOPIC)
    assert "threads/pj-aquafactory/workplan-setup-aquafactory.md" in run[1]
    # Home is Front's own topic, so the ack is fine there; the reply carries no mention.
    assert [r[3] for r in replies(calls)] == [zulip_listener.ACK_TEXT, "autolab says the workspace exists."]


# --- the reply mark, at the source of the doubled turn (`explicit_reply` p1) ---


def test_7222_marked_posts_only_the_reply_and_the_rendering_sees_only_that(monkeypatch, tmp_path):
    """The advice's example: Front's #7222 was two paragraphs of thought and
    then the reply, in one post, and the Arguing Room voiced both. Under the
    contract the thought stays in the run record and the posted speech —
    what `present` re-voices — is the reply alone, with the argue's system
    notice after it."""
    from pathlib import Path as _Path

    from agfront.present import plain_content

    observed = (_Path(__file__).parent / "fixtures" / "reply" / "7222.md").read_text(encoding="utf-8")
    thought, _, said = observed.partition("Let me post a reply asking for that.\n\n")
    output = f"{thought}Let me post a reply asking for that.\n\n```ag-reply\n{said.strip()}\n```\n\n```ag-argue\ndesire: 92\n```"
    calls = []
    wire(monkeypatch, tmp_path, calls, answer=output)
    history = [message(sender_id=BOT_ID, name="Front", content=argue_note(Conversation("front", "front-a")), id=90),
               message(content="Is the world getting better or worse? I want the agents to find out.", id=92)]
    client = Client(calls, history)
    client.humans = {HUMAN_ID}
    argue_module.handle_argue(client, CHANNEL, TOPIC)
    posted = replies(calls)[-1][3]
    assert posted.startswith("Is the following a fair statement"), "the thought is not in the post"
    assert "before I draft anything" not in posted and "Let me post a reply" not in posted
    assert posted.endswith("— the desire is on record as message 92.")
    assert plain_content(posted) == posted, "nothing for the renderer to strip: the post is the speech"
