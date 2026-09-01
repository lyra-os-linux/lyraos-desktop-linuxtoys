#!/usr/bin/env bash
# Daily unattended LinuxToys packaging pipeline, up to OBS staging.
#
# 1. Checks the latest psygreg/linuxtoys GitHub release against the version
#    packaged in linuxtoys.spec (main branch of the GitHub repo).
# 2. If a newer release exists: downloads and checksums the tarball, verifies
#    the self-update-disable patch still applies, regenerates _service /
#    linuxtoys.spec / linuxtoys.changes, validates with the contract test and
#    a full local RPM build, then commits to a new branch, pushes it, and
#    opens a GitHub PR.
# 3. Publishes the same bump to the OBS staging project
#    (home:rodrigosbrito:lyra:staging/linuxtoys) via `osc commit`.
#
# Production (home:rodrigosbrito:lyra) is never touched by this script;
# promoting staging -> production stays a deliberate manual step via
# scripts/promote-linuxtoys-staging.sh.
#
# Runs against an isolated clone/checkout under $WORKDIR, never the
# maintainer's own working tree, so it is safe to run unattended even while
# that tree has work in progress.
#
# Exit codes: 0 = up to date or successfully published to staging;
#             1 = reserved (unused, kept aligned with check-linuxtoys-update.sh);
#             2 = error - see stderr/log.
set -euo pipefail

GH_REPO="lyra-os-linux/lyraos-desktop-linuxtoys"
UPSTREAM_REPO="psygreg/linuxtoys"
STAGING_PRJ="home:rodrigosbrito:lyra:staging"
PKG="linuxtoys"
CHANGELOG_AUTHOR="Lyra OS Release <rodrigo@lyraos.com.br>"

WORKDIR="${AUTO_UPDATE_WORKDIR:-/tmp/lyra-linuxtoys-auto-update}"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }
fail() { echo "error: $*" >&2; exit 2; }

for tool in git gh osc curl python3 rpmbuild sha256sum patch; do
    command -v "$tool" >/dev/null 2>&1 || fail "required tool '$tool' not found"
done

rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"

log "cloning $GH_REPO"
gh repo clone "$GH_REPO" "$WORKDIR/repo" -- --quiet
REPO="$WORKDIR/repo"
cd "$REPO"
git config user.email "rodrigo@lyraos.com.br"
git config user.name "Lyra OS Release"

current_version=$(grep -m1 -E '^Version:' linuxtoys.spec | awk '{print $2}')
[[ -n "$current_version" ]] || fail "could not read Version from linuxtoys.spec"

log "packaged version: $current_version"

release_json="$WORKDIR/release.json"
http_code=$(curl -sS -w '%{http_code}' -o "$release_json" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${UPSTREAM_REPO}/releases/latest")
[[ "$http_code" == "200" ]] || fail "GitHub API request failed (HTTP $http_code)"

latest_version=$(python3 -c 'import json;print(json.load(open("'"$release_json"'"))["tag_name"].lstrip("v"))')
[[ -n "$latest_version" ]] || fail "could not parse latest release tag"

log "latest upstream: $latest_version"

if [[ "$current_version" == "$latest_version" ]]; then
    log "up to date, nothing to do"
    exit 0
fi

branch="packaging/linuxtoys-${latest_version}"
if git ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
    log "branch $branch already exists on origin; a previous run likely already handled this version, skipping"
    exit 0
fi
if gh pr list --repo "$GH_REPO" --state all --search "\"Update LinuxToys to ${latest_version}\" in:title" --json number --jq 'length' | grep -qv '^0$'; then
    log "a PR for ${latest_version} already exists; skipping"
    exit 0
fi

log "update available: ${current_version} -> ${latest_version}"

tarball="linuxtoys-${latest_version}.tar.xz"
tarball_url="https://github.com/${UPSTREAM_REPO}/releases/download/${latest_version}/${tarball}"
curl -sSL -o "$WORKDIR/$tarball" "$tarball_url"
checksum=$(sha256sum "$WORKDIR/$tarball" | awk '{print $1}')

rm -rf "$WORKDIR/src" && mkdir -p "$WORKDIR/src"
tar xf "$WORKDIR/$tarball" -C "$WORKDIR/src"
source_root="$WORKDIR/src"
mapfile -d '' source_entries < <(
    find "$source_root" -mindepth 1 -maxdepth 1 -print0
)
if [[ "${#source_entries[@]}" -eq 1 && -d "${source_entries[0]}" ]]; then
    source_root="${source_entries[0]}"
fi
if ! patch -p1 --dry-run -d "$source_root" < "$REPO/linuxtoys-disable-self-update.patch" >/dev/null 2>&1; then
    fail "self-update-disable patch no longer applies cleanly against ${latest_version}; needs manual rebasing, not auto-publishing this version"
fi

git checkout -b "$branch"

cat > _service <<EOF
<services>
  <service name="download_url">
    <param name="protocol">https</param>
    <param name="host">github.com</param>
    <param name="path">/${UPSTREAM_REPO}/releases/download/${latest_version}/${tarball}</param>
  </service>
  <service name="verify_file">
    <param name="file">_service:download_url:${tarball}</param>
    <param name="verifier">sha256</param>
    <param name="checksum">${checksum}</param>
  </service>
</services>
EOF

sed -i -E "s/^(Version:[[:space:]]+).*/\1${latest_version}/" linuxtoys.spec

change_date=$(LC_TIME=C date -u +"%a %b %e %H:%M:%S UTC %Y")
bullets_file="$WORKDIR/bullets.txt"
python3 - "$release_json" "$bullets_file" <<'PYEOF'
import json, re, sys, textwrap

release_path, out_path = sys.argv[1], sys.argv[2]
body = json.load(open(release_path)).get("body", "") or ""

lines = []
for raw in body.splitlines():
    line = raw.strip()
    if not line.startswith(("- ", "* ")):
        continue
    text = line[2:].strip()
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.*?)\*", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    if text:
        lines.append(text)

