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
from .system_info import APP_ROOT, SERIAL_PATTERN, get_usb_devices, sdk_information, usb_devices_in_use


STATE_DIR = Path(os.environ.get("FASTUMI_STATE_DIR", "/var/lib/fastumi-tools"))
STATE_FILE = STATE_DIR / "state.json"
HISTORY_FILE = STATE_DIR / "history.jsonl"
GENERATED_DIR = STATE_DIR / "generated"
GUI_LOG_DIR = STATE_DIR / "gui"
ROS_DRIVER_UNIT = "fastumi-ros-driver.service"
ROS_LOCAL_ENV = (
    "export ROS_MASTER_URI=http://127.0.0.1:11311; "
    "export ROS_IP=127.0.0.1; unset ROS_HOSTNAME; "
)

TOPICS = {
    "slam-pose": "slam/pose",
    "slam-visual-pose": "slam/visual_pose",
    "rgb": "color_camera/image",
    "fisheye-left": "fisheye_cameras/left/camera_info",
    "fisheye-left2": "fisheye_cameras/left2/camera_info",
    "fisheye-right": "fisheye_cameras/right/camera_info",
    "fisheye-right2": "fisheye_cameras/right2/camera_info",
    "tof": "tof_camera/image",
    "clamp": "clamp/Data",
}

RVIZ_VIEWS = {
    "overview": "general.rviz",
    "four-fisheyes": "four_fisheyes.rviz",
    "rgbd": "rgbd_camera.rviz",
    "rgb": "rgb_camera.rviz",
    "tof": "tof.rviz",
    "slam": "slam_visualization.rviz",
    "trajectory": "slam_pose_markers.rviz",
}


class OperationError(RuntimeError):
    pass


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
    markers = (
        "don't found any xvisio",
        "not found any xvisio",
        "no xvisio hid device",
        "please check if you has plun",
    )
    if any(marker in lowered for marker in markers):
        return "固件更新器没有找到 XVisio HID 设备；未写入固件。"
    return None


