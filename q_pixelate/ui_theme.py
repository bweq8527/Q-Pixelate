"""Shared editor colors, Qt styling, and input feedback."""

import sys

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QRect, Qt, Slot, QVariantAnimation
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QAbstractSlider, QAbstractSpinBox, QApplication,
    QComboBox, QDialog, QLabel, QLineEdit, QSplitterHandle, QTabBar, QTextEdit,
    QWidget,
)


from .palette import (
    BRICK, BRICK_HOVER, BRICK_PRESS, BRICK_WASH, CANVAS, INK, MUSTARD, MUSTARD_WASH,
    MUTED, PAPER, SURFACE, TEAL, TEAL_WASH,
)

STYLE = f'''
QWidget {{
    color: {INK}; font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 12px;
}}
QMainWindow, QDialog {{ background: {PAPER}; }}
QFrame#masthead {{
    background: {SURFACE}; border: 2px solid {INK}; border-right-width: 5px; border-bottom-width: 5px;
    border-left: 8px solid {BRICK};
}}
QFrame#panel, QFrame#editorActions, QFrame#timelineHeader {{
    background: {SURFACE}; border: 2px solid {INK}; border-right-width: 4px; border-bottom-width: 4px;
}}
QFrame#footer {{ background: {INK}; }}
QFrame#footer QLabel {{ color: {SURFACE}; }}
QLabel#brand {{
    font-family: "Bahnschrift", "Segoe UI"; font-size: 26px; font-weight: 800;
}}
QLabel#tagline {{ font-weight: 700; color: {INK}; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#section {{ font-size: 13px; font-weight: 800; }}
QLabel#clipHeading {{ font-size: 16px; font-weight: 800; }}
QLabel#source {{
    background: {SURFACE}; border: 1px solid {INK}; border-left: 5px solid {TEAL};
    padding: 5px 9px; selection-background-color: {TEAL}; selection-color: {SURFACE};
}}
QLabel#timecode {{
    background: {INK}; color: {MUSTARD}; padding: 5px 8px;
    font-family: "Consolas", monospace; font-size: 13px; font-weight: 700;
}}
QLabel#count {{
    background: {MUSTARD_WASH}; border: 1px solid {INK}; padding: 3px 7px; font-weight: 700;
}}
QPushButton {{
    background: {SURFACE}; border: 1px solid {INK}; border-bottom-width: 3px; border-right-width: 3px;
    padding: 5px 9px; font-weight: 650;
}}
QPushButton:hover {{ background: {TEAL_WASH}; }}
QPushButton:focus {{ border-top: 2px solid {INK}; padding-top: 4px; }}
QPushButton:pressed {{
    background: {MUSTARD_WASH}; border: 1px solid {INK}; border-top-width: 3px; border-left-width: 3px;
    padding: 5px 9px;
}}
QPushButton:checked {{ background: {TEAL}; color: {SURFACE}; }}
QPushButton:checked:pressed {{ background: {MUSTARD_WASH}; color: {INK}; }}
QPushButton#primary {{ background: {BRICK}; color: {SURFACE}; font-weight: 800; }}
QPushButton#primary:hover {{ background: {BRICK_HOVER}; }}
QPushButton#primary:pressed {{ background: {BRICK_PRESS}; }}
QPushButton#danger {{ color: #872f21; }}
QPushButton#danger:hover {{ background: {BRICK_WASH}; }}
QPushButton#danger:pressed {{ background: {BRICK}; color: {SURFACE}; }}
QPushButton:disabled, QPushButton#primary:disabled, QPushButton#danger:disabled {{
    color: #716c60; background: #e8e3d6; border-color: #a49e8f;
}}
QPushButton#parameterStep {{ padding: 4px 5px; }}
QPushButton#parameterStep:focus {{ padding-top: 3px; }}
QPushButton#parameterStep:pressed {{ padding: 4px 5px; }}
QListWidget, QTextEdit {{
    background: {SURFACE}; border: 1px solid {INK}; padding: 3px;
    selection-background-color: {TEAL}; selection-color: {SURFACE};
}}
QListWidget::item {{ padding: 6px 5px; }}
QListWidget#clipList {{ border: 2px solid {INK}; font-size: 13px; alternate-background-color: {PAPER}; }}
QListWidget#clipList::item {{ padding: 4px 9px; }}
QListWidget::item:hover {{ background: {TEAL_WASH}; }}
QListWidget::item:selected {{ background: {TEAL}; color: {SURFACE}; }}
QListWidget:focus, QTextEdit:focus {{ border-color: {TEAL}; }}
QTextEdit#logs {{ background: {CANVAS}; color: {SURFACE}; font-family: "Consolas", "Microsoft YaHei UI"; }}
QMenu {{
    background: {SURFACE}; border: 2px solid {INK}; border-right-width: 4px;
    border-bottom-width: 4px; padding: 3px;
}}
QMenu::item {{ padding: 6px 19px; }}
QMenu::item:selected {{ background: {TEAL}; color: {SURFACE}; }}
QMenu::item:disabled {{ color: {MUTED}; }}
QDoubleSpinBox, QComboBox, QLineEdit {{
    background: {SURFACE}; border: 1px solid {INK}; padding: 4px 5px;
    selection-background-color: {TEAL}; selection-color: {SURFACE};
}}
QDoubleSpinBox:hover, QComboBox:hover, QLineEdit:hover {{ background: {PAPER}; }}
QDoubleSpinBox:focus, QComboBox:focus, QLineEdit:focus {{
    background: {TEAL_WASH}; border-color: {TEAL};
}}
QDoubleSpinBox:disabled, QComboBox:disabled, QLineEdit:disabled {{
    color: #716c60; background: #e8e3d6; border-color: #a49e8f;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {INK};
    selection-background-color: {TEAL}; selection-color: {SURFACE};
}}
QCheckBox {{ spacing: 7px; }}
QCheckBox:hover, QCheckBox:focus {{ background: {TEAL_WASH}; }}
QSlider::groove:horizontal {{ height: 6px; background: #d8d1c0; border: 1px solid {INK}; }}
QSlider::sub-page:horizontal {{ background: {TEAL}; }}
QSlider::handle:horizontal {{
    background: {MUSTARD}; width: 11px; margin: -5px 0; border: 1px solid {INK};
    border-right-width: 3px; border-bottom-width: 3px;
}}
QSlider::handle:horizontal:hover, QSlider::handle:horizontal:pressed,
QSlider::handle:horizontal:focus {{ background: {BRICK}; }}
QSlider::handle:horizontal:disabled {{ background: #d2ccbc; border-color: #a49e8f; }}
QSlider::sub-page:horizontal:disabled {{ background: #d8d1c0; }}
QProgressBar {{
    background: {SURFACE}; border: 1px solid {INK}; min-height: 14px;
    text-align: center; font-weight: 700;
}}
QProgressBar::chunk {{ background: {MUSTARD}; border-right: 2px solid {INK}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:horizontal, QScrollBar:vertical {{ background: #d8d1c0; border: 1px solid {INK}; margin: 0; }}
QScrollBar:horizontal {{ height: 13px; }}
QScrollBar:vertical {{ width: 13px; }}
QScrollBar::handle:horizontal, QScrollBar::handle:vertical {{
    background: {SURFACE}; border: 1px solid {INK}; min-width: 24px; min-height: 24px;
}}
QScrollBar::handle:hover {{ background: {MUSTARD}; }}
QScrollBar::handle:pressed {{ background: {BRICK}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; background: transparent; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QTabWidget::pane {{ border: 2px solid {INK}; background: {SURFACE}; top: -1px; }}
QTabBar::tab {{
    background: #e7e0cf; border: 1px solid {INK}; border-bottom-width: 2px;
    padding: 7px 11px; margin-right: 3px; font-weight: 700;
}}
QTabBar::tab:hover {{ background: {TEAL_WASH}; }}
QTabBar::tab:selected {{ background: {TEAL}; color: {SURFACE}; border-top-width: 2px; padding-top: 6px; }}
QTabBar::tab:pressed {{ background: {MUSTARD_WASH}; color: {INK}; }}
QWidget#layerProperties, QWidget#effectsPanel {{ background: {SURFACE}; }}
QSplitter::handle {{ background: #d7cebb; }}
QSplitter::handle:hover {{ background: {MUSTARD}; }}
QSplitter::handle:pressed {{ background: {BRICK}; }}
QToolTip {{ background: {INK}; color: {SURFACE}; border: 1px solid {MUSTARD}; padding: 3px; }}
'''


