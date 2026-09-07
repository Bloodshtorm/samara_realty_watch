#!/usr/bin/env bash
set -euo pipefail
# All container collection entrypoints share the same host-backed lock inode.
exec flock --no-fork --nonblock --conflict-exit-code 75 /app/data/collector.lock python -m app collect "$@"
