import math

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QSlider, QWidget,
)

from .widgets import time_text


class WheelSafeComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class _ParameterSlider(QSlider):
    def wheelEvent(self, event):
        event.ignore()


class ParamControl(QWidget):
    """Edit one value by dragging, typing, or stepping by 5% of its range."""

    valueChanged = Signal(object)

    def __init__(self, parent=None, *, decimals=None, time=False):
        super().__init__(parent)
        self._time = time
        self._decimals = (3 if time else 2) if decimals is None else decimals
        self._scale = 10 ** self._decimals
        self._mixed = False
        self._tick = 0

        self.slider = _ParameterSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimumWidth(40)
        self.slider.setRange(0, 99 * self._scale)
        self.editor = QLineEdit()
        self.editor.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.editor.setMinimumWidth(96 if time else 54)
        self.editor.setMaximumWidth(104 if time else 78)
        self.setFocusProxy(self.editor)
        self._suffix = QLabel()
        self._suffix.hide()
        self.decrease = QPushButton('−5%')
        self.increase = QPushButton('+5%')
        self.decrease.setToolTip('减小可用范围的 5%')
        self.increase.setToolTip('增大可用范围的 5%')

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.editor)
        layout.addWidget(self._suffix)
        for button in (self.decrease, self.increase):
            button.setAutoDefault(False)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
            button.setObjectName('parameterStep')
            layout.addWidget(button)

        self.slider.valueChanged.connect(lambda tick: self._set_tick(tick, user=True))
        self.slider.sliderReleased.connect(self._finish_slide)
        self.editor.editingFinished.connect(self.interpretText)
        self.decrease.clicked.connect(lambda: self._nudge(-1))
        self.increase.clicked.connect(lambda: self._nudge(1))
        self._refresh()

    def _number(self, tick):
        return tick / self._scale if self._decimals else tick

    def value(self):
        return self._number(self._tick)

    def minimum(self):
        return self._number(self.slider.minimum())

    def maximum(self):
        return self._number(self.slider.maximum())

    def setRange(self, minimum, maximum):
        low, high = round(minimum * self._scale), round(maximum * self._scale)
        with QSignalBlocker(self.slider):
            self.slider.setRange(low, max(low, high))
            self.slider.setPageStep(self._step())
        self._set_tick(self._tick)

    def setMinimum(self, value):
        self.setRange(value, self.maximum())

    def setMaximum(self, value):
        self.setRange(min(value, self.minimum()), value)

    def setSuffix(self, suffix):
        suffix = suffix.strip()
        self._suffix.setText(suffix)
        self._suffix.setVisible(bool(suffix))

    def setValue(self, value):
        self._mixed = False
        self._set_tick(round(value * self._scale))

    def setMixed(self, mixed):
        self._mixed = mixed
        self._refresh()

    def _refresh(self):
        with QSignalBlocker(self.slider):
            self.slider.setValue(self._tick)
        current = self.value()
        formatted = time_text(current) if self._time else f'{current:.{self._decimals}f}'
        self.editor.setPlaceholderText('多值' if self._mixed else '')
        self.editor.setText('' if self._mixed else formatted)

    def _set_tick(self, tick, *, user=False):
        tick = min(max(tick, self.slider.minimum()), self.slider.maximum())
        changed = tick != self._tick or (user and self._mixed)
        self._tick = tick
        if user:
            self._mixed = False
        self._refresh()
        if changed and not self._mixed:
            self.valueChanged.emit(self.value())

    def _step(self):
        span = self.slider.maximum() - self.slider.minimum()
        return max(1, (span + 10) // 20)

    def _nudge(self, direction):
        self._set_tick(self._tick + direction * self._step(), user=True)

    def _finish_slide(self):
        if self._mixed:
            self._set_tick(self.slider.value(), user=True)

    def interpretText(self):
        try:
            text = self.editor.text().strip()
            if self._time:
                parts = [float(part) for part in text.split(':')]
                if len(parts) > 3 or any(part < 0 or not math.isfinite(part) for part in parts):
                    raise ValueError
                value = sum(part * 60 ** index for index, part in enumerate(reversed(parts)))
            else:
                value = float(text)
            tick = round(value * self._scale)
        except (ValueError, OverflowError):
            self._refresh()
            return
        self._set_tick(tick, user=True)
