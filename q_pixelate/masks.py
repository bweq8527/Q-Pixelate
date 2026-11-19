"""Normalized mask geometry shared by the editor and FFmpeg mask rasterizer."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath


def mask_path(shape, rect, points, box):
    path = QPainterPath()
    area = QRectF(box.x()+rect.x*box.width(), box.y()+rect.y*box.height(),
                  rect.w*box.width(), rect.h*box.height())
    if shape == 'ellipse':
        path.addEllipse(area)
    elif shape == 'lasso':
        if points:
            path.moveTo(QPointF(box.x()+points[0][0]*box.width(), box.y()+points[0][1]*box.height()))
            for x, y in points[1:]:
                path.lineTo(box.x()+x*box.width(), box.y()+y*box.height())
            path.closeSubpath()
    else:
        path.addRect(area)
    return path


def mask_image(edit, width, height):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.black)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillPath(mask_path(edit.shape, edit.rect, edit.points, QRectF(0, 0, width, height)), Qt.GlobalColor.white)
    painter.end()
    return image
