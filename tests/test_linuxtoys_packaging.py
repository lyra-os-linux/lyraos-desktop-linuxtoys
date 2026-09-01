from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LinuxtoysPackagingTests(unittest.TestCase):
    def test_self_update_paths_are_disabled_in_favor_of_rpm_zypper(self) -> None:
        spec = (ROOT / "linuxtoys.spec").read_text(encoding="utf-8")
        patch = (ROOT / "linuxtoys-disable-self-update.patch").read_text(
            encoding="utf-8"
        )
        self.assertIn("Requires:       git", spec)
        self.assertIn("upstream self-update bypasses RPM ownership", spec)
        self.assertIn("LinuxToys is managed by Lyra OS", patch)

    def test_auto_update_accepts_release_archives_with_a_top_level_directory(self) -> None:
        updater = (ROOT / "scripts/auto-update-linuxtoys.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('source_root="$WORKDIR/src"', updater)
        self.assertIn('source_root="${source_entries[0]}"', updater)
        self.assertIn('patch -p1 --dry-run -d "$source_root"', updater)


if __name__ == "__main__":
    unittest.main()
