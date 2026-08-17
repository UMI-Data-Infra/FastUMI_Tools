import json
import unittest
from pathlib import Path

from fastumi_tools import __version__


class VersionTests(unittest.TestCase):
    def test_runtime_version_matches_version_file(self):
        project_root = Path(__file__).resolve().parents[1]
        recorded = (project_root / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(__version__, recorded)

    def test_payload_manifest_does_not_duplicate_project_version(self):
        project_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (project_root / "payloads" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("project_version", manifest)


if __name__ == "__main__":
    unittest.main()
