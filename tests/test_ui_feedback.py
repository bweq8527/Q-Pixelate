import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QWidget

from q_pixelate.app import MainWindow
from q_pixelate.widgets import Timeline


class UiFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        motion = patch('q_pixelate.ui_theme.motion_enabled', return_value=True)
        motion.start()
        self.addCleanup(motion.stop)
        self.window = MainWindow()
        self.addCleanup(self.window.close)
        self.window.show()
        self.app.processEvents()

    def test_press_animation_preserves_the_hit_target_and_click(self):
        button = self.window.open_button
        calls = []
        button.clicked.disconnect()
        button.clicked.connect(lambda: calls.append(True))
        bounds = button.geometry()
        QTest.mousePress(button, Qt.MouseButton.LeftButton)
        pulse = button.findChild(QWidget, 'interactionPulse')
        self.assertIsNotNone(pulse)
        self.assertTrue(pulse.isVisible())
        self.assertEqual(button.geometry(), bounds)
        self.assertIs(button.parentWidget().childAt(bounds.center()), button)
        self.assertIsNone(button.childAt(QPoint(button.width() // 2, button.height() - 5)))
        finished = QSignalSpy(pulse.animation.finished)
        QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
        self.assertEqual(calls, [True])
        self.assertTrue(finished.wait(1500))
        self.assertFalse(pulse.isVisible())

    def test_reduced_motion_still_activates_the_control(self):
        button = self.window.open_button
        calls = []
        button.clicked.disconnect()
        button.clicked.connect(lambda: calls.append(True))
        with patch('q_pixelate.ui_theme.motion_enabled', return_value=False):
            button.setFocus()
            QTest.keyClick(button, Qt.Key.Key_Space)
        self.assertEqual(calls, [True])
        self.assertIsNone(button.findChild(QWidget, 'interactionPulse'))

    def test_keyboard_switches_tabs_with_feedback(self):
        self.window.body.setEnabled(True)
        tabs = self.window.editor_tabs
        bar = tabs.tabBar()
        bar.setFocus()
        QTest.keyClick(bar, Qt.Key.Key_Right)
        self.assertEqual(tabs.currentIndex(), 1)
        pulse = bar.findChild(QWidget, 'interactionPulse')
        self.assertIsNotNone(pulse)
        self.assertTrue(pulse.isVisible())

    def test_window_reopening_restarts_feedback_only_on_input(self):
        button = self.window.open_button
        button.clicked.disconnect()
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        pulse = button.findChild(QWidget, 'interactionPulse')
        self.assertTrue(pulse.isVisible())
        self.window.close()
        self.window.show()
        self.assertFalse(pulse.isVisible())
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.assertTrue(pulse.isVisible())

    def test_clip_list_receives_extra_height_instead_of_the_count_badge(self):
        self.window.resize(1040, 760)
        self.app.processEvents()
        clip_list = self.window.clip_list
        compact_height = clip_list.height()
        self.window.resize(1800, 1160)
        self.app.processEvents()
        self.assertGreater(clip_list.height(), compact_height + 260)
        self.assertGreater(clip_list.height(), self.window.editor_tabs.height() / 2)
        self.assertEqual(self.window.count_label.height(), self.window.count_label.sizeHint().height())


class TimelineFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_seeking_shows_brief_feedback_at_the_existing_playhead(self):
        motion = patch('q_pixelate.gesture_feedback.motion_enabled', return_value=True)
        motion.start()
        self.addCleanup(motion.stop)
        timeline = Timeline()
        self.addCleanup(timeline.close)
        timeline.resize(480, 116)
        timeline.set_duration(10)
        timeline.position = 5
        timeline.show()
        point = QPoint(240, 44)
        QTest.mouseMove(timeline, point)
        QTest.qWait(250)
        before = timeline.grab().toImage()
        requested = []
        timeline.seekRequested.connect(requested.append)

        QTest.mouseClick(timeline, Qt.MouseButton.LeftButton, pos=point)

        self.assertEqual(requested, [5.0, 5.0])
        self.assertNotEqual(timeline.grab().toImage(), before)
        QTest.qWait(250)
        self.assertEqual(timeline.grab().toImage(), before)
