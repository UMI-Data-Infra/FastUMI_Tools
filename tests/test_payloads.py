import unittest
from pathlib import Path

from fastumi_tools.catalog import Catalog, sha256_file


class ProductionPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / "payloads"
        cls.catalog = Catalog(root, {"VERSION_CODENAME": "focal", "PRETTY_NAME": "Ubuntu 20.04"})

    def test_all_payload_files_exist_and_match_hash(self):
        for kind in ("sdk", "firmware"):
            for raw in self.catalog.data[kind]:
                path = self.catalog.root / raw["path"]
                self.assertTrue(path.is_file())
                self.assertEqual(sha256_file(path), raw["sha256"])

    def test_one_recommended_release_per_supported_os(self):
        for codename in ("focal",):
            items = [item for item in self.catalog.data["sdk"] if codename in item["os_codenames"]]
            self.assertEqual(sum(bool(item.get("recommended")) for item in items), 1)

    def test_only_ubuntu_2004_is_declared(self):
        declared = {
            codename
            for item in self.catalog.data["sdk"]
            for codename in item["os_codenames"]
        }
        self.assertEqual(declared, {"focal"})

    def test_ubuntu_2204_cannot_manage_sdk_or_firmware(self):
        unsupported = Catalog(
            self.catalog.root,
            {"VERSION_CODENAME": "jammy", "PRETTY_NAME": "Ubuntu 22.04"},
        )
        for kind in ("sdk", "firmware"):
            self.assertTrue(unsupported.items(kind))
            self.assertTrue(all(not item["compatible"] for item in unsupported.items(kind)))


if __name__ == "__main__":
    unittest.main()
