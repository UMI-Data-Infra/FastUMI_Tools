import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from fastumi_tools.ros_wrapper import patch_wrapper_source, TIMESTAMP_GUARD


class WrapperFixTests(unittest.TestCase):
    def vendor_source(self):
        return 'namespace xv {\n' + '\n'.join(
            'm_xvDevice->%s()->registerCallback([this](const %s & sample) { consume(sample); });'
            % (sensor, kind) for sensor, kind in [('slam', 'Pose'), ('imuSensor', 'Imu'), ('fisheyeCameras', 'FisheyeImages'), ('colorCamera', 'ColorImage'), ('tofCamera', 'DepthImage')]
        ) + '\nm_fisheyeCameraInfos.resize(m_xvFisheyesCalibs.size());\n}'

    def test_patching_is_idempotent_and_rejects_unknown_layout(self):
        fixed = patch_wrapper_source(self.vendor_source())
        self.assertEqual(patch_wrapper_source(fixed), fixed)
        self.assertEqual(fixed.count('if (!fastumiValidTimestamp('), 5)
        self.assertIn('std::max<std::size_t>(4, m_xvFisheyesCalibs.size())', fixed)
        with self.assertRaises(ValueError):
            patch_wrapper_source('namespace xv {}')

    def test_gen2_without_tof_is_supported(self):
        source = self.vendor_source().replace('m_xvDevice->tofCamera()', '// m_xvDevice->tofCamera()')
        fixed = patch_wrapper_source(source)
        self.assertEqual(fixed.count('if (!fastumiValidTimestamp('), 4)

    @unittest.skipUnless(shutil.which('g++'), 'C++ compiler unavailable')
    def test_cpp_timestamp_boundary_rejects_nonfinite_and_ros_overflow(self):
        source = '#include <cmath>\n#include <cassert>\n#include <limits>\n' + TIMESTAMP_GUARD + '''
int main() {
  assert(fastumiValidTimestamp(770524.33));
  assert(fastumiValidTimestamp(1789530500.123));
  assert(!fastumiValidTimestamp(std::numeric_limits<double>::infinity()));
  assert(!fastumiValidTimestamp(-std::numeric_limits<double>::infinity()));
  assert(!fastumiValidTimestamp(std::numeric_limits<double>::quiet_NaN()));
  assert(!fastumiValidTimestamp(-1));
  assert(!fastumiValidTimestamp(0));
  assert(!fastumiValidTimestamp(4294967296.0));
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'timestamps.cpp'
            src.write_text(source)
            binary = Path(tmp) / 'timestamps'
            subprocess.run(['g++', '-std=c++11', str(src), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
