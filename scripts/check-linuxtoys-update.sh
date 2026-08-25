#!/usr/bin/env bash
# Compares the LinuxToys version packaged in linuxtoys.spec against the
# latest upstream GitHub release (psygreg/linuxtoys) and reports whether a
# packaging update is due.
#
# Exit codes:
#   0 - packaged version already matches the latest upstream release
#   1 - a newer upstream release is available
#   2 - error (network, missing tools, unparsable input)
set -euo pipefail

REPO="psygreg/linuxtoys"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPEC="${ROOT}/linuxtoys.spec"

for tool in curl python3; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "error: required tool '$tool' not found" >&2
        exit 2
    fi
done

if [[ ! -f "$SPEC" ]]; then
    echo "error: $SPEC not found" >&2
    exit 2
fi

packaged_version=$(grep -m1 -E '^Version:' "$SPEC" | awk '{print $2}')
if [[ -z "$packaged_version" ]]; then
    echo "error: could not read Version from $SPEC" >&2
    exit 2
fi

response=$(curl -sS -w '\n%{http_code}' \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPO}/releases/latest")
http_code=$(tail -n1 <<<"$response")
body=$(sed '$d' <<<"$response")

if [[ "$http_code" != "200" ]]; then
    echo "error: GitHub API request failed (HTTP $http_code)" >&2
    echo "$body" >&2
    exit 2
fi

latest_version=$(python3 -c '
import json, sys
data = json.load(sys.stdin)
tag = data.get("tag_name", "")
print(tag.lstrip("v"))
' <<<"$body")

if [[ -z "$latest_version" ]]; then
    echo "error: could not parse latest release tag from GitHub response" >&2
    exit 2
fi

echo "packaged version: $packaged_version"
echo "latest upstream:  $latest_version"

if [[ "$packaged_version" == "$latest_version" ]]; then
    echo "up to date"
    exit 0
fi

echo "update available: ${packaged_version} -> ${latest_version}"
echo "release notes:    https://github.com/${REPO}/releases/tag/${latest_version}"
echo "tarball:          https://github.com/${REPO}/releases/download/${latest_version}/linuxtoys-${latest_version}.tar.xz"
exit 1
