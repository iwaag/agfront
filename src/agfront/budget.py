"""The budget windows, as a routine run reads them (`refine_routine` p1 step 3).

A run condition like "until the 5-hour window is 50 % used" is judged by
Front, and Front judges on what it can read. The agentroom relay already
reads each harness's plan window through the CLIs' own stores (`GET
/budget`, `ag.budget.v1`: per harness `ok`, `windows[]` with `percent` used
and `resets_at`, `read_at`, and on a failed read the `error` plus the last
good card as `stale`). This module is the run's way to that read:

- `agbudget` on the run's PATH prints the observation as text, so a run can
  read it again in the middle of a serving;
- `tools/budget.md` is written at the start of every run serving with the
  same text and the explanation of how to read it, so a run that never
  thinks to ask still sees the numbers it is judging against.

What is rendered is the observation and nothing else: the percent as the
vendor stated it, the reset time in absolute and relative terms, whether the
reset time has already passed since the read, when the read was made, and —
for a failed read — the failure and the last good numbers marked as stale.
**A failed or stale read is unknown, never 0 and never "reached"**; saying
so is this module's whole job, and deciding what to do about it is the
run's.

Since `runtime-profile` step4 each section also names the **usage pool** it
is: an execution option (`ag.exec-options.v1`) advertises the pool it
consumes, the budget document is keyed by *harness*, and a threshold like
"until agy's usage exceeds 70 %" is only answerable when the two can be put
side by side. The pool is the provider whose account the harness spends —
`agag.agent_config.HARNESS_PROVIDER`, the same table the configuration
validates profiles against — so the correspondence is derived, not invented
here. A harness with no entry (agcode, whose account depends on its model)
says its pool is unknown rather than guessing one.

`AGFRONT_BUDGET_URL` names the source: an `http(s)://` URL (default: the
relay on loopback) or a filesystem path to a JSON document of the same
shape, which is how a controlled observation is placed in front of a run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from agag.agent_config import HARNESS_PROVIDER

SOURCE_VARIABLE = "AGFRONT_BUDGET_URL"
DEFAULT_SOURCE = "http://127.0.0.1:8094/budget"
READ_TIMEOUT_SECONDS = 25.0
DOC_NAME = "budget.md"

USAGE = """# Budget windows

Each harness (backend) the agents run on is metered by its vendor in
**windows** — a 5-hour session window, a weekly window — and each window has
a `percent` **used**, as the vendor itself states it, and a time at which it
**resets** to 0. There is no absolute credit number; 100 % is the window's
whole capacity. This is *not* `cost_usd` on run records (an API-equivalent
price), and it is not something this run alone consumes: every run on the
same account moves the same meter.

How to read a window:

- `percent used` is the state of the **current** window at the time it was
  read (`read at`). It goes up as anybody uses the account and drops to 0
  when the window resets.
- `resets` says when the current window ends. When that time has already
  passed since the read, the number shown is from a window that is over:
  the current usage is unknown until it is read again.
- `pool` is the account this harness spends, and it is how a window is
  matched to an execution option: an option that says `pool: antigravity`
  is judged against the section marked `pool antigravity`. Two harnesses can
  share a pool; a section whose pool is `unknown` cannot be matched to an
  option at all, and saying so is the answer.
- `READ FAILED` means the vendor could not be asked; the numbers under
  *last good* are what was read earlier and are **stale** — they say what
  was true then, not now. A failed or stale read never counts as a
  condition reached, and never as 0.

