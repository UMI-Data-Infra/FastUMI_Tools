#!/usr/bin/env python3
"""Low-latency V4L2 preview used by FastUMI Tools."""

import argparse
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="FastUMI V4L2 preview")
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=1280)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--ready-file")
    args = parser.parse_args()

    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        print("缺少相机预览依赖，请安装 python3-opencv 和 python3-numpy：%s" % exc, file=sys.stderr)
        return 2

    cap = cv2.VideoCapture(args.device, cv2.CAP_V4L2)
    if not cap.isOpened():
        print("无法打开 %s" % args.device, file=sys.stderr)
        return 1
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YU12"))
    cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, args.fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = cap.get(cv2.CAP_PROP_FPS)
    print(
        "已打开 %s：请求 %dx%d @ %d FPS，实际 %dx%d @ %.1f FPS"
        % (args.device, args.width, args.height, args.fps, actual_width, actual_height, actual_fps),
        flush=True,
    )

    title = "FastUMI Camera · %s · %dx%d @ %d FPS" % (
        args.device, args.width, args.height, args.fps,
    )
    startup_deadline = time.monotonic() + 6
    ready = False
    try:
        while True:
            ok, raw = cap.read()
            if not ok or raw is None:
                if not ready and time.monotonic() >= startup_deadline:
                    print("已打开设备，但 6 秒内没有收到有效画面。", file=sys.stderr)
                    return 3
                continue
            if raw.ndim == 2 and raw.size == args.width * args.height * 3 // 2:
                yuv = np.ascontiguousarray(raw).reshape(args.height * 3 // 2, args.width)
                frame = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)
            elif raw.ndim == 3:
                frame = raw
            else:
                if not ready and time.monotonic() >= startup_deadline:
                    print("相机返回了无法识别的画面格式。", file=sys.stderr)
                    return 4
                continue
            cv2.imshow(title, frame)
            key = cv2.waitKey(1) & 0xFF
            if not ready:
                ready = True
                if args.ready_file:
                    try:
                        Path(args.ready_file).write_text("ready\n", encoding="ascii")
                    except OSError as exc:
                        print("无法写入预览就绪标记：%s" % exc, file=sys.stderr)
                        return 5
            if key in (ord("q"), 27):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
