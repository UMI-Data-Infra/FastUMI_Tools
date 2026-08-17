#!/usr/bin/env python3
"""Expose the vendor SLAM pose as a standard ROS PoseStamped message."""

import sys

import rospy
from geometry_msgs.msg import PoseStamped
from xv_sdk.msg import PoseStampedConfidence


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: slam_pose_bridge.py <device_serial>", file=sys.stderr)
        raise SystemExit(2)

    serial = sys.argv[1]
    input_topic = "/xv_sdk/%s/slam/pose" % serial
    output_topic = "/xv_sdk/%s/slam/current_pose" % serial
    publisher = rospy.Publisher(output_topic, PoseStamped, queue_size=5)

    def publish(message: PoseStampedConfidence) -> None:
        publisher.publish(message.poseMsg)

    rospy.init_node("xv_slam_pose_bridge_%s" % serial, anonymous=False)
    rospy.Subscriber(input_topic, PoseStampedConfidence, publish, queue_size=20)
    rospy.loginfo("SLAM pose bridge: %s -> %s", input_topic, output_topic)
    rospy.spin()


if __name__ == "__main__":
    main()
