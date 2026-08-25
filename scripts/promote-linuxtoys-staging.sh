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
#   -r, --revision REV    Promote a specific staging source revision instead
#                         of the current head.
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

while [[ $# -gt 0 ]]; do
    case "$1" in
        -e|--evidence) evidence="$2"; shift 2 ;;
        -r|--revision) revision="$2"; shift 2 ;;
        --accept) accept=1; shift ;;
        --yes) assume_yes=1; shift ;;
        --diff) diff_only=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "error: unknown option '$1'" >&2; usage; exit 2 ;;
    esac
done

if ! command -v osc >/dev/null 2>&1; then
    echo "error: 'osc' not found in PATH" >&2
    exit 2
fi

if [[ "$diff_only" -eq 1 ]]; then
    exec osc rdiff "$STAGING_PRJ" "$PKG" "$PROD_PRJ" "$PKG"
fi

if [[ -z "$evidence" ]]; then
    echo "error: --evidence TEXT is required (or pass --diff to just inspect changes)" >&2
    exit 2
fi

if [[ "$accept" -eq 1 && "$assume_yes" -ne 1 ]]; then
    echo "error: --accept requires --yes (this publishes to the production OBS project)" >&2
    exit 2
fi

staging_rev="$revision"
if [[ -z "$staging_rev" ]]; then
    staging_rev=$(osc log "$STAGING_PRJ" "$PKG" | awk -F'|' '/^r[0-9]+ /{print $3; exit}' | xargs)
fi

version=$(osc cat "$STAGING_PRJ" "$PKG" "linuxtoys.spec" | grep -m1 -E '^Version:' | awk '{print $2}')

message="Promote linuxtoys from staging

Source revision: ${staging_rev}
Test evidence: ${evidence}"

echo "About to submit a request:"
echo "  ${STAGING_PRJ}/${PKG} (rev ${staging_rev}, version ${version}) -> ${PROD_PRJ}/${PKG}"
echo
echo "$message"
echo

if [[ "$assume_yes" -ne 1 ]]; then
    read -r -p "Create this submit request? [y/N] " reply
    case "$reply" in
        [yY]|[yY][eE][sS]) ;;
        *) echo "aborted"; exit 1 ;;
    esac
fi

osc_args=(submitrequest -m "$message")
if [[ -n "$revision" ]]; then
    osc_args+=(-r "$revision")
fi
osc_args+=("$STAGING_PRJ" "$PKG" "$PROD_PRJ" "$PKG")

req_output=$(osc "${osc_args[@]}")
echo "$req_output"

if [[ "$accept" -eq 1 ]]; then
    req_id=$(grep -oE '[0-9]+' <<<"$req_output" | head -1)
    if [[ -z "$req_id" ]]; then
        echo "error: could not parse request id from osc output; accept it manually with 'osc request accept <id>'" >&2
        exit 2
    fi
    echo "Accepting request $req_id ..."
    osc request accept -m "Promoted via promote-linuxtoys-staging.sh" "$req_id"
fi
