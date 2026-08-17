import json
import os
import pwd
import tempfile
import unittest
from pathlib import Path

from fastumi_tools.catalog import Catalog
from fastumi_tools.operations import (
    OperationError,
    OperationManager,
    desktop_environment,
    firmware_update_error,
)


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

    def test_desktop_environment_uses_real_session_display(self):
        root = Path(self.temporary.name) / "proc"
        process = root / "123"
        process.mkdir(parents=True)
        (process / "comm").write_text("gnome-shell\n", encoding="utf-8")
        (process / "environ").write_bytes(
            b"DISPLAY=:1\0XAUTHORITY=/run/user/1000/gdm/Xauthority\0"
            b"XDG_RUNTIME_DIR=/run/user/1000\0DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus\0"
        )
        account = pwd.struct_passwd(("operator", "x", os.getuid(), os.getgid(), "", "/home/operator", "/bin/bash"))
        environment = desktop_environment(account, proc_root=root)
        self.assertEqual(environment["DISPLAY"], ":1")
        self.assertEqual(environment["XAUTHORITY"], "/run/user/1000/gdm/Xauthority")

    def test_firmware_updater_false_success_is_rejected(self):
        output = "don't found any xvisio hid device\nplease check if you has plun in a xvisio usb device"
        self.assertIsNotNone(firmware_update_error(output))
        self.assertIsNone(firmware_update_error("update completed successfully"))


if __name__ == "__main__":
    unittest.main()