class OperationManager:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self.lock = threading.Lock()
        self.current: Dict[str, Any] = {
            "id": None, "action": None, "status": "idle", "logs": [],
            "started_at": None, "finished_at": None, "error": None,
        }

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.current, ensure_ascii=False))

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
            "firmware-flash": self.flash_firmware,
            "topic-check": self.check_topic,
            "rviz-launch": self.launch_rviz,
            "camera-preview": self.launch_camera_preview,
            "calibration-launch": self.launch_calibration,
            "ros-wrapper-install": self.install_ros_wrapper,
            "ros-driver-start": self.start_ros_driver,
            "ros-driver-stop": self.stop_ros_driver,
        }
        if action not in handlers:
            raise OperationError("不支持的操作：%s" % action)
        with self.lock:
            if self.current.get("status") == "running":
                raise OperationError("已有操作正在执行，请等待完成。")
            self.current = {
                "id": str(uuid.uuid4()), "action": action, "status": "running",
                "logs": [], "started_at": now_iso(), "finished_at": None, "error": None,
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
            self.log("失败：%s" % exc)
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
        self.log("执行：%s" % display)
        try:
            process = subprocess.Popen(
                list(argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env,
            )
        except OSError as exc:
            raise OperationError("无法启动命令：%s" % exc) from exc
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
                    raise OperationError("操作超时（%d 秒）" % timeout)
        finally:
            selector.close()
        if process.returncode != 0:
            raise OperationError("命令失败，退出码 %d" % process.returncode)
        return "\n".join(captured)

    def require_root(self) -> None:
        if os.geteuid() != 0:
            raise OperationError("该操作需要管理员权限，请使用安装后的 FastUMI Tools 服务。")

    def install_rule(self) -> None:
        source = self.catalog.root / "firmware" / "99-LumosVisio.rules"
        if not source.is_file():
            raise OperationError("缺少 USB 权限规则。")
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
            self.log("未生成 SDK 直接探测器；版本与 USB 检测仍可使用。")
            return
        output_dir = APP_ROOT / "bin"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / "xvsdk_version"
        self.run([
            "g++", "-std=c++11", "-O2", "-I%s" % header.parent,
            str(source), "-o", str(output), "-L%s" % library.parent,
            "-Wl,-rpath,%s" % library.parent, "-lxvsdk",
        ], timeout=120)
        output.chmod(0o755)

    def install_sdk(self, payload: Dict[str, Any]) -> None:
        self.require_root()
        artifact_id = str(payload.get("artifact_id", ""))
        item, path = self.catalog.resolve("sdk", artifact_id)
        self.log("已校验 SDK %s（%s）。" % (item["label"], item["id"]))
        self.install_rule()
        environment = dict(os.environ)
        environment["DEBIAN_FRONTEND"] = "noninteractive"
        self.run(["apt-get", "install", "-y", "--reinstall", str(path)], timeout=1800, env=environment)
        self.compile_sdk_probe()
        state = load_state()
        state.update({
            "sdk_release": item["release"], "sdk_artifact": item["id"],
            "sdk_installed_at": now_iso(),
        })
        save_state(state)
        self.log("SDK %s 安装完成。" % item["label"])

    def flash_firmware(self, payload: Dict[str, Any]) -> None:
        self.require_root()
        artifact_id = str(payload.get("artifact_id", ""))
        item, archive = self.catalog.resolve("firmware", artifact_id)
        if payload.get("acknowledged") is not True:
            raise OperationError("请确认刷新期间不会拔线或断电。")
        devices = usb_devices_in_use(get_usb_devices())
        if len(devices) != 1:
            raise OperationError("固件刷新要求只连接一台 FastUMI 相机，当前检测到 %d 台。" % len(devices))
        device = devices[0]
        serial = str(device.get("serial", ""))
        if str(payload.get("serial_confirmation", "")) != serial:
            raise OperationError("相机序列号确认不匹配。")
        owners = device.get("in_use_by") or []
        if owners:
            detail = "、".join("%s(%s)" % (entry["command"], entry["pid"]) for entry in owners)
            raise OperationError("相机正在被进程占用：%s。请先停止采集或 ROS 节点。" % detail)
        if device.get("usb_generation") == "USB 2.0":
            raise OperationError("相机当前工作在 USB 2.0，请连接 USB 3.x 接口后再刷新。")
        current = load_state().get("sdk_release")
        minimum = item.get("minimum_sdk_release")
        if minimum and (not current or str(current) < str(minimum)):
            detected = sdk_information().get("runtime_version") or "未记录"
            raise OperationError(
                "该固件要求 SDK %s 或更新版本；当前受管版本为 %s（运行时 %s）。请先安装推荐 SDK。"
                % (minimum, current or "未知", detected)
            )
        self.log("安全检查通过，相机 %s，目标固件 %s。" % (serial, item["label"]))
        self.install_rule()
        if not shutil.which("dfu-util"):
            self.run(["apt-get", "install", "-y", "dfu-util"], timeout=900)
        with tempfile.TemporaryDirectory(prefix="fastumi-firmware-") as temporary:
            root = Path(temporary)
            with zipfile.ZipFile(str(archive)) as bundle:
                for member in bundle.infolist():
                    target = (root / member.filename).resolve()
                    try:
                        target.relative_to(root)
                    except ValueError as exc:
                        raise OperationError("固件包包含不安全路径。") from exc
                bundle.extractall(str(root))
            updater = next(root.rglob("LumosFastUMIUpdateImg"), None)
            loader = next(root.rglob("usbLoader.img"), None)
            framework = next(root.rglob("framework.img"), None)
            if not updater or not loader or not framework:
                raise OperationError("固件包不完整。")
            updater.chmod(0o755)
            self.log("开始写入 USB Loader，请勿拔线或断电。")
            loader_output = self.run([str(updater), str(loader)], timeout=180)
            failure = firmware_update_error(loader_output)
            if failure:
                raise OperationError(failure)
            self.log("开始写入主固件，请勿拔线或断电。")
            framework_output = self.run([str(updater), str(framework)], timeout=300)
            failure = firmware_update_error(framework_output)
            if failure:
                raise OperationError(failure)
        state = load_state()
        firmware = state.setdefault("firmware", {})
        firmware[serial] = {"release": item["release"], "artifact": item["id"], "flashed_at": now_iso()}
        save_state(state)
        self.log("固件 %s 刷新完成，请重新插拔相机后复查版本。" % item["label"])

    def validated_serial(self, payload: Dict[str, Any]) -> str:
        serial = str(payload.get("serial", ""))
        if not SERIAL_PATTERN.match(serial):
            raise OperationError("设备序列号格式不正确。")
        return serial

    def check_topic(self, payload: Dict[str, Any]) -> None:
        serial = self.validated_serial(payload)
        metric = str(payload.get("metric", ""))
        suffix = TOPICS.get(metric)
        if not suffix:
            raise OperationError("未知监控指标。")
        topic = "/xv_sdk/%s/%s" % (serial, suffix)
        probe_command = (
            "timeout 12 rostopic echo -n 10 %s" % shlex.quote(topic)
            if metric == "clamp" else
            "timeout --signal=INT 15 rostopic hz -w 5 %s; "
            "probe_status=$?; [ $probe_status -eq 0 ] || [ $probe_status -eq 124 ]"
            % shlex.quote(topic)
        )
        command = (
            "source /opt/ros/noetic/setup.bash; "
            "[ -f $HOME/catkin_ws/devel/setup.bash ] && source $HOME/catkin_ws/devel/setup.bash; "
            "%s%s" % (ROS_LOCAL_ENV, probe_command)
        )
        output = self.run(self.gui_command(["/bin/bash", "-lc", command]), timeout=25)
        if metric != "clamp" and "average rate:" not in output.lower():
            raise OperationError("采样窗口内没有收到可计算频率的数据：%s" % topic)

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
            raise OperationError("未找到已登录的桌面用户。")
        environment = desktop_environment(account)
        if not environment.get("DISPLAY") and not environment.get("WAYLAND_DISPLAY"):
            raise OperationError("未找到桌面显示会话，请确认用户已登录图形桌面。")
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

    def spawn_gui(
        self,
        argv: Sequence[str],
        startup_timeout: float = 1.5,
        allow_early_success: bool = False,
        ready_file: Optional[Path] = None,
    ) -> None:
        command = self.gui_command(argv)
        self.log("启动桌面程序：%s" % shlex.join([str(value) for value in argv]))
        GUI_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = GUI_LOG_DIR / ("%s.log" % uuid.uuid4().hex)
        try:
            with log_path.open("ab", buffering=0) as output:
                process = subprocess.Popen(
                    command, stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
                )
        except OSError as exc:
            raise OperationError("无法启动桌面程序：%s" % exc) from exc
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if ready_file and ready_file.is_file():
                ready_file.unlink(missing_ok=True)
                self.log("桌面程序已就绪；日志：%s" % log_path)
                return
            returncode = process.poll()
            if returncode is not None:
                if returncode == 0 and allow_early_success and ready_file is None:
                    self.log("桌面程序请求已提交；日志：%s" % log_path)
                    return
                detail = self.gui_log_tail(log_path)
                message = "桌面程序启动失败，退出码 %d" % returncode
                if detail:
                    message += "：%s" % detail
                raise OperationError(message)
            time.sleep(0.1)
        if ready_file:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                process.terminate()
            detail = self.gui_log_tail(log_path)
            message = "相机预览在 %.0f 秒内没有收到有效画面" % startup_timeout
            if detail:
                message += "：%s" % detail
            raise OperationError(message)
        self.log("桌面程序已启动；日志：%s" % log_path)

    def launch_rviz(self, payload: Dict[str, Any]) -> None:
        serial = self.validated_serial(payload)
        view = str(payload.get("view", ""))
        filename = RVIZ_VIEWS.get(view)
        if not filename:
            raise OperationError("未知 RViz 视图。")
        rviz = Path("/opt/ros/noetic/bin/rviz")
        if not rviz.is_file():
            raise OperationError("未安装 RViz。")
        config = self.generate_rviz(serial) / filename
        if not config.is_file():
            raise OperationError("RViz 配置不存在：%s" % filename)
        command = (
            "source /opt/ros/noetic/setup.bash; "
            "[ -f $HOME/catkin_ws/devel/setup.bash ] && source $HOME/catkin_ws/devel/setup.bash; "
            "%sexec %s -d %s" % (ROS_LOCAL_ENV, shlex.quote(str(rviz)), shlex.quote(str(config)))
        )
        if view == "trajectory":
            marker = APP_ROOT.parent / "assets" / "monitor" / "pose_to_markers.py"
            command = (
                "source /opt/ros/noetic/setup.bash; "
                "[ -f $HOME/catkin_ws/devel/setup.bash ] && source $HOME/catkin_ws/devel/setup.bash; "
                "%s/usr/bin/python3 %s %s & marker_pid=$!; "
                "trap 'kill $marker_pid >/dev/null 2>&1 || true' EXIT; %s -d %s"
                % (ROS_LOCAL_ENV, shlex.quote(str(marker)), shlex.quote(serial), shlex.quote(str(rviz)), shlex.quote(str(config)))
            )
        self.spawn_gui(["/bin/bash", "-lc", command], startup_timeout=2.0)
        self.log("RViz 已启动。")

    def launch_camera_preview(self, payload: Dict[str, Any]) -> None:
        device = str(payload.get("device", "/dev/video0"))
        if not re.match(r"^/dev/video[0-9]+$", device) or not Path(device).exists():
            raise OperationError("无效的视频设备。")
        try:
            width = int(payload.get("width", 1280))
            height = int(payload.get("height", 1280))
            fps = int(payload.get("fps", 60))
        except (TypeError, ValueError) as exc:
            raise OperationError("画面参数格式不正确。") from exc
        if width not in (640, 1280, 1920) or height not in (480, 720, 1080, 1280) or fps not in (30, 60, 100):
            raise OperationError("不支持的分辨率或帧率。")
        script = APP_ROOT / "tools" / "camera_preview.py"
        dependency_check = subprocess.run(
            ["/usr/bin/python3", "-c", "import cv2,numpy"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if dependency_check.returncode != 0:
            raise OperationError("缺少相机预览依赖，请先安装 python3-opencv 和 python3-numpy。")
        ready_file = Path(tempfile.gettempdir()) / ("fastumi-preview-%s.ready" % uuid.uuid4().hex)
        self.spawn_gui([
            "/usr/bin/python3", str(script), "--device", device,
            "--width", str(width), "--height", str(height), "--fps", str(fps),
            "--ready-file", str(ready_file),
        ], startup_timeout=8.0, ready_file=ready_file)
        self.log("相机预览已启动，按 q 可关闭窗口。")

    def launch_calibration(self, payload: Dict[str, Any]) -> None:
        calibration = APP_ROOT.parent / "assets" / "calibration"
        if not calibration.is_dir():
            calibration = Path("/opt/fastumi-tools/assets/calibration")
        demo = calibration / "demo-api"
        pipe = calibration / "pipe_srv"
        if not demo.is_file() or not pipe.is_file():
            raise OperationError("标定工具不完整。")
        if not Path("/usr/lib/libxvsdk.so").is_file() and not Path("/usr/local/lib/libxvsdk.so").is_file():
            raise OperationError("未检测到 libxvsdk.so，请先安装 SDK。")
        terminal = shutil.which("gnome-terminal")
        if not terminal:
            raise OperationError("标定控制台需要 gnome-terminal。")
        demo_cmd = "cd %s; ./demo-api; exec bash" % shlex.quote(str(calibration))
        pipe_cmd = "sleep 1; cd %s; ./pipe_srv; exec bash" % shlex.quote(str(calibration))
        self.spawn_gui([terminal, "--title=FastUMI Calibration API", "--", "/bin/bash", "-lc", demo_cmd], allow_early_success=True)
        self.spawn_gui([terminal, "--title=FastUMI Calibration Control", "--", "/bin/bash", "-lc", pipe_cmd], allow_early_success=True)
        self.log("标定控制台已启动；在 Control 窗口输入指令，例如 1-0-37。")

    def install_ros_wrapper(self, payload: Dict[str, Any]) -> None:
        self.require_root()
        source = Path("/usr/share/ros-wrapper/xv_sdk")
        setup = Path("/opt/ros/noetic/setup.bash")
        if not source.is_dir():
            raise OperationError("当前 SDK 未提供 ROS1 wrapper。")
        if not setup.is_file():
            raise OperationError("未安装 ROS Noetic。")
        account = active_desktop_user()
        if not account:
            raise OperationError("未找到桌面用户。")
        workspace = Path(account.pw_dir) / "catkin_ws"
        destination = workspace / "src" / "xv_sdk"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(source), str(destination), dirs_exist_ok=True)
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
        self.log("ROS1 wrapper 已安装到 %s。" % workspace)

    def ros_driver_active(self) -> bool:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", ROS_DRIVER_UNIT],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
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
            self.log("FastUMI ROS 数据源已经在运行。")
            return
        if any(topic.startswith("/xv_sdk/") and topic.count("/") > 2 for topic in self.ros_topic_list()):
            raise OperationError("检测到其他方式启动的 XVSDK ROS 节点，请先在原终端中停止它。")
        account = active_desktop_user()
        if not account:
            raise OperationError("未找到已登录的桌面用户。")
        setup = Path("/opt/ros/noetic/setup.bash")
        workspace_setup = Path(account.pw_dir) / "catkin_ws" / "devel" / "setup.bash"
        node = Path(account.pw_dir) / "catkin_ws" / "devel" / "lib" / "xv_sdk" / "xv_sdk"
        if not setup.is_file():
            raise OperationError("未安装 ROS Noetic。")
        if not workspace_setup.is_file() or not node.is_file():
            raise OperationError("ROS1 wrapper 尚未编译，请先在“软件与固件”页面安装 ROS1 Wrapper。")
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
        self.log("等待 XVSDK 发现相机并发布 Topic……")
        deadline = time.monotonic() + 30
        topics: List[str] = []
        while time.monotonic() < deadline:
            if not self.ros_driver_active():
                break
            topics = self.ros_topic_list()
            if any(topic.startswith("/xv_sdk/%s/" % serial) for serial in serials for topic in topics):
                self.log("ROS 数据源已就绪，共发现 %d 个 Topic。" % len(topics))
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
        raise OperationError("ROS 数据源启动失败：30 秒内未发现相机 Topic。")

    def restore_uvc(self) -> None:
        self.require_root()
        if self.ros_driver_active():
            raise OperationError("ROS 数据源仍在运行，不能同时恢复 V4L2 视频设备。")
        owners = usb_devices_in_use(get_usb_devices())
        occupied = [owner for device in owners for owner in (device.get("in_use_by") or [])]
        if occupied:
            detail = "、".join("%s(%s)" % (item["command"], item["pid"]) for item in occupied)
            raise OperationError("相机仍被其他进程占用：%s。" % detail)
        usb_root = Path("/sys/bus/usb/devices")
        bind = Path("/sys/bus/usb/drivers/uvcvideo/bind")
        if not bind.exists():
            self.run(["modprobe", "uvcvideo"], timeout=15)
        if not bind.exists():
            raise OperationError("系统没有提供 uvcvideo 驱动绑定入口。")
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
                        raise OperationError("无法恢复 UVC 接口 %s：%s" % (interface.name, exc)) from exc
                restored += 1
        if not restored:
            raise OperationError("没有找到可恢复的 FastUMI UVC 接口。")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if list(Path("/dev").glob("video*")):
                self.log("UVC 视频设备已恢复。")
                return
            time.sleep(0.2)
        raise OperationError("UVC 接口已绑定，但系统没有创建 /dev/video*。")

    def stop_ros_driver(self, payload: Dict[str, Any]) -> None:
        self.require_root()
        if self.ros_driver_active():
            self.run(["systemctl", "stop", ROS_DRIVER_UNIT], timeout=30)
            self.log("ROS 数据源已停止。")
            time.sleep(1)
        self.restore_uvc()
