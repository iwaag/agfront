"""`agrun`: Front's own routine runs — see them, continue them, adopt work into them, end them.

failsafe p5 step 4. A run is a conversation Front owns and serves as
itself, and three things kept going wrong around it in progress_panel p1:

- **Continuing it.** Front's own post in its own run serves nothing (a
  topic whose last speaker is Front is never served by its owner route), so
  "Resuming: …" (#13715, #13790) was words, not a serving. `agrun continue`
  writes the one note of Front's that *is* work for its listener —
  `[selfnote][start]` (`agag.selfnote.start_note`, the mechanism autolab
  starts tasks with) — and the run is served again, durably: the listener
  enqueues it from the note, and an unanswered start survives a restart.
- **Work opened beside it.** A desk serving opened a workplan itself and
  then the run (#13661), so every answer came back to the desk and the run
  held nothing. `agrun adopt` moves Front's own root note in that work's
  topic to the run (`[selfnote][rootchat-moved]`, the explicit relation
  every reader follows) — nothing is inferred from topic names.
- **Ending it from wherever the last evidence arrived.** `agrun finish`
  (was `agrunfinish`) writes the run's end record and then completes the
  close-out from the records (`agfront.routine.close_out`): the report and
  the `[delivered]` note in the conversation that asked, then the ✔. A
  repeat after an interruption finishes what is missing and writes nothing
  twice — an end record no longer implies a delivery.

    agrun status [<channel>/<topic>]
    agrun continue <run channel> <run topic> --because <post id> [--note "…"]
    agrun adopt <work channel> <work topic> --run <run channel>/<run topic>
    agrun finish <run channel> <run topic> --achieved|--not-achieved --reason "…" --report "…" [--report-file F]

Exit 0 on success (a repeat included), 1 with one line otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agag.chat import client_from_environment
from agag.selfnote import (Conversation, effective_rootchat, home_from_environment, is_speech, parse_conversation,
                           rootchat_moved_note, start_note)
from agag.trace import finish_record
from agag.zulip import RESOLVED_TOPIC_PREFIX, ZulipError, live_topic_name, topic_history_across_resolve

from .routine import (
    FinishError,
    FinishReport,
    close_out,
    is_run_topic,
    parse_finish,
    record_text,
    run_state,
    runs_document,
)

__all__ = ["main", "adopt", "continue_run", "finish", "status"]


def _bare(topic: str) -> str:
    return topic[len(RESOLVED_TOPIC_PREFIX):] if topic.startswith(RESOLVED_TOPIC_PREFIX) else topic


class Refused(RuntimeError):
    """Nothing was written."""


def _own_run(client, channel: str, topic: str) -> tuple[int, list[dict]]:
    run_topic = _bare(topic)
    if not is_run_topic(run_topic):
        raise Refused(f"{channel}/{run_topic} is not a routine run (a `routinerun-` topic)")
    self_id = int(client.whoami()["user_id"])
    history = topic_history_across_resolve(client, channel, run_topic, 400, strict=True)
    spoken = [m for m in history if is_speech(m)]
    if not spoken or spoken[0].get("sender_id") != self_id:
        raise Refused(f"{channel}/{run_topic} was not opened by this account, so it is not your run")
    return self_id, history


def status(client, home: Conversation | None, out) -> int:
    if home is None:
        raise Refused("say which conversation's runs to list (<channel>/<topic>), or run inside a serving")
    self_id = int(client.whoami()["user_id"])
    print(runs_document(client, (home.channel, _bare(home.topic)), self_id), file=out, end="")
    return 0


def continue_run(client, channel: str, topic: str, because: int, note: str, *, ended_from: Conversation | None,
                 out) -> int:
    """Have the run served again: a visible line saying why, then the start
    note the listener serves."""
    self_id, history = _own_run(client, channel, topic)
    state = run_state(client, channel, topic, self_id)
    if state.ended_id:
        raise Refused(f"{channel}/{_bare(topic)} has already ended (#{state.ended_id}); a finished run is not "
                      "continued — open a new run if more work is wanted")
    if state.start_owed:
        print(f"a continuation of {channel}/{_bare(topic)} already waits to be served; nothing was written", file=out)
        return 0
    if client.message(int(because)) is None:
        raise Refused(f"#{because} does not exist: name the post that makes the run's next step due")
    live = state.live or live_topic_name(client, channel, _bare(topic))
    where = f" from {ended_from.channel}/{ended_from.topic}" if ended_from is not None else ""
    text = f"Continuing this run{where}, because of #{int(because)}" + (f": {note.strip()}" if note.strip() else ".")
    client.send_to_channel(channel, live, text + "\n\n`ag-post intent=progress`")
    client.send_to_channel(channel, live, start_note(int(because), self_id, ""))
    print(f"{channel}/{_bare(topic)} will be served again (because of #{because}); its serving reads what arrived "
          "and decides the next step", file=out)
    return 0


def adopt(client, channel: str, topic: str, run: Conversation, out) -> int:
    """Move Front's own root note in a work topic to the run, so the work's
    answers and every reader's tree follow the run."""
    self_id, run_history = _own_run(client, run.channel, run.topic)
    run_state_now = run_state(client, run.channel, run.topic, self_id)
    if run_state_now.ended_id:
        raise Refused(f"{run.channel}/{run.topic} has ended; work is not adopted into a finished run")
    history = topic_history_across_resolve(client, channel, _bare(topic), 400, strict=True)
    current = effective_rootchat(history, self_id)
    if current is None:
        raise Refused(f"{channel}/{_bare(topic)} carries no root note of yours: it is not work you opened")
    if (current.channel, _bare(current.topic)) == (run.channel, _bare(run.topic)):
        print(f"{channel}/{_bare(topic)} already answers to {run.channel}/{_bare(run.topic)}; nothing was written",
              file=out)
        return 0
    # Only work of the same request: the run must have been opened from the
    # conversation the work currently answers to.
    origin = effective_rootchat(run_history, self_id)
    if origin is None or (origin.channel, _bare(origin.topic)) != (current.channel, _bare(current.topic)):
        raise Refused(f"{channel}/{_bare(topic)} answers to {current.channel}/{current.topic}, and "
                      f"{run.channel}/{_bare(run.topic)} was not opened from there: it is another request's work")
    anchor = next((int(m["id"]) for m in run_history if is_speech(m) and m.get("sender_id") == self_id), 0)
    live = live_topic_name(client, channel, _bare(topic))
    client.send_to_channel(channel, live, rootchat_moved_note(Conversation(run.channel, _bare(run.topic), anchor)))
    print(f"{channel}/{_bare(topic)} now answers to {run.channel}/{_bare(run.topic)}: its answers serve the run",
          file=out)
    return 0


