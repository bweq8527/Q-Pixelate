import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from media_helpers import fixture
from q_pixelate.app import MainWindow
from q_pixelate.model import Project, Edit
from q_pixelate.media import probe
from q_pixelate.process import Runner
from q_pixelate.widgets import Timeline, VideoCanvas


class EditorUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.temp = tempfile.TemporaryDirectory()
        cls.source = fixture(Path(cls.temp.name)/'source.mp4', audio=False)

    @classmethod
    def tearDownClass(cls):
        cls.app.processEvents()
        cls.temp.cleanup()

    def window(self, edits=None):
        window = MainWindow()
        window.error = lambda text: self.fail(text)
        window.set_project(Project(str(self.source), edits or []), probe(self.source, Runner()))
        self.addCleanup(self.close_window, window)
        return window

    def close_window(self, window):
        window.dirty = False
        window.close()
        self.app.processEvents()

    def test_confirm_locks_and_blocks_later_edits_until_explicit_unlock(self):
        w = self.window()
        w.set_selection(1, 2)
        w.add_edit()
        w.set_selection(0, 3)
        self.assertEqual(w.timeline.selection, (1, 2))
        w.speed.setValue(2)
        w.apply_edit()
        self.assertTrue(w.project.edits[0].locked)
        w.strength.setValue(88)
        w.apply_edit()
        w.delete_edit()
        self.assertEqual(len(w.project.edits), 1)
        self.assertEqual(w.project.edits[0].strength, 20)
        self.assertEqual(w.project.edits[0].speed, 2)
        w.unlock_edit()
        w.strength.setValue(40)
        w.apply_edit()
        self.assertEqual(w.project.edits[0].strength, 40)

    def test_unconfirmed_draft_blocks_switch_and_export_commit(self):
        w = self.window([Edit(1, 2), Edit(3, 4)])
        w.clip_list.setCurrentRow(0)
        w.strength.setValue(37)
        w.clip_list.setCurrentRow(1)
        self.assertEqual(w.clip_list.currentRow(), 0)
        self.assertFalse(w.commit_form())
        self.assertEqual(w.project.edits[0].strength, 20)
        w.apply_edit()
        w.clip_list.setCurrentRow(1)
        self.assertEqual(w.project.edits[0].strength, 37)

    def test_legacy_unlocked_range_drag_marks_unsaved_draft(self):
        w = self.window([Edit(1, 2)])
        self.assertFalse(w.dirty)
        w.timeline.selectionChanged.emit(.5, 2.5)
        self.assertTrue(w.dirty)
        self.assertEqual((w.current_edit().start, w.current_edit().end), (.5, 2.5))

    def test_playback_lane_and_locked_selection_never_resize_range(self):
        t = Timeline()
        t.resize(800, 120)
        t.set_duration(100)
        t.selection = (20, 40)
        t.show()
        self.addCleanup(t.close)
        seeks = []
        t.seekRequested.connect(seeks.append)
        pos = t.point_for_time(20)
        QTest.mouseClick(t, Qt.LeftButton, Qt.NoModifier, QPoint(pos.x(), 40))
        self.assertTrue(seeks)
        self.assertEqual(t.selection, (20, 40))
        t.selection_locked = True
        QTest.mousePress(t, Qt.LeftButton, Qt.ShiftModifier, t.point_for_time(50))
        QTest.mouseRelease(t, Qt.LeftButton, Qt.ShiftModifier, t.point_for_time(70))
        self.assertEqual(t.selection, (20, 40))

    def test_zoom_pan_changes_view_only(self):
        t = Timeline()
        t.resize(800, 120)
        t.set_duration(100)
        t.set_zoom(10)
        t.show()
        self.addCleanup(t.close)
        QTest.mousePress(t, Qt.MiddleButton, Qt.NoModifier, QPoint(500, 70))
        QTest.mouseRelease(t, Qt.MiddleButton, Qt.NoModifier, QPoint(200, 70))
        self.assertGreater(t.view_start, 3)
        self.assertEqual(t.position, 0)
        self.assertEqual(t.selection, (0, 0))

    def test_scrollbar_reaches_zoomed_video_end_without_moving_selection(self):
        w = self.window()
        self.assertFalse(w.scrollbar.isEnabled())
        selected = w.timeline.selection
        w.zoom.setValue(10)
        w.scrollbar.setValue(w.scrollbar.maximum())
        self.assertAlmostEqual(w.timeline.view_start, 5.4)
        self.assertEqual(w.timeline.selection, selected)
        w.zoom.setValue(1)
        self.assertFalse(w.scrollbar.isEnabled())

    def test_space_over_focused_button_does_not_apply_or_resize(self):
        w = self.window()
        w.show()
        w.set_selection(1, 2)
        w.add_edit()
        w.confirm_button.setFocus()
        QTest.keyClick(w.confirm_button, Qt.Key_Space)
        self.assertFalse(w.project.edits[0].locked)
        self.assertEqual(w.timeline.selection, (1, 2))

    def test_lasso_draw_emits_normalized_points_and_replaces_existing_shape(self):
        c = VideoCanvas()
        c.resize(640, 360)
        c.editable = True
        c.shape = 'lasso'
        c.show()
        self.addCleanup(c.close)
        paths = []
        c.pointsChanged.connect(paths.append)
        QTest.mousePress(c, Qt.LeftButton, Qt.NoModifier, QPoint(100, 100))
        QTest.mouseMove(c, QPoint(400, 100))
        QTest.mouseMove(c, QPoint(250, 300))
        QTest.mouseRelease(c, Qt.LeftButton, Qt.NoModifier, QPoint(100, 100))
        self.assertGreaterEqual(len(paths[-1]), 3)
        self.assertTrue(all(0 <= x <= 1 and 0 <= y <= 1 for x, y in paths[-1]))

    def test_main_window_lasso_path_survives_coordinate_signal_and_confirmation(self):
        w = self.window()
        w.show()
        w.set_selection(1, 2)
        w.add_edit()
        w.effect.setCurrentIndex(1)
        w.shape.setCurrentIndex(2)
        c = w.canvas
        box = c.video_rect()
        points = [QPoint(round(box.x()+box.width()*x), round(box.y()+box.height()*y))
                  for x, y in ((.2, .2), (.8, .2), (.5, .8), (.2, .2))]
        QTest.mousePress(c, Qt.LeftButton, Qt.NoModifier, points[0])
        for point in points[1:-1]:
            QTest.mouseMove(c, point)
        QTest.mouseRelease(c, Qt.LeftButton, Qt.NoModifier, points[-1])
        self.assertGreaterEqual(len(w.mask_points), 3)
        self.assertAlmostEqual(w.current_edit().rect.x, .2, delta=.01)
        self.assertAlmostEqual(w.current_edit().rect.w, .6, delta=.01)
        w.apply_edit()
        self.assertEqual(w.project.edits[0].points, w.mask_points)

    def test_single_shift_and_held_arrow_preview_preserve_selection(self):
        w = self.window()
        w.show()
        for _ in range(100):
            self.app.processEvents()
            if not w.canvas.image.isNull() and not w._pause_on_frame:
                break
            QTest.qWait(10)
        w.player.pause()
        w.player.setPosition(1000)
        w.canvas.setFocus()
        QTest.keyClick(w.canvas, Qt.Key_Right)
        self.assertAlmostEqual(w.player.position(), 1050, delta=3)
        w.seek_seconds.setValue(2)
        QTest.keyClick(w.canvas, Qt.Key_Right, Qt.ShiftModifier)
        self.assertAlmostEqual(w.player.position(), 3050, delta=3)
        selected = w.timeline.selection
        QTest.keyPress(w.canvas, Qt.Key_Left)
        QTest.qWait(550)
        QTest.keyRelease(w.canvas, Qt.Key_Left)
        self.assertLess(w.player.position(), 2700)
        self.assertEqual(w.timeline.selection, selected)
        self.assertFalse(w.shuttle_timer.isActive())

    def test_sticker_library_copies_asset_and_survives_reopening(self):
        from q_pixelate.stickers import StickerLibrary
        from PySide6.QtGui import QImage
        root = Path(self.temp.name)
        asset = root/'自制.png'
        image = QImage(20, 20, QImage.Format_ARGB32)
        image.fill(Qt.green)
        image.save(str(asset))
        library = StickerLibrary(root/'library')
        saved = library.add(asset)
        self.assertNotEqual(Path(saved), asset)
        self.assertEqual(StickerLibrary(root/'library').items(), [saved])
        self.assertEqual(library.add(asset), saved)

