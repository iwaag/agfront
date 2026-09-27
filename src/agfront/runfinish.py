"""`agrunfinish`: end a routine run from outside its own topic, on the record.

progress_panel p1 step 5. A run ends when a `routine_run` serving writes the
`ag-routinerun` block, and the listener then records it in the run topic,
delivers the report to the conversation that opened the run and resolves
the run topic (`zulip_listener.finish_run`). But a routine's last steps can
happen elsewhere: the mission's acceptance has to be the requester's own
words (`agentchat accept` refuses Front's), so it lands in the request's
own conversation, and that conversation's serving records it and asks for
the refresh. Both trial runs of step 5 were completed that way and neither
run ever ended: one got a sentence "Run complete." posted into it, which is
prose, not a record; the other got nothing. The progress panel kept "run
ended" and "report delivered" pending, correctly, and Observer asked about
the run as held by nobody.

This is the same end, done from any serving:

    agrunfinish <run channel> <run topic> --achieved|--not-achieved \\
        --reason "why the run ends" --report "the report" [--report-file F]

It posts the canonical block into the run topic (with one line saying
where it was ended from), delivers the report and the delivered note to the
run's origin exactly as the listener does, and resolves the run. It refuses
a topic that is not one of this account's runs, and a run that already has
its end record says so and changes nothing — a repeat is harmless. Exit 0
on an end recorded now or before, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agag.chat import client_from_environment
from agag.selfnote import Conversation, home_from_environment, is_speech
from agag.trace import finish_record
from agag.zulip import RESOLVED_TOPIC_PREFIX, ZulipError, live_topic_name, locate, topic_history_across_resolve

from .routine import (
    FinishError,
    FinishReport,
    delivered_note,
    delivery_text,
    is_run_topic,
    origin_of,
    parse_finish,
    record_text,
)

__all__ = ["main", "finish"]


def _bare(topic: str) -> str:
    return topic[len(RESOLVED_TOPIC_PREFIX):] if topic.startswith(RESOLVED_TOPIC_PREFIX) else topic


def finish(client, channel: str, topic: str, report: FinishReport, *, ended_from: Conversation | None = None,
           out=sys.stdout) -> int:
    run_topic = _bare(topic)
    if not is_run_topic(run_topic):
        print(f"refused: {channel}/{run_topic} is not a routine run (a `routinerun-` topic)", file=out)
        return 1
    self_id = int(client.whoami()["user_id"])
    history = topic_history_across_resolve(client, channel, run_topic, 400, strict=True)
    spoken = [m for m in history if is_speech(m)]
    if not spoken or spoken[0].get("sender_id") != self_id:
        print(f"refused: {channel}/{run_topic} was not opened by this account, so it is not your run", file=out)
        return 1
    ended = next((m for m in history if m.get("sender_id") == self_id and finish_record(m.get("content"))), None)
    if ended is not None:
        live = str(history[-1].get("subject") or run_topic)
        if not live.startswith(RESOLVED_TOPIC_PREFIX):
            # An earlier attempt recorded the end and stopped before the ✔.
            client.resolve_topic(int(ended["id"]), live)
            print(f"already ended at #{ended['id']}; the run was not resolved yet and now is", file=out)
            return 0
        print(f"already ended: {channel}/{run_topic} has its end record at #{ended['id']}; nothing was changed",
              file=out)
        return 0
    live = live_topic_name(client, channel, run_topic)
    where = f" from {ended_from.channel}/{ended_from.topic}" if ended_from is not None else ""
    note = (f"The run is ended{where}: its request was completed outside this topic. "
            "This is the run's end record.")
    record_id = client.send_to_channel(channel, live, record_text(note, report, None))
    run = Conversation(channel, run_topic)
    origin = origin_of(history, self_id)
    delivered = ""
    if origin is not None and (origin.channel, _bare(origin.topic)) != (channel, run_topic):
        located = locate(client, origin) if origin.anchor else None
        name = located.topic if located is not None else live_topic_name(client, origin.channel, origin.topic)
        client.send_to_channel(origin.channel, name, delivery_text(report, run))
        client.send_to_channel(origin.channel, name, delivered_note(run))
        delivered = f"; the report is delivered to {origin.channel}/{name}"
    client.resolve_topic(int(record_id), live)
    print(f"ended {channel}/{run_topic} with its end record #{record_id} "
          f"({'goal reached' if report.achieved else 'goal not reached'}){delivered}; the run is resolved", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agrunfinish", description=__doc__.split("\n\n")[0])
    parser.add_argument("channel")
    parser.add_argument("topic")
    goal = parser.add_mutually_exclusive_group(required=True)
    goal.add_argument("--achieved", action="store_true", help="the routine's goal was reached")
    goal.add_argument("--not-achieved", action="store_true", help="the run ends without the routine's goal")
    parser.add_argument("--reason", required=True, help="why the run ends, in one or two sentences")
    parser.add_argument("--report", help="the report for whoever asked for the run")
    parser.add_argument("--report-file", help="read the report from this file instead")
    args = parser.parse_args(argv)
    text = Path(args.report_file).read_text(encoding="utf-8") if args.report_file else (args.report or "")
    import json

    try:
        report = parse_finish(json.dumps({"schema": "ag.routinerun-finish.v1", "achieved": bool(args.achieved),
                                          "reason": args.reason, "report": text}))
    except FinishError as error:
        print(f"refused: {error}")
        return 1
    try:
        client = client_from_environment()
        return finish(client, args.channel, args.topic, report, ended_from=home_from_environment())
    except ZulipError as error:
        print(f"failed: {error} — nothing may have been written; run it again (a repeat is harmless)")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
