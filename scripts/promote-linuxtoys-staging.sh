#!/usr/bin/env bash
# Promotes the linuxtoys package from the Lyra OBS staging project to the
# signed production project via an OBS submit request.
#
#   home:rodrigosbrito:lyra:staging/linuxtoys -> home:rodrigosbrito:lyra/linuxtoys
#
# Usage:
#   scripts/promote-linuxtoys-staging.sh [options]
#
# Options:
#   -e, --evidence TEXT   Test evidence to record on the request (required
#                         unless --diff is used). Wrap in quotes.
#   -r, --revision REV    Select a numeric revision, source hash, or latest.
#                         Always resolved to an immutable hash before review.
#       --accept          Also accept the request immediately instead of
#                         leaving it open for review. Requires --yes.
#       --yes             Skip the interactive confirmation prompt.
#       --diff            Only show the source diff between staging and
#                         production; makes no changes.
#   -h, --help            Show this help.
#
# Examples:
#   scripts/promote-linuxtoys-staging.sh --diff
#   scripts/promote-linuxtoys-staging.sh -e "OBS build green; RPM signature and About=6.6.6 verified"
#   scripts/promote-linuxtoys-staging.sh -e "..." --accept --yes
set -euo pipefail

STAGING_PRJ="home:rodrigosbrito:lyra:staging"
PROD_PRJ="home:rodrigosbrito:lyra"
PKG="linuxtoys"

evidence=""
revision=""
accept=0
assume_yes=0
diff_only=0

usage() {
    sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

fail() { echo "error: $*" >&2; exit 2; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        -e|--evidence)
            [[ $# -ge 2 && -n "$2" ]] || fail "$1 requires a value"
            evidence="$2"; shift 2 ;;
        -r|--revision)
            [[ $# -ge 2 && -n "$2" ]] || fail "$1 requires a value"
            revision="$2"; shift 2 ;;
        --accept) accept=1; shift ;;
        --yes) assume_yes=1; shift ;;
        --diff) diff_only=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "error: unknown option '$1'" >&2; usage; exit 2 ;;
    esac
done

for tool in osc python3; do
    command -v "$tool" >/dev/null 2>&1 || fail "'$tool' not found in PATH"
done

if [[ "$diff_only" -ne 1 && -z "$evidence" ]]; then
    echo "error: --evidence TEXT is required (or pass --diff to just inspect changes)" >&2
    exit 2
fi

if [[ "$diff_only" -ne 1 && "$accept" -eq 1 && "$assume_yes" -ne 1 ]]; then
    echo "error: --accept requires --yes (this publishes to the production OBS project)" >&2
    exit 2
fi

revision="${revision:-latest}"
[[ "$revision" =~ ^([0-9]+|[0-9a-f]{32}|latest)$ ]] || fail "invalid source revision: $revision"

# Resolve once from structured metadata, not the human-readable commit log.
# expand=1 also binds linked sources; subsequent reads use the resolved tree.
resolve_revision() {
    local metadata
    metadata=$(osc api "/source/$1/$PKG?rev=$2&expand=1") || fail "could not resolve source revision"
    python3 -c '
import re, sys, xml.etree.ElementTree as ET
try:
    root = ET.fromstring(sys.stdin.read())
    revision, checksum = root.get("rev", ""), root.get("srcmd5", "")
    if root.tag != "directory" or root.get("name") != "linuxtoys":
        raise ValueError("unexpected source directory")
    if not re.fullmatch(r"[0-9]+|[0-9a-f]{32}", revision):
        raise ValueError("missing or invalid revision")
    if not re.fullmatch(r"[0-9a-f]{32}", checksum):
        raise ValueError("missing or invalid immutable source hash")
    print(revision, checksum)
except (ET.ParseError, ValueError) as error:
    print(f"error: {error}", file=sys.stderr)
    sys.exit(2)
' <<<"$metadata"
}

revision_info=$(resolve_revision "$STAGING_PRJ" "$revision")
read -r revision_number staging_rev <<<"$revision_info"

spec_output=$(osc cat --unexpand -r "$staging_rev" "$STAGING_PRJ" "$PKG" "linuxtoys.spec") || fail "could not read the selected revision's spec"
version=$(awk '$1 == "Version:" && NF == 2 {print $2}' <<<"$spec_output")
[[ -n "$version" && "$version" != *$'\n'* ]] || fail "missing or ambiguous RPM Version"

if [[ "$diff_only" -eq 1 ]]; then
    production_info=$(resolve_revision "$PROD_PRJ" latest)
    read -r production_number production_rev <<<"$production_info"
    echo "Comparing production rev ${production_number} with staging rev ${revision_number} (srcmd5 ${staging_rev}, version ${version})"
    exec osc rdiff -r "${production_rev}:${staging_rev}" "$PROD_PRJ" "$PKG" "$STAGING_PRJ" "$PKG"
fi

message="Promote linuxtoys from staging

Source revision: ${staging_rev}
OBS revision: ${revision_number}
Source version: ${version}
Test evidence: ${evidence}"

echo "About to submit a request:"
echo "  ${STAGING_PRJ}/${PKG} (rev ${revision_number}, srcmd5 ${staging_rev}, version ${version}) -> ${PROD_PRJ}/${PKG}"
echo
echo "$message"
echo

if [[ "$assume_yes" -ne 1 ]]; then
    if ! read -r -p "Create this submit request? [y/N] " reply; then
        echo "aborted"; exit 1
    fi
    case "$reply" in
        [yY]|[yY][eE][sS]) ;;
        *) echo "aborted"; exit 1 ;;
    esac
fi

osc_args=(submitrequest -r "$staging_rev" -m "$message")
osc_args+=("$STAGING_PRJ" "$PKG" "$PROD_PRJ" "$PKG")

req_output=$(osc "${osc_args[@]}")
echo "$req_output"

if [[ "$accept" -eq 1 ]]; then
    # Match osc's result line; warnings/URLs may contain unrelated numbers.
    req_id=$(sed -nE 's/^created request id ([0-9]+)$/\1/p' <<<"$req_output")
    if [[ ! "$req_id" =~ ^[0-9]+$ ]]; then
        echo "error: could not parse request id from osc output; accept it manually with 'osc request accept <id>'" >&2
        exit 2
    fi
    echo "Accepting request $req_id ..."
    osc request accept -m "Promoted via promote-linuxtoys-staging.sh" "$req_id"
fi
