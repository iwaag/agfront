"""The conversation reaches the model, not just the disk.

`routine_tests` p2 ex1 step 2 (problem C). Front's first serving of a new
conversation could answer as if it were empty: twice out of the first two
requests of `routine_tests` p2 (`front` run-0594 and run-0597, `num_turns` 1,
**no tool calls**) the reply was *"I don't see a message or request from the
developer yet in this conversation"* — with the request sitting verbatim in
`chatlog.md`. The conversation was a file the run had to decide to open, and
a one-turn reply never opens a file. No wording removes the possibility of a
one-turn answer.

So these run the whole prompt path down to the **harness boundary** with a
`fake` harness whose script does exactly one thing: read its stdin. It opens
no file, so nothing it sees came from the workspace. What it sees is what the
model would have seen.

Against the old implementation every assertion here fails: the prompt was
`chatlog_placement` plus the guide, and the conversation appeared in neither.

Covered: all three Front roles (`front`, `character_talk`, `routine_run`), a
history too large to carry whole, and a genuinely empty conversation.
"""

import stat
from dataclasses import replace
from pathlib import Path

import pytest
from agag import topics

from agfront import zulip_listener
from agfront.instance import SPEC

BOT_ID = 15
HUMAN_ID = 8
REQUEST = "Please run the publish routine for studyuspolitics only."


def message(content=REQUEST, sender_id=HUMAN_ID, name="Developer", id=1, topic="front-1"):
    return {
        "id": id,
        "type": "stream",
        "sender_id": sender_id,
        "sender_full_name": name,
        "display_recipient": "front",
        "subject": topic,
        "content": content,
    }


class Client:
    """Only what one serving of `zulip_listener.serve` actually calls."""

    email = "front-bot@example.invalid"

    def __init__(self, history):
        self.history = history

    def whoami(self):
        return {"user_id": BOT_ID, "full_name": "Front"}

    def stream_id(self, name):
        return 30

    def channel_topics(self, stream_id):
        return ["intro-agforge-agstudio1"]

    def own_rootchat_notes(self, num_before=200):
        return []

    def own_moved_notes(self, num_before=200):
        """`sender:me search:rootchat-moved` — the anchors this bot
        deliberately corrected. None, in these fixtures unless one says so."""
        return list(getattr(self, "moved_notes", []))

    def topic_history(self, channel, topic, num_before):
        if channel == "agents":
            return [message(content="# agforge\n\nOpen an `assetplan-…` topic.",
                            sender_id=13, name="Forge", id=99)]
        return self.history

    def send_to_channel(self, channel, topic, content):
        return 900


def stub_agent(tmp_path) -> Path:
    """A `fake` harness that reads its stdin and reads nothing else.

    No `cat chatlog.md`, no `ls`: if the conversation is in `prompt.seen` it
    was carried in the prompt. The run's own answer is one fixed line, so
    nothing downstream depends on what a model would have said.
    """
    script = tmp_path / "stub-agent.sh"
    script.write_text("#!/bin/sh\ncat > prompt.seen\necho 'noted'\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "agents.toml").write_text(
        'schema = "ag.agent-config.v2"\n'
        'project = "agfront"\n'
        '[models."ollama/test-model"]\n'
        "[profiles.stub]\n"
        'harness = "fake"\n'
        'model = "ollama/test-model"\n'
        + "".join(
            f"[roles.{role}]\nprofile = \"stub\"\nrequires = []\nallowed_tools = \"Read\"\n"
            for role in ("front", "character_talk", "routine_run")
        )
    )
    (tmp_path / ".local").mkdir(exist_ok=True)
    (tmp_path / ".local" / "agents.local.toml").write_text(
        'schema = "ag.agent-config.v1"\n'
        "[local.harness.fake]\n"
        f'command = "{script}"\n'
    )
    return script


def wire(monkeypatch, tmp_path):
    stub_agent(tmp_path)
    spec = replace(SPEC, root=tmp_path)
    monkeypatch.setattr(zulip_listener, "SPEC", spec)
    monkeypatch.setattr(zulip_listener, "TOPICS_ROOT", tmp_path / "topics")
    monkeypatch.setattr(zulip_listener, "RECORDS_ROOT", tmp_path / "records")
    # The real guides of this repository, so the prompt under test is the
    # prompt that ships.
    monkeypatch.setattr(zulip_listener, "GUIDES", SPEC.guides)
    # The budget observation is a read of the relay on loopback; a test is not
    # the place to find out whether it is up.
    monkeypatch.setattr(zulip_listener, "write_budget_doc", lambda directory: None)


class Context:
    """`agag.topics.TopicContext` as one serving needs it."""

    def __init__(self, client, channel, topic, history):
        self.client = client
        self.channel = channel
        self.topic = topic
        self.history = history
        self.self_id = BOT_ID
        self.bot_name = "Front"
        self.step = ""
        self.selection = None


def serve_and_capture(monkeypatch, tmp_path, channel, topic, history) -> str:
    """One real serving; the bytes the harness read on stdin."""
    wire(monkeypatch, tmp_path)
    client = Client(history)
    zulip_listener.serve(Context(client, channel, topic, history))
    seen = list((tmp_path / "topics").rglob("prompt.seen"))
    assert len(seen) == 1, seen
    return seen[0].read_text(encoding="utf-8")


# --- the three roles --------------------------------------------------------