Re-read at any time with `agbudget` (the same text, fresh). `agbudget --json`
prints the raw document. Reading costs nothing that matters here; judge on
the newest read you have, and write in your entry which read you judged
on (its time) and what it said.
"""


# --- reading -------------------------------------------------------------------


def source_from_environment(environ=None) -> str:
    environ = os.environ if environ is None else environ
    return environ.get(SOURCE_VARIABLE) or DEFAULT_SOURCE


def read_budget(source: str, *, timeout: float = READ_TIMEOUT_SECONDS) -> dict:
    """The `ag.budget.v1` document from a URL or a file; raises on failure."""
    if source.startswith(("http://", "https://")):
        import urllib.error
        import urllib.request

        request = urllib.request.Request(source, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured URL
                body = response.read()
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"{source} answered HTTP {error.code}") from None
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            reason = getattr(error, "reason", None) or error
            raise RuntimeError(f"{source} is not answering: {reason}") from None
        text = body.decode("utf-8", "replace")
    else:
        try:
            text = Path(source).expanduser().read_text(encoding="utf-8")
        except OSError as error:
            raise RuntimeError(f"cannot read {source}: {error}") from None
    try:
        document = json.loads(text)
    except ValueError as error:
        raise RuntimeError(f"{source} did not answer JSON: {error}") from None
    if not isinstance(document, dict) or not isinstance(document.get("harnesses"), dict):
        raise RuntimeError(f"{source} answered something that is not a budget document")
    return document


# --- rendering -----------------------------------------------------------------


def pool_for(harness: str) -> str | None:
    """The account a harness spends, or None when it cannot be said.

    `agag.agent_config.HARNESS_PROVIDER` is the authority: a harness bound to
    one provider spends that provider's account, and that provider name is
    what an execution option publishes as its `pool`. `agcode` is absent on
    purpose — its account follows its model — and None is written as
    `unknown`, never guessed, because a threshold matched to the wrong window
    is worse than a threshold that says it cannot be judged.
    """
    return HARNESS_PROVIDER.get(harness)


def _utc(epoch) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(float(epoch)))
    except (TypeError, ValueError, OverflowError):
        return "unknown time"


def _relative(epoch, now: float) -> str:
    try:
        delta = float(epoch) - now
    except (TypeError, ValueError):
        return ""
    ahead = delta >= 0
    seconds = abs(int(delta))
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours >= 48:
        text = f"{hours // 24}d {hours % 24}h"
    elif hours:
        text = f"{hours}h {minutes:02d}m"
    else:
        text = f"{minutes}m"
    return f"in {text}" if ahead else f"{text} ago"


def _window_line(window: dict, read_at: float | None, now: float) -> str:
    label = str(window.get("label") or window.get("kind") or "window")
    percent = window.get("percent")
    used = f"{percent:g} % used" if isinstance(percent, (int, float)) else "usage unknown"
    resets = window.get("resets_at")
    line = f"- {label}: {used}"
    if isinstance(resets, (int, float)):
        line += f"; resets {_utc(resets)} ({_relative(resets, now)})"
        if resets <= now:
            line += " — **this reset has already passed since the read: the window shown is over, current usage unknown until re-read**"
    else:
        line += "; reset time unknown"
    return line


def render(document: dict, *, now: float | None = None, source: str | None = None) -> str:
    """The observation as text: one section per harness, one line per window."""
    now = time.time() if now is None else now
    lines = [f"# Budget observation — read {_utc(now)}"]
    if source:
        lines.append(f"Source: {source}")
    generated = document.get("generated_at")
    if isinstance(generated, (int, float)) and abs(generated - now) > 120:
        lines.append(f"The document itself was generated {_utc(generated)} ({_relative(generated, now)}).")
    harnesses = document.get("harnesses") or {}
    if not harnesses:
        lines.append("\nNo harness in the document: nothing observed.")
    for name in sorted(harnesses):
        card = harnesses[name] or {}
        plan = card.get("plan")
        pool = pool_for(name)
        marks = ", ".join(
            part for part in (f"plan {plan}" if plan else "",
                              f"pool {pool}" if pool else "pool unknown") if part
        )
        head = f"## {name}" + (f" ({marks})" if marks else "")
        read_at = card.get("read_at")
        if card.get("ok") is False:
            lines.append(f"\n{head} — READ FAILED")
            lines.append(f"Reason: {card.get('error') or 'unknown'}")
            stale = card.get("stale")
            if isinstance(stale, dict) and stale.get("windows"):
                stale_at = stale.get("read_at")
                lines.append(f"Last good numbers, read {_utc(stale_at)} ({_relative(stale_at, now)}) — **STALE**, "
                             "what was true then, not now:")
                for window in stale["windows"]:
                    lines.append(_window_line(window, stale_at, now))
            else:
                lines.append("No earlier numbers to show: usage unknown.")
            continue
        lines.append(f"\n{head} — read at {_utc(read_at)} ({_relative(read_at, now)})")
        windows = card.get("windows") or []
        if not windows:
            lines.append("- no windows reported: usage unknown")
        for window in windows:
            lines.append(_window_line(window, read_at, now))
    return "\n".join(lines) + "\n"


def observe(source: str | None = None, *, now: float | None = None) -> str:
    """Read and render; a read that fails is rendered as the failure."""
    source = source or source_from_environment()
    try:
        document = read_budget(source)
    except RuntimeError as error:
        now = time.time() if now is None else now
        return (f"# Budget observation — read {_utc(now)}\nSource: {source}\n\n"
                f"**READ FAILED: {error}.** Usage is unknown; nothing here says a condition was reached.\n")
    return render(document, now=now, source=source)


def write_budget_doc(directory: Path, source: str | None = None, *, observation: str | None = None) -> Path:
    """`tools/budget.md` — the explanation and the observation at this moment."""
    text = observation if observation is not None else observe(source)
    path = directory / "tools" / DOC_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(USAGE + "\n" + text, encoding="utf-8")
    return path


# --- the CLI -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agbudget",
        description="Print how much of each harness's plan window is used right now — "
                    "the observation a routine run judges its conditions against.",
    )
    parser.add_argument("--json", action="store_true", help="print the raw ag.budget.v1 document")
    parser.add_argument("--source", default=None,
                        help=f"URL or file to read (default: ${SOURCE_VARIABLE} or {DEFAULT_SOURCE})")
    args = parser.parse_args(argv)
    source = args.source or source_from_environment()
    if args.json:
        try:
            print(json.dumps(read_budget(source), ensure_ascii=False, indent=1))
        except RuntimeError as error:
            print(json.dumps({"ok": False, "error": str(error), "source": source}))
        return 0
    sys.stdout.write(observe(source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
