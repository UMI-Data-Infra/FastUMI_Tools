"""Validated, auditable operations exposed by the local web application."""

from __future__ import annotations

import json
import os
import pwd
import re
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .catalog import Catalog, CatalogError
from .system_info import (
    APP_ROOT,
    SERIAL_PATTERN,
    get_usb_devices,
    sdk_information,
    sdk_probe_path,
    usb_devices_in_use,
    video_devices,
)


STATE_DIR = Path(os.environ.get("FASTUMI_STATE_DIR", "/var/lib/fastumi-tools"))
STATE_FILE = STATE_DIR / "state.json"
HISTORY_FILE = STATE_DIR / "history.jsonl"
GENERATED_DIR = STATE_DIR / "generated"
GUI_LOG_DIR = STATE_DIR / "gui"
RUNTIME_DIR = STATE_DIR / "runtime"
ROS_DRIVER_UNIT = "fastumi-ros-driver.service"
NORMAL_USB_PRODUCT_ID = "f408"
DFU_USB_PRODUCT_ID = "f003"
ROS_LOCAL_ENV = (
    "export ROS_MASTER_URI=http://127.0.0.1:11311; "
    "export ROS_IP=127.0.0.1; unset ROS_HOSTNAME; "
)

TOPICS = {
    "slam-pose": {"suffix": "slam/pose", "type": "xv_sdk/PoseStampedConfidence", "probe": "hz"},
    "slam-trajectory": {"suffix": "slam/trajectory", "type": "nav_msgs/Path", "probe": "hz"},
    "rgb": {"suffix": "color_camera/image", "type": "sensor_msgs/Image", "probe": "hz"},
    "rgbd": {"suffix": "rgbd_camera/image", "type": "sensor_msgs/Image", "probe": "hz"},
    "fisheye-left": {"suffix": "fisheye_cameras/left/image", "type": "sensor_msgs/Image", "probe": "hz"},
    "fisheye-left2": {"suffix": "fisheye_cameras/left2/image", "type": "sensor_msgs/Image", "probe": "hz"},
    "fisheye-right": {"suffix": "fisheye_cameras/right/image", "type": "sensor_msgs/Image", "probe": "hz"},
    "fisheye-right2": {"suffix": "fisheye_cameras/right2/image", "type": "sensor_msgs/Image", "probe": "hz"},
    "tof": {"suffix": "tof_camera/image", "type": "sensor_msgs/Image", "probe": "hz"},
    "imu": {"suffix": "imu_sensor/data_raw", "type": "sensor_msgs/Imu", "probe": "hz"},
    "clamp": {"suffix": "clamp/Data", "type": "xv_sdk/Clamp", "probe": "sample"},
}

RVIZ_VIEWS = {
    "overview": "general.rviz",
    "four-fisheyes": "four_fisheyes.rviz",
    "rgbd": "rgbd_camera.rviz",
    "rgb": "rgb_camera.rviz",
    "tof": "tof.rviz",
    "slam": "slam_visualization.rviz",
}


class OperationError(RuntimeError):
    pass


def normalized_locale(value: Any) -> str:
    return "en" if str(value or "").lower().startswith("en") else "zh-CN"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_state() -> Dict[str, Any]:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(value: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o644)
    temporary.replace(STATE_FILE)


def active_desktop_user() -> Optional[pwd.struct_passwd]:
    if os.geteuid() != 0:
        return pwd.getpwuid(os.geteuid())
    try:
        result = subprocess.run(["who"], text=True, capture_output=True, timeout=3, check=False)
        names = [line.split()[0] for line in result.stdout.splitlines() if line.split()]
    except (OSError, subprocess.TimeoutExpired):
        names = []
    for name in names:
        try:
            account = pwd.getpwnam(name)
        except KeyError:
            continue
        if account.pw_uid >= 1000:
            return account
    for account in pwd.getpwall():
        if account.pw_uid >= 1000 and account.pw_uid < 65534 and Path(account.pw_dir).is_dir():
            return account
    return None


