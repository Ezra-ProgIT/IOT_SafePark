"""Check the complete installed package, including mixed/partial updates."""
import hashlib
import json
from pathlib import Path


def check_files(directory, manifest):
    failures = []
    for name, expected in manifest['files'].items():
        path = directory / name
        # Manifests only refer to direct package files, never external paths.
        if Path(name).name != name or '/' in name or '\\' in name:
            failures.append((name, 'nama berkas tidak valid'))
        elif not path.is_file():
            failures.append((name, 'tidak ditemukan'))
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            failures.append((name, 'isi berbeda / versi campur'))
    return failures


def main():
    directory = Path(__file__).resolve().parent
    print('Folder package:', directory)
    path = directory / 'build_manifest.json'
    if not path.is_file():
        print('GAGAL: build_manifest.json tidak ada; ekstrak paket lengkap.')
        raise SystemExit(1)
    manifest = json.loads(path.read_text(encoding='utf-8'))
    print('Paket:', manifest['build'])
    failures = check_files(directory, manifest)
    if failures:
        for name, reason in failures:
            print('GAGAL:', name, '-', reason)
        raise SystemExit(1)
    from . import mqttlink
    from .config import Settings
    print('MQTT core:', getattr(mqttlink, 'CORE_VERSION', 'VERSI_LAMA'))
    print('Project ID:', Settings().project)
    print('OK: seluruh berkas package cocok. Restart program A/B untuk memakai berkas ini.')


if __name__ == '__main__':
    main()
