import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import tempfile
import unittest
import time
import subprocess
import sys
from pathlib import Path
from PySide6.QtCore import QPointF, Qt
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from media_helpers import fixture, command
from q_pixelate.app import MainWindow, PreviewDialog
from q_pixelate.media import probe
from q_pixelate.model import Project, Edit, Rect
from q_pixelate.process import Runner
from q_pixelate.widgets import VideoCanvas, Timeline, TimeSpinBox
from q_pixelate.render import render_preview
from PySide6.QtMultimedia import QMediaPlayer


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.temp = tempfile.TemporaryDirectory()
        cls.source = fixture(Path(cls.temp.name) / 'ui.mp4', audio=False)

    @classmethod
    def tearDownClass(cls):
        QTest.qWait(200)  # Qt releases media handles asynchronously after setSource(QUrl()).
        cls.temp.cleanup()

    def test_editor_add_modify_and_project_roundtrip(self):
        window = MainWindow()
        window.set_project(Project(str(self.source)), probe(self.source, Runner()))
        window.in_time.setValue(1)
        window.out_time.setValue(2)
        window.add_edit()
        self.assertEqual(len(window.project.edits), 1)
        window.effect.setCurrentIndex(1)
        window.apply_edit()
        self.assertEqual(window.project.edits[0].effect, 'region')
        path = Path(self.temp.name) / 'ui.qpixel'
        window.project.save(path)
        self.assertEqual(Project.load(path).edits[0].effect, 'region')
        window.dirty = False
        window.close()

    def test_letterbox_coordinate_mapping_is_independent_of_widget_size(self):
        canvas = VideoCanvas()
        canvas.resize(1000, 1000)
        canvas.set_dimensions(1920, 1080)
        rect = canvas.video_rect()
        self.assertAlmostEqual(rect.top(), 218.75, delta=.1)
        point = canvas.normalized_point(QPointF(250, 218.75 + 562.5/2))
        self.assertAlmostEqual(point.x(), .25)
        self.assertAlmostEqual(point.y(), .5)
        canvas.close()

    def test_timeline_shift_drag_creates_time_range(self):
        timeline = Timeline()
        timeline.resize(800, 120)
        timeline.set_duration(100)
        changes = []
        timeline.selectionChanged.connect(lambda a, b: changes.append((a, b)))
        timeline.show()
        QTest.mousePress(timeline, Qt.LeftButton, Qt.ShiftModifier, timeline.point_for_time(20))
        QTest.mouseMove(timeline, timeline.point_for_time(40))
        QTest.mouseRelease(timeline, Qt.LeftButton, Qt.ShiftModifier, timeline.point_for_time(40))
        self.assertTrue(changes)
        self.assertAlmostEqual(changes[-1][0], 20, delta=.2)
        self.assertAlmostEqual(changes[-1][1], 40, delta=.2)
        timeline.close()

    def test_time_input_understands_hours_and_milliseconds(self):
        time = TimeSpinBox()
        self.assertAlmostEqual(time.valueFromText('01:02:03.125'), 3723.125)
        self.assertEqual(time.textFromValue(3723.125), '01:02:03.125')

    def wait_until(self, condition, timeout=8):
        deadline = time.monotonic()+timeout
        while not condition() and time.monotonic()<deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertTrue(condition())

    def test_open_decodes_initial_frame_and_export_requires_confirmed_form(self):
        window = MainWindow()
        errors = []
        window.error = errors.append
        window.open_video(str(self.source))
        self.wait_until(lambda: window.project is not None and window.worker is None)
        self.wait_until(lambda: not window.canvas.image.isNull())
        window.set_selection(2.25, 2.75)
        window.add_edit()
        window.effect.setCurrentIndex(1)
        out = Path(self.temp.name) / 'worker-export.mp4'
        window.export_to(out)
        self.assertFalse(out.is_file())
        self.assertTrue(window.apply_edit())
        window.export_to(out)
        self.wait_until(lambda: window.worker is None)
        self.assertEqual(errors, [])
        self.assertTrue(out.is_file())
        self.assertEqual(window.project.edits[0].effect, 'region')
        self.assertAlmostEqual(window.last_export.encoded_seconds, 2)
        window.dirty = False
        window.close()

    def test_short_preview_keeps_visible_frames_when_looping(self):
        path = Path(self.temp.name) / 'loop-preview.mp4'
        render_preview(Project(str(self.source), [Edit(1, 1.25)]), 0, path, Runner())
        window = MainWindow()
        dialog = PreviewDialog(path, window)
        dialog.show()
        self.wait_until(lambda: not dialog.canvas.image.isNull())
        end = time.monotonic()+.9
        while time.monotonic()<end:
            self.app.processEvents()
            time.sleep(.01)
        self.assertFalse(dialog.canvas.image.isNull())
        self.assertEqual(dialog.player.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        dialog.close()
        window.close()

    def test_saved_project_becomes_dirty_when_mask_form_changes(self):
        window = MainWindow()
        window.set_project(Project(str(self.source), [Edit(1, 2)]), probe(self.source, Runner()))
        window.clip_list.setCurrentRow(0)
        window.dirty = False
        window.strength.setValue(37)
        dirty = window.dirty
        window.dirty = False
        window.close()
        self.assertTrue(dirty, 'Unsaved form edits must trigger the save/discard prompt')

    def test_switching_clips_requires_confirmation_and_preserves_mask_settings(self):
        window = MainWindow()
        window.set_project(Project(str(self.source), [Edit(1, 2), Edit(3, 4)]), probe(self.source, Runner()))
        window.clip_list.setCurrentRow(0)
        window.toggle_range_lock()
        window.strength.setValue(37)
        window.clip_list.setCurrentRow(1)
        self.assertEqual(window.clip_list.currentRow(), 0)
        self.assertEqual(window.strength.value(), 37)
        window.apply_edit()
        window.clip_list.setCurrentRow(1)
        strength = window.project.edits[0].strength
        window.dirty = False
        window.close()
        self.assertEqual(strength, 37)

    def test_preview_preserves_four_three_aspect(self):
        path = Path(self.temp.name) / 'four-three.mp4'
        command('ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=red:size=320x240:duration=1',
                '-c:v', 'libx264', str(path))
        window = MainWindow()
        dialog = PreviewDialog(path, window)
        dialog.show()
        self.wait_until(lambda: not dialog.canvas.image.isNull())
        ratio = dialog.canvas.video_rect().width()/dialog.canvas.video_rect().height()
        dialog.close()
        window.close()
        self.assertAlmostEqual(ratio, 4/3, places=3)

    def test_main_preview_respects_sample_aspect_ratio(self):
        window = MainWindow()
        info = probe(self.source, Runner())
        info.video['sample_aspect_ratio'] = '4:3'
        window.set_project(Project(str(self.source)), info)
        ratio = window.canvas.video_rect().width()/window.canvas.video_rect().height()
        window.close()
        self.assertAlmostEqual(ratio, 64/27, places=3)

    def test_playback_mask_follows_active_clip_and_hides_in_gaps(self):
        window = MainWindow()
        edits = [Edit(1, 2), Edit(3, 4, 'region', Rect(.1, .2, .3, .4))]
        window.set_project(Project(str(self.source), edits), probe(self.source, Runner()))
        window.clip_list.setCurrentRow(0)
        self.wait_until(lambda: not window.canvas.image.isNull() and not window._pause_on_frame)
        window.player.play()
        window.position_changed(500)
        gap = window.canvas.show_mask
        window.position_changed(3500)
        active = window.canvas.rect
        window.player.pause()
        window.dirty = False
        window.close()
        self.assertFalse(gap)
        self.assertEqual(active, Rect(.1, .2, .3, .4))

    def test_background_job_result_is_delivered_on_gui_thread(self):
        window = MainWindow()
        threads = []
        window.start_job(lambda runner: 42, lambda result: threads.append(QThread.currentThread()))
        self.wait_until(lambda: window.worker is None)
        window.close()
        self.assertEqual(threads, [self.app.thread()])

    def test_reloading_project_with_audio_does_not_deadlock(self):
        source = fixture(Path(self.temp.name) / 'audio-reload.mp4', audio=True)
        code = '''
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from q_pixelate.app import MainWindow
from q_pixelate.model import Project,Edit
from q_pixelate.media import probe
from q_pixelate.process import Runner
app=QApplication([])
w=MainWindow()
def load():
    w.set_project(Project(sys.argv[1],[Edit(2.25,2.75)]),probe(sys.argv[1],Runner()))
load()
def reload():
    load()
    QTimer.singleShot(300,finish)
def finish():
    w.close()
    app.quit()
QTimer.singleShot(500,reload)
app.exec()
'''
        result = subprocess.run([sys.executable, '-c', code, str(source)], timeout=10, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))


if __name__ == '__main__':
    unittest.main()
