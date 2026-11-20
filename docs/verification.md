# 验证记录 · 2026-11-20

## 便携组件与自动马赛克

`python -m unittest discover -s tests -v` 通过 85 项测试。`python -m compileall -q q_pixelate launcher.py scripts` 通过。新增验证覆盖离线安装、包校验与回滚、启动目录迁移、精确选区采样、同屏目标、切镜、取消、标注审核、移动遮罩及变速导出。在本机 HTTP 服务上实际下载安装了 320n 组件，确认了 SHA-256 校验和卸载。

在当前 Windows 主机上，使用没有系统 Python 或 FFmpeg 路径的进程环境，从空目录启动打包后的 EXE。安装私有基础环境和 FFmpeg 后，编辑器能打开传入的视频参数，重启时可直接进入编辑器。尚未在全新 Windows 虚拟机单独执行安装验收。

使用私有 Python 3.12.10 运行两个真实 NudeNet 模型。测试视频为 6 秒、640×360、20 fps 的合成 H.264 视频，以约 5 帧/秒扫描 30 帧。时间包含模型初始化。GPU 为 RTX 4070 SUPER，驱动版本 621.24，ONNX Runtime 跟踪确认实际执行 CUDA 算子。

| 模型 | CPU 扫描 | CUDA 扫描 | CUDA 额外显存约数 |
| --- | ---: | ---: | ---: |
| 320n | 1.88 秒 | 2.06 秒 | 229 MiB |
| 640m | 13.30 秒 | 2.48 秒 | 687 MiB |

合成片用于验证推理和耗时，移动区域与多目标由可重复的标注测试验证。实际真人、游戏和动画素材的漏检率与误报率尚未测量。

## 编辑器验证

`python -m unittest discover -s tests -v`: 51 tests passed (39.265s). `python -m compileall -q q_pixelate`: passed.

Added real FFmpeg checks for per-clip speed and preview, two speed changes inside one GOP, 0.25× at EOF, 3× fractional FPS without audio, all audio tracks/language retention and delayed audio onset. A 6-second 120-frame fixture with 2–4s at 2× produces 100 frames/5 seconds; original prefix and shifted tail frame hashes remain identical.

Mask checks cover ellipse geometry, lasso rendering, inside-only feather with pixel differences, PNG alpha and Unicode/punctuation paths. Qt checks cover range/completed locks, confirmation before switch/export, Space on focused buttons, frame/Shift/held arrow navigation, pan independent of selection, lasso path/bounds preservation, legacy-range dirty state, and persistent copied sticker imports. Screenshots inspected at default and minimum window sizes using offscreen Qt, without desktop automation.

Independent review findings (delayed audio retime and legacy unsaved range changes) reproduced RED → GREEN. Slow reverse preview uses silent repeated seeking rather than hardware reverse playback; smoothness on long high-resolution recordings remains dependent on local decoding. Existing supported-codec and avc3 website compatibility limitations still apply.

---

# 历史验证 · 2026-11-19

日期：2026-11-19。环境：Windows、Python 3.12.2、PySide6/Qt 6.11.2、FFmpeg 8.0.1（libx264）。

## 自动化

运行 `python -m unittest discover -s tests -v`：最终 33 项通过（20.778 秒）。`python -m compileall -q q_pixelate` 返回 0。测试使用本地真实模型、Qt 控件和 FFmpeg 生成的视频；没有模拟编码输出。

已核对：

- 片段时间和遮挡区域校验、相邻片段、重复 GOP 编码范围合并。
- 工程相对路径保存/载入、格式错误、源文件发生变化。
- 全屏模糊、区域模糊、图片遮挡、带中文/空格/引号/标点的素材路径。
- H.264 带 B 帧，MP4 与零视频起始时间的 MKV，整数与 30000/1001 帧率。
- 首尾片段、无修改复制、无音轨、双音轨保留。
- 实际解码帧数、未修改 GOP 的逐帧 MD5、一致的音频解码哈希。
- 提前取消、处理中取消、已有目标不被破坏、任务临时目录清理。
- 不兼容编码和开放 GOP 边界明确拒绝。
- Qt 时间输入、时间轴拖选、预览黑边坐标映射、后台打开和导出、初始画面显示、短预览循环播放。
- 30000/1001 帧率下恰好位于入点的第一帧已被遮挡；矩形外沿向外取整，完整覆盖所选像素。
- 待应用参数会触发未保存提示，切换片段不会丢失；4:3 预览和非方形像素比例正确；播放期间遮挡框跟随当前生效片段，在空白区间隐藏。

MKV 定位测试发现并修复了 FFmpeg 输入快速 seek 返回前一 GOP 的情况：复制段使用源关键帧 DTS 控制输出侧修剪，保留 B 帧解码延迟；只检查帧数不足以发现该类错误，因此测试还比较未修改片段的实际画面哈希。

## 一小时长度测试

另行生成 3600 秒、160×90、30fps、带 B 帧、无音轨的 H.264 合成视频：

- 输入 137,903,218 字节，共 108,000 帧。
- 效果范围 1800.25–1800.75 秒，关键帧编码范围 1800–1802 秒。
- 最终版本导出用时 4.156 秒；重新编码 2 秒，直接复制 3598 秒。
- 输出时长 3600 秒，共 108,000 帧。
- 完整解码后逐帧比较：仅第 54,000–54,059 帧改变，其余 107,940 帧完全一致。

这个结果证明一小段修改无需重新编码整条一小时视频；它是低分辨率合成测试，不能作为 1080p/4K 实况、机械硬盘或不同机器的速度保证。

## 桌面验证

在实际 Windows 桌面启动应用并完成：打开双音轨测试视频；设置 2.25–2.75 秒选区；选择区域模糊；鼠标拖出遮挡矩形；应用参数；生成短预览；保存工程；缝合导出。界面结果为“重编码 2.00s / 直接复制 4.00s · 2 条原音轨”。

随后针对桌面检查发现的首帧加载和短预览播完清屏体验，增加了首帧解码暂停和保留画面的循环预览，并通过 Qt 实际解码测试。

最终桌面复测又复现了双音轨工程重载挂起。使用 faulthandler 取得堆栈，定位到播放器重建过程中调用 `QAudioOutput.setMuted`；取消该额外的静音切换后，相同重载步骤完成，实际窗口中正确显示工程片段并播放区域模糊循环预览。测试窗口均已退出。该问题不能仅通过 offscreen 环境判断，因此保留桌面验证记录。

## 独立审阅

独立只读审阅提出的五项问题均已修复，并为每项运行了先失败、后通过的回归测试：时间边界首帧漏遮、区域远端取整不足、未应用表单丢失、预览比例、播放期间片段提示。没有遗留的审阅阻断项；第三方播放器和视频网站兼容性仍以以下限制为准。

## 实际限制

- 尚未测试目标视频网站的上传与审核，也未用用户自己的长时间游戏录屏验收。
- 首版不支持任意格式的局部拼接，特别是 HEVC/AV1、HDR/10-bit、VFR、开放 GOP 和非零视频起始时间。失败不会自动改成完整转码。
- MP4 使用 avc3 参数集更新；旧播放器、其他剪辑软件和视频网站接收能力需以实际文件验证。
- 字幕、章节、附件和封面不在首版保留范围。
