"""Connect a user-drawn target box to a local sticker or blur tracking job."""

import sys
from dataclasses import dataclass
from pathlib import Path

from .model import Layer, Rect, Transform
from .widgets import time_text


@dataclass(frozen=True)
class TrackingRequest:
    row: int
    start: float
    end: float
    frame: float
    kind: str
    asset: str
    python: Path | str
    site_packages: Path


class StickerTracking:
    def tracking_runtime(self):
        manager = self.components
        runtime = next((name for name in ('cpu', 'gpu') if manager.status(name)['ready']), None)
        if runtime is None:
            return None
        python = manager.path('base') / 'python.exe' if manager.status('base')['ready'] else sys.executable
        return python, manager.path(runtime) / 'site-packages'

    def tracking_frame(self):
        seen = self.canvas.frame_time
        if seen is None or self.info is None:
            return None
        return round(seen * self.info.fps_float) / self.info.fps_float

    def begin_sticker_tracking(self):
        self.begin_tracking('sticker')

    def begin_blur_tracking(self):
        self.begin_tracking('blur')

    def begin_tracking(self, kind):
        edit = self.promote_selected_edit()
        if not edit or edit.locked:
            self.status.setText('请先建立或解锁要追踪的选区。')
            return
        asset = ''
        if kind == 'sticker':
            item = self.sticker_library_list.currentItem()
            layer = self.selected_layer(edit)
            asset = item.data(self.sticker_library_list.asset_role) if item else (
                layer.image if layer and layer.kind == 'sticker' else '')
            if not asset:
                self.status.setText('请先在贴纸素材库选中一张贴纸。')
                return
        self.player.pause()
        frame = self.tracking_frame()
        if frame is None or not edit.start <= frame < edit.end:
            self.status.setText('请先暂停在选区内能清楚看到目标的一帧。')
            return
        runtime = self.tracking_runtime()
        if runtime is None:
            self.status.setText('请先安装 CPU 或 NVIDIA 识别环境。追踪无需下载裸体模型。')
            self.open_components()
            return
        self.cancel_layer_interaction()
        self._tracking_request = TrackingRequest(self._editing_row, edit.start, edit.end, frame, kind, asset, *runtime)
        self.canvas.mask_shape = 'rect'
        self.canvas.setFocus()
        self.status.setText('在画面上按住左键框出目标。松开后向后追踪，Esc 取消。')
        self.update_canvas_mask()

    def finish_tracking(self, mask):
        request, self._tracking_request = self._tracking_request, None
        edit = self.promote_selected_edit() if request else None
        if (not request or not edit or edit.locked or request.row != self._editing_row
                or (edit.start, edit.end) != (request.start, request.end)
                or self.tracking_frame() != request.frame):
            self.status.setText('画面或选区已改变，请重新框选。')
            return
        box = mask.transform
        rect = Rect(max(0, box.x-box.w/2), max(0, box.y-box.h/2), box.w, box.h)
        project, info = self.project, self.info

        def run(runner):
            from .tracking import track

            project.check_source()
            result = track(info, edit, runner, start=request.frame, rect=rect,
                           python=request.python, site_packages=request.site_packages)
            project.check_source()
            return result

        def apply(result):
            if self.project is not project or request.row != self._editing_row:
                return
            current = self.promote_selected_edit()
            if (not current or current.locked
                    or (current.start, current.end) != (request.start, request.end)):
                return
            start, initial = result.points[0]
            end = min(current.end, result.end)
            sticker = request.kind == 'sticker'
            name = f'追踪贴纸 · {Path(request.asset).stem}' if sticker else '追踪模糊遮罩层'
            layer = Layer(start-current.start, end-current.start, request.kind,
                          name, request.asset, Transform.from_rect(initial),
                          motion=[(time-start, bounds) for time, bounds in result.points])
            self.add_layer(current, layer, 'sticker' if sticker else 'mask')
            self.open_layer_properties()
            self.status.setText(f'已生成{name} · {result.reason} · 结束帧 {time_text(end)}')

        self.start_job(run, apply)

    def review_tracking_end(self):
        edit = self.layer_edit()
        layer = self.selected_layer(edit)
        if layer and layer.motion:
            self.player.pause()
            end = edit.layer_span(layer)[1]
            last_frame = max(0, self.info.duration - 1/self.info.fps_float)
            self.seek(min(end, last_frame))
