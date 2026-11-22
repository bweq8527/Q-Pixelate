import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from q_pixelate.parameter_widgets import ParamControl, WheelSafeComboBox


def spin_wheel(widget):
    center = widget.rect().center()
    event = QWheelEvent(QPointF(center), QPointF(widget.mapToGlobal(center)),
                        QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)
    return event


def enter(widget, value):
    widget.editor.selectAll()
    QTest.keyClicks(widget.editor, value)
    QTest.keyClick(widget.editor, Qt.Key.Key_Return)


class ParameterControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_five_percent_of_full_range_rounded_and_clamped(self):
        widget = ParamControl(decimals=2)
        widget.setRange(.25, 4)
        widget.setValue(1)
        QTest.mouseClick(widget.increase, Qt.MouseButton.LeftButton)
        self.assertEqual(widget.value(), 1.19)
        self.assertEqual(widget.editor.text(), '1.19')
        enter(widget, '3.95')
        QTest.mouseClick(widget.increase, Qt.MouseButton.LeftButton)
        self.assertEqual(widget.value(), 4)
        QTest.mouseClick(widget.decrease, Qt.MouseButton.LeftButton)
        self.assertEqual(widget.value(), 3.81)

    def test_mouse_wheel_does_not_change_numeric_values_or_choices(self):
        widget = ParamControl(decimals=0)
        widget.setRange(1, 100)
        widget.setValue(20)
        combo = WheelSafeComboBox()
        combo.addItems(['方框', '椭圆'])
        combo.setCurrentIndex(1)
        widget.show()
        widget.editor.setFocus()
        for control in (widget.editor, widget.slider, widget.increase, widget.decrease, combo):
            self.assertFalse(spin_wheel(control).isAccepted())
        self.assertEqual(widget.value(), 20)
        self.assertEqual(combo.currentIndex(), 1)

    def test_manual_time_input_updates_display_and_value(self):
        widget = ParamControl(time=True, decimals=3)
        widget.setRange(0, 360000)
        enter(widget, '1:02:03.456')
        self.assertEqual(widget.value(), 3723.456)
        self.assertEqual(widget.editor.text(), '01:02:03.456')
        enter(widget, 'incorrect')
        self.assertEqual(widget.value(), 3723.456)
        self.assertEqual(widget.editor.text(), '01:02:03.456')

    def test_mixed_value_leaves_values_alone_until_user_commits(self):
        widget = ParamControl(decimals=0)
        widget.setRange(1, 100)
        widget.setValue(20)
        received = []
        widget.valueChanged.connect(received.append)
        widget.setMixed(True)
        self.assertEqual(widget.editor.text(), '')
        self.assertEqual(widget.editor.placeholderText(), '多值')
        widget.interpretText()
        self.assertEqual(received, [])
        enter(widget, '20')
        self.assertEqual(received, [20])
        self.assertEqual(widget.editor.text(), '20')
