"""`agrunfinish` (progress_panel p1 step 5): a run ended from outside its
topic gets the same end record, delivery and ✔ the listener gives it; a
repeat changes nothing; somebody else's topic or a non-run is refused."""

import io
import json

import pytest

from agag.trace import finish_record
from agag.zulip import RESOLVED_TOPIC_PREFIX

from agfront import runfinish
from agfront.routine import FinishReport

FRONT, DEV = 15, 8
RUN = "routinerun-20260927T092403Z"
DESK = "front-desk-20260927-pp1-growbox"


class Realm:
    def __init__(self, opener=FRONT):
        self.messages = []
        self.next = 100
        self.post("front", DESK, "please run it", DEV)
        self.post("routine-study-x", RUN, f"[selfnote][rootchat] front/{DESK} #100", FRONT)
        self.post("routine-study-x", RUN, "Please run the routine once.", opener)

    def post(self, channel, topic, content, sender):
        self.messages.append({"id": self.next, "display_recipient": channel, "subject": topic, "sender_id": sender,
                              "sender_full_name": "Front" if sender == FRONT else "x", "content": content,
                              "timestamp": 1000 + self.next})
        self.next += 1
        return self.next - 1

    def history(self, channel, topic):
        bare = topic[len(RESOLVED_TOPIC_PREFIX):] if topic.startswith(RESOLVED_TOPIC_PREFIX) else topic
        return [m for m in self.messages if m["display_recipient"] == channel
                and m["subject"] in (bare, f"{RESOLVED_TOPIC_PREFIX}{bare}")]


class Client:
    def __init__(self, realm):
        self.realm = realm
        self.resolved = []

    def whoami(self):
        return {"user_id": FRONT}

    def send_to_channel(self, channel, topic, content):
        return self.realm.post(channel, topic, content, FRONT)

    def resolve_topic(self, message_id, topic):
        self.resolved.append(topic)
        for m in self.realm.messages:
            if m["subject"] == topic:
                m["subject"] = f"{RESOLVED_TOPIC_PREFIX}{topic}"


@pytest.fixture
def realm(monkeypatch):
    found = Realm()
    monkeypatch.setattr(runfinish, "topic_history_across_resolve",
                        lambda client, channel, topic, n, strict=False: found.history(channel, topic))
    monkeypatch.setattr(runfinish, "live_topic_name",
                        lambda client, channel, topic: next((m["subject"] for m in found.history(channel, topic)), topic))
    monkeypatch.setattr(runfinish, "locate", lambda client, conversation: None)
    return found


REPORT = FinishReport(achieved=True, reason="the mission was accepted and the sage refreshed", report="done: m1")


def test_a_run_is_ended_with_its_record_its_delivery_and_its_tick(realm):
    client, out = Client(realm), io.StringIO()
    assert runfinish.finish(client, "routine-study-x", RUN, REPORT, out=out) == 0
    record = [m for m in realm.history("routine-study-x", RUN) if finish_record(m["content"])]
    assert len(record) == 1 and finish_record(record[0]["content"])["achieved"] is True
    desk = realm.history("front", DESK)
    assert "Routine run finished" in desk[-2]["content"]
    assert desk[-1]["content"] == f"[selfnote][delivered] routine-study-x/{RUN}"
    assert client.resolved == [RUN]
    # A repeat finds the record and changes nothing.
    before = len(realm.messages)
    assert runfinish.finish(client, "routine-study-x", RUN, REPORT, out=out) == 0
    assert len(realm.messages) == before and "already ended" in out.getvalue()


def test_somebody_elses_topic_and_a_non_run_are_refused(monkeypatch):
    other = Realm(opener=DEV)
    monkeypatch.setattr(runfinish, "topic_history_across_resolve",
                        lambda client, channel, topic, n, strict=False: other.history(channel, topic))
    out = io.StringIO()
    assert runfinish.finish(Client(other), "routine-study-x", RUN, REPORT, out=out) == 1
    assert "not opened by this account" in out.getvalue()
    assert runfinish.finish(Client(other), "front", DESK, REPORT, out=out) == 1
    assert "not a routine run" in out.getvalue()


def test_the_command_line_validates_the_report(capsys):
    assert runfinish.main(["routine-study-x", RUN, "--achieved", "--reason", "r", "--report", ""]) == 1
    assert "report" in capsys.readouterr().out
