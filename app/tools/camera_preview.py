#!/usr/bin/env python3
"""Direct OpenCV/V4L2 preview; no ROS or XVSDK imports are required."""

import argparse
import sys
import time
from pathlib import Path


def decode_frame(raw, pixel_format, width, height, converted=False):
    import cv2
    import numpy as np

    if raw is None or not raw.size:
        return None
    if converted and raw.ndim == 3 and raw.shape[2] == 3:
        return raw
    fmt = pixel_format.strip()
    data = np.ascontiguousarray(raw)
    if fmt in ("MJPG", "JPEG"):
        return cv2.imdecode(data.reshape(-1), cv2.IMREAD_COLOR)
    if fmt in ("NV12", "YU12", "I420") and data.nbytes == width * height * 3 // 2:
        yuv = data.view(np.uint8).reshape(height * 3 // 2, width)
        return cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_NV12 if fmt == "NV12" else cv2.COLOR_YUV2BGR_I420)
    if fmt in ("YUYV", "YUY2", "UYVY") and data.nbytes == width * height * 2:
        yuv = data.view(np.uint8).reshape(height, width, 2)
        return cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_UYVY if fmt == "UYVY" else cv2.COLOR_YUV2BGR_YUY2)
    if fmt in ("Y8", "GREY") and data.nbytes == width * height:
        return cv2.cvtColor(data.reshape(height, width), cv2.COLOR_GRAY2BGR)
    if fmt == "Y16" and data.nbytes == width * height * 2:
        depth = data.view(np.uint16).reshape(height, width)
        gray = cv2.normalize(depth, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    if raw.ndim == 3 and raw.shape[2] == 3:
        return raw
    return None


def window_closed(cv2, title):
    try:
        # Ubuntu 20.04's GTK backend does not implement WND_PROP_VISIBLE and
        # returns -1 even for an open window. AUTOSIZE is supported by GTK.
        return cv2.getWindowProperty(title, cv2.WND_PROP_AUTOSIZE) < 0
    except cv2.error:
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description="FastUMI OpenCV camera preview (without ROS)")
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=1280)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--format", default="AUTO", choices=("AUTO", "NV12", "YU12", "Y8", "Y16", "MJPG", "YUYV"))
    parser.add_argument("--ready-file")
    parser.add_argument("--check-frames", type=int, default=0, help="Check N decoded frames without opening a window")
    args = parser.parse_args()
    try:
        import cv2
    except ImportError as exc:
        print("缺少相机预览依赖，请安装 python3-opencv 和 python3-numpy：%s" % exc, file=sys.stderr)
        return 2

    cap = cv2.VideoCapture(args.device, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap.release()
        print("无法打开 %s；请检查设备是否被 ROS 或其他程序占用。" % args.device, file=sys.stderr)
        return 1
    try:
        if args.format != "AUTO":
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*args.format.ljust(4)))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
        cap.set(cv2.CAP_PROP_FPS, args.fps)
        # AUTO lets OpenCV decode ordinary webcams. Sensor raw formats use the
        # actual negotiated FOURCC below, even if a driver rejected our request.
        cap.set(cv2.CAP_PROP_CONVERT_RGB, int(args.format == "AUTO"))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        fmt = ''.join(chr((fourcc >> (8 * i)) & 255) for i in range(4)).strip()
        converted = bool(cap.get(cv2.CAP_PROP_CONVERT_RGB))
        print("已打开 %s：请求 %dx%d @ %d FPS，实际 %dx%d @ %.1f FPS，格式 %s" % (
            args.device, args.width, args.height, args.fps, width, height, fps, fmt), flush=True)
        title = "FastUMI Camera - %s - %dx%d @ %.1f FPS" % (args.device, width, height, fps)
        last_valid = time.monotonic()
        count = 0
        started = last_valid
        while True:
            ok, raw = cap.read()
            frame = decode_frame(raw, fmt, width, height, converted) if ok else None
            if frame is None:
                if time.monotonic() - last_valid >= 6:
                    print("6 秒内没有收到可解码的画面；实际格式：%s。" % fmt, file=sys.stderr)
                    return 3
                time.sleep(0.01)
                continue
            last_valid = time.monotonic()
            if not args.check_frames:
                if count == 0:
                    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
                    scale = min(1.0, 1000 / frame.shape[1], 800 / frame.shape[0])
                    cv2.resizeWindow(title, int(frame.shape[1] * scale), int(frame.shape[0] * scale))
                cv2.imshow(title, frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27) or window_closed(cv2, title):
                    break
            if count == 0 and args.ready_file:
                Path(args.ready_file).write_text("ready\n", encoding="ascii")
            count += 1
            if args.check_frames and count >= args.check_frames:
                print("验证成功：%d 帧，图像 %s，实测 %.1f FPS" % (
                    count, frame.shape, count / max(time.monotonic() - started, 0.001)), flush=True)
                break
    except (cv2.error, OSError, ValueError) as exc:
        print("相机预览失败：%s" % exc, file=sys.stderr)
        return 4
    finally:
        cap.release()
        if not args.check_frames:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
