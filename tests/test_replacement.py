"""A callback from a conversation that replaced the one Front was anchored in.

`routine_tests` p2 ex1 step 3 (problem A). Two agent identities, one
retirement, one replacement — the realm p2 actually produced:

- Front opened a run and delegated it into `#pj-studyuspolitics` ›
  `workplan-collect-and-analyze-contributions`, anchoring that topic with its
  own root note, message **6367**;
- the plan was scrapped and autolab retired mission m6371.
  `retire_conversation` renames the **whole topic**, and a rename moves every
  message in it — Front's 6367 went into
  `✔ retired-workplan-collect-and-analyze-contributions-m6371`;
- the replacement took the freed display name and wrote
  `[selfnote][replaces] 6371` into itself;
- autolab then named Front, correctly, and Front answered:

      mention in 'pj-studyuspolitics'/'workplan-collect-and-analyze-contributions'
      carries no root note of ours; ignoring

Neither agent was wrong. Autolab cannot copy Front's note — a root note is
identified by its sender, so a note autolab writes is autolab's. The reader
side is what had to change, and it changed by reading a relation that was
already being written.

These drive the whole mention route: the lookup, the serving it buys, the
thread the run reads the answer in, and the mark that stops a restart
replaying it.
"""

import pytest
from agag import intro as agents_md
from agag import topics

from agfront import zulip_listener

from test_zulip_listener import (  # noqa: E402  - the sibling suite's fixtures
    BOT_ID,
    CHANNEL,
    Client,
    TOPIC,
    message,
    replies,
    wire,
)

AUTOLAB_ID = 11
OTHER_BOT_ID = 20

PROJECT = "pj-studyuspolitics"
LIVE_TOPIC = "workplan-collect-and-analyze-contributions"
RETIRED_TOPIC = "✔ retired-collect-and-analyze-contributions-m6371"

#: The anchor of the retired mission, named by the replacement's relation.
RETIRED_ANCHOR = 6371
#: Front's own root note, which the rename carried into the retired topic.
FRONT_ANCHOR = 6367

CALLBACK = "@**Front** the replanned publication is ready; may I start task 1?"


def post(id, sender_id, content, channel=PROJECT, topic=LIVE_TOPIC, name="Autolab"):
    return {
        "id": id,
        "type": "stream",
        "sender_id": sender_id,
        "sender_full_name": name,
        "display_recipient": channel,
        "subject": topic,
        "content": content,
    }


class Realm(Client):
    """Several conversations, plus `GET messages/<id>` — the one read that
    survives a rename, and the only way the retired conversation is found."""

    def __init__(self, calls, histories, messages_by_id=None):
        super().__init__(calls)
        self.histories = dict(histories)
        self.messages_by_id = dict(messages_by_id or {})
        self.message_calls = []

    def topic_history(self, channel, topic, num_before):
        if channel == agents_md.AGENTS_CHANNEL:
            return [message(sender_id=13, name="Forge", content=self.board[topic], id=99)]
        self.calls.append(("history", channel, topic, num_before))
        return list(self.histories.get((channel, topic), []))

    def own_rootchat_notes(self, num_before=200):
        """`sender:me search:rootchat`. The replacement carries no note of
        Front's, so this narrow cannot find it — which is exactly why the
        thread it holds has to be supplied by the mention route."""
        found = []
        for (channel, topic), history in self.histories.items():
            for row in history:
                if row.get("sender_id") != BOT_ID:
                    continue
                if str(row.get("content", "")).startswith("[selfnote][rootchat]"):
                    found.append({**row, "type": "stream",
                                  "display_recipient": channel, "subject": topic})
        return found

    def own_moved_notes(self, num_before=200):
        """`sender:me search:rootchat-moved` — the anchors this bot
        deliberately corrected. None, in these fixtures unless one says so."""
        return list(getattr(self, "moved_notes", []))

    def message(self, message_id):
        self.calls.append(("message", int(message_id)))
        self.message_calls.append(int(message_id))
        return self.messages_by_id.get(int(message_id))


