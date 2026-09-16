import importlib.util
import unittest
from pathlib import Path

import cv2
import numpy as np

spec = importlib.util.spec_from_file_location('camera_preview', Path(__file__).resolve().parents[1] / 'app/tools/camera_preview.py')
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


class CameraDecodeTests(unittest.TestCase):
    def test_yuyv_two_channel_frame_is_converted_before_display(self):
        raw = np.full((4, 6, 2), 128, dtype=np.uint8)
        frame = preview.decode_frame(raw, 'YUYV', 6, 4)
        self.assertEqual(frame.shape, (4, 6, 3))
        np.testing.assert_array_equal(frame, cv2.cvtColor(raw, cv2.COLOR_YUV2BGR_YUY2))

    def test_raw_planar_formats_use_negotiated_size(self):
        for fmt, code in [('NV12', cv2.COLOR_YUV2BGR_NV12), ('YU12', cv2.COLOR_YUV2BGR_I420)]:
            raw = np.arange(36, dtype=np.uint8).reshape(6, 6)
            np.testing.assert_array_equal(preview.decode_frame(raw, fmt, 6, 4), cv2.cvtColor(raw, code))

    def test_auto_bgr_is_not_decoded_a_second_time(self):
        raw = np.arange(72, dtype=np.uint8).reshape(4, 6, 3)
        self.assertIs(preview.decode_frame(raw, 'MJPG', 6, 4, converted=True), raw)

    def test_mjpeg_is_decoded(self):
        raw = np.full((4, 6, 3), 127, dtype=np.uint8)
        ok, encoded = cv2.imencode('.jpg', raw)
        self.assertTrue(ok)
        frame = preview.decode_frame(encoded, 'MJPG', 6, 4)
        self.assertEqual(frame.shape, raw.shape)

    def test_gray_and_depth_and_invalid_frame(self):
        self.assertEqual(preview.decode_frame(np.zeros((4, 6), np.uint8), 'GREY', 6, 4).shape, (4, 6, 3))
        self.assertEqual(preview.decode_frame(np.zeros((4, 6), np.uint16), 'Y16 ', 6, 4).shape, (4, 6, 3))
        self.assertIsNone(preview.decode_frame(np.zeros((4, 6, 2), np.uint8), 'NV12', 6, 4))
        self.assertIsNone(preview.decode_frame(None, 'NV12', 6, 4))

    def test_gtk_without_visible_property_does_not_close_preview(self):
        from unittest.mock import Mock
        backend = Mock()
        backend.WND_PROP_AUTOSIZE = cv2.WND_PROP_AUTOSIZE
        backend.error = cv2.error
        backend.getWindowProperty.side_effect = lambda title, prop: 0 if prop == cv2.WND_PROP_AUTOSIZE else -1
        self.assertFalse(preview.window_closed(backend, 'Preview'))
        backend.getWindowProperty.side_effect = cv2.error('closed window')
        self.assertTrue(preview.window_closed(backend, 'Preview'))
