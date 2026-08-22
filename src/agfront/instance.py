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
"""

from __future__ import annotations

from pathlib import Path

from agag.agent import AgentSpec

AGFRONT_ROOT = Path(__file__).resolve().parents[2]
FRONT_TOPIC_PREFIX = "front-"

SPEC = AgentSpec("front", AGFRONT_ROOT, plan_prefix=FRONT_TOPIC_PREFIX)

__all__ = ["AGFRONT_ROOT", "FRONT_TOPIC_PREFIX", "SPEC"]