with open(out_path, "w") as f:
    for text in lines:
        wrapped = textwrap.wrap(
            text, width=75, initial_indent="  * ", subsequent_indent="    "
        )
        f.write("\n".join(wrapped) + "\n")
PYEOF

{
    echo "-------------------------------------------------------------------"
    echo "${change_date} - ${CHANGELOG_AUTHOR}"
    echo
    echo "- Update to upstream LinuxToys ${latest_version}:"
    cat "$bullets_file"
    echo "- Keep upstream self-update paths disabled in favor of signed RPM updates."
    echo
    cat linuxtoys.changes
} > "$WORKDIR/changes.new"
mv "$WORKDIR/changes.new" linuxtoys.changes

log "running packaging contract test"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v

log "running full local RPM build"
RPMTOP="$WORKDIR/rpmbuild"
mkdir -p "$RPMTOP"/{SOURCES,SPECS,BUILD,RPMS,SRPMS,BUILDROOT}
cp "$WORKDIR/$tarball" "$RPMTOP/SOURCES/"
cp linuxtoys-update-self linuxtoys-disable-self-update.patch "$RPMTOP/SOURCES/"
cp linuxtoys.spec "$RPMTOP/SPECS/"
rpmbuild --define "_topdir $RPMTOP" -bb "$RPMTOP/SPECS/linuxtoys.spec"

log "local validation passed, committing"
git add _service linuxtoys.changes linuxtoys.spec
git commit -m "packaging: update LinuxToys to ${latest_version}

Automated daily update check found upstream release ${latest_version}.
Verified tarball checksum, confirmed the self-update-disable patch still
applies, and validated with the packaging contract test plus a full
local RPM build before publishing.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
git push -u origin "$branch"

pr_url=$(gh pr create --repo "$GH_REPO" \
    --title "Update LinuxToys to ${latest_version}" \
    --body "Automated update: LinuxToys ${current_version} -> ${latest_version}.

Verified tarball checksum, confirmed the self-update-disable patch still applies cleanly, and validated locally (packaging contract test + full RPM build with the \`%check\` gate).

Also published to OBS staging (\`${STAGING_PRJ}/${PKG}\`) by this same run. Promotion to production stays manual via \`scripts/promote-linuxtoys-staging.sh\`.

Release notes: https://github.com/${UPSTREAM_REPO}/releases/tag/${latest_version}")
log "opened PR: $pr_url"

log "publishing to OBS staging"
osc co "$STAGING_PRJ" "$PKG" -o "$WORKDIR/obs-staging"
cp _service linuxtoys.spec linuxtoys.changes "$WORKDIR/obs-staging/"
(
    cd "$WORKDIR/obs-staging"
    osc commit -m "Update LinuxToys to ${latest_version} (automated)"
)

log "done: ${latest_version} published to ${STAGING_PRJ}/${PKG}, PR: $pr_url"
