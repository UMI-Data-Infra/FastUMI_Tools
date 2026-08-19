import json
import os
import pwd
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from fastumi_tools.catalog import Catalog
import fastumi_tools.operations as operations
from fastumi_tools.operations import (
    OperationError,
    OperationManager,
    RVIZ_VIEWS,
    TOPICS,
    desktop_environment,
    firmware_update_error,
    firmware_update_succeeded,
    firmware_version_from_probe,
    hidraw_nodes_for_usb,
    usb_hid_interfaces,
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

    def test_sdk_install_rejects_generation_mismatch_before_system_changes(self):
        item = {
            "id": "gen2-sdk", "label": "2026-05-08", "release": "20260508",
            "camera_generation": "gen2",
        }
        with patch.object(self.manager, "require_root"), \
                patch.object(self.manager.catalog, "resolve", return_value=(item, Path("/tmp/gen2.deb"))), \
                patch.object(self.manager, "install_rule") as install_rule:
            with self.assertRaisesRegex(OperationError, "代际"):
                self.manager.install_sdk({
                    "artifact_id": "gen2-sdk", "camera_generation": "gen1",
                })
        install_rule.assert_not_called()

    def test_gen2_firmware_is_rejected_before_preflight(self):
        item = {
            "id": "gen1-firmware", "label": "Gen 1", "release": "20260514",
            "camera_generation": "gen1",
        }
        with patch.object(self.manager, "require_root"), \
                patch.object(self.manager.catalog, "resolve", return_value=(item, Path("/tmp/gen2.zip"))):
            with self.assertRaisesRegex(OperationError, "Windows"):
                self.manager.prepare_firmware_flash(
                    {"camera_generation": "gen2"}, require_acknowledgement=False,
                )

    def test_operation_snapshot_reports_managed_preview(self):
        process = Mock()
        process.poll.return_value = None
        self.manager.gui_processes["camera-preview"] = process
        self.assertTrue(self.manager.snapshot()["preview_running"])

        process.poll.return_value = 0
        self.assertFalse(self.manager.snapshot()["preview_running"])
        self.assertNotIn("camera-preview", self.manager.gui_processes)

    def test_web_operation_closes_managed_preview_process_group(self):
        process = Mock(pid=4321)
        process.poll.return_value = None
        self.manager.gui_processes["camera-preview"] = process
        with patch("fastumi_tools.operations.os.killpg") as killpg:
            self.manager.stop_camera_preview({})
        killpg.assert_called_once_with(4321, operations.signal.SIGTERM)
        process.wait.assert_called_once_with(timeout=3)
        self.assertNotIn("camera-preview", self.manager.gui_processes)
        self.assertIn("预览窗口已关闭", "\n".join(self.manager.current["logs"]))

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
        self.assertFalse(firmware_update_succeeded(output))
        self.assertFalse(firmware_update_succeeded("update completed successfully"))

    def test_firmware_updater_requires_positive_dfu_completion(self):
        output = "About to run dfu-util for downloading...\nDownload done.\nFile downloaded successfully"
        self.assertIsNone(firmware_update_error(output))
        self.assertTrue(firmware_update_succeeded(output))
        self.assertEqual(firmware_update_error("request switch-mode fail"), "mode_switch_failed")
        self.assertEqual(firmware_update_error("Error during download"), "dfu_failed")

    def test_firmware_probe_extracts_actual_device_version(self):
        output = (
            "noise\nXVISION_PROBE_JSON_BEGIN\n"
            '{"devices":[{"serial":"250801DR48FP25002993","info":'
            '{"Version":"V1.04|20260514_03|release"}}]}\n'
            "XVISION_PROBE_JSON_END\n"
        )
        self.assertEqual(
            firmware_version_from_probe(output, "250801DR48FP25002993"),
            "V1.04|20260514_03|release",
        )
        self.assertIsNone(firmware_version_from_probe(output, "another-camera"))

    def test_hid_preflight_maps_only_target_usb_device(self):
        root = Path(self.temporary.name)
        sys_root = root / "sys"
        dev_root = root / "dev"
        usb_target = sys_root / "devices/pci/usb2/2-2"
        interface = usb_target / "2-2:1.3"
        interface.mkdir(parents=True)
        (interface / "bInterfaceClass").write_text("03\n", encoding="ascii")
        usb_bus = sys_root / "bus/usb/devices"
        usb_bus.mkdir(parents=True)
        (usb_bus / "2-2").symlink_to(usb_target)
        (usb_bus / "2-2:1.3").symlink_to(interface)
        hid_device = interface / "0003:040E:F408.0001"
        hid_device.mkdir()
        hid_class = sys_root / "class/hidraw/hidraw9"
        hid_class.mkdir(parents=True)
        (hid_class / "device").symlink_to(hid_device)
        dev_root.mkdir()
        (dev_root / "hidraw9").touch()
        self.assertEqual(usb_hid_interfaces("2-2", usb_bus), [usb_bus / "2-2:1.3"])
        self.assertEqual(hidraw_nodes_for_usb("2-2", sys_root, dev_root), [dev_root / "hidraw9"])

    def test_dfu_runtime_is_checked_before_hid_mode_switch(self):
        item = {
            "id": "firmware-pmdtof-20260514", "label": "2026-05-14",
            "release": "20260514", "minimum_sdk_release": "20260522",
        }
        archive = Path(self.temporary.name) / "firmware.zip"
        archive.touch()
        device = {
            "serial": "250801DR48FP26003241", "usb_product_id": "f408",
            "usb_generation": "USB 3.x", "in_use_by": [],
        }
        events = []

        def run(argv, timeout=600, env=None):
            events.append(("run", list(argv)))
            return "dfu-util 0.9"

        def bind(target):
            events.append(("bind", target["serial"]))

        with patch.object(self.manager.catalog, "resolve", return_value=(item, archive)), \
                patch.object(operations, "get_usb_devices", return_value=[device]), \
                patch.object(operations, "usb_devices_in_use", side_effect=lambda value: value), \
                patch.object(self.manager, "require_root"), \
                patch.object(self.manager, "require_firmware_sdk"), \
                patch.object(self.manager, "stop_sources_for_firmware"), \
                patch.object(self.manager, "wait_for_released_firmware_device", return_value=device), \
                patch.object(self.manager, "install_rule"), \
                patch.object(self.manager, "bind_firmware_hid", side_effect=bind), \
                patch.object(self.manager, "run", side_effect=run), \
                patch("fastumi_tools.operations.shutil.which", return_value="/usr/bin/dfu-util"):
            self.manager.prepare_firmware_flash({
                "artifact_id": item["id"], "acknowledged": True,
                "serial_confirmation": device["serial"],
            }, require_acknowledgement=True)

        self.assertEqual(events[0], ("run", ["dfu-util", "-l"]))
        self.assertEqual(events[1], ("bind", device["serial"]))

    def test_main_firmware_waits_for_stable_loader_reenumeration(self):
        clock = [0.0]

        def monotonic():
            return clock[0]

        def sleep(seconds):
            clock[0] += seconds

        def devices():
            devnum = "012" if clock[0] < 2 else "013"
            return [{
                "serial": "0.0", "usb_product_id": "f003", "usb_path": "1-9",
                "usb_device_node": "/dev/bus/usb/001/%s" % devnum,
            }]

        probe = subprocess.CompletedProcess(
            ["dfu-util", "-l"], 0, "Found DFU: [040e:f003]", "",
        )
        with patch.object(operations.time, "monotonic", side_effect=monotonic), \
                patch.object(operations.time, "sleep", side_effect=sleep), \
                patch.object(operations, "get_usb_devices", side_effect=devices), \
                patch("fastumi_tools.operations.subprocess.run", return_value=probe):
            result = self.manager.ensure_next_firmware_stage_ready(
                "250801DR48FP26003241", timeout=12,
            )

        self.assertEqual(result["usb_device_node"], "/dev/bus/usb/001/013")
        self.assertGreaterEqual(clock[0], 5)

    def test_firmware_success_is_recorded_only_with_actual_version(self):
        root = Path(self.temporary.name)
        archive = root / "firmware.zip"
        with zipfile.ZipFile(str(archive), "w") as bundle:
            bundle.writestr("firmware/LumosFastUMIUpdateImg", b"updater")
            bundle.writestr("firmware/usbLoader.img", b"loader")
            bundle.writestr("firmware/framework.img", b"image-20260514_03")
        item = {
            "id": "firmware-pmdtof-20260514", "label": "2026-05-14",
            "release": "20260514", "minimum_sdk_release": "20260522",
        }
        device = {
            "serial": "250801DR48FP25002993", "usb_product_id": "f408",
            "usb_generation": "USB 3.x", "in_use_by": [],
        }
        successful_dfu = "About to run dfu-util for downloading...\nDownload done."
        state_file = root / "state.json"
        with patch.object(self.manager.catalog, "resolve", return_value=(item, archive)), \
                patch.object(operations, "get_usb_devices", return_value=[device]), \
                patch.object(operations, "usb_devices_in_use", side_effect=lambda value: value), \
                patch.object(operations, "STATE_DIR", root), \
                patch.object(operations, "STATE_FILE", state_file), \
                patch.object(self.manager, "require_root"), \
                patch.object(self.manager, "require_firmware_sdk"), \
                patch.object(self.manager, "stop_sources_for_firmware"), \
                patch.object(self.manager, "wait_for_released_firmware_device", return_value=device), \
                patch.object(self.manager, "install_rule"), \
                patch.object(self.manager, "bind_firmware_hid"), \
                patch.object(self.manager, "ensure_next_firmware_stage_ready"), \
                patch.object(self.manager, "wait_for_normal_firmware_device"), \
                patch.object(self.manager, "probe_firmware_version", return_value="V1.04|20260514_03|release"), \
                patch.object(self.manager, "restore_uvc"), \
                patch.object(self.manager, "run", return_value=successful_dfu), \
                patch("fastumi_tools.operations.shutil.which", return_value="/usr/bin/dfu-util"):
            self.manager.flash_firmware({
                "artifact_id": item["id"], "acknowledged": True,
                "serial_confirmation": device["serial"],
            })
        state = json.loads(state_file.read_text(encoding="utf-8"))
        recorded = state["firmware"][device["serial"]]
        self.assertEqual(recorded["release"], "20260514")
        self.assertEqual(recorded["actual_version"], "V1.04|20260514_03|release")
        self.assertIn("verified_at", recorded)

    def test_firmware_version_mismatch_never_updates_success_state(self):
        root = Path(self.temporary.name)
        archive = root / "firmware-mismatch.zip"
        with zipfile.ZipFile(str(archive), "w") as bundle:
            bundle.writestr("firmware/LumosFastUMIUpdateImg", b"updater")
            bundle.writestr("firmware/usbLoader.img", b"loader")
            bundle.writestr("firmware/framework.img", b"image-20260514_03")
        item = {"id": "firmware-pmdtof-20260514", "label": "2026-05-14", "release": "20260514"}
        serial = "250801DR48FP25002993"
        successful_dfu = "Download done."
        state_file = root / "mismatch-state.json"
        with patch.object(
                    self.manager, "prepare_firmware_flash",
                    return_value=(item, archive, {}, serial),
                ), patch.object(self.manager, "run", return_value=successful_dfu), \
                patch.object(self.manager, "ensure_next_firmware_stage_ready"), \
                patch.object(self.manager, "wait_for_normal_firmware_device"), \
                patch.object(self.manager, "probe_firmware_version", return_value="V1.04|20260430_03|release"), \
                patch.object(operations, "STATE_DIR", root), \
                patch.object(operations, "STATE_FILE", state_file):
            with self.assertRaisesRegex(OperationError, "写后验真失败"):
                self.manager.flash_firmware({})
        self.assertFalse(state_file.exists())

    def test_service_sandbox_and_udev_rule_allow_hid_preflight(self):
        root = Path(__file__).resolve().parents[1]
        unit = (root / "packaging/systemd/fastumi-tools.service").read_text(encoding="utf-8")
        rule = (root / "payloads/firmware/99-LumosVisio.rules").read_text(encoding="utf-8")
        self.assertIn("/sys/bus/usb/drivers/usbhid", unit)
        self.assertIn('/sys/bus/usb/drivers/usbfs', unit)
        self.assertIn("AF_NETLINK", unit)
        self.assertIn('KERNEL=="hidraw*"', rule)


if __name__ == "__main__":
    unittest.main()
