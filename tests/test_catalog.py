import json
import tempfile
import unittest
from pathlib import Path

from fastumi_tools import __version__
from fastumi_tools.catalog import Catalog, CatalogError, sha256_file


class CatalogTests(unittest.TestCase):
    def make_catalog(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "sdk").mkdir()
        artifact = root / "sdk" / "sample.deb"
        artifact.write_bytes(b"sample-sdk")
        manifest = {
            "schema_version": 1,
            "catalog_version": "test",
            "sdk": [{
                "id": "sample-focal", "path": "sdk/sample.deb",
                "sha256": sha256_file(artifact), "os_codenames": ["focal"],
            }],
            "firmware": [],
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return temporary, Catalog(root, {"VERSION_CODENAME": "focal", "PRETTY_NAME": "Ubuntu Test"})

    def test_resolve_verified_artifact(self):
        temporary, catalog = self.make_catalog()
        self.addCleanup(temporary.cleanup)
        item, path = catalog.resolve("sdk", "sample-focal")
        self.assertEqual(item["id"], "sample-focal")
        self.assertEqual(path.name, "sample.deb")

    def test_hash_mismatch_is_rejected(self):
        temporary, catalog = self.make_catalog()
        self.addCleanup(temporary.cleanup)
        (catalog.root / "sdk" / "sample.deb").write_bytes(b"changed")
        with self.assertRaises(CatalogError):
            catalog.resolve("sdk", "sample-focal")

    def test_wrong_os_is_rejected(self):
        temporary, catalog = self.make_catalog()
        self.addCleanup(temporary.cleanup)
        catalog.os_release["VERSION_CODENAME"] = "jammy"
        with self.assertRaises(CatalogError):
            catalog.resolve("sdk", "sample-focal")

    def test_unsupported_host_disables_catalog(self):
        temporary, catalog = self.make_catalog()
        self.addCleanup(temporary.cleanup)
        catalog.os_release["VERSION_CODENAME"] = "jammy"
        self.assertFalse(catalog.public()["host_supported"])
        self.assertFalse(catalog.items("sdk")[0]["compatible"])

    def test_public_project_version_comes_from_application(self):
        temporary, catalog = self.make_catalog()
        self.addCleanup(temporary.cleanup)
        self.assertEqual(catalog.public()["project_version"], __version__)

    def test_path_escape_is_rejected(self):
        temporary, catalog = self.make_catalog()
        self.addCleanup(temporary.cleanup)
        catalog.data["sdk"][0]["path"] = "../outside.deb"
        with self.assertRaises(CatalogError):
            catalog.items("sdk")


if __name__ == "__main__":
    unittest.main()
