# 框选目标与贴纸跟随

已获用户授权先实现框选目标后的贴纸跟随。抽象图样的自动发现另行验证。跟随在本机运行，沿用可选识别环境的 OpenCV 和视频解码链路，无需裸体模型。首版向后逐帧跟随框选目标的位置和大小，切镜、持续丢失或选区结束时终止，允许复核停止画面和重新框选。实际跟随与用户选用的贴纸质量仍需要在代表性视频上复核。

- [x] 实现可取消的后台追踪，返回源时间轴上的矩形轨迹、排他结束时间及停止原因。真实 OpenCV 测试覆盖移动、缩放、消失、切镜、短暂遮挡及取消。
- [x] 保存和渲染跟随贴纸，保持现有静态贴纸与马赛克工程兼容，验证成片的贴纸位置和消失帧。使用 v5 存储新图层，原有静态和马赛克工程保留原版本。
- [x] 在未锁定选区提供框选入口，衔接素材库、画布、播放定位及图层控制。已经验证界面框选、结果转换、停止帧回看和真实 Qt 解码帧时间。
- [x] 跑回归，重建版本化 Windows 安装包，校验包和当前源码，对未验证场景留下可复现说明。

接口约定：`tracking.track(info, edit, runner, *, start, rect, python, site_packages)` 返回 `points`（非空的源时间、`Rect` 元组列表）、`end`（源时间排他末端）和 `reason`（中文状态）。保存时转换为相对图层入点的 `Layer.motion`。`Layer.sticker_at(elapsed)` 提供在给定相对时间渲染和命中测试所用的贴纸副本。已有组件包继续复用。

发行和接手命令。重建 0.3.0 的基础包时，使用原发行清单复用五个可选组件，并将重建产物放在独立目录。

```powershell
python -X utf8 -m unittest discover -s tests -v
python -m compileall -q q_pixelate launcher.py scripts
python scripts/build_portable.py --offline --components base --reuse-catalog dist/windows-0.3.0/component-catalog.json --output dist/windows-0.3.0-rebuild
python scripts/build_installer.py --portable dist/windows-0.3.0-rebuild --iscc build/inno-tool/compiler/ISCC.exe
```

验收样例 `.test-artifacts/sticker-tracking-smoke.py` 使用已安装的私有 OpenCV 和 Qt 画布显示帧，生成带移动目标的合成视频，执行框选、保存载入及真实导出，再检查贴纸位置和消失后的实际像素。换镜、长遮挡或外观大幅变化仍需要在实际视频里检查停止帧并重新框选。

交付产物为 `dist/windows-0.3.0/Q-Pixelate-Setup.exe`。阶段完整回归通过 113 项。最终追踪代码通过 8 项定向测试，另有真实本地追踪与导出像素验证。目录中的六个组件校验通过，基础包中的 Python 源码与工作区一致。隔离 Windows 安装测试通过自选位置、迁移、登记更新和默认卸载保留。独立测试脚本在本机 `.test-artifacts/installer-lifecycle-smoke.py`，传入 `dist/windows-0.3.0` 可重跑。追踪测试可将 `QPIXEL_TRACKING_TEST_SITE_PACKAGES` 指向已安装的 CPU 或 GPU 组件的 `site-packages` 后执行。
