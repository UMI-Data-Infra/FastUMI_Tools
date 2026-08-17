"""Read-only host, SDK, USB, ROS and camera inspection."""

from __future__ import annotations

import glob
import json
import os
import platform
import re
import shutil
import subprocess
import xmlrpc.client
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .catalog import SUPPORTED_OS_CODENAMES


APP_ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = Path(os.environ.get("FASTUMI_STATE_FILE", "/var/lib/fastumi-tools/state.json"))
ROS_MASTER_URI = os.environ.get("ROS_MASTER_URI", "http://localhost:11311")
ROS_DRIVER_UNIT = "fastumi-ros-driver.service"
SERIAL_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def run_command(argv: Sequence[str], timeout: int = 8) -> Tuple[str, Optional[str]]:
    try:
        result = subprocess.run(
            list(argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", str(exc)
    if result.returncode == 0:
        return result.stdout.strip(), None
    return "", (result.stderr.strip() or result.stdout.strip() or "exit %d" % result.returncode)


def ros_shell(command: str, timeout: int = 8) -> Tuple[str, Optional[str]]:
    prefix = (
        "[ -f /opt/ros/noetic/setup.bash ] && source /opt/ros/noetic/setup.bash; "
        "[ -f /opt/ros/melodic/setup.bash ] && source /opt/ros/melodic/setup.bash; "
    )
    return run_command(["/bin/bash", "-lc", prefix + command], timeout=timeout)


def format_bcd_version(raw: str) -> str:
    if len(raw) == 4:
        try:
            return "%d.%s" % (int(raw[:2], 16), raw[2:])
        except ValueError:
            return raw
    return raw


def get_usb_devices() -> List[Dict[str, Any]]:
    devices: List[Dict[str, Any]] = []
    root = Path("/sys/bus/usb/devices")
    if not root.is_dir():
        return devices
    try:
        nodes = list(root.iterdir())
    except OSError:
        return devices
    for node in nodes:
        if read_text(node / "idVendor").lower() != "040e":
            continue
        bus = read_text(node / "busnum")
        number = read_text(node / "devnum")
        device_node = None
        try:
            device_node = "/dev/bus/usb/%03d/%03d" % (int(bus), int(number))
        except ValueError:
            pass
        speed_raw = read_text(node / "speed")
        try:
            speed_mbps: Optional[float] = float(speed_raw)
        except ValueError:
            speed_mbps = None
        serial = read_text(node / "serial") or "usb-%s" % node.name
        devices.append({
            "serial": serial,
            "manufacturer": read_text(node / "manufacturer") or "XVisio Technology",
            "product": read_text(node / "product") or "XVisio device",
            "usb_vendor_id": "040e",
            "usb_product_id": read_text(node / "idProduct").lower(),
            "usb_descriptor_version": format_bcd_version(read_text(node / "bcdDevice")),
            "usb_path": node.name,
            "usb_device_node": device_node,
            "usb_speed_mbps": speed_mbps,
            "usb_generation": "USB 3.x" if speed_mbps and speed_mbps >= 5000 else (
                "USB 2.0" if speed_mbps else "未知"
            ),
            "connected": True,
        })
    return sorted(devices, key=lambda item: str(item.get("serial", "")))


def usb_devices_in_use(devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    targets = {item.get("usb_device_node"): item for item in devices if item.get("usb_device_node")}
    owners: Dict[str, List[Dict[str, Any]]] = {str(path): [] for path in targets}
    if not targets:
        return devices
    try:
        processes = list(Path("/proc").iterdir())
    except OSError:
        return devices
    for process in processes:
        if not process.name.isdigit() or process.name == str(os.getpid()):
            continue
        try:
            descriptors = list((process / "fd").iterdir())
        except OSError:
            continue
        for descriptor in descriptors:
            try:
                target = os.readlink(str(descriptor))
            except OSError:
                continue
            if target not in targets:
                continue
            entry = {
                "pid": int(process.name),
                "command": read_text(process / "comm") or "unknown",
            }
            if entry not in owners[target]:
                owners[target].append(entry)
    for item in devices:
        item["in_use_by"] = owners.get(str(item.get("usb_device_node")), [])
    return devices


def sdk_probe_path() -> Optional[Path]:
    configured = os.environ.get("FASTUMI_XVSDK_PROBE")
    candidates = [
        Path(configured) if configured else Path("/__not_configured__"),
        Path("/opt/fastumi-tools/bin/xvsdk_version"),
        APP_ROOT / "bin" / "xvsdk_version",
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(str(candidate), os.X_OK):
            return candidate
    return None


def sdk_information() -> Dict[str, Any]:
    package, _ = run_command(["dpkg-query", "-W", "-f=${Version}", "xvsdk"])
    runtime = ""
    probe = sdk_probe_path()
    if probe:
        runtime, _ = run_command([str(probe), "--version"], timeout=12)
    state: Dict[str, Any] = {}
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "installed": bool(package or runtime),
        "package_version": package or None,
        "runtime_version": runtime or None,
        "managed_release": state.get("sdk_release"),
        "managed_artifact": state.get("sdk_artifact"),
    }


def ros_information() -> Dict[str, Any]:
    topics: List[str] = []
    output, error = ros_shell("rostopic list", timeout=5)
    if not error:
        topics = [line.strip() for line in output.splitlines() if line.strip()]
    serials = sorted({
        match.group(1) for topic in topics
        for match in [re.match(r"^/xv_sdk/([^/]+)/", topic)] if match
    })
    version_info: Dict[str, Any] = {}
    try:
        master = xmlrpc.client.ServerProxy(ROS_MASTER_URI, allow_none=True)
        code, _, value = master.getParam("/fastumi_tools", "/xv_sdk/version_info")
        if code == 1 and isinstance(value, dict):
            version_info = value
    except Exception:
        pass
    unit_state, _ = run_command(["systemctl", "is-active", ROS_DRIVER_UNIT], timeout=3)
    return {
        "online": not bool(error),
        "driver_running": bool(serials),
        "managed_driver_running": unit_state == "active",
        "master": ROS_MASTER_URI,
        "topic_count": len(topics),
        "serials": serials,
        "topics": topics,
        "version_info": version_info,
        "error": error,
    }


def merge_firmware(devices: List[Dict[str, Any]], ros: Dict[str, Any]) -> None:
    raw_devices = ros.get("version_info", {}).get("devices", {})
    if not isinstance(raw_devices, dict):
        return
    by_serial = {str(item.get("serial")): item for item in devices}
    for namespace, info in raw_devices.items():
        if not isinstance(info, dict):
            continue
        serial = str(info.get("reported_serial") or namespace)
        device = by_serial.get(serial)
        if not device:
            continue
        for key in ("firmware_version", "device_version", "firmware", "version", "Version"):
            value = info.get(key)
            if isinstance(value, str) and value.strip():
                device["firmware_version"] = value.strip()
                device["firmware_source"] = "ROS / Device::info()"
                break


def merge_fallback_firmware(devices: List[Dict[str, Any]]) -> None:
    """Use existing logs or the SDK helper without disturbing an occupied camera."""
    if not devices or any(item.get("firmware_version") for item in devices):
        return
    by_serial = {str(item.get("serial")): item for item in devices}
    terminal_probe = APP_ROOT / "tools" / "terminal_probe.py"
    if terminal_probe.is_file():
        output, _ = run_command(["/usr/bin/python3", str(terminal_probe)], timeout=10)
        try:
            value = json.loads(output)
        except json.JSONDecodeError:
            value = {}
        for raw in value.get("devices", []) if isinstance(value, dict) else []:
            if not isinstance(raw, dict):
                continue
            device = by_serial.get(str(raw.get("serial", "")))
            firmware = str(raw.get("firmware_version", "")).strip()
            if device and firmware:
                device["firmware_version"] = firmware
                device["firmware_source"] = "已有启动日志"
    if any(item.get("firmware_version") for item in devices):
        return
    # Initializing XVSDK to read Device::info() claims every USB interface and
    # leaves /dev/video* detached on current releases. Keep status refreshes
    # read-only unless an administrator explicitly opts in for diagnostics.
    if os.environ.get("FASTUMI_ALLOW_DEVICE_PROBE") != "1":
        return
    if any(item.get("in_use_by") for item in devices):
        return
    probe = sdk_probe_path()
    if not probe:
        return
    output, _ = run_command([str(probe), "--devices"], timeout=15)
    begin = "XVISION_PROBE_JSON_BEGIN"
    end = "XVISION_PROBE_JSON_END"
    start = output.rfind(begin)
    finish = output.rfind(end)
    if start < 0 or finish <= start:
        return
    try:
        value = json.loads(output[start + len(begin):finish].strip())
    except json.JSONDecodeError:
        return
    for raw in value.get("devices", []) if isinstance(value, dict) else []:
        if not isinstance(raw, dict):
            continue
        device = by_serial.get(str(raw.get("serial", "")))
        info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
        if not device:
            continue
        for key, raw_value in info.items():
            normalized = str(key).lower().replace("-", "_").replace(" ", "_")
            if "firmware" in normalized or normalized in ("version", "device_version"):
                firmware = str(raw_value).strip()
                if firmware:
                    device["firmware_version"] = firmware
                    device["firmware_source"] = "SDK Device::info()"
                    break


def merge_managed_firmware(devices: List[Dict[str, Any]]) -> None:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    firmware = state.get("firmware", {}) if isinstance(state, dict) else {}
    if not isinstance(firmware, dict):
        return
    for device in devices:
        managed = firmware.get(str(device.get("serial", "")))
        if isinstance(managed, dict):
            device["managed_firmware_release"] = managed.get("release")


def video_devices() -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for name in sorted(glob.glob("/dev/video*")):
        item: Dict[str, Any] = {"path": name, "name": Path(name).name}
        if shutil.which("v4l2-ctl"):
            output, _ = run_command(["v4l2-ctl", "-d", name, "--info"], timeout=3)
            for line in output.splitlines():
                if "Card type" in line and ":" in line:
                    item["label"] = line.split(":", 1)[1].strip()
                    break
        result.append(item)
    return result


def host_information() -> Dict[str, Any]:
    os_release: Dict[str, str] = {}
    for line in read_text(Path("/etc/os-release")).splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            os_release[key] = value.strip().strip('"')
    disk = shutil.disk_usage("/")
    return {
        "hostname": platform.node(),
        "os": os_release.get("PRETTY_NAME") or platform.platform(),
        "codename": os_release.get("VERSION_CODENAME"),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "disk_free_gib": round(disk.free / 1024 ** 3, 1),
    }


def collect_status(project_version: str) -> Dict[str, Any]:
    devices = usb_devices_in_use(get_usb_devices())
    ros = ros_information()
    merge_firmware(devices, ros)
    merge_fallback_firmware(devices)
    merge_managed_firmware(devices)
    sdk = sdk_information()
    videos = video_devices()
    host = host_information()
    host["supported"] = str(host.get("codename") or "").lower() in SUPPORTED_OS_CODENAMES
    warnings: List[str] = []
    if not host["supported"]:
        warnings.append("当前系统不受支持；FastUMI Tools 仅支持 Ubuntu 20.04（Focal）。")
    if not devices:
        warnings.append("未检测到 FastUMI/XVisio USB 相机。")
    if devices and any(item.get("usb_generation") == "USB 2.0" for item in devices):
        warnings.append("检测到相机工作在 USB 2.0，建议连接 USB 3.x 接口。")
    if not sdk["installed"]:
        warnings.append("未检测到 XVSDK，可在“软件与固件”页面安装。")
    if devices and not videos:
        if ros.get("driver_running"):
            warnings.append("ROS 数据源正在独占相机；停止数据源后可恢复实时画面预览。")
        else:
            warnings.append("未检测到 V4L2 视频设备；可在“相机工具”中恢复 UVC 接口。")
    return {
        "project": {"name": "FastUMI Tools", "version": project_version},
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": host,
        "sdk": sdk,
        "devices": devices,
        "video_devices": videos,
        "ros": ros,
        "warnings": warnings,
        "healthy": host["supported"] and bool(devices) and sdk["installed"],
    }
