import hashlib
import json
import shutil
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from q_pixelate.components import ComponentError, ComponentManager
from q_pixelate.process import Cancelled, Runner


class ComponentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'release'
        self.root.mkdir()
        self.archive = self.root.parent / 'offline.zip'

    def package(self, files, version='1.0', key='320n'):
        with zipfile.ZipFile(self.archive, 'w') as archive:
            for name, data in files.items():
                member = zipfile.ZipInfo(name)
                member.filename = name
                archive.writestr(member, data)
        content = self.archive.read_bytes()
        (self.root / 'component-catalog.json').write_text(json.dumps({'components': {key: {
            'version': version, 'url': '', 'size': len(content),
            'sha256': hashlib.sha256(content).hexdigest(),
        }}}), encoding='utf-8')

    def install_model(self):
        self.package({'weights.onnx': b'original'})
        manager = ComponentManager(self.root)
        manager.install('320n', Runner(), self.archive)
        return manager

    def test_verified_offline_install_survives_relocation(self):
        manager = self.install_model()
        self.assertEqual(manager.status('320n')['installed_size'], 8)
        moved = self.root.rename(self.root.parent / 'moved')
        manager = ComponentManager(moved)
        self.assertTrue(manager.status('320n')['ready'])
        self.assertEqual(manager.status('320n')['version'], '1.0')
        self.assertEqual(manager.path('320n'), moved / 'components' / '320n')
        self.assertEqual((manager.path('320n') / 'weights.onnx').read_bytes(), b'original')

    def test_install_uses_the_adjacent_release_archive_without_a_url(self):
        self.package({'weights.onnx': b'local model'})
        catalog = self.root / 'component-catalog.json'
        data = json.loads(catalog.read_text(encoding='utf-8'))
        data['components']['320n']['archive'] = 'model.zip'
        catalog.write_text(json.dumps(data), encoding='utf-8')
        self.archive.rename(self.root / 'model.zip')
        manager = ComponentManager(self.root)
        self.assertTrue(manager.install('320n', Runner())['ready'])
        self.assertEqual((manager.path('320n') / 'weights.onnx').read_bytes(), b'local model')

    def test_same_size_corruption_keeps_the_previous_version(self):
        manager = self.install_model()
        content = self.archive.read_bytes()
        self.archive.write_bytes(bytes([content[0] ^ 1]) + content[1:])
        with self.assertRaises(ComponentError):
            manager.install('320n', Runner(), self.archive)
        self.assertEqual((manager.path('320n') / 'weights.onnx').read_bytes(), b'original')

    def test_rejected_archive_cannot_escape_or_replace_old_version(self):
        manager = self.install_model()
        for member in ('../escape', 'C:/escape', 'data\\escape', 'aux.dat',
                       'data/../../escape', 'WEIGHTS.ONNX', 'weights.onnx:alternate'):
            with self.subTest(member=member):
                self.package({'weights.onnx': b'new', member: b'bad'}, version='2.0')
                with self.assertRaises(ComponentError):
                    ComponentManager(self.root).install('320n', Runner(), self.archive)
                self.assertEqual((manager.path('320n') / 'weights.onnx').read_bytes(), b'original')

    def test_failed_verification_retains_the_previous_version(self):
        manager = self.install_model()
        self.package({'weights.onnx': b''}, version='2.0')
        with self.assertRaises(ComponentError):
            ComponentManager(self.root).install('320n', Runner(), self.archive)
        self.assertEqual(manager.status('320n')['version'], '1.0')

    def test_cancellation_in_staging_retains_the_previous_version(self):
        manager = self.install_model()
        self.package({'weights.onnx': b'new'}, version='2.0')
        cancel = threading.Event()

        def progress(value, _message):
            if value >= 50:
                cancel.set()

        with self.assertRaises(Cancelled):
            ComponentManager(self.root).install('320n', Runner(cancel, progress), self.archive)
        self.assertEqual((manager.path('320n') / 'weights.onnx').read_bytes(), b'original')
        self.assertEqual(sorted(path.name for path in manager.path('320n').parent.iterdir()
                                if path.is_dir()), ['320n'])

    def test_second_manager_cannot_change_components_during_install(self):
        manager = self.install_model()
        checked = False

        def progress(_value, _message):
            nonlocal checked
            if not checked:
                checked = True
                with self.assertRaises(ComponentError):
                    ComponentManager(self.root).uninstall('320n')

        manager.install('320n', Runner(progress=progress), self.archive)
        self.assertTrue(checked)
        self.assertTrue(manager.status('320n')['ready'])

    def test_uninstall_only_removes_owned_component(self):
        manager = self.install_model()
        external = manager.path('640m')
        external.mkdir()
        (external / 'weights.onnx').write_bytes(b'user file')
        with self.assertRaises(ComponentError):
            manager.uninstall('640m')
        manager.uninstall('320n')
        self.assertFalse(manager.path('320n').exists())
        self.assertEqual((external / 'weights.onnx').read_bytes(), b'user file')

    def test_install_refuses_to_overwrite_an_unowned_directory(self):
        self.package({'weights.onnx': b'new'})
        manager = ComponentManager(self.root)
        manager.path('320n').mkdir(parents=True)
        (manager.path('320n') / 'weights.onnx').write_bytes(b'user file')
        with self.assertRaises(ComponentError):
            manager.install('320n', Runner(), self.archive)
        self.assertEqual((manager.path('320n') / 'weights.onnx').read_bytes(), b'user file')

    def test_preferences_keep_internal_reference_relative_after_moving(self):
        manager = ComponentManager(self.root)
        tools = self.root / 'tools'
        tools.mkdir()
        manager.set_ffmpeg_directory(tools)
        manager.set_preferences(model='640m', device='gpu')
        moved = self.root.rename(self.root.parent / 'moved')
        manager = ComponentManager(moved)
        preferences = manager.preferences()
        self.assertEqual(Path(preferences['ffmpeg_directory']), moved / 'tools')
        self.assertEqual((preferences['model'], preferences['device']), ('640m', 'gpu'))
        manager.set_ffmpeg_directory(None)
        self.assertEqual(manager.preferences(), {'model': '640m', 'device': 'gpu',
                                                  'ffmpeg_directory': None})

    def test_failed_replacement_rename_restores_the_previous_version(self):
        manager = self.install_model()
        self.package({'weights.onnx': b'new'}, version='2.0')
        original_rename = Path.rename

        def rename(path, target):
            if Path(target) == manager.path('320n') and path.name == 'payload':
                raise OSError('Destination temporarily unavailable')
            return original_rename(path, target)

        with patch.object(Path, 'rename', rename), self.assertRaises(ComponentError):
            ComponentManager(self.root).install('320n', Runner(), self.archive)
        self.assertEqual((manager.path('320n') / 'weights.onnx').read_bytes(), b'original')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg pair unavailable')
    def test_ffmpeg_tools_use_system_pair_when_configured_path_has_no_tools(self):
        manager = ComponentManager(self.root)
        empty = self.root / 'tools'
        empty.mkdir()
        manager.set_ffmpeg_directory(empty)
        state = manager.status('ffmpeg', verify=True)
        self.assertTrue(state['ready'], state['message'])
        self.assertEqual(state['source'], 'PATH')
        self.assertEqual({name: Path(path) for name, path in manager.ffmpeg_tools().items()},
                         {name: Path(shutil.which(name)).resolve() for name in ('ffmpeg', 'ffprobe')})

    def test_explicit_ffmpeg_directory_takes_priority_over_the_managed_copy(self):
        files = {'bin/ffmpeg.exe': b'ffmpeg', 'bin/ffprobe.exe': b'ffprobe'}
        self.package(files, key='ffmpeg')
        external = self.root / 'external'
        external.mkdir()
        for name, content in files.items():
            (external / Path(name).name).write_bytes(content)
        manager = ComponentManager(self.root)
        with patch('q_pixelate.components._probe_ffmpeg', return_value='8.0'):
            manager.install('ffmpeg', Runner(), self.archive)
            manager.set_ffmpeg_directory(external)
            self.assertEqual(manager.status('ffmpeg')['source'], 'configured')
            self.assertEqual(Path(manager.ffmpeg_tools()['ffmpeg']).parent, external)
            manager.set_ffmpeg_directory(None)
            self.assertEqual(manager.status('ffmpeg')['source'], 'managed')
