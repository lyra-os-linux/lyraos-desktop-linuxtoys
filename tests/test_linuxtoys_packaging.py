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


if __name__ == "__main__":
    unittest.main()
