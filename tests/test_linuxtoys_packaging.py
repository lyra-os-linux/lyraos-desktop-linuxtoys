from __future__ import annotations

import unittest
import importlib.util
import io
import tarfile
import tempfile
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
        spec = importlib.util.spec_from_file_location("pipeline", ROOT / "scripts/auto_update_linuxtoys.py")
        pipeline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pipeline)
        for prefix in ("", "linuxtoys-6.7.2/"):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root / "release.tar.xz"
                with tarfile.open(archive, "w:xz") as tar:
                    # Direct-root releases contain more than one top-level entry.
                    for name in ("usr/bin/linuxtoys", "README"):
                        entry = tarfile.TarInfo(prefix + name)
                        entry.size = 7
                        tar.addfile(entry, io.BytesIO(b"fixture"))
                source = pipeline.release_source(archive, root / "src")
                self.assertEqual((source / "usr/bin/linuxtoys").read_bytes(), b"fixture")



if __name__ == "__main__":
    unittest.main()
