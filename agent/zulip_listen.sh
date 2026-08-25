#!/bin/sh
# Run the agfront Zulip listener (credentials: .local/zulip.env).
set -eu
cd "$(dirname "$0")/.."
PATH="$(cd ../devenv/routine && pwd):$PATH"
export PATH
exec uv run python -m agfront.listener
