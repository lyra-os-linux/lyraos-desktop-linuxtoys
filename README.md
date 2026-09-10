# LinuxToys packaging

RPM packaging for [LinuxToys](https://linux.toys/), a third-party graphical
collection of Linux tools and configuration helpers, repackaged for the
signed Lyra OS OBS repositories.

- `_service`: OBS source service that fetches and checksum-verifies the
  upstream release tarball directly from GitHub;
- `linuxtoys.spec`: builds the package from the upstream tree with no
  compilation step, validates the desktop file, and fails the build if any
  upstream self-update path (`curl | sh`, `git pull`) survives patching;
- `linuxtoys-disable-self-update.patch` and `linuxtoys-update-self`: disable
  LinuxToys' own updater so updates flow exclusively through Zypper and the
  signed Lyra repository;
- `linuxtoys.changes`: RPM changelog.
- `scripts/check-linuxtoys-update.sh`: compares the packaged version against
  the latest psygreg/linuxtoys GitHub release (exit 1 when an update is
  available);
- `scripts/promote-linuxtoys-staging.sh`: creates (and optionally accepts) an
  OBS submit request promoting `linuxtoys` from
  `home:rodrigosbrito:lyra:staging` to `home:rodrigosbrito:lyra`;
- `scripts/auto-update-linuxtoys.sh`: unattended daily pipeline that bumps
  the package to a new upstream release, validates it locally, opens a PR,
  and publishes to OBS staging — see `scripts/systemd/` for the systemd user
  timer that runs it. Production is never touched automatically; promoting
  staging to production stays a manual step via
  `promote-linuxtoys-staging.sh`.

This package is not Lyra-authored application code; it is packaging metadata
only. Upstream license and source stay in the tarball fetched by `_service`.

## Resuming an interrupted automatic update

The shell entry point delegates to `scripts/auto_update_linuxtoys.py` (Python
3.12 or newer). Each run checks GitHub and OBS independently. An existing
release branch or PR is not proof that staging received the sources.

- A pushed branch is reused at its existing commit; the changelog and checksum
  are not regenerated. Missing PRs are created with a pending staging status.
- An existing open or merged PR is reused. Merged PRs can be recovered through
  their Git pull reference even when the branch was deleted and main already
  contains the new version. Closed, unmerged PRs stop automatic publication.
- Missing staging delivery reruns the checksum, patch, packaging contracts and
  full RPM build before publishing. Validation failures leave staging pending.
- Staging is compared against all five packaging inputs: `_service`, spec,
  changelog, patch and updater wrapper. Success is recorded in the PR only
  after a fresh OBS directory response matches their hashes. The status records
  the Git commit, OBS source revision and source digest. Human PR text is
  preserved; the old pipeline's premature success sentence is replaced.
- If the OBS commit succeeded but its response was lost, the next run detects
  the matching sources and only completes the GitHub status. Completed retries
  do not rebuild, push, create another PR or make another OBS commit.

`AUTO_UPDATE_WORKDIR` now holds a lock and disposable per-run directories;
the caller's directory is never erased. The lock serializes runs sharing that
directory. Separate hosts/workdirs still rely on normal Git/OBS conflict checks
and source verification, not a distributed lock. A delivery completed between
the initial check and OBS checkout is recognized without an empty commit.

The pipeline targets the latest stable numeric upstream release and refuses
to overwrite a newer main/staging version. Old pending PRs superseded by a
new upstream release require maintainer review. It does not change the manual
production promotion script or qualify OBS builds/RPM installation; a verified
source receipt is not a successful package build.

## Pipeline regression tests

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

The tests execute the real launcher against a local bare Git repository and
CLI fixtures for GitHub/OBS/downloads/patch/RPM build. They inject failures after
push and PR creation, before/during OBS delivery, after lost responses, and
during PR status updates. Retries verify unchanged commit identity, exact
received files and absence of duplicate writes. Additional cases cover merged
PRs with deleted branches, closed/changed PRs, remote query failures, changed
tarballs, wrong OBS files, a newer staging version and concurrent completion.
Archive extraction and the packaging contracts run for real. These tests
qualify orchestration; they do not contact production services or build an
upstream RPM. The pipeline itself still requires a real RPM build before any
missing source publication.

The baseline at `d725f13621de26b37c9fe7ce67eb1e883f0798dd` was also exercised:
after losing the PR creation response, a second run exited successfully while
staging retained the old version and the PR already claimed publication.
The corrected launcher delivered the missing files once and then updated the PR.

## Credits

LinuxToys is developed by [psygreg](https://github.com/psygreg) and made
possible by its community of contributors. For the full list of authors and
acknowledgements, see the upstream
[Credits page](https://linux.toys/credits.html).
