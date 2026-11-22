import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint, Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from media_helpers import fixture
from q_pixelate.app import PreviewDialog
from q_pixelate.model import Edit, Layer, Project
from q_pixelate.process import Runner
from q_pixelate.render import render_preview, render_project_preview


class PreviewProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.source = fixture(cls.root / 'source.mp4', audio=False)

    @classmethod
    def tearDownClass(cls):
        QTest.qWait(200)
        cls.temp.cleanup()

    def test_marks_use_output_time_and_consecutive_deletions_make_one_seam(self):
        project = Project(str(self.source), [
            Edit(1, 2, 'none', speed=2),
            Edit(2, 3, 'none', omit=True),
            Edit(3, 4, 'none', omit=True),
            Edit(4, 5, 'none', layers=[Layer(.25, .75, 'blur')]),
        ])
        whole = render_project_preview(project, self.root / 'whole.mp4', Runner(), with_timeline=True)
        selected = render_preview(project, 2, self.root / 'cut.mp4', Runner(), with_timeline=True)
        marks = lambda result: {(span.kind, span.start_ms, span.end_ms) for span in result.spans}
        self.assertEqual(marks(whole), {('speed', 1000, 1500), ('cut', 1500, 1500), ('blur', 1750, 2250)})
        self.assertEqual(marks(selected), {('speed', 0, 500), ('cut', 500, 500), ('blur', 750, 1250)})

    def test_transition_marks_follow_retained_fade_frames(self):
        project = Project(str(self.source), [
            Edit(2, 4, 'none', omit=True, video_transition='black', transition_duration=.25),
        ])
        result = render_preview(project, 0, self.root / 'fade.mp4', Runner(), with_timeline=True)
        self.assertEqual({(span.kind, span.start_ms, span.end_ms) for span in result.spans},
                         {('transition', 750, 1000), ('cut', 1000, 1000), ('transition', 1000, 1250)})

    def test_drag_scrubs_in_preview_and_preserves_playback_state(self):
        dialog = PreviewDialog(self.source, None)
        self.addCleanup(dialog.close)
        dialog.show()
        self.wait_until(dialog.player.isSeekable)
        self.wait_until(lambda: dialog.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        progress = dialog.progress
        self.assertEqual(progress.maximum(), 6000)
        point = lambda fraction: QPoint(round(progress.width() * fraction), progress.height() // 2)
        QTest.mousePress(progress, Qt.MouseButton.LeftButton, pos=point(.25))
        QTest.mouseMove(progress, point(.75))
        self.wait_until(lambda: abs(dialog.player.position() - 4500) < 175)
        self.wait_until(lambda: dialog.canvas.frame_time is not None and abs(dialog.canvas.frame_time - 4.5) < .2)
        self.assertEqual(dialog.player.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        QTest.mouseRelease(progress, Qt.MouseButton.LeftButton, pos=point(.75))
        self.wait_until(lambda: dialog.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        dialog.player.pause()
        QTest.keyClick(progress, Qt.Key.Key_Home)
        self.wait_until(lambda: dialog.player.position() == 0)
        self.assertEqual(dialog.player.playbackState(), QMediaPlayer.PlaybackState.PausedState)

    def wait_until(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(condition())


if __name__ == '__main__':
    unittest.main()
