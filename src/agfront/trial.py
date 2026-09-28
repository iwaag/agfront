"""`python -m agfront.trial <probe> --store <fixture> --out <dir>` — one Front
Desk serving of a fixture probe (`agent_guide` p2 step 6).

The serving is agfront's own `serve`: the evidence chatlog, `tools/agents.md`
harvested from the board, `tools/runs.md`, the prompt, the run. Only two
things differ from the listener: the client is the fixture board
(`agag.fixture.run.client`), and the run's `agentchat` reads it too
(`fixture_environment`). Nothing is posted anywhere.

`--guides <root>` serves with another guide tree (the live checkout's, for a
baseline) and `--no-shared` leaves out pyagag's shared sections, which
together reproduce the composition before this phase. The reply, its verdict
under the probe's rule, the run record's cost and the transcript's tool calls
are written to `--out` (`outcome.json`, `reply.md`).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from agag.fixture import PROBES
from agag.fixture.run import client, fixture_environment, outcome, probe_history
from agag.topics import TopicContext

from . import zulip_listener


def _newest(directory: Path, pattern: str) -> Path | None:
    found = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime) if directory.is_dir() else []
    return found[-1] if found else None


def _tool_calls(transcript: Path | None) -> list[str]:
    """The Bash commands and other tool calls a run made, in order."""
    calls: list[str] = []
    if transcript is None or not transcript.is_file():
        return calls
    for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        for block in ((event.get("message") or {}).get("content") or []) if isinstance(event, dict) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                arguments = block.get("input") or {}
                calls.append(f"{block.get('name')}: {arguments.get('command') or arguments.get('file_path') or ''}"[:200])
    return calls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agfront.trial", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("probe", choices=sorted(n for n, p in PROBES.items() if p.agent == "agfront"))
    parser.add_argument("--store", type=Path, required=True, help="the fixture board's mirror.sqlite")
    parser.add_argument("--out", type=Path, required=True, help="where outcome.json and reply.md go")
    parser.add_argument("--guides", type=Path, default=None, help="serve with this guide tree (default: this checkout's)")
    parser.add_argument("--no-shared", action="store_true", help="leave out pyagag's shared guide sections")
    parser.add_argument("--dry-run", action="store_true",
                        help="build the serving and write its prompt to <out>/prompt.md; run nothing")
    args = parser.parse_args(argv)
    probe = PROBES[args.probe]
    if args.guides is not None:
        zulip_listener.GUIDES = args.guides
    if args.no_shared:
        zulip_listener.PYAGAG_SECTIONS = {}
    board = client(args.store)
    me = board.whoami()
    context = TopicContext(board, probe.channel, probe.topic, int(me["user_id"]), str(me["full_name"]),
                           history=probe_history(probe))
    role = zulip_listener.role_for(probe.channel, probe.topic)
    if args.dry_run:
        def capture(prompt, cwd, home, role=role, **_):
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / "prompt.md").write_text(prompt, encoding="utf-8")
            print(f"prompt: {len(prompt)} chars, workspace {cwd}")
            return "<ag-reply intent=report>\n(dry run)\n</ag-reply>"

        zulip_listener.run_front = capture
    with fixture_environment(args.store):
        result = zulip_listener.serve(context)
    record_path = _newest(zulip_listener.RECORDS_ROOT / role, "run-*.json")
    record = json.loads(record_path.read_text(encoding="utf-8")) if record_path else {}
    workspace = _newest(zulip_listener.SPEC.topics_root / probe.channel / probe.topic, "*")
    # Claude Code keeps the session under ~/.claude/projects/<the run's cwd, slugged>.
    transcript = None
    if workspace is not None:
        slug = re.sub(r"[^A-Za-z0-9]", "-", str((workspace / role).resolve()))
        transcript = _newest(Path.home() / ".claude" / "projects" / slug, "*.jsonl")
    verdict = outcome(probe, result.output or "", args.out, role=role, guides=str(zulip_listener.GUIDES),
                      shared=not args.no_shared, record=str(record_path or ""),
                      cost_usd=record.get("cost_usd"), turns=record.get("num_turns"),
                      duration_s=round((record.get("duration_ms") or 0) / 1000, 1),
                      tool_calls=_tool_calls(transcript))
    print(json.dumps({k: v for k, v in verdict.items() if k != "reply"}, ensure_ascii=False, indent=1))
    return 0 if verdict["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
