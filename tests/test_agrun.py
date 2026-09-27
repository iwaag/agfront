"""`agrun` and the run close-out (failsafe p5 step 4; `agrunfinish` since
progress_panel p1 step 5).

A run ended from anywhere gets the same end record, delivery, `[delivered]`
note and ✔ as a run ending itself, and the close-out is finished from the
records: an interruption between any two of the steps is completed by the
next attempt, and nothing is written twice. `continue` serves a run again
through the listener's own start note; `adopt` moves work opened beside a
run under it by Front's own root note.
"""

import io

import pytest

from agag.selfnote import effective_rootchat, owed_start, parse_start
from agag.trace import finish_record
from agag.zulip import RESOLVED_TOPIC_PREFIX, ZulipError

from agfront import agrun, routine
from agfront.routine import FinishReport, close_out, close_out_pending, parse_delivered, run_state

FRONT, DEV, AUTOLAB = 15, 8, 11
NAMES = {FRONT: "Front", DEV: "Developer", AUTOLAB: "autolab-agstudio1", 0: "Notification Bot"}
RUN = "routinerun-20260927T092403Z"
DESK = "front-desk-20260927-pp1-growbox"
RUN_CHANNEL = "routine-study-x"


class Realm:
    """Messages with live topic names; a client over it speaking as Front."""

    def __init__(self, opener=FRONT):
        self.messages = []
        self.next = 100
        self.origin = self.post("front", DESK, "please run it", DEV)
        self.post(RUN_CHANNEL, RUN, f"[selfnote][rootchat] front/{DESK} #{self.origin}", FRONT)
        self.opening = self.post(RUN_CHANNEL, RUN, "Please run the routine once.", opener)

    def post(self, channel, topic, content, sender):
        live = self.live(channel, topic)
        self.messages.append({"id": self.next, "display_recipient": channel, "subject": live, "sender_id": sender,
                              "sender_full_name": NAMES.get(sender, "x"), "content": content,
                              "timestamp": 1_790_500_000 + self.next, "type": "stream"})
        self.next += 1
        return self.next - 1

    def live(self, channel, topic):
        bare = topic[len(RESOLVED_TOPIC_PREFIX):] if topic.startswith(RESOLVED_TOPIC_PREFIX) else topic
        for m in self.messages:
            if m["display_recipient"] == channel and m["subject"] in (bare, f"{RESOLVED_TOPIC_PREFIX}{bare}"):
                return m["subject"]
        return bare

    def history(self, channel, topic):
        bare = topic[len(RESOLVED_TOPIC_PREFIX):] if topic.startswith(RESOLVED_TOPIC_PREFIX) else topic
        return [m for m in self.messages if m["display_recipient"] == channel
                and m["subject"] in (bare, f"{RESOLVED_TOPIC_PREFIX}{bare}")]


class Client:
    def __init__(self, realm, die_on=None):
        self.realm, self.die_on, self.sends = realm, die_on, 0

    def whoami(self, refresh=False):
        return {"user_id": FRONT, "full_name": "Front"}

    def message(self, message_id, strict=False):
        return next((dict(m) for m in self.realm.messages if m["id"] == int(message_id)), None)

    def topic_history(self, channel, topic, num_before=50):
        return [dict(m) for m in self.realm.messages
                if m["display_recipient"] == channel and m["subject"] == topic][-num_before:]

    def stream_id(self, channel):
        return channel

    def channel_topics(self, channel):
        return sorted({m["subject"] for m in self.realm.messages if m["display_recipient"] == channel})

    def send_to_channel(self, channel, topic, content):
        if self.die_on and self.die_on in content:
            self.die_on = None
            raise ZulipError("connection dropped")
        self.sends += 1
        return self.realm.post(channel, topic, content, FRONT)

    def resolve_topic(self, message_id, topic):
        if self.die_on == "resolve":
            self.die_on = None
            raise ZulipError("connection dropped")
        bare = topic[len(RESOLVED_TOPIC_PREFIX):] if topic.startswith(RESOLVED_TOPIC_PREFIX) else topic
        channel = next(m["display_recipient"] for m in self.realm.messages if m["id"] == message_id)
        for m in self.realm.messages:
            if m["display_recipient"] == channel and m["subject"] == bare:
                m["subject"] = f"{RESOLVED_TOPIC_PREFIX}{bare}"


