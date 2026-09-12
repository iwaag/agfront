"""Correcting a mis-anchored delegation, on purpose, and what it moves.

`routine_tests` p2 ex1 step 4 (problem B2). p2's `publish` run delegated from
the serving that opened it, so the delegation topic's root note named the
Front **Desk** (message 6482). The repair attempted by hand — a second
ordinary root note naming the run, message 6500 — changed nothing, and was
right not to: `own_rootchat` takes the earliest, because a repeat must not be
able to redirect a live conversation. p1 wanted the same capability four
times for a different cause.

`[selfnote][rootchat-moved] <channel>/<topic>` is how the correction is said
deliberately, `agentchat anchor` is how an agent writes it, and one
effective-anchor rule reads it everywhere: the newest valid move written by
this agent wins, otherwise its earliest ordinary root note.

These pin the sequence end to end, through Front's own mention route.
"""

import pytest
from agag import intro as agents_md
from agag.zulip import RESOLVED_TOPIC_PREFIX

from agfront import zulip_listener

from test_routine_run import wire_runs  # noqa: E402
from test_zulip_listener import (  # noqa: E402 - the sibling suite's fixtures
    BOT_ID,
    Client,
    message,
)


def wire(monkeypatch, tmp_path, calls, **kw):
    """The sibling suites' wiring, plus the run role's guide and a budget
    read that never leaves the machine: a correction can move a callback into
    a `routinerun-` topic, which is served by `routine_run`."""
    wire_runs(monkeypatch, tmp_path, calls, **kw)


AUTOLAB_ID = 11

DESK = ("front", "front-desk-20260912-1636")
RUN = ("routine-publish", "routinerun-20260912T1636Z")
DELEGATE = ("pj-studyuspolitics", "workplan-publish-studyuspolitics")

COMPLETION = "@**Front** the publication gate is applied and the task is done."


def post(id, sender_id, content, channel=DELEGATE[0], topic=DELEGATE[1], name="Autolab"):
    return {
        "id": id, "type": "stream", "sender_id": sender_id, "sender_full_name": name,
        "display_recipient": channel, "subject": topic, "content": content,
    }


class Realm(Client):
    """Several conversations, and both note narrows."""

    def __init__(self, calls, histories):
        super().__init__(calls)
        self.histories = dict(histories)

    def topic_history(self, channel, topic, num_before):
        if channel == agents_md.AGENTS_CHANNEL:
            return [message(sender_id=13, name="Forge", content=self.board[topic], id=99)]
        self.calls.append(("history", channel, topic, num_before))
        return list(self.histories.get((channel, topic), []))

    def channel_topics(self, stream_id):
        """Every topic name the realm holds, so `live_topic_name` can see a
        ✔ rename — plus the board, which the harvest reads."""
        return [topic for _, topic in self.histories] + list(self.board)

    def _notes(self, marker):
        found = []
        for (channel, topic), history in self.histories.items():
            for row in history:
                if row.get("sender_id") != BOT_ID:
                    continue
                if str(row.get("content", "")).startswith(marker):
                    found.append({**row, "type": "stream",
                                  "display_recipient": channel, "subject": topic})
        return found

    def own_rootchat_notes(self, num_before=200):
        return self._notes("[selfnote][rootchat] ")

    def own_moved_notes(self, num_before=200):
        return self._notes("[selfnote][rootchat-moved] ")


def realm(calls, *, correction=None, correction_sender=BOT_ID, repeat=True,
          run_topic=RUN[1], delegate_extra=()):
    """The p2 sequence: Desk anchor, ordinary run repeat, then whatever the
    test adds."""
    delegate = [post(6482, BOT_ID, f"[selfnote][rootchat] {DESK[0]}/{DESK[1]}", name="Front"),
                post(6484, AUTOLAB_ID, "Plan registered; starting task 1.")]
    if repeat:
        delegate.append(
            post(6500, BOT_ID, f"[selfnote][rootchat] {RUN[0]}/{run_topic}", name="Front")
        )
    if correction is not None:
        delegate.append(post(6520, correction_sender, correction, name="Front"))
    delegate.extend(delegate_extra)
    delegate.append(post(6560, AUTOLAB_ID, COMPLETION))
    return Realm(calls, {
        DESK: [message()],
        (RUN[0], run_topic): [post(6470, BOT_ID, "Opened this run.",
                                   channel=RUN[0], topic=run_topic, name="Front")],
        DELEGATE: delegate,
    })


