import json
import os
import pwd
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastumi_tools.catalog import Catalog
import fastumi_tools.operations as operations
from fastumi_tools.operations import (
    OperationError,
    OperationManager,
    RVIZ_VIEWS,
    TOPICS,
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

    def test_topic_registry_matches_pc_b_ros_interfaces(self):
        self.assertNotIn("slam-visual-pose", TOPICS)
        self.assertEqual(TOPICS["slam-pose"]["type"], "xv_sdk/PoseStampedConfidence")
        self.assertEqual(TOPICS["slam-trajectory"], {
            "suffix": "slam/trajectory", "type": "nav_msgs/Path", "probe": "hz",
        })
        for name in ("fisheye-left", "fisheye-left2", "fisheye-right", "fisheye-right2"):
            self.assertTrue(TOPICS[name]["suffix"].endswith("/image"))
            self.assertEqual(TOPICS[name]["type"], "sensor_msgs/Image")

    def test_sample_topic_timeout_is_reported_as_inactive_stream(self):
        self.manager.current["locale"] = "en"
        completed = subprocess.CompletedProcess([], 0, "")
        with patch.object(self.manager, "gui_command", side_effect=lambda command: command), \
                patch.object(self.manager, "run", return_value=completed.stdout) as run:
            with self.assertRaisesRegex(OperationError, "stream is inactive or unsupported"):
                self.manager.check_topic({
                    "serial": "250801DR48FP25002993", "metric": "clamp",
                })
        self.assertIn("probe_status -eq 124", run.call_args.args[0][-1])

    def test_slam_is_the_only_slam_rviz_entry(self):
        self.assertIn("slam", RVIZ_VIEWS)
        self.assertNotIn("trajectory", RVIZ_VIEWS)

    def test_generated_slam_view_uses_native_path_and_standard_pose(self):
        generated = Path(self.temporary.name) / "generated"
        with patch.object(operations, "GENERATED_DIR", generated):
            output = self.manager.generate_rviz("250801DR48FP25002993")
        content = (output / "slam_visualization.rviz").read_text(encoding="utf-8")
        self.assertIn("/slam/trajectory", content)
        self.assertIn("/slam/current_pose", content)
        self.assertIn("250801DR48FP25002993/map_optical_frame", content)

    def test_operation_locale_is_recorded(self):
        self.manager.current["locale"] = "en"
        self.assertEqual(self.manager.tr("中文", "English"), "English")
        self.manager.current["locale"] = "zh-CN"
        self.assertEqual(self.manager.tr("中文", "English"), "中文")

    def test_calibration_pid_readiness_rejects_stale_process(self):
        pid_file = Path(self.temporary.name) / "tool.pid"
        pid_file.write_text(str(os.getpid()), encoding="ascii")
        self.assertTrue(self.manager.process_from_pid_file_alive(pid_file))
        pid_file.write_text("99999999", encoding="ascii")
        self.assertFalse(self.manager.process_from_pid_file_alive(pid_file))

    def test_ros_node_health_uses_live_ping(self):
        with patch("fastumi_tools.operations.subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertTrue(self.manager.ros_driver_node_online())
            run.return_value.returncode = 1
            self.assertFalse(self.manager.ros_driver_node_online())

    def test_desktop_runtime_directory_is_shared_outside_private_tmp(self):
        runtime = Path(self.temporary.name) / "runtime"
        account = pwd.getpwuid(os.getuid())
        with patch.object(operations, "RUNTIME_DIR", runtime):
            directory = self.manager.desktop_runtime_directory("preview", account)
        self.assertEqual(directory.parent, runtime)
        self.assertTrue(directory.name.startswith("preview-"))
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)

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