def finish(client, channel: str, topic: str, report: FinishReport, *, ended_from: Conversation | None = None,
           out=sys.stdout) -> int:
    """End the run on the record, then complete its close-out (module doc)."""
    self_id, history = _own_run(client, channel, topic)
    run_topic = _bare(topic)
    ended = next((m for m in history if m.get("sender_id") == self_id and finish_record(m.get("content"))), None)
    if ended is None:
        live = live_topic_name(client, channel, run_topic)
        where = f" from {ended_from.channel}/{ended_from.topic}" if ended_from is not None else ""
        note = (f"The run is ended{where}: its request was completed outside this topic. "
                "This is the run's end record.")
        client.send_to_channel(channel, live, record_text(note, report, None))
        said = "ended"
    else:
        said = f"already ended at #{ended['id']}"
    state = close_out(client, channel, run_topic, self_id)
    delivered = (f"; the report is delivered to {state.origin.channel}/{state.origin.topic}"
                 if state.origin is not None else "")
    print(f"{channel}/{run_topic}: {said} ({'goal reached' if state.finish and state.finish.achieved else 'goal not reached'})"
          f"{delivered}; the run is resolved", file=out)
    return 0


def _conversation(value: str) -> Conversation:
    found = parse_conversation(value)
    if found is None:
        raise Refused(f"{value!r} is not <channel>/<topic>")
    return found


USAGE = """\
`agrun`: Front's own routine runs — see them, continue them, adopt work into
them, end them.

A routine is a process guide kept on the board: one `routine-<name>` channel
per routine, and the newest post in its `guide` topic is the whole guide.
A run of it is a `routinerun-<id>` conversation in that channel, which you
own and are served in as the `routine_run` role. It is opened by one post —
`agentchat send routine-<name> routinerun-<id> "…"`, with a name that does not
exist there yet (the UTC time is a good id) — carrying the request in the
requester's own words, the execution and end conditions as you read them
(a way of executing they asked for, in their words), the guide post you
read (its message id), and the conversation the request came from. That one
post is the start: the run is served after the reply that opened it, it does
its own delegating, and its report comes back to the conversation that asked.
There is no schedule: a timed or recurring run cannot be arranged.

A run's last steps sometimes arrive in the conversation that asked for it
instead of in the run: a result, or the requester's acceptance. An open run
of yours is yours to move from there with the commands below; a post of yours
into the run topic serves nothing, and a sentence saying it is complete ends
nothing.

    agrun status [<channel>/<topic>]
    agrun continue <run channel> <run topic> --because <post id> [--note "…"]
    agrun adopt <work channel> <work topic> --run <run channel>/<run topic>
    agrun finish <run channel> <run topic> --achieved|--not-achieved --reason "…" --report "…"

`agrun <command> --help` says what each one does."""

