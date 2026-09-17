"""Front's instance name and the spec the skeleton runs it by.

`front` is the agent; `front-agstudio1` is *this running instance of it*.
The name lives in `.local/instance.toml` (`instance.example.toml` shows the
shape) and `FRONT_INSTANCE_NAME` overrides it — both read by
`agag.agent.AgentSpec`.

Front is the Developer's own agent, served in `#front` through its `front-`
topics. Those are its `plan_prefix`: the skeleton sweeps them wherever Front
is subscribed, which is what `main` did by hand before `agag_builder` p2.
A channel named after the instance need not exist; if it did, every topic in
it would be answered by `agag.entrance`, like any other agent's.

Since `runtime-profile` step4 Front publishes its own **execution options**
(`ag.exec-options.v1`) like every other agent, derived from `agents.toml` so
a name whose profile is gone is not advertised. They are Front's own: asking
Front to run *its* conversations on `agy` is a different request from asking
it to have autolab run a mission on autolab's `agy`, and Front honouring the
second must not change the first.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from agag.agent import AgentSpec
from agag.argue import ARGUE_TOPIC_PREFIX
from agag.execopt import Option

AGFRONT_ROOT = Path(__file__).resolve().parents[2]
FRONT_TOPIC_PREFIX = "front-"
#: A routine run (`refine_routine` p1): a topic Front opens in the routine's
#: own channel and then owns — swept wherever Front is subscribed, which is
#: every channel of the `routine` folder.
ROUTINE_RUN_PREFIX = "routinerun-"

#: What Front is willing to be asked for: `(profile name, usage pool, phrase)`.
#: The profile name is the public name — one-to-one, the contract's suggested
#: start — and `exec_options` publishes one only if `agents.toml` has it.
#: `sonnet`, `desk`, `present` and `routine_run` stay private: they are how
#: Front is wired, not a choice anybody outside makes.
PUBLIC_PROFILES = (
    ("agy", "antigravity", "Antigravity CLI (`agy`), Gemini 3.8 Flash"),
    ("agy-claude", "antigravity", "Antigravity CLI (`agy`), Claude Sonnet 4.6"),
    ("codex", "openai", "OpenAI Codex CLI, GPT-5.6"),
    ("gemini", "google", "Gemini CLI, Gemini 2.5 Flash"),
)
#: Front's roles are its entrance, the Front Desk voice and a routine run, and
#: an option applies to all three: the conversation is what carries it.
COVERS = "my own conversations: this entrance, the Front Desk, routine runs and argues"
#: The roles `COVERS` is a sentence about, and the roles the published pool is
#: **derived** from (`agag.execpool`). The two must name the same work: a
#: sentence and a pool that disagree is precisely the failure this list
#: exists to make impossible.
EXEC_ROLES = ("front", "desk", "routine_run", "argue")
#: What running under no selection costs. Published because a threshold like
#: "until the pool is 70 % used" cannot be judged against a default that
#: declines to name a pool.
DEFAULT_OPTION_DETAIL = ("anthropic", "my configured defaults — Claude Sonnet 5 through claude_code")


def configured_profiles(path: Path | None = None) -> frozenset[str]:
    """The profile names `agents.toml` declares.

    Read straight rather than through the validating loader: this runs at
    import time, and a schema complaint here would take the listener down
    while the only fact needed is which names exist.
    """
    try:
        data = tomllib.loads((path or (AGFRONT_ROOT / "agents.toml")).read_text(encoding="utf-8"))
        return frozenset(data.get("profiles", {}))
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return frozenset()


def exec_options(path: Path | None = None) -> tuple[Option, ...]:
    """Front's menu, from the profiles it is actually configured with.

    Publishing nothing when the config cannot be read leaves a reader at
    *unknown*, which is the honest answer for an instance that cannot say —
    and better than advertising a name that would fail at execution time.
    """
    profiles = configured_profiles(path)
    if not profiles:
        return ()
    pool, summary = DEFAULT_OPTION_DETAIL
    return (
        Option("default", pool, COVERS, summary),
        *(
            Option(name, option_pool, COVERS, option_summary)
            for name, option_pool, option_summary in PUBLIC_PROFILES
            if name in profiles
        ),
    )


SPEC = AgentSpec(
    "front", AGFRONT_ROOT, plan_prefix=FRONT_TOPIC_PREFIX,
    # `argue-` (argue p1): a conversation in `#argue` Front owns and
    # facilitates; every other agent takes part there only when named.
    extra_prefixes=(ROUTINE_RUN_PREFIX, ARGUE_TOPIC_PREFIX),
    exec_options=exec_options(),
    exec_roles=EXEC_ROLES,
)

__all__ = [
    "AGFRONT_ROOT", "ARGUE_TOPIC_PREFIX", "COVERS", "DEFAULT_OPTION_DETAIL", "FRONT_TOPIC_PREFIX",
    "PUBLIC_PROFILES", "ROUTINE_RUN_PREFIX", "SPEC",
    "configured_profiles", "exec_options",
]
