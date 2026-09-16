"""Apply narrow, repeatable fixes to the vendor ROS1 wrapper before building."""

import re
from pathlib import Path


TIMESTAMP_GUARD = """// FASTUMI_VALID_TIMESTAMP: reject corrupt samples without inventing capture times.
static bool fastumiValidTimestamp(double stamp)
{
  return std::isfinite(stamp) && stamp > 0.0 && stamp < 4294967295.0;
}

"""


def _patch_timestamp_guards(content: str) -> str:
    if "// FASTUMI_VALID_TIMESTAMP:" in content:
        return content
    # These callbacks feed the publisher queues. Reject before queueing, so an
    # invalid timestamp cannot reach ROS, TF, trajectories, or RGB alignment.
    pattern = re.compile(
        r"(^[ \t]*m_xvDevice->(?:slam|imuSensor|fisheyeCameras|colorCamera|tofCamera)"
        r"\(\)->registerCallback\(\[this\]\(const "
        r"(Pose|Imu|FisheyeImages|ColorImage|DepthImage)\s*&\s*(\w+)\)\s*\{)",
        re.MULTILINE,
    )
    found = set()

    def guard(match):
        kind, name = match.group(2, 3)
        found.add(kind)
        stamp = name + (".hostTimestamp()" if kind == "Pose" else ".hostTimestamp")
        return match.group(1) + (
            '\n      if (!fastumiValidTimestamp(%s))\n'
            '      {\n'
            '        ROS_WARN_THROTTLE(5.0, "FastUMI: dropped %s sample with invalid host timestamp %%g", %s);\n'
            '        return;\n'
            '      }\n' % (stamp, kind, stamp)
        )

    patched = pattern.sub(guard, content)
    # The stable Gen 2 SDK intentionally has no ToF callback.
    if not {"Pose", "Imu", "FisheyeImages", "ColorImage"}.issubset(found):
        raise ValueError("Unsupported XVSDK wrapper layout: timestamp guards were not applied")
    marker = "namespace xv {"
    if marker not in patched:
        raise ValueError("Unsupported XVSDK wrapper namespace")
    return "#include <cmath>\n" + patched.replace(marker, TIMESTAMP_GUARD + marker, 1)


def patch_wrapper_source(content: str) -> str:
    patched = _patch_timestamp_guards(content)
    old = "m_fisheyeCameraInfos.resize(m_xvFisheyesCalibs.size());"
    new = "m_fisheyeCameraInfos.resize(std::max<std::size_t>(4, m_xvFisheyesCalibs.size()));"
    # Simple cameras can deliver four images with fewer calibration records.
    # Empty CameraInfo keeps K[0] == 0 (ROS's uncalibrated marker), rather than
    # indexing beyond the vector or inventing calibration values.
    if old not in patched and new not in patched:
        raise ValueError("Unsupported XVSDK fisheye calibration layout")
    patched = patched.replace(old, new)
    return patched


def patch_ros_wrapper(destination: Path) -> None:
    source = destination / "src" / "xv_sdk_wrapper.cpp"
    original = source.read_text(encoding="utf-8")
    patched = patch_wrapper_source(original)
    if patched != original:
        source.write_text(patched, encoding="utf-8")
