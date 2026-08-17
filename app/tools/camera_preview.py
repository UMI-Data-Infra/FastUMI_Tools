#!/usr/bin/env python3
"""Low-latency V4L2 preview used by FastUMI Tools."""

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="FastUMI V4L2 preview")
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=1280)
    parser.add_argument("--fps", type=int, default=60)
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

    title = "FastUMI Camera · %s · %dx%d @ %d FPS" % (
        args.device, args.width, args.height, args.fps,
    )
    try:
        while True:
            ok, raw = cap.read()
            if not ok or raw is None:
                continue
            if raw.ndim == 2 and raw.size == args.width * args.height * 3 // 2:
                yuv = np.ascontiguousarray(raw).reshape(args.height * 3 // 2, args.width)
                frame = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)
            elif raw.ndim == 3:
                frame = raw
            else:
                continue
            cv2.imshow(title, frame)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
