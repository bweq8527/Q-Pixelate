"""Persistent user-owned sticker assets, imported without depending on original paths."""
import hashlib
import shutil
from pathlib import Path
from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QImageReader

STICKER_MIME = 'application/x-qpixel-sticker'


class StickerLibrary:
    def __init__(self, root=None):
        self.root = Path(root) if root else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))/'Q-Pixelate'/'stickers'

    def items(self):
        return sorted(str(path.resolve()) for path in self.root.glob('*') if path.is_file() and QImageReader(str(path)).canRead())

    def add(self, source):
        source = Path(source)
        if not QImageReader(str(source)).canRead():
            raise ValueError('无法读取贴纸，请选择有效图片。')
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        self.root.mkdir(parents=True, exist_ok=True)
        existing = next(self.root.glob(f'*-{digest}.*'), None)
        if existing:
            return str(existing.resolve())
        target = self.root/f'{source.stem[:60]}-{digest}{source.suffix.lower()}'
        shutil.copyfile(source, target)
        return str(target.resolve())