@pytest.fixture
def realm(monkeypatch):
    found = Realm()
    reader = type("Reader", (), {
        "rootchats": lambda self, include_resolved=False: [((RUN_CHANNEL, RUN), None)],
    })()
    monkeypatch.setattr(routine, "reader_for", lambda client: reader)
    monkeypatch.setattr(routine, "opened_runs", lambda client, home: [routine.Conversation(RUN_CHANNEL, RUN)])
    return found


REPORT = FinishReport(achieved=True, reason="the mission was accepted and the sage refreshed", report="done: m1")


def desk(realm):
    return [m["content"] for m in realm.history("front", DESK)]


def records(realm):
    return [m for m in realm.history(RUN_CHANNEL, RUN) if finish_record(m["content"])]


def test_a_run_is_ended_with_its_record_its_delivery_and_its_tick(realm):
    out = io.StringIO()
    assert agrun.finish(Client(realm), RUN_CHANNEL, RUN, REPORT, out=out) == 0
    assert len(records(realm)) == 1 and finish_record(records(realm)[0]["content"])["achieved"] is True
    assert "Routine run finished" in desk(realm)[-2]
    assert parse_delivered(desk(realm)[-1]).topic == RUN
    assert realm.live(RUN_CHANNEL, RUN).startswith(RESOLVED_TOPIC_PREFIX)
    before = len(realm.messages)
    again = io.StringIO()
    assert agrun.finish(Client(realm), RUN_CHANNEL, RUN, REPORT, out=again) == 0
    assert len(realm.messages) == before and "already ended" in again.getvalue()


@pytest.mark.parametrize("die_on", ["Routine run finished", "[selfnote][delivered]", "resolve"])
def test_an_interrupted_close_out_is_finished_by_the_next_attempt_once(realm, die_on):
    """The plan's hypothesis (step 1): an end record without its delivery
    was never delivered on retry. Every interruption point now converges."""
    with pytest.raises(ZulipError):
        agrun.finish(Client(realm, die_on=die_on), RUN_CHANNEL, RUN, REPORT, out=io.StringIO())
    assert len(records(realm)) == 1
    assert agrun.finish(Client(realm), RUN_CHANNEL, RUN, REPORT, out=io.StringIO()) == 0
    assert sum("Routine run finished" in c for c in desk(realm)) == 1
    assert sum(c.startswith("[selfnote][delivered]") for c in desk(realm)) == 1
    assert len(records(realm)) == 1 and realm.live(RUN_CHANNEL, RUN).startswith(RESOLVED_TOPIC_PREFIX)
    assert run_state(Client(realm), RUN_CHANNEL, RUN, FRONT).complete


def test_the_listener_path_the_end_record_first_then_the_close_out(realm):
    """A run that ended itself: its serving posted the record (the reply);
    a restart before the delivery is completed by startup recovery."""
    client = Client(realm)
    realm.post(RUN_CHANNEL, RUN, routine.record_text("entry", REPORT, None), FRONT)
    assert not run_state(client, RUN_CHANNEL, RUN, FRONT).complete
    done = close_out_pending(client, FRONT, now=realm.messages[-1]["timestamp"] + 60)
    assert [s.run.topic for s in done] == [RUN] and done[0].complete
    assert close_out_pending(client, FRONT, now=realm.messages[-1]["timestamp"] + 60) == []
    # Older than the horizon: left as its own time closed it.
    other = Realm()
    other.post(RUN_CHANNEL, RUN, routine.record_text("entry", REPORT, None), FRONT)
    assert close_out_pending(Client(other), FRONT, now=other.messages[-1]["timestamp"] + 2 * 86400) == []


