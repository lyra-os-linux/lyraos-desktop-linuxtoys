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

## Credits

LinuxToys is developed by [psygreg](https://github.com/psygreg) and made
possible by its community of contributors. For the full list of authors and
acknowledgements, see the upstream
[Credits page](https://linux.toys/credits.html).
