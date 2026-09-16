#!/usr/bin/env python3
"""Require real image/IMU samples before declaring a managed ROS source ready."""
import argparse
import collections
import json
import threading


def main():
    import rospy
    from sensor_msgs.msg import Image, Imu

    parser = argparse.ArgumentParser()
    parser.add_argument('serial', nargs='+')
    parser.add_argument('--timeout', type=float, default=10)
    args = parser.parse_args()
    rospy.init_node('fastumi_stream_probe', anonymous=True, disable_signals=True)
    counts = collections.Counter()
    ready = threading.Event()
    lock = threading.Lock()

    def received(msg, topic):
        if msg.header.stamp.to_sec() <= 0:
            return
        if isinstance(msg, Image) and (not msg.data or not msg.width or not msg.height):
            return
        with lock:
            counts[topic] += 1
            if counts[topic] >= 3:
                ready.set()

    subscribers = []
    for serial in args.serial:
        for suffix, kind in [('color_camera/image', Image), ('fisheye_cameras/left/image', Image), ('imu_sensor/data_raw', Imu)]:
            topic = '/xv_sdk/%s/%s' % (serial, suffix)
            subscribers.append(rospy.Subscriber(topic, kind, received, callback_args=topic, queue_size=1))
    ok = ready.wait(args.timeout)
    for sub in subscribers:
        sub.unregister()
    with lock:
        print(json.dumps(dict(counts), sort_keys=True), flush=True)
    rospy.signal_shutdown('probe complete')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
