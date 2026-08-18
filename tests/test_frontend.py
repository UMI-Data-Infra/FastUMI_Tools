import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendTests(unittest.TestCase):
    def test_every_static_translation_key_has_both_languages(self):
        html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
        keys = set(re.findall(r'data-i18n(?:-html)?="([^"]+)"', html))
        self.assertGreater(len(keys), 40)
        for key in keys:
            self.assertGreaterEqual(
                javascript.count('"%s"' % key), 2,
                "%s must exist in both zh-CN and en dictionaries" % key,
            )

    def test_theme_and_language_controls_exist(self):
        html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
        self.assertIn('id="language-toggle"', html)
        self.assertIn('id="theme-toggle"', html)
        self.assertIn('src="/kuaimi-mark.svg"', html)
        self.assertIn("Copyright © 2026 FastUMI Team", html)
        self.assertIn("yding25@binghamton.edu", html)

    def test_firmware_preflight_is_separate_from_flash(self):
        html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
        self.assertIn('id="preflight-firmware"', html)
        self.assertIn('id="flash-firmware"', html)
        self.assertIn('startOperation("firmware-preflight"', javascript)
        self.assertIn('startOperation("firmware-flash"', javascript)

    def test_firmware_display_distinguishes_actual_reading_from_flash_record(self):
        javascript = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
        self.assertIn("d.firmware_release", javascript)
        self.assertIn("d.firmware_source", javascript)
        self.assertIn("d.firmware_observed_at", javascript)
        self.assertIn("d.managed_firmware_release", javascript)
        self.assertIn('"overview.recordMismatch"', javascript)

    def test_obsolete_visual_pose_is_not_exposed(self):
        html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
        self.assertNotIn("slam/visual_pose", html)
        self.assertNotIn('data-rviz="trajectory"', html)

    def test_calibration_instructions_use_verified_integer_sequence(self):
        content = (
            (ROOT / "app/static/index.html").read_text(encoding="utf-8")
            + (ROOT / "app/static/app.js").read_text(encoding="utf-8")
        )
        self.assertNotIn("1-0-37", content)
        self.assertIn("先输入 1，初始化后再输入 37", content)

    def test_camera_preview_can_be_closed_from_web_interface(self):
        html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
        self.assertIn('id="stop-preview"', html)
        self.assertIn('startOperation("camera-preview-stop"', javascript)
        self.assertIn("preview_running", javascript)
        self.assertIn("stayOnPage=false", javascript)
        self.assertIn('startOperation("camera-preview-stop",{},true)', javascript)


if __name__ == "__main__":
    unittest.main()
