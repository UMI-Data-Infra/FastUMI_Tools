#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import rospy
from xv_sdk.msg import PoseStampedConfidence
from visualization_msgs.msg import Marker, MarkerArray


if len(sys.argv) < 2:
    print("Usage: pose_to_markers.py <device_serial>", file=sys.stderr)
    sys.exit(1)

SERIAL_NUMBER = sys.argv[1]
class PoseStampedToMarkers:
    def __init__(self):
        self.in_topic   = rospy.get_param("~in_topic", f"/xv_sdk/{SERIAL_NUMBER}/slam/pose")
        self.out_topic  = rospy.get_param("~out_topic", f"/xv_sdk/{SERIAL_NUMBER}/slam/pose_markers")
        self.max_points = int(rospy.get_param("~max_points", 100))  # 轨迹最多保留点数
        self.min_dist   = float(rospy.get_param("~min_dist", 0.01))  # 抽稀间距(米)
        self.min_dt     = float(rospy.get_param("~min_dt", 0.0))     # 时间抽稀(秒)，0 表示不用
        self.override_frame = rospy.get_param("~frame_id", "")       # 非空则强制改写 frame_id
        self.arrow_scale = float(rospy.get_param("~arrow_scale", 0.5))  # 箭头大小

        # 时间窗口和渐变效果参数
        self.time_window = float(rospy.get_param("~time_window", 5.0))  # 时间窗口长度(秒)
        self.fade_duration = float(rospy.get_param("~fade_duration", 1.0))  # 渐变持续时间(秒)
        self.stationary_threshold = float(rospy.get_param("~stationary_threshold", 2.0))  # 静止检测阈值(秒)
        self.stationary_fade_speed = float(rospy.get_param("~stationary_fade_speed", 2.0))  # 静止时衰减速度
        self.update_rate = float(rospy.get_param("~update_rate", 10.0))  # 更新频率(Hz)

        # 发布MarkerArray消息
        self.pub = rospy.Publisher(self.out_topic, MarkerArray, queue_size=1, latch=True)

        self.markers = []  # 存储所有marker，每个元素是(marker, create_time)的元组
        self.last_ps = None
        self.last_stamp = None
        self.last_movement_time = rospy.Time.now()  # 记录最后一次运动时间
        self.is_stationary = False  # 是否处于静止状态

        self.sub = rospy.Subscriber(self.in_topic, PoseStampedConfidence, self.cb, queue_size=200)

        # 添加定时器来定期更新marker透明度
        self.update_timer = rospy.Timer(rospy.Duration(1.0/self.update_rate), self.update_markers)
        rospy.loginfo("PoseStampedToMarkers: in=%s (PoseStamped) -> out=%s (MarkerArray)",
                      self.in_topic, self.out_topic)


    def cb(self, ps: PoseStampedConfidence):
        # 可选：强制改写坐标系
        if self.override_frame:
            ps.poseMsg.header.frame_id = self.override_frame
        # 时间抽稀
        if self.min_dt > 0 and self.last_stamp is not None:
            if (ps.poseMsg.header.stamp - self.last_stamp).to_sec() < self.min_dt:
                return

        # 距离抽稀
        if self.last_ps is not None:
            dx = ps.poseMsg.pose.position.x - self.last_ps.poseMsg.pose.position.x
            dy = ps.poseMsg.pose.position.y - self.last_ps.poseMsg.pose.position.y
            dz = ps.poseMsg.pose.position.z - self.last_ps.poseMsg.pose.position.z
            if (dx*dx + dy*dy + dz*dz) < (self.min_dist**2):
                return

        self.last_ps = ps
        self.last_stamp = ps.poseMsg.header.stamp
        self.last_movement_time = rospy.Time.now()  # 更新运动时间
        self.is_stationary = False  # 重置静止状态

        # 创建箭头marker
        marker = Marker()
        marker.header = ps.poseMsg.header
        marker.ns = "pose_arrows"
        marker.id = len(self.markers)
        marker.type = Marker.ARROW
        marker.action = Marker.ADD

        # 设置位置和方向
        marker.pose = ps.poseMsg.pose

        # 设置箭头大小
        marker.scale.x = self.arrow_scale * 0.3333  # 箭头长度
        marker.scale.y = self.arrow_scale * 0.01  # 箭头宽度
        marker.scale.z = self.arrow_scale * 0.01  # 箭头高度

        # 设置颜色（初始完全不透明）
        marker.color.r = 1.0
        marker.color.g = 0.0
        marker.color.b = 0.0
        marker.color.a = 1.0

        # 添加到列表，同时记录创建时间
        create_time = rospy.Time.now()
        self.markers.append((marker, create_time))

        # 限制marker数量
        if len(self.markers) > self.max_points:
            self.markers.pop(0)
            # 重新分配ID
            for i, (m, _) in enumerate(self.markers):
                m.id = i

    def update_markers(self, event):
        """定时更新marker透明度"""
        current_time = rospy.Time.now()

        # 检测是否静止
        time_since_movement = (current_time - self.last_movement_time).to_sec()
        if time_since_movement > self.stationary_threshold:
            self.is_stationary = True
        else:
            self.is_stationary = False

        # 更新每个marker的透明度
        markers_to_remove = []
        for i, (marker, create_time) in enumerate(self.markers):
            age = (current_time - create_time).to_sec()

            # 计算透明度
            alpha = self.calculate_alpha(age, time_since_movement)

            if alpha <= 0:
                # 完全透明的marker标记为删除
                markers_to_remove.append(i)
            else:
                # 更新透明度
                marker.color.a = alpha

        # 移除完全透明的marker（从后往前删除，避免索引问题）
        for i in reversed(markers_to_remove):
            self.markers.pop(i)

        # 重新分配ID
        for i, (marker, _) in enumerate(self.markers):
            marker.id = i

        # 发布更新后的MarkerArray
        if self.markers:
            marker_array = MarkerArray()
            marker_array.markers = [marker for marker, _ in self.markers]
            self.pub.publish(marker_array)

    def calculate_alpha(self, age, time_since_movement):
        """计算marker的透明度"""
        if self.is_stationary:
            # 静止状态：加速衰减
            stationary_time = time_since_movement - self.stationary_threshold
            fade_factor = 1.0 + stationary_time * self.stationary_fade_speed
            base_alpha = 1.0 / fade_factor
        else:
            # 运动状态：正常衰减
            base_alpha = 1.0

        # 时间窗口内的marker保持不透明
        if age <= self.time_window:
            return base_alpha

        # 超出时间窗口的marker开始渐变
        fade_start = self.time_window
        fade_end = self.time_window + self.fade_duration

        if age >= fade_end:
            return 0.0  # 完全透明

        # 线性渐变
        fade_progress = (age - fade_start) / self.fade_duration
        return base_alpha * (1.0 - fade_progress)

if __name__ == "__main__":
    rospy.init_node(f"xv_pose_to_markers_{SERIAL_NUMBER}")
    PoseStampedToMarkers()
    rospy.spin()
