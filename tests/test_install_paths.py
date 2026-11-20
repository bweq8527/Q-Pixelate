import hashlib
import json
import os
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from q_pixelate.components import ComponentError, ComponentManager
from q_pixelate.process import Cancelled, Runner


class InstallPathsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.top = Path(temporary.name).resolve()
        self.program = self.top / '程序 目录'
        self.program.mkdir()
        self.settings = self.top / 'Local App Data' / 'Q-Pixelate'
        self.settings.mkdir(parents=True)
        self.config = self.settings / 'installation.json'
        self.source = self.top / '组件 目录'
        self.destination = self.top / '组件 新目录'
        self.archives = self.top / '离线 包'
        self.archives.mkdir()
        self.installer = self.settings / 'Q-Pixelate-Setup.exe'
        env = patch.dict(os.environ, LOCALAPPDATA=str(self.settings.parent))
        env.start()
        self.addCleanup(env.stop)

    def register(self, program=None):
        self.config.write_text(json.dumps({
            'program_dir': str(program or self.program), 'component_dir': str(self.source),
            'archive_dir': str(self.archives), 'installer': str(self.installer),
        }, ensure_ascii=False), encoding='utf-8-sig')

    def install_model(self, manager):
        archive = self.archives / 'model.zip'
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr('weights.onnx', b'original model')
        content = archive.read_bytes()
        (self.program / 'component-catalog.json').write_text(json.dumps({'components': {
            '320n': {'version': '1.0', 'archive': archive.name, 'size': len(content),
                     'sha256': hashlib.sha256(content).hexdigest()},
        }}), encoding='utf-8')
        manager = ComponentManager(manager.root)
        manager.install('320n', Runner(), archive)
        return manager

    def test_registered_components_and_archive_paths_are_independent(self):
        self.register()
        manager = self.install_model(ComponentManager(self.program))
        self.assertEqual(manager.root, self.program)
        self.assertEqual(manager.component_directory, self.source)
        self.assertEqual(manager.installer, self.installer)
        self.assertTrue((self.source / '320n' / 'weights.onnx').is_file())
        self.assertEqual(manager.local_archive('320n'), self.archives / 'model.zip')
        adjacent = self.program / 'model.zip'
        adjacent.touch()
        self.assertEqual(manager.local_archive('320n'), adjacent)

    def test_registered_preferences_survive_program_move_without_affecting_portable(self):
        self.register()
        external = self.program / 'other tools'
        manager = ComponentManager(self.program)
        manager.set_preferences(model='640m', device='gpu', ffmpeg_directory=external)
        moved = self.top / '新位置'
        moved.mkdir()
        self.register(moved)
        self.assertEqual(ComponentManager(moved).preferences(), {
            'model': '640m', 'device': 'gpu', 'ffmpeg_directory': str(external),
        })
        portable = ComponentManager(self.program)
        self.assertEqual(portable.component_directory, self.program / 'components')
        self.assertIsNone(portable.installer)
        self.assertIsNone(portable.preferences()['ffmpeg_directory'])

    def test_move_keeps_unknown_files_and_updates_registered_component_path(self):
        self.register()
        manager = self.install_model(ComponentManager(self.program))
        old_file = self.source / 'my-file.txt'
        old_file.write_text('original user file', encoding='utf-8')
        self.destination.mkdir()
        new_file = self.destination / 'my-file.txt'
        new_file.write_text('destination user file', encoding='utf-8')
        self.assertEqual(manager.relocate_components(self.destination, Runner(), cleanup=True),
                         self.destination)
        self.assertEqual(manager.component_directory, self.destination)
        reopened = ComponentManager(self.program)
        self.assertTrue(reopened.status('320n')['ready'])
        self.assertEqual((reopened.path('320n') / 'weights.onnx').read_bytes(), b'original model')
        self.assertFalse((self.source / '320n').exists())
        self.assertEqual(old_file.read_text(encoding='utf-8'), 'original user file')
        self.assertEqual(new_file.read_text(encoding='utf-8'), 'destination user file')

    def test_move_refuses_a_destination_component_directory_owned_by_user(self):
        manager = self.install_model(ComponentManager(self.program))
        occupied = self.destination / '320n'
        occupied.mkdir(parents=True)
        user_file = occupied / 'weights.onnx'
        user_file.write_bytes(b'user file')
        with self.assertRaises(ComponentError):
            manager.relocate_components(self.destination, Runner())
        self.assertEqual(user_file.read_bytes(), b'user file')
        self.assertEqual(ComponentManager(self.program).path('320n'), manager.path('320n'))

    def test_cancellation_leaves_old_configuration_and_component_copy(self):
        self.register()
        manager = self.install_model(ComponentManager(self.program))
        initial_config = self.config.read_bytes()
        for stop_at in (10, 95):
            with self.subTest(stop_at=stop_at):
                cancel = threading.Event()

                def progress(value, _message):
                    if value >= stop_at:
                        cancel.set()

                with self.assertRaises(Cancelled):
                    manager.relocate_components(self.destination, Runner(cancel, progress))
                self.assertEqual(self.config.read_bytes(), initial_config)
                self.assertTrue(manager.status('320n')['ready'])
                self.assertFalse((self.destination / '320n').exists())

    def test_insufficient_space_keeps_the_current_component_path(self):
        self.register()
        manager = self.install_model(ComponentManager(self.program))
        with patch('q_pixelate.components.shutil.disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaises(ComponentError):
                manager.relocate_components(self.destination, Runner(), cleanup=True)
        self.assertEqual(ComponentManager(self.program).component_directory, self.source)
        self.assertTrue(manager.status('320n')['ready'])
        self.assertFalse((self.destination / '320n').exists())

    def test_failed_configuration_write_removes_the_promoted_copy(self):
        self.register()
        manager = self.install_model(ComponentManager(self.program))
        original_config = self.config.read_bytes()
        original_replace = Path.replace

        def reject_config_write(path, target):
            if target == self.config:
                raise PermissionError('configuration is read-only')
            return original_replace(path, target)

        with patch.object(Path, 'replace', reject_config_write):
            with self.assertRaisesRegex(ComponentError, 'configuration is read-only'):
                manager.relocate_components(self.destination, Runner(), cleanup=True)
        self.assertEqual(self.config.read_bytes(), original_config)
        self.assertTrue(manager.status('320n')['ready'])
        self.assertFalse((self.destination / '320n').exists())

    def test_candidate_probe_failure_restores_original_layout(self):
        self.register()
        manager = self.install_model(ComponentManager(self.program))
        initial_config = self.config.read_bytes()
        probe = ComponentManager._probe

        def fail_at_candidate(candidate, component, directory, *args, **kwargs):
            if component == '320n' and candidate.path('320n') == self.destination / '320n':
                raise ComponentError('candidate probe failed')
            return probe(candidate, component, directory, *args, **kwargs)

        with patch.object(ComponentManager, '_probe', fail_at_candidate):
            with self.assertRaisesRegex(ComponentError, 'candidate probe failed'):
                manager.relocate_components(self.destination, Runner(), cleanup=True)
        self.assertEqual(self.config.read_bytes(), initial_config)
        self.assertTrue(manager.status('320n')['ready'])
        self.assertFalse((self.destination / '320n').exists())

    def test_portable_relocation_reopens_with_component_path_relative_to_program(self):
        manager = self.install_model(ComponentManager(self.program))
        manager.relocate_components(self.program / 'standalone-data', Runner())
        self.assertTrue((self.program / 'components' / '320n').is_dir())
        moved = self.program.rename(self.top / 'moved')
        reopened = ComponentManager(moved)
        self.assertEqual(reopened.path('320n'), moved / 'standalone-data' / '320n')
        self.assertTrue(reopened.status('320n')['ready'])
        self.assertFalse(self.config.exists())

    def test_stale_manager_cannot_change_old_components_after_move(self):
        self.register()
        manager = self.install_model(ComponentManager(self.program))
        stale = ComponentManager(self.program)
        manager.relocate_components(self.destination, Runner())
        with self.assertRaises(ComponentError):
            stale.uninstall('320n')
        self.assertTrue((self.source / '320n').is_dir())

    def test_outdated_base_needs_update_but_can_be_moved(self):
        self.register()
        (self.program / 'component-catalog.json').write_text(json.dumps({'components': {
            'base': {'version': '2.0'},
        }}), encoding='utf-8')
        manager = ComponentManager(self.program)
        base = self.source / 'base'
        (base / 'app' / 'q_pixelate').mkdir(parents=True)
        for name in ('python.exe', 'pythonw.exe', 'app/q_pixelate/__main__.py'):
            (base / name).write_bytes(b'base file')
        (base / '.q-pixelate-component.json').write_text(json.dumps({
            'id': 'base', 'version': '1.0', 'sha256': 'a' * 64,
        }), encoding='utf-8')
        state = manager.status('base')
        self.assertTrue(state['installed'])
        self.assertFalse(state['ready'])
        with patch('q_pixelate.components._run_probe', return_value='3.12.10'):
            manager.relocate_components(self.destination, Runner())
        self.assertFalse(ComponentManager(self.program).status('base')['ready'])
        self.assertTrue((self.destination / 'base' / 'python.exe').is_file())
