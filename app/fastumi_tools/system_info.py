"""Host, SDK, USB and ROS inspection with safe device-version probing."""

from __future__ import annotations

import glob
import json
import os
import platform
import re
import shutil
import subprocess
import time
import xmlrpc.client
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .catalog import Catalog, SUPPORTED_OS_CODENAMES


APP_ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = Path(os.environ.get("FASTUMI_STATE_FILE", "/var/lib/fastumi-tools/state.json"))
ROS_MASTER_URI = os.environ.get("ROS_MASTER_URI", "http://localhost:11311")
ROS_DRIVER_UNIT = "fastumi-ros-driver.service"
PROBE_HELPER_ROOT = Path(os.environ.get(
    "FASTUMI_PROBE_HELPER_ROOT", "/usr/lib/fastumi-tools/probes",
))
SERIAL_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
FIRMWARE_RELEASE_PATTERN = re.compile(r"(?<!\d)(20\d{6}(?:_\d+)?)(?!\d)")
DEVICE_VERSION_LOG_PATTERN = re.compile(
    r"Device [Vv]ersion\s*:\s*(.*?)\s+\(SN=([A-Za-z0-9_.:-]+)\)"
)
PROBE_JSON_BEGIN = "XVISION_PROBE_JSON_BEGIN"
PROBE_JSON_END = "XVISION_PROBE_JSON_END"
PROBE_FAILURE_BACKOFF_SECONDS = 30.0

_DEVICE_PROBE_CACHE: Dict[str, Dict[str, Any]] = {}
_DEVICE_PROBE_FAILURES: Dict[str, float] = {}


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


def firmware_release(value: Any) -> Optional[str]:
    matches = FIRMWARE_RELEASE_PATTERN.findall(str(value or ""))
    if not matches:
        return None
    return next((match for match in matches if "_" in match), matches[0])


def set_firmware_reading(
    device: Dict[str, Any],
    version: str,
    source: str,
    observed_at: Optional[str],
    current_session: bool,
    verified: bool,
) -> None:
    device["firmware_version"] = version.strip()
    device["firmware_release"] = firmware_release(version)
    device["firmware_source"] = source
    device["firmware_observed_at"] = observed_at
    device["firmware_current_session"] = current_session
    device["firmware_verified"] = verified


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
    keys = {
        id(item): "%s|%s" % (item.get("serial", ""), item.get("usb_path", ""))
        for item in devices
    }
    targets: Dict[str, str] = {}
    owners: Dict[str, List[Dict[str, Any]]] = {key: [] for key in keys.values()}
    usb_root = Path("/sys/bus/usb/devices")
    video_root = Path("/sys/class/video4linux")
    for item in devices:
        key = keys[id(item)]
        if item.get("usb_device_node"):
            targets[str(item["usb_device_node"])] = key
        usb_path = str(item.get("usb_path") or "")
        try:
            usb_device = (usb_root / usb_path).resolve(strict=True)
        except OSError:
            continue
        for video in video_root.glob("video*"):
            try:
                video_device = (video / "device").resolve(strict=True)
            except OSError:
                continue
            if usb_device == video_device or usb_device in video_device.parents:
                targets["/dev/%s" % video.name] = key
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
            owner_key = targets.get(target)
            if not owner_key:
                continue
            entry = {
                "pid": int(process.name),
                "command": read_text(process / "comm") or "unknown",
            }
            if entry not in owners[owner_key]:
                owners[owner_key].append(entry)
    for item in devices:
        item["in_use_by"] = owners.get(keys[id(item)], [])
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


def bundled_probe_specs() -> List[Dict[str, Any]]:
    """Return signed-catalog probe helpers without extracting their SDKs."""
    roots = [PROBE_HELPER_ROOT, Catalog.find_root() / "probes"]
    result: List[Dict[str, Any]] = []
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        for metadata_path in sorted(root.glob("*/probe.json")):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            helper = metadata_path.with_name("xvsdk_version")
            artifact_id = str(metadata.get("artifact_id") or "")
            generation = str(metadata.get("camera_generation") or "")
            release = str(metadata.get("release") or "")
            identity = (artifact_id, generation, release)
            if (
                metadata.get("schema_version") != 1
                or not artifact_id
                or generation not in ("gen1", "gen2")
                or not release
                or identity in seen
                or not helper.is_file()
                or not os.access(str(helper), os.X_OK)
                or not (helper.parent / "usr/lib/libxvsdk.so").is_file()
            ):
                continue
            seen.add(identity)
            result.append({
                "artifact_id": artifact_id,
                "camera_generation": generation,
                "release": release,
                "runtime_version": str(metadata.get("runtime_version") or "") or None,
                "helper": helper,
                "source": "bundled",
            })
    return sorted(result, key=lambda item: str(item["camera_generation"]))