def motion_enabled():
    if sys.platform != 'win32':
        return True
    from ctypes import byref, windll, wintypes

    enabled = wintypes.BOOL()
    # Windows accessibility preference for animations inside applications.
    query_client_area_animation = 0x1042
    return not windll.user32.SystemParametersInfoW(query_client_area_animation, 0, byref(enabled), 0) or bool(enabled.value)


class InteractionPulse(QWidget):
    """A short accent sweep that leaves the underlying widget and input alone."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('interactionPulse')
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(180)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self.update_frame)
        self.animation.finished.connect(self.hide)

    def play(self, area):
        band = QRect(area.left() + 3, area.bottom() - 5, area.width() - 6, 3)
        band = band.intersected(self.parentWidget().rect())
        if band.isEmpty():
            return
        self.animation.stop()
        self.setGeometry(band)
        self.raise_()
        self.show()
        self.animation.start()

    def paintEvent(self, event):
        progress = float(self.animation.currentValue())
        width = max(1, round(self.width() * (0.24 + 0.76 * progress)))
        shadow = QColor(INK)
        shadow.setAlphaF(1 - progress * progress)
        bar = QColor(MUSTARD)
        bar.setAlphaF(shadow.alphaF())
        painter = QPainter(self)
        painter.fillRect(0, 0, width, 3, shadow)
        painter.fillRect(1, 1, max(0, width - 2), 1, bar)

    @Slot()
    def update_frame(self):
        self.update()

    def hideEvent(self, event):
        self.animation.stop()
        super().hideEvent(event)


class InteractionFeedback(QObject):
    """Observe input without consuming events or changing their order."""

    controls = (QAbstractButton, QAbstractSlider, QAbstractSpinBox, QComboBox, QLineEdit,
                QTextEdit, QTabBar, QAbstractItemView, QSplitterHandle)
    mouse_events = (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.Wheel)
    watched_events = (*mouse_events, QEvent.Type.KeyPress, QEvent.Type.FocusIn, QEvent.Type.Show,
                      QEvent.Type.Enter, QEvent.Type.ContextMenu)
    hover_controls = (QAbstractButton, QAbstractSlider, QComboBox, QSplitterHandle)
    activation_keys = (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)
    navigation_keys = (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
                       Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown)

    def __init__(self, window):
        super().__init__(window)

    def start(self):
        QApplication.instance().installEventFilter(self)
        for combo in self.parent().findChildren(QComboBox):
            self.watch_combo(combo)

    def stop(self):
        QApplication.instance().removeEventFilter(self)
        for pulse in self.parent().findChildren(InteractionPulse):
            pulse.hide()

    def play(self, widget, area=None):
        if not widget.isVisible() or not widget.isEnabled() or not motion_enabled():
            return
        pulse = widget.findChild(InteractionPulse, options=Qt.FindChildOption.FindDirectChildrenOnly)
        if pulse is None:
            pulse = InteractionPulse(widget)
        pulse.play(widget.rect() if area is None else area)

    def eventFilter(self, widget, event):
        kind = event.type()
        if kind not in self.watched_events:
            return False
        if not isinstance(widget, QWidget) or isinstance(widget, InteractionPulse):
            return False
        # Qt's popup views manage their own mouse grab and child widgets.
        if widget.property('customInteractionFeedback') or widget.window().windowType() == Qt.WindowType.Popup:
            return False
        parent = widget
        window = self.parent()
        while parent is not None and parent is not window:
            parent = parent.parentWidget()
        if parent is None or not widget.isEnabled():
            return False

        if kind == QEvent.Type.Show:
            if isinstance(widget, QComboBox):
                self.watch_combo(widget)
            if isinstance(widget, QDialog) or widget.property('feedbackPanel'):
                self.play(widget)
            return False

        view = widget.parentWidget()
        viewport = isinstance(view, QAbstractItemView) and view.viewport() is widget
        selectable = isinstance(widget, QLabel) and bool(widget.textInteractionFlags())
        if not isinstance(widget, self.controls) and not viewport and not selectable:
            return False

        if kind in self.mouse_events:
            if kind != QEvent.Type.Wheel and event.button() != Qt.MouseButton.LeftButton:
                return False
            self.mouse_feedback(widget, event.position().toPoint(), view if viewport else None)
        elif kind == QEvent.Type.Enter:
            if isinstance(widget, self.hover_controls):
                self.play(widget)
        elif kind == QEvent.Type.ContextMenu:
            self.play(widget)
        elif kind == QEvent.Type.FocusIn:
            if event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason):
                self.play(widget)
        elif not event.isAutoRepeat():
            self.key_feedback(widget, event)
        return False

    def mouse_feedback(self, widget, point, view):
        if view is not None:
            index = view.indexAt(point)
            if index.isValid():
                self.play(widget, view.visualRect(index))
        elif isinstance(widget, QTabBar):
            tab = widget.tabAt(point)
            if tab >= 0:
                self.play(widget, widget.tabRect(tab))
        else:
            self.play(widget)

    def watch_combo(self, combo):
        if not combo.property('feedbackConnected'):
            combo.activated.connect(self.combo_activated)
            combo.setProperty('feedbackConnected', True)

    @Slot()
    def combo_activated(self):
        self.play(self.sender())

    def key_feedback(self, widget, event):
        key = event.key()
        if isinstance(widget, QAbstractItemView):
            if key in (*self.navigation_keys, *self.activation_keys, Qt.Key.Key_Delete) or event.text().isprintable():
                self.play(widget.viewport())
        elif isinstance(widget, (QLineEdit, QTextEdit, QAbstractSpinBox)):
            if event.text() or key in (*self.navigation_keys, *self.activation_keys, Qt.Key.Key_Delete):
                self.play(widget)
        elif key in self.activation_keys or (
                isinstance(widget, (QAbstractSlider, QComboBox)) and key in self.navigation_keys):
            self.play(widget)
