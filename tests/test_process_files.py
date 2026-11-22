import errno
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from q_pixelate.components import ComponentError, ComponentManager
from q_pixelate.process import Cancelled, Runner
from q_pixelate.render import _publish_output


class ProcessFilesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.top = Path(temporary.name).resolve()
        self.program = self.top / 'program'
        self.program.mkdir()
        state = self.top / 'Q-Pixelate'
        state.mkdir()
        (state / 'installation.json').write_text(json.dumps({
            'program_dir': str(self.program), 'component_dir': str(self.top / 'components'),
        }), encoding='utf-8')
        environment = patch.dict(os.environ, {'LOCALAPPDATA': str(self.top)})
        environment.start()
        self.addCleanup(environment.stop)
        self.storage = ComponentManager(self.program).process_files

    def test_new_jobs_use_configured_location_and_clean_up(self):
        choice = self.top / '临时素材'
        self.storage.set_directory(choice)
        reopened = ComponentManager(self.program).process_files
        with reopened.workspace() as workspace:
            self.assertTrue(workspace.is_relative_to(choice))
            (workspace / 'preview.mp4').touch()
        self.assertFalse(workspace.exists())

    def test_uninstall_cleans_crashed_jobs_from_previous_location_only(self):
        first = self.top / 'first'
        self.storage.set_directory(first)
        process = subprocess.run([sys.executable, '-c',
                                  'import os, sys; from q_pixelate.components import ComponentManager; '
                                  'files = ComponentManager(sys.argv[1]).process_files; '
                                  'job = files.workspace(); print(job.__enter__(), flush=True); os._exit(0)',
                                  str(self.program)], encoding='utf-8', env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                                 capture_output=True, check=True)
        leftover = Path(process.stdout.strip())
        self.assertTrue(leftover.is_dir())
        (leftover / 'intermediate.ts').touch()
        personal_file = leftover.parent / 'personal.mp4'
        personal_file.touch()
        unrelated = leftover.parent / 'job-0123456789abcdef0123456789abcdef'
        unrelated.mkdir()
        self.storage.set_directory(self.top / 'second')

        self.storage.remove_abandoned()

        self.assertFalse(leftover.exists())
        self.assertTrue(personal_file.is_file())
        self.assertTrue(unrelated.is_dir())

    def test_active_job_is_kept_during_cleanup(self):
        with self.storage.workspace() as workspace:
            file = workspace / 'encoding.ts'
            file.touch()
            with self.assertRaises(ComponentError):
                self.storage.remove_abandoned()
            self.assertTrue(file.exists())

    @unittest.skipUnless(os.name == 'nt', 'Requires Windows file sharing semantics')
    def test_workspace_cleanup_waits_for_transient_file_locks(self):
        for files in (None, self.storage):
            with self.subTest(managed=files is not None):
                lock = None
                try:
                    with Runner(process_files=files).workspace('qpixel-test-') as workspace:
                        handle = (workspace / 'scene.log').open('wb')
                        lock = threading.Timer(.20, handle.close)
                        lock.start()
                    self.assertFalse(workspace.exists())
                finally:
                    if lock is not None:
                        lock.join()

    def test_cross_volume_export_preserves_previous_file_on_cancel(self):
        destination = self.top / 'export.mp4'
        destination.write_bytes(b'existing export')
        with self.storage.workspace() as workspace:
            source = workspace / 'complete.mp4'
            source.write_bytes(b'new export')
            runner = Runner(progress=lambda *_args: runner.cancel_event.set())
            with patch('q_pixelate.render.os.replace', side_effect=OSError(errno.EXDEV, 'cross-device link')):
                with self.assertRaises(Cancelled):
                    _publish_output(source, destination, runner)
            self.assertEqual(destination.read_bytes(), b'existing export')
        self.assertEqual(list(self.top.glob('.qpixel-publish-*')), [])

    def test_cross_volume_export_replaces_destination_after_copy(self):
        destination = self.top / 'export.mp4'
        destination.write_bytes(b'existing export')
        replace = os.replace
        with self.storage.workspace() as workspace:
            source = workspace / 'complete.mp4'
            source.write_bytes(b'new export')

            def cross_volume_replace(path, target):
                if path == source:
                    raise OSError(errno.EXDEV, 'cross-device link')
                replace(path, target)

            with patch('q_pixelate.render.os.replace', side_effect=cross_volume_replace):
                _publish_output(source, destination, Runner())
        self.assertEqual(destination.read_bytes(), b'new export')
        self.assertEqual(list(self.top.glob('.qpixel-publish-*')), [])
