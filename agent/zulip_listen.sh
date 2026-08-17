#!/bin/sh
# Run the agfront Zulip listener (credentials: .local/zulip.env).
set -eu
cd "$(dirname "$0")/.."
exec uv run python -m agfront.zulip_listener