def moved(home=f"{RUN[0]}/{RUN[1]}"):
    return f"[selfnote][rootchat-moved] {home}"


# --- the sequence -----------------------------------------------------------


def test_without_a_correction_the_completion_still_serves_the_desk(monkeypatch, tmp_path):
    """p2's outcome, unchanged and correct: an ordinary repeat loses."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    zulip_listener.handle_mention(realm(calls), *DELEGATE)
    assert next(c[3] for c in calls if c[0] == "front") == DESK


def test_an_explicit_correction_moves_the_callback_to_the_run(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, answer="Recorded: task 1 done.")
    zulip_listener.handle_mention(realm(calls, correction=moved()), *DELEGATE)
    assert next(c[3] for c in calls if c[0] == "front") == RUN
    # …and it is served as a run, by the run's own role.
    assert next(c[4] for c in calls if c[0] == "front") == zulip_listener.ROUTINE_ROLE
    assert {c[1] for c in calls if c[0] == "reply"} == {RUN[0]}


def test_a_later_ordinary_repeat_does_not_undo_the_correction(monkeypatch, tmp_path):
    """The whole sequence the plan names: Desk anchor, ordinary run repeat,
    explicit correction, another ordinary repeat. Only the correction moved
    anything."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = realm(
        calls, correction=moved(),
        delegate_extra=[post(6540, BOT_ID, "[selfnote][rootchat] front/front-elsewhere",
                             name="Front")],
    )
    zulip_listener.handle_mention(client, *DELEGATE)
    assert next(c[3] for c in calls if c[0] == "front") == RUN