@pytest.mark.parametrize(
    "channel,topic,role",
    [
        ("front", "front-20260913-090000", "front"),
        ("front", "front-desk-20260913-090000", "character_talk"),
        ("routine-publish", "routinerun-20260913T0900Z", "routine_run"),
    ],
)
def test_the_request_and_its_sender_reach_the_model(monkeypatch, tmp_path, channel, topic, role):
    """Whichever role serves it, the new conversation's request and who made
    it are in the prompt — not only in a file."""
    assert zulip_listener.role_for(channel, topic) == role
    history = [message(topic=topic)]
    prompt = serve_and_capture(monkeypatch, tmp_path, channel, topic, history)

    assert REQUEST in prompt
    assert "Developer" in prompt
    # …and it is fenced off from the role's guide, which follows it.
    assert topics.CONVERSATION_BEGIN in prompt
    assert prompt.index(topics.CONVERSATION_BEGIN) < prompt.index(REQUEST)
    assert prompt.index(REQUEST) < prompt.index(topics.CONVERSATION_END)


def test_the_prompt_copy_and_the_file_are_one_snapshot(monkeypatch, tmp_path):
    """Rendered once. Two copies of a conversation that could disagree is
    worse than one, and the file is what the prompt tells the run to read."""
    topic = "front-20260913-090000"
    prompt = serve_and_capture(monkeypatch, tmp_path, "front", topic, [message(topic=topic)])
    chatlog = next((tmp_path / "topics").rglob("chatlog.md")).read_text(encoding="utf-8")
    assert chatlog.strip("\n") in prompt
    assert '"chatlog.md"' in prompt


def test_selfnotes_and_acks_are_absent_from_the_prompt_as_from_the_file(monkeypatch, tmp_path):
    """The carried bytes are the rendered bytes, so every filter the renderer
    already applies applies here too — with no second read to disagree."""
    topic = "front-20260913-090000"
    history = [
        message(content="[selfnote][rootchat] front/front-1", id=1, topic=topic),
        message(content="Message received. Please wait for the reply.",
                sender_id=BOT_ID, name="Front", id=2, topic=topic),
        message(id=3, topic=topic),
    ]
    prompt = serve_and_capture(monkeypatch, tmp_path, "front", topic, history)
    assert REQUEST in prompt
    assert "selfnote" not in prompt
    assert "Please wait for the reply." not in prompt


# --- the bounded and the empty ---------------------------------------------


def test_a_large_history_is_bounded_visibly_and_keeps_the_newest_request(monkeypatch, tmp_path):
    """A conversation past the budget is not dropped and not silently cut:
    the newest message and its sender are carried, and the prompt says how
    much was left out and which file holds it."""
    topic = "front-20260913-090000"
    filler = [
        message(content=f"older note {n} " + "x" * 400, id=n + 10, topic=topic)
        for n in range(200)
    ]
    history = filler + [message(content=REQUEST, id=999, topic=topic)]
    prompt = serve_and_capture(monkeypatch, tmp_path, "front", topic, history)

    assert REQUEST in prompt and "Developer" in prompt
    assert "are not carried here" in prompt
    assert '"chatlog.md"' in prompt
    assert "older note 3 " not in prompt
    # The file is still the complete copy the prompt points at.
    chatlog = next((tmp_path / "topics").rglob("chatlog.md")).read_text(encoding="utf-8")
    assert "older note 3 " in chatlog


def test_one_oversized_message_is_cut_where_the_run_can_see_it(monkeypatch, tmp_path):
    """Zulip accepts about 10 000 characters in one post and a chain of
    quoted evidence reaches that. The cut is stated in the prompt — an
    invisible truncation is a run confidently answering half a request."""
    topic = "front-20260913-090000"
    huge = REQUEST + " " + "and then " * 6000
    prompt = serve_and_capture(
        monkeypatch, tmp_path, "front", topic, [message(content=huge, id=1, topic=topic)]
    )
    assert REQUEST in prompt
    assert "this message is cut off here" in prompt
    assert len(prompt) < len(huge)


def test_a_genuinely_empty_conversation_says_it_is_empty(monkeypatch, tmp_path):
    """A topic holding nothing but Front's own notes renders to nothing, and
    the prompt says so in words. That is a real state and it is a different
    answer from "the conversation was not delivered" — which is exactly the
    sentence a run used to produce when it had simply never opened the file.

    In production the `front-*` route answers such a topic before the run
    (`EMPTY_REPLY`); this calls the serving directly to pin what the prompt
    would carry."""
    topic = "front-20260913-090000"
    history = [message(content="[selfnote][rootchat] front/front-1", id=1, topic=topic)]
    prompt = serve_and_capture(monkeypatch, tmp_path, "front", topic, history)
    assert "this conversation is empty" in prompt
    assert "selfnote" not in prompt


def test_an_empty_run_topic_is_carried_as_the_evidence_header(monkeypatch, tmp_path):
    """A run serving renders with `agfront.evidence`, which writes a header
    about the conversation even when it holds no posts. So an empty run
    carries "0 posts" rather than the plain-empty sentence — and either way
    the run is told what is there, not left to guess from silence."""
    topic = "routinerun-20260913T0900Z"
    history = [message(content="[selfnote][rootchat] front/front-1", id=1, topic=topic)]
    prompt = serve_and_capture(monkeypatch, tmp_path, "routine-publish", topic, history)
    assert "0 posts" in prompt
    assert f"#routine-publish \u203a {topic}" in prompt
    assert "selfnote" not in prompt