def realm(calls, *, anchor_sender=BOT_ID, relation=f"[selfnote][replaces] {RETIRED_ANCHOR}",
          target_exists=True, anchor_in_replacement=None, retired_topic=RETIRED_TOPIC,
          retired_channel=PROJECT):
    live = [
        post(6400, AUTOLAB_ID, "[selfnote][mission] studyuspolitics"),
        post(6401, AUTOLAB_ID, relation),
        post(6402, AUTOLAB_ID, "Replanned: the publication gate is now three checks."),
        post(6450, AUTOLAB_ID, CALLBACK),
    ]
    if anchor_in_replacement is not None:
        live.insert(3, post(6403, BOT_ID, anchor_in_replacement, name="Front"))
    retired = [
        post(RETIRED_ANCHOR, AUTOLAB_ID, "[selfnote][mission] studyuspolitics",
             channel=retired_channel, topic=retired_topic),
        post(FRONT_ANCHOR, anchor_sender, f"[selfnote][rootchat] {CHANNEL}/{TOPIC}",
             channel=retired_channel, topic=retired_topic, name="Front"),
        post(6370, AUTOLAB_ID, "This plan stalled and is retired.",
             channel=retired_channel, topic=retired_topic),
    ]
    histories = {
        (CHANNEL, TOPIC): [message()],
        (PROJECT, LIVE_TOPIC): live,
        (retired_channel, retired_topic): retired,
    }
    by_id = {}
    if target_exists:
        by_id[RETIRED_ANCHOR] = retired[0]
    return Realm(calls, histories, by_id)


# --- the repair -------------------------------------------------------------


def test_the_mention_serves_the_run_that_was_anchored_in_the_retired_plan(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, answer="Yes — start task 1.")
    client = realm(calls)

    zulip_listener.handle_mention(client, PROJECT, LIVE_TOPIC)

    # The serving is the original conversation's: its chatlog, its workspace,
    # and its reply. Nothing was said in the topic that called.
    _, cwd, home = next((c[1], c[2], c[3]) for c in calls if c[0] == "front")
    assert home == (CHANNEL, TOPIC)
    assert {c[1] for c in calls if c[0] == "reply"} == {CHANNEL}
    assert replies(calls)[-1] == "@**Developer**\n\nYes — start task 1."
    # The pointer was resolved by id, not by the name the replacement reused.
    assert client.message_calls == [RETIRED_ANCHOR]


def test_the_replacement_s_own_words_are_readable_in_that_serving(monkeypatch, tmp_path):
    """The answer is the whole reason the run is being served, and no root
    note of Front's names the conversation it is in — so `remotes_for_home`
    cannot supply it and the mention route does."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = realm(calls)

    zulip_listener.handle_mention(client, PROJECT, LIVE_TOPIC)

    cwd = next(c[2] for c in calls if c[0] == "front")
    thread = (cwd / "threads" / PROJECT / f"{LIVE_TOPIC}.md").read_text()
    assert "may I start task 1?" in thread
    assert "Replanned: the publication gate is now three checks." in thread
    assert "selfnote" not in thread
    # …and the prompt says the file is there.
    prompt = next(c[1] for c in calls if c[0] == "front")
    assert f'"threads/{PROJECT}/{LIVE_TOPIC}.md"' in prompt


def test_the_callback_is_marked_served_so_a_restart_does_not_replay_it(monkeypatch, tmp_path):
    """Front answers at home, so it never becomes the last poster where it
    was named; without the mark every restart would serve this again — and
    an inherited anchor makes that no less true."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = realm(calls)
    zulip_listener.handle_mention(client, PROJECT, LIVE_TOPIC)
    assert [c for c in calls if c[0] == "post"] == [
        ("post", CHANNEL, TOPIC, f"[selfnote][served] {PROJECT}/{LIVE_TOPIC} 6450"),
    ]


# --- and where it must not fire --------------------------------------------


