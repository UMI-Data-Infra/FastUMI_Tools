import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fastumi_tools.system_info as system_info


class FirmwareStatusTests(unittest.TestCase):
    def test_extracts_camera_firmware_release(self):
        value = "V1.04P31||2085V5|V1.00|20260514_03|20260512_sync|680d76c"
        self.assertEqual(system_info.firmware_release(value), "20260514_03")
        self.assertIsNone(system_info.firmware_release("unknown"))

    def test_current_ros_activation_is_a_verified_reading(self):
        devices = [{"serial": "250801DR48FP26003241"}]
        ros = {"managed_driver_running": True}
        record = json.dumps({
            "MESSAGE": (
                "124012.37034 [info] Device version : "
                "V1.04P31||2085V5|V1.00|20260430_03|develop|8f39fd0 "
                "(SN=250801DR48FP26003241)"
            ),
            "__REALTIME_TIMESTAMP": "1786983733000000",
        })

        def command(argv, timeout=8):
            if argv[0] == "systemctl":
                return "Tue 2026-08-18 00:22:07 CST", None
            if argv[0] == "journalctl":
                return record, None
            return "", "unexpected command"

        with patch.object(system_info, "run_command", side_effect=command):
            system_info.merge_current_ros_journal_firmware(devices, ros)

        device = devices[0]
        self.assertEqual(device["firmware_release"], "20260430_03")
        self.assertEqual(device["firmware_source"], "ros_current_session")
        self.assertTrue(device["firmware_current_session"])
        self.assertTrue(device["firmware_verified"])
        self.assertIsNotNone(device["firmware_observed_at"])

    def test_inactive_ros_does_not_reuse_old_journal(self):
        devices = [{"serial": "250801DR48FP26003241"}]
        with patch.object(system_info, "run_command") as command:
            system_info.merge_current_ros_journal_firmware(
                devices, {"managed_driver_running": False},
            )
        command.assert_not_called()
        self.assertNotIn("firmware_version", devices[0])

    def test_post_flash_version_is_used_only_as_timestamped_verification(self):
        serial = "250801DR48FP26003241"
        with tempfile.TemporaryDirectory() as temporary:
            state_file = Path(temporary) / "state.json"
            state_file.write_text(json.dumps({
                "firmware": {
                    serial: {
                        "release": "20260514",
                        "actual_version": "V1.04|20260514_03|release",
                        "verified_at": "2026-08-18T01:00:00+08:00",
                    },
                },
            }), encoding="utf-8")
            devices = [{"serial": serial}]
            with patch.object(system_info, "STATE_FILE", state_file):
                system_info.merge_managed_firmware(devices)

        device = devices[0]
        self.assertEqual(device["firmware_release"], "20260514_03")
        self.assertEqual(device["firmware_source"], "post_flash_verification")
        self.assertFalse(device["firmware_current_session"])
        self.assertTrue(device["firmware_verified"])
        self.assertEqual(device["managed_firmware_release"], "20260514")

    def test_post_flash_verification_overrides_stale_startup_log(self):
        serial = "250801DR48FP26003241"
        with tempfile.TemporaryDirectory() as temporary:
            state_file = Path(temporary) / "state.json"
            state_file.write_text(json.dumps({
                "firmware": {
                    serial: {
                        "release": "20260514",
                        "actual_version": "V1.04|20260514_03|release",
                        "verified_at": "2026-08-18T09:42:43+08:00",
                    },
                },
            }), encoding="utf-8")
            devices = [{
                "serial": serial,
                "firmware_version": "V1.04|20260430_03|develop",
                "firmware_release": "20260430_03",
                "firmware_source": "historical_startup_log",
                "firmware_current_session": False,
                "firmware_verified": False,
            }]
            with patch.object(system_info, "STATE_FILE", state_file):
                system_info.merge_managed_firmware(devices)

        self.assertEqual(devices[0]["firmware_release"], "20260514_03")
        self.assertEqual(devices[0]["firmware_source"], "post_flash_verification")


if __name__ == "__main__":
    unittest.main()
