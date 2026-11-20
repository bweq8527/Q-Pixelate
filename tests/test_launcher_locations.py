import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from launcher import SetupWindow, main, parse_options
from q_pixelate.component_dialog import ComponentDialog
from q_pixelate.components import ComponentManager


class LauncherLocationTests(unittest.TestCase):
    def test_settings_shows_separate_paths_and_requests_the_selected_move(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program, payload = root / '程序', root / '组件'
            program.mkdir()
            registration = root / 'Q-Pixelate' / 'installation.json'
            registration.parent.mkdir()
            installer = root / 'Q-Pixelate-Setup.exe'
            installer.touch()
            registration.write_text(json.dumps({'program_dir': str(program), 'component_dir': str(payload),
                                                 'installer': str(installer)}), encoding='utf-8')
            with patch.dict(os.environ, {'LOCALAPPDATA': directory, 'QPIXELATE_LAUNCHER': str(program/'Q-Pixelate.exe')}), \
                    patch.object(ComponentDialog, 'refresh_states'), \
                    patch.object(QFileDialog, 'getExistingDirectory', return_value=str(root/'另一个组件目录')), \
                    patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No):
                window = ComponentDialog(ComponentManager(program))
                received = Mock()
                window.handoff.connect(received)
                window.location_controls[0].click()
                received.assert_called_once_with('move-program', '', '')
                received.reset_mock()
                window.location_controls[1].click()
                received.assert_called_once_with('move-components', '', str(root/'另一个组件目录'))
                app.processEvents()

    def test_component_move_waits_for_editor_before_copying(self):
        directory = Path.cwd() / '组件 空格'
        options = parse_options(['--move-components', str(directory), '--remove-old-copy',
                                 '--wait-pid', '1234', '--'], Path.cwd())
        operations = Mock()
        setup = SimpleNamespace(options=options, manager=operations.manager)
        runner = Mock()
        with patch('launcher.wait_for_editor', operations.wait):
            SetupWindow.apply_handoff(setup, runner)
        self.assertEqual([call[0] for call in operations.mock_calls],
                         ['wait', 'manager.relocate_components'])
        operations.wait.assert_called_once_with(1234, runner)
        operations.manager.relocate_components.assert_called_once_with(directory, runner, cleanup=True)

    def test_normal_launch_keeps_editor_file_arguments(self):
        options = parse_options(['--', '录屏 01.mp4'], Path.cwd())
        self.assertEqual(options.editor_args, [str(Path.cwd() / '录屏 01.mp4')])
        self.assertFalse(options.maintenance)

    def test_move_and_removal_are_distinct_operations(self):
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            parse_options(['--move-components', 'test', '--remove-installed-components'], Path.cwd())

    def test_uninstaller_returns_failure_headlessly_if_component_cannot_be_removed(self):
        manager = Mock()
        manager.status.return_value = {'installed': True}
        manager.uninstall.side_effect = OSError('component is busy')
        with patch('q_pixelate.components.ComponentManager', return_value=manager), \
                patch('sys.argv', ['launcher.py', '--remove-installed-components']), \
                patch('launcher.tk.Tk') as window:
            self.assertEqual(main(), 1)
            window.assert_not_called()