def test_an_anchor_in_the_replacement_itself_wins(monkeypatch, tmp_path):
    """A conversation Front has already anchored is anchored. The inherited
    anchor is a fallback for a topic with no note of ours, never an override
    of one that has."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = realm(calls, anchor_in_replacement=f"[selfnote][rootchat] {CHANNEL}/front-newer")
    client.histories[(CHANNEL, "front-newer")] = [message()]
    zulip_listener.handle_mention(client, PROJECT, LIVE_TOPIC)
    home = next(c[3] for c in calls if c[0] == "front")
    assert home == (CHANNEL, "front-newer")
    assert client.message_calls == []


def test_a_root_note_belonging_to_another_agent_is_not_inherited(monkeypatch, tmp_path):
    """The retired conversation may hold several agents' anchors. Only this
    bot's own is this bot's business."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = realm(calls, anchor_sender=OTHER_BOT_ID)
    zulip_listener.handle_mention(client, PROJECT, LIVE_TOPIC)
    assert [c for c in calls if c[0] == "front"] == []
    assert [c for c in calls if c[0] == "post"] == []


def test_a_deleted_predecessor_is_absent_and_buys_no_run(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = realm(calls, target_exists=False)
    zulip_listener.handle_mention(client, PROJECT, LIVE_TOPIC)
    assert [c for c in calls if c[0] == "front"] == []


@pytest.mark.parametrize("relation", [
    "[selfnote][replaces] nothing-numeric",
    "[selfnote][replaces]",
    "[selfnote][mission] studyuspolitics",
])
def test_a_malformed_or_absent_relation_buys_no_run(monkeypatch, tmp_path, relation):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = realm(calls, relation=relation)
    zulip_listener.handle_mention(client, PROJECT, LIVE_TOPIC)
    assert [c for c in calls if c[0] == "front"] == []


def test_a_second_replacement_hop_is_not_followed(monkeypatch, tmp_path):
    """One hop, then stop. The relation is a fact about the conversation that
    wrote it; chaining it would make a bounded lookup into a walk whose
    length nobody declared."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    first = (PROJECT, "✔ retired-collect-and-analyze-contributions-m6200")
    client = realm(calls)
    # The retired conversation was itself a replacement, and Front's anchor
    # is one further back.
    client.histories[(PROJECT, RETIRED_TOPIC)] = [
        post(RETIRED_ANCHOR, AUTOLAB_ID, "[selfnote][mission] x", topic=RETIRED_TOPIC),
        post(6372, AUTOLAB_ID, "[selfnote][replaces] 6200", topic=RETIRED_TOPIC),
    ]
    client.histories[first] = [
        post(6200, AUTOLAB_ID, "[selfnote][mission] x", topic=first[1]),
        post(6100, BOT_ID, f"[selfnote][rootchat] {CHANNEL}/{TOPIC}",
             topic=first[1], name="Front"),
    ]
    client.messages_by_id[6200] = client.histories[first][0]

    zulip_listener.handle_mention(client, PROJECT, LIVE_TOPIC)

    assert [c for c in calls if c[0] == "front"] == []
    assert client.message_calls == [RETIRED_ANCHOR]


def test_a_predecessor_that_was_moved_to_another_channel_is_still_found(monkeypatch, tmp_path):
    """Which is the point of an id: a rename is not the only thing that moves
    a conversation out from under its remembered name."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = realm(calls, retired_channel="pj-archive", retired_topic="old-contributions")
    zulip_listener.handle_mention(client, PROJECT, LIVE_TOPIC)
    home = next(c[3] for c in calls if c[0] == "front")
    assert home == (CHANNEL, TOPIC)


def test_nothing_is_guessed_from_the_reused_display_name(monkeypatch, tmp_path):
    """The replacement holds the retired conversation's *name*. A lookup that
    fell back to a name would read the replacement itself — the one
    conversation the pointer certainly does not mean."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = realm(calls, target_exists=False)
    zulip_listener.handle_mention(client, PROJECT, LIVE_TOPIC)
    assert [c for c in calls if c[0] == "front"] == []
    # The live topic is not empty and does hold a conversation — it is simply
    # not the one the pointer named, and no run is bought by confusing them.
    assert client.histories[(PROJECT, LIVE_TOPIC)]
