#!/usr/bin/env bash
# Reconcile GitHub and OBS staging independently; production stays manual.
# Exit 0: up to date or verified staging sources; exit 2: failure/incomplete.
set -euo pipefail
exec python3 "$(dirname -- "${BASH_SOURCE[0]}")/auto_update_linuxtoys.py" "$@"