def process_environment(process: Path) -> Dict[str, str]:
    try:
        raw = (process / "environ").read_bytes()
    except OSError:
        return {}
    result: Dict[str, str] = {}
    for entry in raw.split(b"\0"):
        if b"=" not in entry:
            continue
        key, value = entry.split(b"=", 1)
        result[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return result


def desktop_environment(account: pwd.struct_passwd, proc_root: Path = Path("/proc")) -> Dict[str, str]:
    """Find the actual graphical session instead of assuming DISPLAY=:0."""
    preferred = {"gnome-shell", "plasmashell", "xfce4-session", "mate-session", "cinnamon"}
    best: Dict[str, str] = {}
    best_score = -1
    try:
        processes = list(proc_root.iterdir())
    except OSError:
        processes = []
    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != account.pw_uid:
                continue
        except OSError:
            continue
        environment = process_environment(process)
        if not environment.get("DISPLAY") and not environment.get("WAYLAND_DISPLAY"):
            continue
        command = ""
        try:
            command = (process / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            pass
        score = 10 if command in preferred else 0
        score += 4 if environment.get("DISPLAY") else 0
        score += 3 if environment.get("XAUTHORITY") else 0
        score += 2 if environment.get("DBUS_SESSION_BUS_ADDRESS") else 0
        score += 1 if environment.get("XDG_RUNTIME_DIR") else 0
        if score > best_score:
            best = environment
            best_score = score

    runtime = best.get("XDG_RUNTIME_DIR") or "/run/user/%d" % account.pw_uid
    result = {
        "HOME": account.pw_dir,
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
        "XDG_RUNTIME_DIR": runtime,
        "DBUS_SESSION_BUS_ADDRESS": best.get("DBUS_SESSION_BUS_ADDRESS") or "unix:path=%s/bus" % runtime,
    }
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_SESSION_TYPE", "LANG"):
        if best.get(key):
            result[key] = best[key]
    fallback_authority = Path(runtime) / "gdm" / "Xauthority"
    if "XAUTHORITY" not in result and fallback_authority.is_file():
        result["XAUTHORITY"] = str(fallback_authority)
    return result


def firmware_update_error(output: str) -> Optional[str]:
    lowered = output.lower()
    hid_markers = (
        "don't found any xvisio",
        "not found any xvisio",
        "no xvisio hid device",
        "please check if you has plun",
        "did not find usb device",
        "can not find usb device",
        "device found but the connection failed",
    )
    if any(marker in lowered for marker in hid_markers):
        return "hid_not_found"
    if "request pre-mode fail" in lowered or "request switch-mode fail" in lowered:
        return "mode_switch_failed"
    if "timeout error" in lowered:
        return "mode_switch_timeout"
    dfu_markers = (
        "no dfu capable usb device available",
        "cannot open dfu device",
        "error during download",
        "error resetting after download",
        "download failed",
    )
    if any(marker in lowered for marker in dfu_markers):
        return "dfu_failed"
    return None


def firmware_update_succeeded(output: str) -> bool:
    """Require positive DFU evidence because the vendor helper returns 0 on failure."""
    lowered = output.lower()
    if firmware_update_error(output):
        return False
    completed = (
        "file downloaded successfully" in lowered
        or "download done" in lowered
    )
    return completed


def firmware_version_from_probe(output: str, serial: str) -> Optional[str]:
    begin = "XVISION_PROBE_JSON_BEGIN"
    end = "XVISION_PROBE_JSON_END"
    start = output.rfind(begin)
    finish = output.rfind(end)
    if start < 0 or finish <= start:
        return None
    try:
        value = json.loads(output[start + len(begin):finish].strip())
    except json.JSONDecodeError:
        return None
    for device in value.get("devices", []) if isinstance(value, dict) else []:
        if not isinstance(device, dict) or str(device.get("serial", "")) != serial:
            continue
        info = device.get("info") if isinstance(device.get("info"), dict) else {}
        for key, raw in info.items():
            normalized = str(key).lower().replace("-", "_").replace(" ", "_")
            if "firmware" in normalized or normalized in ("version", "device_version"):
                version = str(raw).strip()
                if version:
                    return version
    return None


def usb_hid_interfaces(usb_path: str, usb_root: Path = Path("/sys/bus/usb/devices")) -> List[Path]:
    result: List[Path] = []
    for interface in usb_root.glob("%s:*" % usb_path):
        try:
            interface_class = (interface / "bInterfaceClass").read_text(encoding="ascii").strip().lower()
        except OSError:
            continue
        if interface_class == "03":
            result.append(interface)
    return sorted(result)


def hidraw_nodes_for_usb(
    usb_path: str,
    sys_root: Path = Path("/sys"),
    dev_root: Path = Path("/dev"),
) -> List[Path]:
    usb_device = (sys_root / "bus" / "usb" / "devices" / usb_path).resolve()
    result: List[Path] = []
    for entry in (sys_root / "class" / "hidraw").glob("hidraw*"):
        try:
            device = (entry / "device").resolve(strict=True)
        except OSError:
            continue
        if usb_device != device and usb_device not in device.parents:
            continue
        node = dev_root / entry.name
        if node.exists():
            result.append(node)
    return sorted(result)


class OperationManager:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self.lock = threading.Lock()
        self.gui_processes: Dict[str, subprocess.Popen] = {}
        self.current: Dict[str, Any] = {
            "id": None, "action": None, "status": "idle", "logs": [],
            "started_at": None, "finished_at": None, "error": None, "locale": "zh-CN",
        }

    def tr(self, zh: str, en: str) -> str:
        with self.lock:
            locale = self.current.get("locale", "zh-CN")
        return en if locale == "en" else zh

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            snapshot = json.loads(json.dumps(self.current, ensure_ascii=False))
        snapshot["preview_running"] = self.gui_process_running("camera-preview")
        return snapshot

    def log(self, message: str) -> None:
        clean = message.rstrip()
        if not clean:
            return
        with self.lock:
            for line in clean.splitlines():
                self.current["logs"].append("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), line))
            self.current["logs"] = self.current["logs"][-500:]

    def start(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        handlers = {
            "sdk-install": self.install_sdk,
            "firmware-preflight": self.preflight_firmware,
            "firmware-flash": self.flash_firmware,
            "topic-check": self.check_topic,
            "rviz-launch": self.launch_rviz,
            "camera-preview": self.launch_camera_preview,
            "camera-preview-stop": self.stop_camera_preview,
            "calibration-launch": self.launch_calibration,
            "ros-wrapper-install": self.install_ros_wrapper,
            "ros-driver-start": self.start_ros_driver,
            "ros-driver-stop": self.stop_ros_driver,
        }
        locale = normalized_locale(payload.pop("locale", "zh-CN"))
        if action not in handlers:
            raise OperationError(("Unsupported operation: %s" if locale == "en" else "不支持的操作：%s") % action)
        with self.lock:
            if self.current.get("status") == "running":
                message = "Another operation is running. Please wait for it to finish." if locale == "en" else "已有操作正在执行，请等待完成。"
                raise OperationError(message)
            self.current = {
                "id": str(uuid.uuid4()), "action": action, "status": "running",
                "logs": [], "started_at": now_iso(), "finished_at": None, "error": None,
                "locale": locale,
            }
            snapshot = dict(self.current)
        thread = threading.Thread(
            target=self._execute, args=(handlers[action], payload),
            name="fastumi-operation", daemon=True,
        )
        thread.start()
        return snapshot

    def _execute(self, handler: Any, payload: Dict[str, Any]) -> None:
        status = "succeeded"
        error = None
        try:
            handler(payload)
        except Exception as exc:
            status = "failed"
            error = str(exc)
            self.log(self.tr("失败：%s", "Failed: %s") % exc)
        with self.lock:
            self.current["status"] = status
            self.current["error"] = error
            self.current["finished_at"] = now_iso()
            record = json.loads(json.dumps(self.current, ensure_ascii=False))
        self._append_history(record)

    def _append_history(self, record: Dict[str, Any]) -> None:
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            with HISTORY_FILE.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def history(self, limit: int = 30) -> List[Dict[str, Any]]:
        try:
            lines = HISTORY_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        result: List[Dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    result.append(value)
            except json.JSONDecodeError:
                continue
        return list(reversed(result))

    def run(self, argv: Sequence[str], timeout: int = 600, env: Optional[Dict[str, str]] = None) -> str:
        display = shlex.join([str(value) for value in argv])
        self.log(self.tr("执行：%s", "Run: %s") % display)
        try:
            process = subprocess.Popen(
                list(argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env,
            )
        except OSError as exc:
            raise OperationError(self.tr("无法启动命令：%s", "Unable to start command: %s") % exc) from exc
        started = time.monotonic()
        captured: List[str] = []
        assert process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while True:
                for key, _ in selector.select(timeout=0.25):
                    line = key.fileobj.readline()
                    if line:
                        captured.append(line.rstrip("\n"))
                        self.log(line)
                if process.poll() is not None:
                    for remaining in process.stdout:
                        captured.append(remaining.rstrip("\n"))
                        self.log(remaining)
                    break
                if time.monotonic() - started > timeout:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise OperationError(self.tr("操作超时（%d 秒）", "Operation timed out after %d seconds") % timeout)
        finally:
            selector.close()
        if process.returncode != 0:
            raise OperationError(self.tr("命令失败，退出码 %d", "Command failed with exit code %d") % process.returncode)
        return "\n".join(captured)

    def require_root(self) -> None:
        if os.geteuid() != 0:
            raise OperationError(self.tr("该操作需要管理员权限，请使用安装后的 FastUMI Tools 服务。", "This operation requires administrator privileges. Use the installed FastUMI Tools service."))

    def install_rule(self) -> None:
        source = self.catalog.root / "firmware" / "99-LumosVisio.rules"
        if not source.is_file():
            raise OperationError(self.tr("缺少 USB 权限规则。", "The USB permission rule is missing."))
        destination = Path("/etc/udev/rules.d/99-LumosVisio.rules")
        shutil.copyfile(str(source), str(destination))
        destination.chmod(0o644)
        self.run(["udevadm", "control", "--reload-rules"], timeout=20)
        self.run(["udevadm", "trigger"], timeout=30)

    def compile_sdk_probe(self) -> None:
        source = APP_ROOT / "tools" / "xvsdk_version.cpp"
        header_candidates = [Path("/usr/include/xvsdk/xv-sdk.h"), Path("/usr/local/include/xvsdk/xv-sdk.h")]
        header = next((item for item in header_candidates if item.is_file()), None)
        library_candidates = [Path("/usr/lib/libxvsdk.so"), Path("/usr/local/lib/libxvsdk.so")]
        library = next((item for item in library_candidates if item.is_file()), None)
        if not source.is_file() or not header or not library or not shutil.which("g++"):
            self.log(self.tr("未生成 SDK 直接探测器；版本与 USB 检测仍可使用。", "The direct SDK probe was not built; version and USB detection remain available."))
            return
        output_dir = APP_ROOT / "bin"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / "xvsdk_version"
        self.run([
            "g++", "-std=c++11", "-O2", "-I%s" % header.parent,
            str(source), "-o", str(output), "-L%s" % library.parent,
            "-Wl,--wrap=__libc_start_main", "-Wl,-rpath,%s" % library.parent, "-lxvsdk",
        ], timeout=120)
        output.chmod(0o755)

    def install_sdk(self, payload: Dict[str, Any]) -> None:
        self.require_root()
        artifact_id = str(payload.get("artifact_id", ""))
        item, path = self.catalog.resolve("sdk", artifact_id)
        requested_generation = str(payload.get("camera_generation", "")).strip().lower()
        item_generation = str(item.get("camera_generation") or "gen1").lower()
        if requested_generation and requested_generation != item_generation:
            raise OperationError(self.tr(
                "所选相机代际与 SDK 资源不匹配（资源属于 %s）。",
                "The selected camera generation does not match this SDK resource (resource is for %s).",
            ) % (self.tr("二代相机" if item_generation == "gen2" else "一代相机", "Gen 2 camera" if item_generation == "gen2" else "Gen 1 camera")))
        if self.ros_driver_active():
            self.run(["systemctl", "stop", ROS_DRIVER_UNIT], timeout=30)
            self.log(self.tr(
                "SDK 安装前已停止 ROS 数据源，以释放相机和旧 wrapper。",
                "Stopped the ROS data source before SDK installation to release the camera and old wrapper.",
            ))
            time.sleep(1)
        self.log(self.tr("已校验 SDK %s（%s）。", "Validated SDK %s (%s).") % (item["label"], item["id"]))
        self.install_rule()
        environment = dict(os.environ)
        environment["DEBIAN_FRONTEND"] = "noninteractive"
        self.run(["apt-get", "install", "-y", "--reinstall", str(path)], timeout=1800, env=environment)
        self.compile_sdk_probe()
        wrapper_source = Path("/usr/share/ros-wrapper/xv_sdk")
        ros_setup = Path("/opt/ros/noetic/setup.bash")
        if wrapper_source.is_dir() and ros_setup.is_file():
            self.install_ros_wrapper({"clean": True})
        else:
            self.log(self.tr(
                "未检测到 ROS Noetic 或 SDK ROS1 wrapper，跳过 ROS1 重编译。",
                "ROS Noetic or the SDK ROS1 wrapper is unavailable; skipped the ROS1 rebuild.",
            ))
        state = load_state()
        state.update({
            "sdk_release": item["release"], "sdk_artifact": item["id"],
            "sdk_camera_generation": item_generation,
            "sdk_installed_at": now_iso(),
        })
        save_state(state)
        self.log(self.tr("SDK %s 安装完成。", "SDK %s installation completed.") % item["label"])

    def require_firmware_sdk(self, minimum: Optional[str]) -> None:
        if not minimum:
            return
        sdk = sdk_information()
        managed = str(sdk.get("managed_release") or "")
        runtime = str(sdk.get("runtime_version") or "")
        runtime_releases = re.findall(r"20[0-9]{6}", runtime)
        runtime_release = max(runtime_releases) if runtime_releases else ""
        if managed >= str(minimum) and runtime_release >= str(minimum):
            self.log(self.tr(
                "SDK 检查通过：受管版本 %s，运行时 %s。",
                "SDK check passed: managed release %s, runtime %s.",
            ) % (managed, runtime))
            return
        raise OperationError(self.tr(
            "固件要求 SDK %s 或更新版本；当前受管版本为 %s，实际运行时为 %s。请先安装推荐 SDK。",
            "This firmware requires SDK %s or newer; managed release is %s and the actual runtime is %s. Install the recommended SDK first.",
        ) % (minimum, managed or self.tr("未知", "unknown"), runtime or self.tr("未检测到", "not detected")))

    def firmware_error_message(self, code: str) -> str:
        messages = {
            "hid_not_found": self.tr(
                "固件更新器没有找到 XVisio HID 设备；没有写入任何内容。",
                "The firmware updater could not find the XVisio HID device; nothing was written.",
            ),
            "mode_switch_failed": self.tr(
                "相机无法从普通模式切换到 DFU 模式；已停止刷新。",
                "The camera could not switch from normal mode to DFU mode; flashing was stopped.",
            ),
            "mode_switch_timeout": self.tr(
                "等待相机进入 DFU 模式超时；已停止刷新。",
                "Timed out waiting for the camera to enter DFU mode; flashing was stopped.",
            ),
            "dfu_failed": self.tr(
                "DFU 下载失败；固件未通过完整性确认。",
                "The DFU download failed; firmware integrity was not confirmed.",
            ),
        }
        return messages.get(code, self.tr("固件更新器报告未知错误。", "The firmware updater reported an unknown error."))

    def validate_firmware_phase(self, output: str, phase: str) -> None:
        failure = firmware_update_error(output)
        if failure:
            raise OperationError(self.firmware_error_message(failure))
        if not firmware_update_succeeded(output):
            raise OperationError(self.tr(
                "%s 阶段没有出现“DFU 下载完成”的正向证据；为避免假成功，任务已判定失败。",
                "%s did not produce positive DFU download-completion evidence. The task is marked failed to prevent a false success.",
            ) % phase)
        self.log(self.tr("%s 阶段已确认 DFU 下载完成。", "%s DFU download verified.") % phase)

    def stop_sources_for_firmware(self) -> None:
        if self.ros_driver_active():
            self.run(["systemctl", "stop", ROS_DRIVER_UNIT], timeout=30)
            self.log(self.tr(
                "已自动停止受管 ROS 数据源并释放相机。",
                "The managed ROS data source was stopped to release the camera.",
            ))
            time.sleep(1)
        if any(topic.startswith("/xv_sdk/") and topic.count("/") > 2 for topic in self.ros_topic_list()):
            raise OperationError(self.tr(
                "检测到非 FastUMI Tools 启动的 XVSDK ROS 节点，请先在原终端停止它。",
                "An XVSDK ROS node not managed by FastUMI Tools is running. Stop it in its original terminal first.",
            ))

    def wait_for_released_firmware_device(self, serial: str, timeout: float = 12) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        last_owners: List[Dict[str, Any]] = []
        while time.monotonic() < deadline:
            devices = usb_devices_in_use(get_usb_devices())
            if len(devices) == 1 and str(devices[0].get("serial", "")) == serial:
                last_owners = devices[0].get("in_use_by") or []
                if not last_owners:
                    return devices[0]
            time.sleep(0.25)
        if last_owners:
            detail = ", ".join("%s(%s)" % (item["command"], item["pid"]) for item in last_owners)
            raise OperationError(self.tr(
                "相机仍被进程占用：%s。请关闭实时预览、标定或其他采集程序。",
                "The camera is still in use by %s. Close live preview, calibration, or other acquisition programs.",
            ) % detail)
        raise OperationError(self.tr(
            "释放数据源后没有重新检测到目标相机。",
            "The target camera was not detected after releasing data sources.",
        ))

    def bind_firmware_hid(self, device: Dict[str, Any]) -> Path:
        if str(device.get("usb_product_id", "")).lower() != NORMAL_USB_PRODUCT_ID:
            raise OperationError(self.tr(
                "相机不在普通 HID 模式（期望 040e:%s）。",
                "The camera is not in normal HID mode (expected 040e:%s).",
            ) % NORMAL_USB_PRODUCT_ID)
        usb_path = str(device.get("usb_path", ""))
        interfaces = usb_hid_interfaces(usb_path)
        if not interfaces:
            raise OperationError(self.tr(
                "相机 USB 描述符中没有 HID 接口，不能安全开始刷新。",
                "The camera USB descriptor has no HID interface, so flashing cannot start safely.",
            ))

        nodes = hidraw_nodes_for_usb(usb_path)
        if not nodes:
            usbhid = Path("/sys/bus/usb/drivers/usbhid")
            if not usbhid.is_dir():
                self.run(["modprobe", "usbhid"], timeout=15)
            bind = usbhid / "bind"
            if not bind.exists():
                raise OperationError(self.tr(
                    "系统没有可用的 usbhid 绑定入口。",
                    "The system does not expose a usable usbhid bind endpoint.",
                ))
            for interface in interfaces:
                driver = interface / "driver"
                if driver.is_symlink() and driver.resolve().name != "usbhid":
                    unbind = driver.resolve() / "unbind"
                    try:
                        unbind.write_text(interface.name, encoding="ascii")
                    except OSError as exc:
                        raise OperationError(self.tr(
                            "无法释放 HID 接口 %s：%s",
                            "Unable to release HID interface %s: %s",
                        ) % (interface.name, exc)) from exc
                if not driver.is_symlink():
                    try:
                        bind.write_text(interface.name, encoding="ascii")
                    except OSError as exc:
                        raise OperationError(self.tr(
                            "无法绑定 HID 接口 %s：%s",
                            "Unable to bind HID interface %s: %s",
                        ) % (interface.name, exc)) from exc
            subprocess.run(["udevadm", "settle"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                nodes = hidraw_nodes_for_usb(usb_path)
                if nodes:
                    break
                time.sleep(0.2)
        if not nodes:
            raise OperationError(self.tr(
                "HID 接口存在，但系统没有创建对应的 /dev/hidraw*；刷新尚未开始。",
                "The HID interface exists, but no matching /dev/hidraw* node was created; flashing has not started.",
            ))
        node = nodes[0]
        try:
            descriptor = os.open(str(node), os.O_RDWR | os.O_NONBLOCK)
            os.close(descriptor)
        except OSError as exc:
            raise OperationError(self.tr(
                "无法读写相机 HID 设备 %s：%s",
                "Cannot read and write camera HID device %s: %s",
            ) % (node, exc)) from exc
        self.log(self.tr(
            "HID 前置检查通过：%s（040e:%s）。",
            "HID preflight passed: %s (040e:%s).",
        ) % (node, NORMAL_USB_PRODUCT_ID))
        return node

    def ensure_next_firmware_stage_ready(self, serial: str, timeout: float = 35) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        minimum_ready_at = time.monotonic() + 5
        candidate_identity = None
        candidate_since = 0.0
        self.log(self.tr(
            "等待 USB Loader 断开并以稳定的 DFU 设备重新枚举……",
            "Waiting for the USB Loader to disconnect and re-enumerate as a stable DFU device…",
        ))
        while time.monotonic() < deadline:
            devices = get_usb_devices()
            if len(devices) == 1:
                device = devices[0]
                product = str(device.get("usb_product_id", "")).lower()
                identity = (
                    product,
                    str(device.get("usb_path", "")),
                    str(device.get("usb_device_node", "")),
                )
                if identity != candidate_identity:
                    candidate_identity = identity
                    candidate_since = time.monotonic()
                stable = time.monotonic() - candidate_since >= 2
                settled = time.monotonic() >= minimum_ready_at and stable
                if product == DFU_USB_PRODUCT_ID:
                    try:
                        probe = subprocess.run(
                            ["dfu-util", "-l"], text=True, capture_output=True,
                            timeout=5, check=False,
                        )
                        listing = "%s\n%s" % (probe.stdout, probe.stderr)
                    except (OSError, subprocess.TimeoutExpired):
                        listing = ""
                    if settled and "040e:%s" % DFU_USB_PRODUCT_ID in listing.lower():
                        self.log(self.tr("相机 DFU 模式已确认。", "Camera DFU mode verified."))
                        return device
                if settled and product == NORMAL_USB_PRODUCT_ID and str(device.get("serial", "")) == serial:
                    self.bind_firmware_hid(device)
                    return device
            else:
                candidate_identity = None
                candidate_since = 0.0
            time.sleep(0.5)
        raise OperationError(self.tr(
            "写入 USB Loader 后，相机没有以普通模式或 DFU 模式重新枚举。",
            "After writing the USB Loader, the camera did not re-enumerate in normal or DFU mode.",
        ))

    def wait_for_normal_firmware_device(self, serial: str, timeout: float = 75) -> Dict[str, Any]:
        self.log(self.tr("等待相机完成重启并重新枚举……", "Waiting for the camera to reboot and re-enumerate…"))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            devices = get_usb_devices()
            if len(devices) == 1:
                device = devices[0]
                if (
                    str(device.get("usb_product_id", "")).lower() == NORMAL_USB_PRODUCT_ID
                    and str(device.get("serial", "")) == serial
                ):
                    time.sleep(2)
                    return device
            time.sleep(0.5)
        raise OperationError(self.tr(
            "主固件下载完成，但相机没有在 75 秒内恢复普通模式；请重新插拔后检查，任务不会记为成功。",
            "The main firmware downloaded, but the camera did not return to normal mode within 75 seconds. Reconnect and inspect it; the task will not be recorded as successful.",
        ))

    def probe_firmware_version(self, serial: str) -> str:
        probe = sdk_probe_path()
        if not probe:
            raise OperationError(self.tr(
                "缺少 SDK 设备版本探测器，不能完成写后验真。",
                "The SDK device-version probe is missing, so post-flash verification cannot complete.",
            ))
        output = self.run([str(probe), "--devices"], timeout=45)
        version = firmware_version_from_probe(output, serial)
        if not version:
            raise OperationError(self.tr(
                "SDK 没有返回目标相机的真实固件版本；任务不会记为成功。",
                "The SDK did not return the target camera's actual firmware version; the task will not be recorded as successful.",
            ))
        self.log(self.tr("相机实际报告固件：%s。", "Camera-reported firmware: %s.") % version)
        return version

    def prepare_firmware_flash(
        self,
        payload: Dict[str, Any],
        require_acknowledgement: bool,
    ) -> tuple:
        self.require_root()
        artifact_id = str(payload.get("artifact_id", ""))
        item, archive = self.catalog.resolve("firmware", artifact_id)
        requested_generation = str(payload.get("camera_generation") or "gen1").lower()
        item_generation = str(item.get("camera_generation") or "gen1").lower()
        if requested_generation != "gen1" or item_generation != "gen1":
            raise OperationError(self.tr(
                "二代相机固件只能在 Windows 升级工具中刷新；FastUMI Tools 只提供二代 SDK 安装。",
                "Gen 2 firmware must be flashed with the Windows upgrade tool; FastUMI Tools only installs the Gen 2 SDK.",
            ))
        if require_acknowledgement and payload.get("acknowledged") is not True:
            raise OperationError(self.tr("请确认刷新期间不会拔线或断电。", "Confirm that USB and power will remain connected during the flash."))
        devices = usb_devices_in_use(get_usb_devices())
        if len(devices) != 1:
            raise OperationError(self.tr("固件刷新要求只连接一台 FastUMI 相机，当前检测到 %d 台。", "Firmware flashing requires exactly one FastUMI camera; %d detected.") % len(devices))
        device = devices[0]
        serial = str(device.get("serial", ""))
        if str(payload.get("serial_confirmation", "")) != serial:
            raise OperationError(self.tr("相机序列号确认不匹配。", "The camera serial number confirmation does not match."))
        product = str(device.get("usb_product_id", "")).lower()
        if product != NORMAL_USB_PRODUCT_ID:
            raise OperationError(self.tr(
                "目标相机 USB ID 为 040e:%s，刷新必须从普通模式 040e:%s 开始。",
                "The target camera USB ID is 040e:%s; flashing must start in normal mode 040e:%s.",
            ) % (product or "????", NORMAL_USB_PRODUCT_ID))
        if device.get("usb_generation") != "USB 3.x":
            raise OperationError(self.tr(
                "无法确认相机工作在 USB 3.x，请更换到 USB 3.x 接口后再刷新。",
                "The camera could not be verified on USB 3.x. Move it to a USB 3.x port before flashing.",
            ))
        minimum = item.get("minimum_sdk_release")
        self.require_firmware_sdk(str(minimum) if minimum else None)
        self.stop_sources_for_firmware()
        device = self.wait_for_released_firmware_device(serial)
        self.install_rule()
        if not shutil.which("dfu-util"):
            self.run(["apt-get", "install", "-y", "dfu-util"], timeout=900)
        self.run(["dfu-util", "-l"], timeout=15)
        self.log(self.tr(
            "DFU 运行环境检查通过；libusb 可以在当前服务中初始化。",
            "DFU runtime check passed; libusb initializes in the current service.",
        ))
        self.bind_firmware_hid(device)
        self.log(self.tr(
            "全部写前检查通过：唯一相机 %s、USB 3.x、SDK、进程释放和 HID 均已确认。",
            "All pre-write checks passed: single camera %s, USB 3.x, SDK, process release, and HID are verified.",
        ) % serial)
        return item, archive, device, serial

    def preflight_firmware(self, payload: Dict[str, Any]) -> None:
        item, _, _, serial = self.prepare_firmware_flash(payload, require_acknowledgement=False)
        self.log(self.tr(
            "固件 %s 的非写入预检通过；相机 %s 尚未写入任何内容。",
            "Non-writing preflight passed for firmware %s and camera %s; nothing was written.",
        ) % (item["label"], serial))
        try:
            self.restore_uvc()
        except OperationError as exc:
            self.log(self.tr(
                "预检通过，但 UVC 自动恢复失败：%s",
                "Preflight passed, but automatic UVC restore failed: %s",
            ) % exc)

    def flash_firmware(self, payload: Dict[str, Any]) -> None:
        item, archive, _, serial = self.prepare_firmware_flash(payload, require_acknowledgement=True)
        with tempfile.TemporaryDirectory(prefix="fastumi-firmware-") as temporary:
            root = Path(temporary)
            with zipfile.ZipFile(str(archive)) as bundle:
                for member in bundle.infolist():
                    target = (root / member.filename).resolve()
                    try:
                        target.relative_to(root)
                    except ValueError as exc:
                        raise OperationError(self.tr("固件包包含不安全路径。", "The firmware archive contains an unsafe path.")) from exc
                bundle.extractall(str(root))
            updater = next(root.rglob("LumosFastUMIUpdateImg"), None)
            loader = next(root.rglob("usbLoader.img"), None)
            framework = next(root.rglob("framework.img"), None)
            if not updater or not loader or not framework:
                raise OperationError(self.tr("固件包不完整。", "The firmware archive is incomplete."))
            if str(item["release"]).encode("ascii") not in framework.read_bytes():
                raise OperationError(self.tr(
                    "主固件镜像不包含目标版本 %s，已在写入前停止。",
                    "The main firmware image does not contain target release %s; stopped before writing.",
                ) % item["release"])
            updater.chmod(0o755)
            self.log(self.tr("开始写入 USB Loader，请勿拔线或断电。", "Writing the USB Loader. Do not disconnect USB or power."))
            loader_output = self.run([str(updater), str(loader)], timeout=180)
            self.validate_firmware_phase(loader_output, self.tr("USB Loader", "USB Loader"))
            self.ensure_next_firmware_stage_ready(serial)
            self.log(self.tr("开始写入主固件，请勿拔线或断电。", "Writing the main firmware. Do not disconnect USB or power."))
            framework_output = self.run([str(updater), str(framework)], timeout=300)
            self.validate_firmware_phase(framework_output, self.tr("主固件", "Main firmware"))
        self.wait_for_normal_firmware_device(serial)
        actual_version = self.probe_firmware_version(serial)
        if str(item["release"]) not in actual_version:
            raise OperationError(self.tr(
                "写后验真失败：目标版本为 %s，相机实际报告 %s；任务不会记为成功。",
                "Post-flash verification failed: target is %s but the camera reports %s; the task will not be recorded as successful.",
            ) % (item["release"], actual_version))
        state = load_state()
        firmware = state.setdefault("firmware", {})
        firmware[serial] = {
            "release": item["release"], "artifact": item["id"],
            "actual_version": actual_version, "flashed_at": now_iso(), "verified_at": now_iso(),
        }
        save_state(state)
        self.log(self.tr(
            "固件 %s 刷新并验真成功；只有此时才更新成功记录。",
            "Firmware %s was flashed and verified; the success record is updated only now.",
        ) % item["label"])
        try:
            self.restore_uvc()
        except OperationError as exc:
            self.log(self.tr(
                "固件已验真，但 UVC 自动恢复失败：%s",
                "Firmware is verified, but automatic UVC restore failed: %s",
            ) % exc)

    def validated_serial(self, payload: Dict[str, Any]) -> str:
        serial = str(payload.get("serial", ""))
        if not SERIAL_PATTERN.match(serial):
            raise OperationError(self.tr("设备序列号格式不正确。", "The device serial number is invalid."))
        return serial

    def check_topic(self, payload: Dict[str, Any]) -> None:
        serial = self.validated_serial(payload)
        metric = str(payload.get("metric", ""))
        spec = TOPICS.get(metric)
        if not spec:
            raise OperationError(self.tr("未知监控指标。", "Unknown monitoring metric."))
        topic = "/xv_sdk/%s/%s" % (serial, spec["suffix"])
        expected_type = spec["type"]
        probe_command = (
            "timeout 12 rostopic echo -n 10 %s; "
            "probe_status=$?; [ $probe_status -eq 0 ] || [ $probe_status -eq 124 ]"
            % shlex.quote(topic)
            if spec["probe"] == "sample" else
            "timeout --signal=INT 15 rostopic hz -w 5 %s; "
            "probe_status=$?; [ $probe_status -eq 0 ] || [ $probe_status -eq 124 ]"
            % shlex.quote(topic)
        )
        command = (
            "source /opt/ros/noetic/setup.bash; "
            "[ -f $HOME/catkin_ws/devel/setup.bash ] && source $HOME/catkin_ws/devel/setup.bash; "
            "%sactual_type=$(rostopic type %s 2>/dev/null) || exit 41; "
            "[ \"$actual_type\" = %s ] || { echo \"TYPE_MISMATCH:$actual_type\"; exit 42; }; %s"
            % (ROS_LOCAL_ENV, shlex.quote(topic), shlex.quote(expected_type), probe_command)
        )
        try:
            output = self.run(self.gui_command(["/bin/bash", "-lc", command]), timeout=25)
        except OperationError as exc:
            message = str(exc)
            if "exit code 41" in message or "退出码 41" in message:
                raise OperationError(self.tr("Topic 不存在或没有发布者：%s", "Topic is unavailable or has no publisher: %s") % topic) from exc
            if "exit code 42" in message or "退出码 42" in message:
                raise OperationError(self.tr("Topic 消息类型不匹配，预期 %s：%s", "Topic type mismatch; expected %s: %s") % (expected_type, topic)) from exc
            raise
        has_measurement = (
            "average rate:" in output.lower()
            if spec["probe"] == "hz" else
            bool(output.strip())
        )
        if not has_measurement:
            raise OperationError(self.tr(
                "Topic 已发布但数据流未启用或当前设备不支持，采样窗口内没有收到数据：%s",
                "The Topic is advertised, but the stream is inactive or unsupported by this device; no data was received: %s",
            ) % topic)
        self.log(self.tr("Topic 检查通过：%s（%s）。", "Topic check passed: %s (%s).") % (topic, expected_type))

    def generate_rviz(self, serial: str) -> Path:
        templates = APP_ROOT.parent / "assets" / "rviz" / "templates"
        if not templates.is_dir():
            templates = Path("/opt/fastumi-tools/assets/rviz/templates")
        output = GENERATED_DIR / serial
        output.mkdir(parents=True, exist_ok=True)
        for template in templates.glob("*.template"):
            content = template.read_text(encoding="utf-8", errors="replace")
            destination = output / template.name[:-len(".template")]
            destination.write_text(content.replace("${XV_DEVICE_SERIAL}", serial), encoding="utf-8")
            destination.chmod(0o644)
        output.chmod(0o755)
        return output

    def gui_command(self, argv: Sequence[str]) -> List[str]:
        account = active_desktop_user()
        if not account:
            raise OperationError(self.tr("未找到已登录的桌面用户。", "No logged-in desktop user was found."))
        environment = desktop_environment(account)
        if not environment.get("DISPLAY") and not environment.get("WAYLAND_DISPLAY"):
            raise OperationError(self.tr("未找到桌面显示会话，请确认用户已登录图形桌面。", "No graphical desktop session was found. Make sure a user is signed in."))
        prefix = ["env"] + ["%s=%s" % item for item in environment.items()]
        if os.geteuid() == 0 and account.pw_uid != os.geteuid():
            prefix = ["runuser", "-u", account.pw_name, "--"] + prefix
        return prefix + list(argv)

    def gui_log_tail(self, path: Path, lines: int = 12) -> str:
        try:
            content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(content[-lines:]).strip()

    def gui_process_running(self, name: str) -> bool:
        with self.lock:
            process = self.gui_processes.get(name)
        if process is None:
            return False
        if process.poll() is None:
            return True
        with self.lock:
            if self.gui_processes.get(name) is process:
                self.gui_processes.pop(name, None)
        return False

    def reap_gui_process(self, process: subprocess.Popen, name: Optional[str] = None) -> None:
        """Reap a detached GUI after its window closes without blocking the API."""
        def wait_and_forget() -> None:
            process.wait()
            if name:
                with self.lock:
                    if self.gui_processes.get(name) is process:
                        self.gui_processes.pop(name, None)

        threading.Thread(target=wait_and_forget, name="fastumi-gui-reaper", daemon=True).start()

    def spawn_gui(
        self,
        argv: Sequence[str],
        startup_timeout: float = 1.5,
        allow_early_success: bool = False,
        ready_file: Optional[Path] = None,
        process_name: Optional[str] = None,
    ) -> subprocess.Popen:
        if process_name and self.gui_process_running(process_name):
            raise OperationError(self.tr("相机预览窗口已经在运行。", "The camera preview window is already running."))
        command = self.gui_command(argv)
        self.log(self.tr("启动桌面程序：%s", "Launch desktop program: %s") % shlex.join([str(value) for value in argv]))
        GUI_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = GUI_LOG_DIR / ("%s.log" % uuid.uuid4().hex)
        try:
            with log_path.open("ab", buffering=0) as output:
                process = subprocess.Popen(
                    command, stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
                )
        except OSError as exc:
            raise OperationError(self.tr("无法启动桌面程序：%s", "Unable to launch desktop program: %s") % exc) from exc
        if process_name:
            with self.lock:
                self.gui_processes[process_name] = process
        self.reap_gui_process(process, process_name)
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if ready_file and ready_file.is_file():
                ready_file.unlink(missing_ok=True)
                self.log(self.tr("桌面程序已就绪；日志：%s", "Desktop program is ready; log: %s") % log_path)
                return process
            returncode = process.poll()
            if returncode is not None:
                if returncode == 0 and allow_early_success and ready_file is None:
                    self.log(self.tr("桌面程序请求已提交；日志：%s", "Desktop launch request submitted; log: %s") % log_path)
                    return process
                detail = self.gui_log_tail(log_path)
                message = self.tr("桌面程序启动失败，退出码 %d", "Desktop program failed with exit code %d") % returncode
                if detail:
                    message += "：%s" % detail
                raise OperationError(message)
            time.sleep(0.1)
        if ready_file:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            detail = self.gui_log_tail(log_path)
            message = self.tr("相机预览在 %.0f 秒内没有收到有效画面", "Camera preview did not receive a valid frame within %.0f seconds") % startup_timeout
            if detail:
                message += "：%s" % detail
            raise OperationError(message)
        self.log(self.tr("桌面程序已启动；日志：%s", "Desktop program started; log: %s") % log_path)
        return process

    def launch_rviz(self, payload: Dict[str, Any]) -> None:
        serial = self.validated_serial(payload)
        view = str(payload.get("view", ""))
        filename = RVIZ_VIEWS.get(view)
        if not filename:
            raise OperationError(self.tr("未知 RViz 视图。", "Unknown RViz view."))
        rviz = Path("/opt/ros/noetic/bin/rviz")
        if not rviz.is_file():
            raise OperationError(self.tr("未安装 RViz。", "RViz is not installed."))
        config = self.generate_rviz(serial) / filename
        if not config.is_file():
            raise OperationError(self.tr("RViz 配置不存在：%s", "RViz configuration does not exist: %s") % filename)
        command = (
            "source /opt/ros/noetic/setup.bash; "
            "[ -f $HOME/catkin_ws/devel/setup.bash ] && source $HOME/catkin_ws/devel/setup.bash; "
            "%sexec %s -d %s" % (ROS_LOCAL_ENV, shlex.quote(str(rviz)), shlex.quote(str(config)))
        )
        if view in ("slam", "overview"):
            bridge = APP_ROOT.parent / "assets" / "monitor" / "slam_pose_bridge.py"
            command = (
                "source /opt/ros/noetic/setup.bash; "
                "[ -f $HOME/catkin_ws/devel/setup.bash ] && source $HOME/catkin_ws/devel/setup.bash; "
                "%s/usr/bin/python3 %s %s & bridge_pid=$!; "
                "trap 'kill $bridge_pid >/dev/null 2>&1 || true' EXIT; %s -d %s"
                % (ROS_LOCAL_ENV, shlex.quote(str(bridge)), shlex.quote(serial), shlex.quote(str(rviz)), shlex.quote(str(config)))
            )
        self.spawn_gui(["/bin/bash", "-lc", command], startup_timeout=2.0)
        self.log(self.tr("RViz 已启动。", "RViz started."))

    def launch_camera_preview(self, payload: Dict[str, Any]) -> None:
        device = str(payload.get("device", "/dev/video0"))
        if not re.match(r"^/dev/video[0-9]+$", device) or not Path(device).exists():
            raise OperationError(self.tr("无效的视频设备。", "Invalid video device."))
        try:
            width = int(payload.get("width", 1280))
            height = int(payload.get("height", 1280))
            fps = int(payload.get("fps", 60))
        except (TypeError, ValueError) as exc:
            raise OperationError(self.tr("画面参数格式不正确。", "Invalid preview parameter format.")) from exc
        if width not in (640, 1280, 1920) or height not in (480, 720, 1080, 1280) or fps not in (30, 60, 100):
            raise OperationError(self.tr("不支持的分辨率或帧率。", "Unsupported resolution or frame rate."))
        details = next((item for item in video_devices() if item.get("path") == device), None)
        if details and not details.get("previewable", True):
            raise OperationError(self.tr(
                "所选视频节点不是可预览的图像采集节点，请选择 RGB、鱼眼或 ToF 节点。",
                "The selected video node is not a previewable capture stream. Choose an RGB, fisheye, or ToF node.",
            ))
        pixel_format = str((details or {}).get("format") or "NV12")
        script = APP_ROOT / "tools" / "camera_preview.py"
        dependency_check = subprocess.run(
            ["/usr/bin/python3", "-c", "import cv2,numpy"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if dependency_check.returncode != 0:
            raise OperationError(self.tr("缺少相机预览依赖，请先安装 python3-opencv 和 python3-numpy。", "Camera preview dependencies are missing. Install python3-opencv and python3-numpy."))
        ready_file = self.desktop_runtime_directory("preview") / "ready"
        self.spawn_gui([
            "/usr/bin/python3", str(script), "--device", device,
            "--width", str(width), "--height", str(height), "--fps", str(fps),
            "--format", pixel_format,
            "--ready-file", str(ready_file),
        ], startup_timeout=8.0, ready_file=ready_file, process_name="camera-preview")
        self.log(self.tr(
            "相机预览已启动；可按 q、Esc，或在 Web 界面点击“关闭预览窗口”。",
            "Camera preview started. Press q or Esc, or click Close preview in the web interface.",
        ))

    def stop_camera_preview(self, payload: Dict[str, Any]) -> None:
        del payload
        with self.lock:
            process = self.gui_processes.get("camera-preview")
        if process is None or process.poll() is not None:
            with self.lock:
                if self.gui_processes.get("camera-preview") is process:
                    self.gui_processes.pop("camera-preview", None)
            self.log(self.tr("预览窗口已经关闭。", "The preview window is already closed."))
            return
        self.log(self.tr("正在关闭相机预览窗口……", "Closing the camera preview window…"))
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
        with self.lock:
            if self.gui_processes.get("camera-preview") is process:
                self.gui_processes.pop("camera-preview", None)
        self.log(self.tr("相机预览窗口已关闭。", "The camera preview window has been closed."))

    def desktop_runtime_directory(self, prefix: str, account: Optional[pwd.struct_passwd] = None) -> Path:
        account = account or active_desktop_user()
        if not account:
            raise OperationError(self.tr("未找到已登录的桌面用户。", "No logged-in desktop user was found."))
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        RUNTIME_DIR.chmod(0o755)
        directory = RUNTIME_DIR / ("%s-%s" % (prefix, uuid.uuid4().hex))
        directory.mkdir(mode=0o700)
        os.chown(directory, account.pw_uid, account.pw_gid)
        return directory

    def launch_calibration(self, payload: Dict[str, Any]) -> None:
        calibration = APP_ROOT.parent / "assets" / "calibration"
        if not calibration.is_dir():
            calibration = Path("/opt/fastumi-tools/assets/calibration")
        demo = calibration / "demo-api"
        pipe = calibration / "pipe_srv"
        if not demo.is_file() or not pipe.is_file():
            raise OperationError(self.tr("标定工具不完整。", "The calibration tools are incomplete."))
        if not Path("/usr/lib/libxvsdk.so").is_file() and not Path("/usr/local/lib/libxvsdk.so").is_file():
            raise OperationError(self.tr("未检测到 libxvsdk.so，请先安装 SDK。", "libxvsdk.so was not found. Install the SDK first."))
        terminal = shutil.which("gnome-terminal")
        if not terminal:
            raise OperationError(self.tr("标定控制台需要 gnome-terminal。", "The calibration console requires gnome-terminal."))

        devices = usb_devices_in_use(get_usb_devices())
        if len(devices) != 1:
            raise OperationError(self.tr(
                "标定要求只连接一台 FastUMI 相机，当前检测到 %d 台。",
                "Calibration requires exactly one FastUMI camera; %d detected.",
            ) % len(devices))
        if self.ros_driver_active():
            self.run(["systemctl", "stop", ROS_DRIVER_UNIT], timeout=30)
            self.log(self.tr("ROS 数据源已停止；标定结束后将保持停止。", "The ROS data source was stopped and will remain stopped after calibration."))
            time.sleep(1)
        elif any(topic.startswith("/xv_sdk/") and topic.count("/") > 2 for topic in self.ros_topic_list()):
            raise OperationError(self.tr(
                "检测到非 FastUMI Tools 启动的 XVSDK ROS 节点，请先在原终端停止它。",
                "An XVSDK ROS node not managed by FastUMI Tools is running. Stop it in its original terminal first.",
            ))

        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            devices = usb_devices_in_use(get_usb_devices())
            owners = [owner for device in devices for owner in (device.get("in_use_by") or [])]
            if not owners:
                break
            time.sleep(0.25)
        else:
            detail = ", ".join("%s(%s)" % (item["command"], item["pid"]) for item in owners)
            raise OperationError(self.tr("相机仍被进程占用：%s。", "The camera is still in use by: %s.") % detail)

        account = active_desktop_user()
        if not account:
            raise OperationError(self.tr("未找到已登录的桌面用户。", "No logged-in desktop user was found."))
        session = self.desktop_runtime_directory("calibration", account)
        for name, source in (("demo-api", demo), ("pipe_srv", pipe)):
            (session / name).symlink_to(source)
        intrinsic = calibration / "rgb_intrinsic_SEUCM.txt"
        if intrinsic.is_file():
            shutil.copy2(str(intrinsic), str(session / intrinsic.name))
        for child in session.iterdir():
            os.lchown(child, account.pw_uid, account.pw_gid)

        def terminal_script(program: str, pid_file: str, log_file: str) -> str:
            exit_label = self.tr("程序已退出，退出码", "Process exited with code")
            foreground = "echo $$ > %s; exec ./%s" % (shlex.quote(pid_file), program)
            return (
                "cd %s; /bin/bash -c %s > >(tee -a %s) 2>&1; status=$?; "
                "printf '\\n[FastUMI] %s %%s\\n' $status; exec bash"
                % (
                    shlex.quote(str(session)), shlex.quote(foreground),
                    shlex.quote(log_file), exit_label,
                )
            )

        demo_pid = session / "demo.pid"
        demo_log = session / "demo.log"
        self.spawn_gui([
            terminal, "--title=FastUMI Calibration API", "--", "/bin/bash", "-lc",
            terminal_script("demo-api", demo_pid.name, demo_log.name),
        ], allow_early_success=True)
        self.wait_for_calibration_tool(demo_pid, demo_log, "demo-api", 12)

        pipe_pid = session / "pipe.pid"
        pipe_log = session / "pipe.log"
        self.spawn_gui([
            terminal, "--title=FastUMI Calibration Control", "--", "/bin/bash", "-lc",
            terminal_script("pipe_srv", pipe_pid.name, pipe_log.name),
        ], allow_early_success=True)
        self.wait_for_calibration_tool(pipe_pid, pipe_log, "pipe_srv", 10)

        fifo_deadline = time.monotonic() + 8
        fifo_paths = (session / "vscSrvRfifo", session / "vscSrvWfifo")
        while time.monotonic() < fifo_deadline:
            if all(path.exists() and stat.S_ISFIFO(path.stat().st_mode) for path in fifo_paths):
                break
            if not self.process_from_pid_file_alive(demo_pid) or not self.process_from_pid_file_alive(pipe_pid):
                break
            time.sleep(0.2)
        if not all(path.exists() and stat.S_ISFIFO(path.stat().st_mode) for path in fifo_paths):
            detail = "\n".join(filter(None, (self.gui_log_tail(demo_log), self.gui_log_tail(pipe_log))))
            raise OperationError(self.tr("标定通信管道未建立。%s", "Calibration communication pipes were not created. %s") % detail)
        self.log(self.tr(
            "标定控制台已就绪；请在 Control 窗口先单独输入 1，初始化完成后再单独输入 37（不要输入连字符），并在 API 窗口查看 RGB 标定参数。",
            "Calibration consoles are ready. In Control, enter 1 first; after initialization, enter 37 separately (do not type hyphens). Read the RGB calibration values in API.",
        ))

    def process_from_pid_file_alive(self, pid_file: Path) -> bool:
        try:
            pid = int(pid_file.read_text(encoding="ascii").strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            return False

    def wait_for_calibration_tool(self, pid_file: Path, log_file: Path, name: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        stable_since: Optional[float] = None
        while time.monotonic() < deadline:
            if self.process_from_pid_file_alive(pid_file):
                if stable_since is None:
                    stable_since = time.monotonic()
                if time.monotonic() - stable_since >= 1.5:
                    self.log(self.tr("标定进程 %s 已启动。", "Calibration process %s started.") % name)
                    return
            elif pid_file.exists():
                break
            time.sleep(0.15)
        detail = self.gui_log_tail(log_file)
        message = self.tr("标定进程 %s 启动失败。", "Calibration process %s failed to start.") % name
        if detail:
            message += " " + detail
        raise OperationError(message)

    def install_ros_wrapper(self, payload: Dict[str, Any]) -> None:
        self.require_root()
        source = Path("/usr/share/ros-wrapper/xv_sdk")
        setup = Path("/opt/ros/noetic/setup.bash")
        if not source.is_dir():
            raise OperationError(self.tr("当前 SDK 未提供 ROS1 wrapper。", "The current SDK does not provide a ROS1 wrapper."))
        if not setup.is_file():
            raise OperationError(self.tr("未安装 ROS Noetic。", "ROS Noetic is not installed."))
        account = active_desktop_user()
        if not account:
            raise OperationError(self.tr("未找到桌面用户。", "No desktop user was found."))
        workspace = Path(account.pw_dir) / "catkin_ws"
        destination = workspace / "src" / "xv_sdk"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(source), str(destination), dirs_exist_ok=True)
        if payload.get("clean"):
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            for generated in (workspace / "build", workspace / "devel"):
                if generated.is_dir():
                    backup = generated.with_name("%s.before-sdk-%s" % (generated.name, stamp))
                    shutil.move(str(generated), str(backup))
                    self.log(self.tr(
                        "已备份旧 ROS 构建目录：%s",
                        "Backed up the old ROS build directory: %s",
                    ) % backup)
        for root, directories, files in os.walk(str(workspace)):
            os.chown(root, account.pw_uid, account.pw_gid)
            for name in directories + files:
                try:
                    os.chown(os.path.join(root, name), account.pw_uid, account.pw_gid)
                except OSError:
                    pass
        command = (
            "source /opt/ros/noetic/setup.bash; cd %s; "
            "catkin_make -DXVSDK_INCLUDE_DIRS=/usr/include/xvsdk -DXVSDK_LIBRARIES=/usr/lib/libxvsdk.so"
            % shlex.quote(str(workspace))
        )
        self.run(["runuser", "-u", account.pw_name, "--", "/bin/bash", "-lc", command], timeout=1800)
        self.log(self.tr("ROS1 wrapper 已安装到 %s。", "ROS1 wrapper installed in %s.") % workspace)

    def ros_driver_active(self) -> bool:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", ROS_DRIVER_UNIT],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        return result.returncode == 0

    def ros_driver_node_online(self) -> bool:
        try:
            result = subprocess.run(
                ["/bin/bash", "-lc", "source /opt/ros/noetic/setup.bash; %srosnode info /xv_sdk >/dev/null" % ROS_LOCAL_ENV],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=4, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def ros_topic_list(self) -> List[str]:
        try:
            result = subprocess.run(
                ["/bin/bash", "-lc", "source /opt/ros/noetic/setup.bash; rostopic list"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=4, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def start_ros_driver(self, payload: Dict[str, Any]) -> None:
        self.require_root()
        if self.ros_driver_active():
            if self.ros_driver_node_online():
                self.log(self.tr("FastUMI ROS 数据源已经在运行。", "The FastUMI ROS data source is already running."))
                return
            self.log(self.tr(
                "检测到 ROS 管理单元仍在运行，但 XVSDK 节点已退出；正在清理并重启。",
                "The ROS unit is active but the XVSDK node has exited; cleaning up before restart.",
            ))
            self.run(["systemctl", "stop", ROS_DRIVER_UNIT], timeout=30)
            time.sleep(1)
        if any(topic.startswith("/xv_sdk/") and topic.count("/") > 2 for topic in self.ros_topic_list()):
            raise OperationError(self.tr("检测到其他方式启动的 XVSDK ROS 节点，请先在原终端中停止它。", "An XVSDK ROS node started outside FastUMI Tools is running. Stop it in its original terminal."))
        account = active_desktop_user()
        if not account:
            raise OperationError(self.tr("未找到已登录的桌面用户。", "No logged-in desktop user was found."))
        setup = Path("/opt/ros/noetic/setup.bash")
        workspace_setup = Path(account.pw_dir) / "catkin_ws" / "devel" / "setup.bash"
        node = Path(account.pw_dir) / "catkin_ws" / "devel" / "lib" / "xv_sdk" / "xv_sdk"
        if not setup.is_file():
            raise OperationError(self.tr("未安装 ROS Noetic。", "ROS Noetic is not installed."))
        if not workspace_setup.is_file() or not node.is_file():
            raise OperationError(self.tr("ROS1 wrapper 尚未编译，请先在“SDK 和固件”页面安装 ROS1 Wrapper。", "The ROS1 wrapper has not been built. Install it from SDK & Firmware first."))
        subprocess.run(
            ["systemctl", "reset-failed", ROS_DRIVER_UNIT],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        launch = "source %s; source %s; %sexec roslaunch xv_sdk xv_sdk.launch" % (
            shlex.quote(str(setup)), shlex.quote(str(workspace_setup)), ROS_LOCAL_ENV,
        )
        self.run([
            "systemd-run", "--unit=fastumi-ros-driver", "--uid=%s" % account.pw_name,
            "--property=WorkingDirectory=%s" % account.pw_dir,
            "--property=KillMode=control-group", "--property=TimeoutStopSec=10",
            "--setenv=HOME=%s" % account.pw_dir, "--setenv=USER=%s" % account.pw_name,
            "/bin/bash", "-lc", launch,
        ], timeout=20)
        serials = [str(item.get("serial")) for item in get_usb_devices() if item.get("serial")]
        self.log(self.tr("等待 XVSDK 发现相机并发布 Topic……", "Waiting for XVSDK to discover the camera and publish Topics…"))
        deadline = time.monotonic() + 30
        topics: List[str] = []
        while time.monotonic() < deadline:
            if not self.ros_driver_active():
                break
            topics = self.ros_topic_list()
            if any(topic.startswith("/xv_sdk/%s/" % serial) for serial in serials for topic in topics):
                self.log(self.tr("ROS 数据源已就绪，共发现 %d 个 Topic。", "ROS data source is ready; %d Topics found.") % len(topics))
                return
            time.sleep(1)
        journal = subprocess.run(
            ["journalctl", "-u", ROS_DRIVER_UNIT, "--no-pager", "-n", "40"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        ).stdout.strip()
        subprocess.run(["systemctl", "stop", ROS_DRIVER_UNIT], check=False)
        self.restore_uvc()
        if journal:
            self.log(journal)
        raise OperationError(self.tr("ROS 数据源启动失败：30 秒内未发现相机 Topic。", "ROS data source startup failed: no camera Topic appeared within 30 seconds."))

    def restore_uvc(self) -> None:
        self.require_root()
        if self.ros_driver_active():
            raise OperationError(self.tr("ROS 数据源仍在运行，不能同时恢复 V4L2 视频设备。", "The ROS data source is still running; V4L2 video cannot be restored at the same time."))
        owners = usb_devices_in_use(get_usb_devices())
        occupied = [owner for device in owners for owner in (device.get("in_use_by") or [])]
        if occupied:
            detail = "、".join("%s(%s)" % (item["command"], item["pid"]) for item in occupied)
            raise OperationError(self.tr("相机仍被其他进程占用：%s。", "The camera is still in use by: %s.") % detail)
        usb_root = Path("/sys/bus/usb/devices")
        bind = Path("/sys/bus/usb/drivers/uvcvideo/bind")
        if not bind.exists():
            self.run(["modprobe", "uvcvideo"], timeout=15)
        if not bind.exists():
            raise OperationError(self.tr("系统没有提供 uvcvideo 驱动绑定入口。", "The system does not expose the uvcvideo bind interface."))
        restored = 0
        for device in get_usb_devices():
            usb_path = str(device.get("usb_path", ""))
            for interface in usb_root.glob("%s:*" % usb_path):
                if (interface / "bInterfaceClass").read_text(errors="replace").strip().lower() != "0e":
                    continue
                if (interface / "bInterfaceSubClass").read_text(errors="replace").strip().lower() != "01":
                    continue
                driver = interface / "driver"
                if driver.is_symlink() and driver.resolve().name == "uvcvideo":
                    restored += 1
                    continue
                try:
                    bind.write_text(interface.name, encoding="ascii")
                except OSError as exc:
                    if not (driver.is_symlink() and driver.resolve().name == "uvcvideo"):
                        raise OperationError(self.tr("无法恢复 UVC 接口 %s：%s", "Unable to restore UVC interface %s: %s") % (interface.name, exc)) from exc
                restored += 1
        if not restored:
            raise OperationError(self.tr("没有找到可恢复的 FastUMI UVC 接口。", "No restorable FastUMI UVC interface was found."))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if list(Path("/dev").glob("video*")):
                self.log(self.tr("UVC 视频设备已恢复。", "UVC video devices restored."))
                return
            time.sleep(0.2)
        raise OperationError(self.tr("UVC 接口已绑定，但系统没有创建 /dev/video*。", "The UVC interface was bound, but no /dev/video* device was created."))

    def stop_ros_driver(self, payload: Dict[str, Any]) -> None:
        self.require_root()
        if self.ros_driver_active():
            self.run(["systemctl", "stop", ROS_DRIVER_UNIT], timeout=30)
            self.log(self.tr("ROS 数据源已停止。", "ROS data source stopped."))
            time.sleep(1)
        self.restore_uvc()