STATUS_HELP = """\
List the runs a conversation opened (default: the one you are serving) and
where each one's end stands: open, ended with its report delivered, or ended
with the close-out incomplete. The same list is written into a serving's
workspace as `tools/runs.md`; this reads it again now. Writes nothing."""

CONTINUE_HELP = """\
Have your own run served again because of a post — an answer the run needs
landed in the conversation that asked, or Observer says the run stopped and
nothing else holds it. The run reads what arrived and decides its next step
itself. It writes one start note into the run (the note its listener serves
from), so the serving happens even across a restart; your own words posted
there would serve nothing."""

ADOPT_HELP = """\
Move work for a run that was opened from the requesting conversation instead
of from the run (a `workplan-` topic whose answers come back there, while the
run holds nothing) under the run: its answers then serve the run. It moves
your own root note in that topic and nothing else; nothing is inferred from
topic names."""

FINISH_HELP = """\
End a run on the record, from wherever the last evidence arrived: when its
work is complete by record (the mission accepted, and whatever the routine's
guide asks after that recorded) or it cannot go on. It writes the run's end
record, delivers the report to the conversation that asked and resolves the
run — exactly what a run ending itself does. Run again after an
interruption, it finishes what is missing and writes nothing twice.

--achieved is whether the routine's goal was reached, not whether the run
ended cleanly; --reason is why it ends. Until the end record exists, the run
reads as open to everybody watching it."""


def main(argv: list[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    raw = argparse.RawDescriptionHelpFormatter
    parser = argparse.ArgumentParser(prog="agrun", formatter_class=raw, description=USAGE)
    commands = parser.add_subparsers(dest="command", required=True)
    see = commands.add_parser("status", help="the runs a conversation opened and where each one's end stands",
                              formatter_class=raw, description=STATUS_HELP)
    see.add_argument("home", nargs="?", default=None, help="<channel>/<topic> (default: the conversation served)")
    go = commands.add_parser("continue", help="have your own run served again, because of a post",
                             formatter_class=raw, description=CONTINUE_HELP)
    go.add_argument("channel")
    go.add_argument("topic")
    go.add_argument("--because", type=int, required=True, help="the post that makes the run's next step due")
    go.add_argument("--note", default="", help="one line for the run's record: what arrived, what is next")
    take = commands.add_parser("adopt", help="make work you opened elsewhere for this request answer to the run",
                               formatter_class=raw, description=ADOPT_HELP)
    take.add_argument("channel")
    take.add_argument("topic")
    take.add_argument("--run", required=True, help="<run channel>/<run topic>")
    end = commands.add_parser("finish", help="end a run on the record and deliver its report",
                              formatter_class=raw, description=FINISH_HELP)
    end.add_argument("channel")
    end.add_argument("topic")
    goal = end.add_mutually_exclusive_group(required=True)
    goal.add_argument("--achieved", action="store_true", help="the routine's goal was reached")
    goal.add_argument("--not-achieved", action="store_true", help="the run ends without the routine's goal")
    end.add_argument("--reason", required=True, help="why the run ends, in one or two sentences")
    end.add_argument("--report", help="the report for whoever asked for the run")
    end.add_argument("--report-file", help="read the report from this file instead")
    args = parser.parse_args(argv)
    try:
        client = client_from_environment()
        home = home_from_environment()
        if args.command == "status":
            return status(client, _conversation(args.home) if args.home else home, out)
        if args.command == "continue":
            return continue_run(client, args.channel, args.topic, args.because, args.note, ended_from=home, out=out)
        if args.command == "adopt":
            return adopt(client, args.channel, args.topic, _conversation(args.run), out)
        text = Path(args.report_file).read_text(encoding="utf-8") if args.report_file else (args.report or "")
        report = parse_finish(json.dumps({"schema": "ag.routinerun-finish.v1", "achieved": bool(args.achieved),
                                          "reason": args.reason, "report": text}))
        return finish(client, args.channel, args.topic, report, ended_from=home, out=out)
    except (Refused, FinishError) as error:
        print(f"refused: {error}", file=out)
        return 1
    except ZulipError as error:
        print(f"failed: {error} — part of it may be written; run the same command again (a repeat finishes it)",
              file=out)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