def _read_state() -> Dict[str, Any]:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _installed_probe_spec() -> Optional[Dict[str, Any]]:
    helper = sdk_probe_path()
    if not helper:
        return None
    state = _read_state()
    return {
        "artifact_id": state.get("sdk_artifact"),
        "camera_generation": state.get("sdk_camera_generation"),
        "release": state.get("sdk_release"),
        "runtime_version": None,
        "helper": helper,
        "source": "installed",
    }


def _prepare_bundled_probe(spec: Dict[str, Any]) -> Optional[Path]:
    """Use the package-owned SDK runtime without installing it system-wide."""
    helper = spec.get("helper")
    if not isinstance(helper, Path) or not helper.is_file():
        return None
    if (helper.parent / "usr/lib/libxvsdk.so").is_file():
        return helper
    return None


def _probe_payload(output: str) -> Dict[str, Any]:
    start = output.rfind(PROBE_JSON_BEGIN)
    finish = output.rfind(PROBE_JSON_END)
    if start < 0 or finish <= start:
        return {}
    try:
        value = json.loads(output[start + len(PROBE_JSON_BEGIN):finish].strip())
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _firmware_from_info(info: Any) -> Optional[str]:
    if not isinstance(info, dict):
        return None
    for key, raw_value in info.items():
        normalized = str(key).lower().replace("-", "_").replace(" ", "_")
        if "firmware" in normalized or normalized in ("version", "device_version"):
            firmware = str(raw_value).strip()
            if firmware:
                return firmware
    return None


def _device_probe_key(device: Dict[str, Any]) -> str:
    return "|".join(str(device.get(key) or "") for key in (
        "serial", "usb_path", "usb_device_node",
    ))


def _restore_uvc_after_probe(devices: List[Dict[str, Any]]) -> bool:
    """Best-effort repair for SDK releases that leave UVC interfaces detached."""
    bind = Path("/sys/bus/usb/drivers/uvcvideo/bind")
    if not bind.exists():
        run_command(["modprobe", "uvcvideo"], timeout=15)
    if not bind.exists():
        return False
    usb_root = Path("/sys/bus/usb/devices")
    found = False
    restored = True
    for device in devices:
        usb_path = str(device.get("usb_path") or "")
        for interface in usb_root.glob("%s:*" % usb_path):
            if read_text(interface / "bInterfaceClass").lower() != "0e":
                continue
            if read_text(interface / "bInterfaceSubClass").lower() != "01":
                continue
            found = True
            driver = interface / "driver"
            try:
                if driver.is_symlink() and driver.resolve().name == "uvcvideo":
                    continue
                bind.write_text(interface.name, encoding="ascii")
            except OSError:
                restored = False
    if found:
        run_command(["udevadm", "settle"], timeout=8)
    return found and restored


def _apply_probe_payload(
    devices: List[Dict[str, Any]], payload: Dict[str, Any], spec: Dict[str, Any],
) -> bool:
    by_serial = {str(item.get("serial")): item for item in devices}
    observed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    matched = False
    runtime_version = str(payload.get("sdk_version") or spec.get("runtime_version") or "").strip()
    for raw in payload.get("devices", []) if isinstance(payload.get("devices"), list) else []:
        if not isinstance(raw, dict):
            continue
        device = by_serial.get(str(raw.get("serial") or ""))
        if not device:
            continue
        matched = True
        if runtime_version:
            device["probe_sdk_runtime_version"] = runtime_version
        if spec.get("release"):
            device["probe_sdk_release"] = spec.get("release")
        if spec.get("camera_generation"):
            device["probe_sdk_generation"] = spec.get("camera_generation")
        device["probe_sdk_source"] = spec.get("source")
        firmware = _firmware_from_info(raw.get("info"))
        if firmware:
            set_firmware_reading(
                device, firmware, "sdk_device_info", observed_at,
                current_session=True, verified=True,
            )
    return matched


