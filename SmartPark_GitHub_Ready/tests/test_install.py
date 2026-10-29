import hashlib
from pathlib import Path
import tempfile
import unittest
from smartpark.check_install import check_files


class InstallTests(unittest.TestCase):
    def test_detects_partial_update_even_if_mqtt_version_is_new(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as name:
            root = Path(name)
            (root/'mqttlink.py').write_bytes(b'current')
            (root/'edge_b.py').write_bytes(b'legacy')
            expected = hashlib.sha256(b'current').hexdigest()
            failures = check_files(root, {'files': {'mqttlink.py': expected, 'edge_b.py': expected}})
            self.assertEqual([f[0] for f in failures], ['edge_b.py'])

    def test_detects_missing_file(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as name:
            self.assertEqual(check_files(Path(name), {'files': {'edge_a.py': 'x'}})[0][1], 'tidak ditemukan')

    def test_valid_package_and_path_boundary(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as name:
            root = Path(name)
            (root/'edge_a.py').write_bytes(b'current')
            manifest = {'files': {'edge_a.py': hashlib.sha256(b'current').hexdigest()}}
            self.assertEqual(check_files(root, manifest), [])
            self.assertTrue(check_files(root, {'files': {'../external.py': 'x'}}))

