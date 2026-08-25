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

    def test_package_update_restarts_running_web_service(self):
        project_root = Path(__file__).resolve().parents[1]
        postinst = (project_root / "packaging" / "postinst").read_text(encoding="utf-8")
        self.assertIn("systemctl restart fastumi-tools.service", postinst)

    def test_proprietary_license_and_contact_are_packaged(self):
        project_root = Path(__file__).resolve().parents[1]
        license_text = (project_root / "LICENSE").read_text(encoding="utf-8")
        control = (project_root / "packaging/control.in").read_text(encoding="utf-8")
        build = (project_root / "scripts/build_deb.sh").read_text(encoding="utf-8")
        self.assertIn("Copyright © 2026 FastUMI Team. All rights reserved.", license_text)
        self.assertIn("must not", license_text)
        self.assertIn("yding25@binghamton.edu", license_text)
        self.assertIn("FastUMI Team <yding25@binghamton.edu>", control)
        self.assertIn("/usr/share/doc/fastumi-tools/copyright", build)

    def test_apt_install_includes_runtime_and_resource_dependencies(self):
        project_root = Path(__file__).resolve().parents[1]
        control = (project_root / "packaging/control.in").read_text(encoding="utf-8")
        for package in ("dfu-util", "python3-opencv", "python3-numpy"):
            self.assertIn(package, control)
        self.assertIn("fastumi-tools-resources", control)
        self.assertTrue((project_root / "scripts/build_resources_deb.sh").is_file())
        probe_build = (project_root / "scripts/build_probe_helpers.sh").read_text(encoding="utf-8")
        config = (project_root / "packaging/fastumi-tools.conf").read_text(encoding="utf-8")
        self.assertIn("*.so*", probe_build)
        self.assertIn("xvsdk_version", probe_build)
        self.assertIn("FASTUMI_AUTO_DEVICE_PROBE=1", config)
        self.assertTrue((project_root / ".github/workflows/release.yml").is_file())


if __name__ == "__main__":
    unittest.main()
