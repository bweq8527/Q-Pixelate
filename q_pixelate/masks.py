"""Normalized mask geometry shared by the editor and FFmpeg mask rasterizer."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath, QTransform

from .model import Rect


def transform_geometry(transform, box, pixel_aspect=1):
    matrix = QTransform()
    matrix.translate(box.x()+transform.x*box.width(), box.y()+transform.y*box.height())
    matrix.scale(1/pixel_aspect, 1)
    matrix.rotate(transform.angle)
    w, h = transform.w*box.width()*pixel_aspect, transform.h*box.height()
    return matrix, QRectF(-w/2, -h/2, w, h)


def layer_mask_path(mask, box, pixel_aspect=1):
    matrix, local = transform_geometry(mask.transform, box, pixel_aspect)
    return matrix.map(mask_path(mask.shape, Rect(0, 0, 1, 1), mask.points, local))


def paint_sticker(painter, layer, image, box, pixel_aspect=1, clip=True):
    painter.save()
    painter.setClipRect(box, Qt.ClipOperation.IntersectClip)
    if clip and layer.mask:
        painter.setClipPath(layer_mask_path(layer.mask, box, pixel_aspect), Qt.ClipOperation.IntersectClip)
    matrix, local = transform_geometry(layer.transform, box, pixel_aspect)
    painter.setTransform(matrix, True)
    painter.drawImage(local, image)
    painter.restore()


def raster_layer(layer, width, height, pixel_aspect=1, *, mask=False, asset=None):
    image = QImage(width, height, QImage.Format.Format_RGB32 if mask else QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.black if mask else Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    box = QRectF(0, 0, width, height)
    try:
        if mask:
            painter.fillPath(layer_mask_path(layer.mask, box, pixel_aspect), Qt.GlobalColor.white)
        else:
            if asset is None:
                asset = QImage(layer.image)
            if asset.isNull():
                raise ValueError('无法读取贴纸图片。')
            paint_sticker(painter, layer, asset, box, pixel_aspect, clip=False)
    finally:
        painter.end()
    return image


def sticker_frames(layer, width, height, pixel_aspect, first, last, fps, origin):
    asset = QImage(layer.image)
    if asset.isNull():
        raise ValueError('无法读取贴纸图片。')
    for frame in range(first, last):
        sticker = layer.sticker_at(frame/fps - origin)
        image = raster_layer(sticker, width, height, pixel_aspect, asset=asset)
        # Raw overlay pixels carry straight alpha.
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        yield image.constBits()


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
