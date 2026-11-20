import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_portable import reuse_components


class ReleaseReuseTests(unittest.TestCase):
    def test_reuses_only_unchanged_archives_after_checking_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / 'old'
            new = root / 'new'
            old.mkdir()
            new.mkdir()
            old_zip = old / 'ffmpeg.zip'
            old_zip.write_bytes(b'verified archive')
            entry = {'archive': old_zip.name, 'size': old_zip.stat().st_size,
                     'sha256': hashlib.sha256(old_zip.read_bytes()).hexdigest(), 'url': ''}
            catalog = old / 'component-catalog.json'
            catalog.write_text(json.dumps({'architecture': 'win-x64', 'components': {
                'base': {'archive': 'base.zip'}, 'ffmpeg': entry,
            }}), encoding='utf-8')
            picked = reuse_components(catalog, new, {'base'}, 'https://example.invalid/release')
            self.assertEqual(list(picked), ['ffmpeg'])
            self.assertEqual(picked['ffmpeg']['url'], 'https://example.invalid/release/ffmpeg.zip')
            self.assertEqual((new / old_zip.name).read_bytes(), old_zip.read_bytes())
            old_zip.write_bytes(b'invalid archive!')
            with self.assertRaises(ValueError):
                reuse_components(catalog, new, {'base'}, '')