def test_somebody_elses_topic_and_a_non_run_are_refused(monkeypatch):
    realm = Realm(opener=DEV)
    out = io.StringIO()
    with pytest.raises(agrun.Refused, match="not opened by this account"):
        agrun.finish(Client(realm), RUN_CHANNEL, RUN, REPORT, out=out)
    with pytest.raises(agrun.Refused, match="not a routine run"):
        agrun.finish(Client(realm), "front", DESK, REPORT, out=out)


def test_the_command_line_validates_the_report(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(agrun, "client_from_environment", lambda: None)
    assert agrun.main(["finish", RUN_CHANNEL, RUN, "--achieved", "--reason", "why"], out=out) == 1
    assert "refused" in out.getvalue()


def test_continue_serves_the_run_again_through_the_start_note(realm):
    """#13715/#13790: Front's own "Resuming" post served nothing. A start
    note of its own is what its listener serves."""
    client = Client(realm)
    answer = realm.post("work-m1", "workrun-task1-m1", "@**Front** task 1 done", AUTOLAB)
    out = io.StringIO()
    assert agrun.continue_run(client, RUN_CHANNEL, RUN, answer, "task 1 reported", ended_from=None, out=out) == 0
    history = realm.history(RUN_CHANNEL, RUN)
    start = parse_start(history[-1]["content"])
    assert start == (answer, FRONT, "")
    assert owed_start(history, FRONT) is not None
    # A second continue while the first is owed writes nothing.
    before = len(realm.messages)
    agrun.continue_run(client, RUN_CHANNEL, RUN, answer, "", ended_from=None, out=io.StringIO())
    assert len(realm.messages) == before
    # A finished run is not continued.
    agrun.finish(client, RUN_CHANNEL, RUN, REPORT, out=io.StringIO())
    with pytest.raises(agrun.Refused, match="already ended"):
        agrun.continue_run(client, RUN_CHANNEL, RUN, answer, "", ended_from=None, out=io.StringIO())


def test_adopt_moves_work_opened_beside_the_run_under_it(realm):
    """B worldtrend (#13661): the desk opened the workplan itself, so its
    answers served the desk. Front's own move makes them serve the run."""
    client = Client(realm)
    realm.post("pj-x", "workplan-x", f"[selfnote][rootchat] front/{DESK} #{realm.origin}", FRONT)
    realm.post("pj-x", "workplan-x", "@**autolab-agstudio1** one mission please", FRONT)
    assert agrun.adopt(client, "pj-x", "workplan-x", routine.Conversation(RUN_CHANNEL, RUN), io.StringIO()) == 0
    home = effective_rootchat(realm.history("pj-x", "workplan-x"), FRONT)
    assert (home.channel, home.topic, home.anchor) == (RUN_CHANNEL, RUN, realm.opening)
    before = len(realm.messages)
    agrun.adopt(client, "pj-x", "workplan-x", routine.Conversation(RUN_CHANNEL, RUN), io.StringIO())
    assert len(realm.messages) == before
    # Work of another request is not adopted.
    realm.post("pj-y", "workplan-y", "[selfnote][rootchat] front/front-desk-other #5", FRONT)
    with pytest.raises(agrun.Refused, match="another request"):
        agrun.adopt(client, "pj-y", "workplan-y", routine.Conversation(RUN_CHANNEL, RUN), io.StringIO())


def test_status_lists_the_runs_and_their_end(realm):
    out = io.StringIO()
    agrun.status(Client(realm), routine.Conversation("front", DESK), out)
    assert f"{RUN}: open (no end record)" in out.getvalue()
    agrun.finish(Client(realm), RUN_CHANNEL, RUN, REPORT, out=io.StringIO())
    out = io.StringIO()
    agrun.status(Client(realm), routine.Conversation("front", DESK), out)
    assert "ended at" in out.getvalue() and "report delivered" in out.getvalue() and "resolved" in out.getvalue()