def test_a_second_correction_wins_over_the_first(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = realm(
        calls, correction=moved(),
        delegate_extra=[post(6545, BOT_ID, moved(f"{DESK[0]}/{DESK[1]}"), name="Front")],
    )
    zulip_listener.handle_mention(client, *DELEGATE)
    assert next(c[3] for c in calls if c[0] == "front") == DESK


def test_a_correction_written_by_another_agent_moves_nothing(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = realm(calls, correction=moved(), correction_sender=AUTOLAB_ID)
    zulip_listener.handle_mention(client, *DELEGATE)
    assert next(c[3] for c in calls if c[0] == "front") == DESK


@pytest.mark.parametrize("bad", [
    "[selfnote][rootchat-moved]",
    "[selfnote][rootchat-moved] no-slash",
])
def test_a_malformed_correction_moves_nothing(monkeypatch, tmp_path, bad):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    zulip_listener.handle_mention(realm(calls, correction=bad), *DELEGATE)
    assert next(c[3] for c in calls if c[0] == "front") == DESK


# --- what else moves with it ------------------------------------------------


def test_the_corrected_delegate_becomes_a_thread_of_the_run_not_the_desk(monkeypatch, tmp_path):
    """Thread ownership follows the effective anchor, so the run reads the
    delegation it owns and the Desk stops being shown somebody else's work."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = realm(calls, correction=moved())

    zulip_listener.handle_mention(client, *DELEGATE)
    cwd = next(c[2] for c in calls if c[0] == "front")
    assert (cwd / "threads" / DELEGATE[0] / f"{DELEGATE[1]}.md").is_file()
    assert COMPLETION.replace("@**Front** ", "") in (
        cwd / "threads" / DELEGATE[0] / f"{DELEGATE[1]}.md"
    ).read_text()

    # Serving the Desk itself now places no thread for that delegation.
    calls2 = []
    wire(monkeypatch, tmp_path / "desk", calls2)
    client2 = realm(calls2, correction=moved())
    zulip_listener.handle_topic(client2, *DESK)
    desk_cwd = next(c[2] for c in calls2 if c[0] == "front")
    assert not (desk_cwd / "threads").exists()


def test_the_mark_that_stops_a_restart_replaying_goes_to_the_new_home(monkeypatch, tmp_path):
    """A corrected callback answered once must not be answered again after a
    listener restart — and the served note lives in home, which is now the
    run."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    zulip_listener.handle_mention(realm(calls, correction=moved()), *DELEGATE)
    assert [c for c in calls if c[0] == "post"] == [
        ("post", RUN[0], RUN[1],
         f"[selfnote][served] {DELEGATE[0]}/{DELEGATE[1]} 6560"),
    ]


def test_the_correction_is_hidden_from_the_conversation(monkeypatch, tmp_path):
    """A selfnote: it is not in any chatlog or thread file, its author's
    included, and it is not somebody speaking."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    zulip_listener.handle_mention(realm(calls, correction=moved()), *DELEGATE)
    cwd = next(c[2] for c in calls if c[0] == "front")
    thread = (cwd / "threads" / DELEGATE[0] / f"{DELEGATE[1]}.md").read_text()
    assert "rootchat-moved" not in thread and "selfnote" not in thread


# --- and what it must not break --------------------------------------------


def test_a_corrected_callback_to_a_finished_run_is_not_reopened(monkeypatch, tmp_path):
    """Resolved-home handling is preserved: the run ended, so nothing serves
    it and no bare-name twin is opened. The origin is told instead."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    resolved = f"{RESOLVED_TOPIC_PREFIX}{RUN[1]}"
    client = realm(calls, correction=moved())
    # The run finished and resolving renamed it; the callback arrived after.
    client.histories[(RUN[0], resolved)] = [
        post(6470, BOT_ID, f"[selfnote][rootchat] {DESK[0]}/{DESK[1]}",
             channel=RUN[0], topic=resolved, name="Front"),
        post(6552, BOT_ID, "Run ended.", channel=RUN[0], topic=resolved, name="Front"),
    ]
    del client.histories[(RUN[0], RUN[1])]

    zulip_listener.handle_mention(client, *DELEGATE)

    assert [c for c in calls if c[0] == "front"] == []
    posted = [c for c in calls if c[0] == "post"]
    # Nothing was written under the run's bare name — that would be a twin.
    assert all(c[2] != RUN[1] for c in posted)
    # The conversation that asked for the run is told, and given the handoff.
    assert [(c[1], c[2]) for c in posted][:2] == [DESK, DESK]
    assert "[selfnote][delivered]" in posted[1][3]


def test_a_replacement_plus_a_correction_resolve_together(monkeypatch, tmp_path):
    """A's hop and B2's rule are one lookup. The retired conversation holds
    both Front's original anchor and Front's correction of it, and the
    correction is what the live replacement inherits."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    live = ("pj-studyuspolitics", "workplan-contributions")
    retired = ("pj-studyuspolitics",
               f"{RESOLVED_TOPIC_PREFIX}retired-workplan-contributions-m6371")

    class WithIds(Realm):
        messages_by_id: dict = {}

        def message(self, message_id):
            self.calls.append(("message", int(message_id)))
            return self.messages_by_id.get(int(message_id))

    retired_history = [
        post(6371, AUTOLAB_ID, "[selfnote][mission] x", channel=retired[0], topic=retired[1]),
        post(6367, BOT_ID, f"[selfnote][rootchat] {DESK[0]}/{DESK[1]}",
             channel=retired[0], topic=retired[1], name="Front"),
        post(6369, BOT_ID, moved(), channel=retired[0], topic=retired[1], name="Front"),
    ]
    client = WithIds(calls, {
        DESK: [message()],
        RUN: [post(6470, BOT_ID, "Opened this run.", channel=RUN[0], topic=RUN[1],
                   name="Front")],
        live: [
            post(6401, AUTOLAB_ID, "[selfnote][replaces] 6371", channel=live[0], topic=live[1]),
            post(6450, AUTOLAB_ID, COMPLETION, channel=live[0], topic=live[1]),
        ],
        retired: retired_history,
    })
    client.messages_by_id = {6371: retired_history[0]}

    zulip_listener.handle_mention(client, *live)

    assert next(c[3] for c in calls if c[0] == "front") == RUN