def sdk_information() -> Dict[str, Any]:
    package, _ = run_command(["dpkg-query", "-W", "-f=${Version}", "xvsdk"])
    runtime = ""
    probe = sdk_probe_path()
    if probe:
        runtime, _ = run_command([str(probe), "--version"], timeout=12)
    state = _read_state()
    bundled = bundled_probe_specs()
    return {
        "installed": bool(package or runtime),
        "package_version": package or None,
        "runtime_version": runtime or None,
        "managed_release": state.get("sdk_release"),
        "managed_artifact": state.get("sdk_artifact"),
        "managed_camera_generation": state.get("sdk_camera_generation"),
        "probe_available": bool(probe or bundled),
        "probe_releases": [item["release"] for item in bundled],
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
    _, node_error = ros_shell("rosnode info /xv_sdk >/dev/null", timeout=4)
    node_online = not bool(node_error)
    return {
        "online": not bool(error),
        "driver_running": bool(serials) and node_online,
        "managed_driver_running": unit_state == "active" and node_online,
        "driver_unit_active": unit_state == "active",
        "driver_node_online": node_online,
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
                set_firmware_reading(
                    device, value, "ros_device_info", datetime.now().astimezone().isoformat(timespec="seconds"),
                    current_session=True, verified=True,
                )
                break


def merge_current_ros_journal_firmware(devices: List[Dict[str, Any]], ros: Dict[str, Any]) -> None:
    """Read Device::info() from this managed ROS service activation only."""
    if not devices or not ros.get("managed_driver_running"):
        return
    active_since, error = run_command([
        "systemctl", "show", "--property=ActiveEnterTimestamp", "--value", ROS_DRIVER_UNIT,
    ], timeout=3)
    if error or not active_since:
        return
    output, error = run_command([
        "journalctl", "-u", ROS_DRIVER_UNIT, "--since", active_since,
        "--grep", r"Device [Vv]ersion.*\(SN=", "--output=json", "--no-pager", "--lines=20",
    ], timeout=5)
    if error:
        return
    by_serial = {str(item.get("serial")): item for item in devices}
    for line in output.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = str(record.get("MESSAGE", ""))
        match = DEVICE_VERSION_LOG_PATTERN.search(message)
        if not match:
            continue
        version, serial = match.groups()
        device = by_serial.get(serial)
        if not device or not version.strip():
            continue
        observed_at = None
        try:
            observed_at = datetime.fromtimestamp(
                int(record.get("__REALTIME_TIMESTAMP")) / 1_000_000
            ).astimezone().isoformat(timespec="seconds")
        except (TypeError, ValueError, OSError, OverflowError):
            pass
        set_firmware_reading(
            device, version, "ros_current_session", observed_at,
            current_session=True, verified=True,
        )


def merge_fallback_firmware(devices: List[Dict[str, Any]]) -> None:
    """Read each newly connected idle camera, then cache the current USB session."""
    if not devices:
        _DEVICE_PROBE_CACHE.clear()
        _DEVICE_PROBE_FAILURES.clear()
        return
    connected_keys = {_device_probe_key(item) for item in devices}
    for key in list(_DEVICE_PROBE_CACHE):
        if key not in connected_keys:
            _DEVICE_PROBE_CACHE.pop(key, None)
    for key in list(_DEVICE_PROBE_FAILURES):
        if key not in connected_keys:
            _DEVICE_PROBE_FAILURES.pop(key, None)
    for device in devices:
        cached = _DEVICE_PROBE_CACHE.get(_device_probe_key(device))
        if cached and not device.get("firmware_current_session"):
            device.update(cached)
    if all(item.get("firmware_current_session") for item in devices):
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
                set_firmware_reading(
                    device, firmware, "historical_startup_log", None,
                    current_session=False, verified=False,
                )

    unresolved = [item for item in devices if not item.get("firmware_current_session")]
    if not unresolved:
        return
    if os.environ.get("FASTUMI_AUTO_DEVICE_PROBE", "1") == "0":
        for item in unresolved:
            item["firmware_probe_status"] = "disabled"
        return
    # Only the installed root service may probe automatically because current
    # XVSDK releases detach UVC while calling Device::info(). Root is required
    # to bind those interfaces back before this status request completes.
    if os.geteuid() != 0:
        for item in unresolved:
            item["firmware_probe_status"] = "requires_service"
        return
    # xv::getDevices() opens every connected XVisio camera. If any camera is
    # busy, defer the entire probe instead of disrupting that device while
    # trying to inspect another one.
    if any(item.get("in_use_by") for item in devices):
        for item in unresolved:
            item["firmware_probe_status"] = "occupied"
        return

    now = time.monotonic()
    for item in unresolved:
        if _DEVICE_PROBE_FAILURES.get(_device_probe_key(item), 0) > now:
            item["firmware_probe_status"] = "failed"
    unresolved = [
        item for item in unresolved
        if _DEVICE_PROBE_FAILURES.get(_device_probe_key(item), 0) <= now
    ]
    if not unresolved:
        return

    specs: List[Dict[str, Any]] = []
    installed = _installed_probe_spec()
    if installed:
        specs.append(installed)
    specs.extend(bundled_probe_specs())
    if not specs:
        for item in unresolved:
            item["firmware_probe_status"] = "unavailable"
        return

    for spec in specs:
        if not any(not item.get("firmware_current_session") for item in unresolved):
            break
        helper = spec.get("helper") if spec.get("source") == "installed" else _prepare_bundled_probe(spec)
        if not isinstance(helper, Path):
            continue
        try:
            output, error = run_command([str(helper), "--devices"], timeout=15)
            if not error:
                _apply_probe_payload(unresolved, _probe_payload(output), spec)
        finally:
            restored = _restore_uvc_after_probe(unresolved)
            for item in unresolved:
                if restored:
                    item.pop("probe_uvc_restore_failed", None)
                else:
                    item["probe_uvc_restore_failed"] = True

    cache_fields = (
        "firmware_version", "firmware_release", "firmware_source",
        "firmware_observed_at", "firmware_current_session", "firmware_verified",
        "probe_sdk_runtime_version", "probe_sdk_release", "probe_sdk_generation",
        "probe_sdk_source", "probe_uvc_restore_failed",
    )
    for item in unresolved:
        key = _device_probe_key(item)
        if item.get("firmware_current_session"):
            item["firmware_probe_status"] = "succeeded"
            _DEVICE_PROBE_CACHE[key] = {
                field: item[field] for field in cache_fields if field in item
            }
            _DEVICE_PROBE_FAILURES.pop(key, None)
        else:
            item["firmware_probe_status"] = "failed"
            _DEVICE_PROBE_FAILURES[key] = time.monotonic() + PROBE_FAILURE_BACKOFF_SECONDS


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
            device["last_verified_firmware_version"] = managed.get("actual_version")
            device["last_verified_at"] = managed.get("verified_at")
            use_verified_record = (
                not device.get("firmware_version")
                or (
                    not device.get("firmware_current_session")
                    and not device.get("firmware_verified")
                )
            )
            if use_verified_record and managed.get("actual_version"):
                set_firmware_reading(
                    device, str(managed["actual_version"]), "post_flash_verification",
                    str(managed.get("verified_at") or "") or None,
                    current_session=False, verified=True,
                )


def video_devices() -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for name in sorted(glob.glob("/dev/video*")):
        item: Dict[str, Any] = {"path": name, "name": Path(name).name}
        if shutil.which("v4l2-ctl"):
            output, _ = run_command(["v4l2-ctl", "-d", name, "--all"], timeout=3)
            for line in output.splitlines():
                if "Card type" in line and ":" in line:
                    item["label"] = line.split(":", 1)[1].strip()
                if "Driver name" in line and ":" in line:
                    item["driver"] = line.split(":", 1)[1].strip()
                if "Device Caps" in line and ":" in line:
                    item["capabilities"] = line.split(":", 1)[1].strip()
            label = str(item.get("label", ""))
            capabilities = str(item.get("capabilities", ""))
            # v4l2-ctl prints the capability names on lines following the
            # hexadecimal Device Caps value, so inspect the complete report.
            item["capture"] = "Video Capture" in output or not output
            item["metadata"] = "Metadata Capture" in output and "Video Capture" not in output
            if "UVC_RGB" in label:
                item["role"] = "rgb"
                item["format"] = "NV12"
            elif "UVC_FE" in label:
                item["role"] = "fisheye"
                item["format"] = "Y8"
            elif "UVC_TOF" in label:
                item["role"] = "tof"
                item["format"] = "Y16"
            else:
                item["role"] = "webcam"
            item["previewable"] = bool(item["capture"] and not item["metadata"])
        else:
            item["capture"] = True
            item["previewable"] = True
        result.append(item)
    # Prefer the first actual RGB stream as the default; metadata nodes remain
    # visible for diagnostics but are excluded from the preview selector.
    preferred = False
    for item in result:
        if item.get("role") == "rgb" and item.get("previewable") and not preferred:
            item["recommended"] = True
            preferred = True
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
    merge_current_ros_journal_firmware(devices, ros)
    merge_fallback_firmware(devices)
    merge_managed_firmware(devices)
    sdk = sdk_information()
    for device in devices:
        if str(device.get("firmware_source") or "").startswith("ros_"):
            if sdk.get("managed_release"):
                device["probe_sdk_release"] = sdk["managed_release"]
            if sdk.get("runtime_version"):
                device["probe_sdk_runtime_version"] = sdk["runtime_version"]
            if sdk.get("managed_camera_generation"):
                device["probe_sdk_generation"] = sdk["managed_camera_generation"]
            device["probe_sdk_source"] = "installed"
    videos = video_devices()
    host = host_information()
    host["supported"] = str(host.get("codename") or "").lower() in SUPPORTED_OS_CODENAMES
    warnings: List[str] = []
    warning_codes: List[str] = []
    if not host["supported"]:
        warnings.append("当前系统不受支持；FastUMI Tools 仅支持 Ubuntu 20.04（Focal）。")
        warning_codes.append("unsupported_os")
    if not devices:
        warnings.append("未检测到 FastUMI/XVisio USB 相机。")
        warning_codes.append("camera_missing")
    if devices and any(item.get("usb_generation") == "USB 2.0" for item in devices):
        warnings.append("检测到相机工作在 USB 2.0，建议连接 USB 3.x 接口。")
        warning_codes.append("usb2")
    if not sdk["installed"]:
        if sdk.get("probe_available"):
            warnings.append("系统未安装 XVSDK；已使用隔离的只读 SDK 探测运行时读取设备版本。")
            warning_codes.append("sdk_probe_only")
        else:
            warnings.append("未检测到 XVSDK 或只读探测运行时，可在“SDK 和固件”页面安装。")
            warning_codes.append("sdk_missing")
    probe_statuses = {str(item.get("firmware_probe_status") or "") for item in devices}
    if "occupied" in probe_statuses:
        warnings.append("相机正被其他程序占用；释放设备后将自动读取固件版本。")
        warning_codes.append("firmware_probe_occupied")
    elif "requires_service" in probe_statuses:
        warnings.append("请通过已安装的 FastUMI Tools 服务打开控制台，以自动读取固件版本。")
        warning_codes.append("firmware_probe_requires_service")
    elif "unavailable" in probe_statuses:
        warnings.append("未安装设备只读探测资源，暂时无法自动读取固件版本。")
        warning_codes.append("firmware_probe_unavailable")
    elif "failed" in probe_statuses:
        warnings.append("本次未能从设备读取固件版本；软件会稍后自动重试。")
        warning_codes.append("firmware_probe_failed")
    if any(item.get("probe_uvc_restore_failed") for item in devices):
        warnings.append("读取版本后未能确认 UVC 接口已恢复；请在“相机工具”中恢复视频设备。")
        warning_codes.append("probe_uvc_restore_failed")
    for device in devices:
        actual = str(device.get("firmware_version") or "")
        managed = str(device.get("managed_firmware_release") or "")
        if managed and actual and managed not in actual:
            if device.get("firmware_current_session"):
                warnings.append("当前会话读取的相机实际固件与软件记录的刷新目标不一致；以相机实际版本为准。")
                warning_codes.append("firmware_record_mismatch")
            else:
                warnings.append("历史固件结果与软件记录的刷新目标不一致；请获取当前会话读数后再判断。")
                warning_codes.append("firmware_unverified_mismatch")
            break
    if devices and not videos:
        if ros.get("driver_running"):
            warnings.append("ROS 数据源正在独占相机；停止数据源后可恢复实时画面预览。")
            warning_codes.append("ros_owns_camera")
        else:
            warnings.append("未检测到 V4L2 视频设备；可在“相机工具”中恢复 UVC 接口。")
            warning_codes.append("video_missing")
    return {
        "project": {"name": "FastUMI Tools", "version": project_version},
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": host,
        "sdk": sdk,
        "devices": devices,
        "video_devices": videos,
        "ros": ros,
        "warnings": warnings,
        "warning_codes": warning_codes,
        "healthy": host["supported"] and bool(devices) and sdk["installed"],
    }
