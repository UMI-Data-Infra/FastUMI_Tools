import json
import tempfile
import unittest
from pathlib import Path

from fastumi_tools.catalog import Catalog
from fastumi_tools.operations import OperationError, OperationManager


class OperationValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        manifest = {
            "schema_version": 1, "catalog_version": "test",
            "sdk": [], "firmware": [],
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.manager = OperationManager(Catalog(root, {"VERSION_CODENAME": "focal"}))

    def tearDown(self):
        self.temporary.cleanup()

    def test_serial_validation(self):
        self.assertEqual(self.manager.validated_serial({"serial": "250801DR48FP25002993"}), "250801DR48FP25002993")
        with self.assertRaises(OperationError):
            self.manager.validated_serial({"serial": "bad; reboot"})

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(OperationError):
            self.manager.start("shell", {"command": "id"})


if __name__ == "__main__":
    unittest.main()
