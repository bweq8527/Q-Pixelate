# Q-Pixelate Windows x64 便携发行

将 `Q-Pixelate.exe` 和 `component-catalog.json` 放在同一个可写目录中，双击启动器。首次启动会检测视频编辑环境，并勾选缺少的基础组件。选择「下载安装所选项」安装，检测通过后点击「打开编辑器」。离线安装时，将清单 `archive` 字段对应的 ZIP 放在启动器旁，点击「安装所选项」。也可以单选一个组件，再点击「导入离线包」选择 ZIP。

编辑器内的「设置 → 环境与组件」可管理组件和模型。已安装所需组件后，启动与识别均可离线运行。搬迁程序时复制整个目录，`components` 和 `component-preferences.json` 保留在启动器旁边。视频工程素材与贴纸库沿用编辑器原有路径规则。

## 构建

在安装了 Tkinter 的 Windows x64 CPython 3.12 环境中，从项目根目录执行。

```powershell
python -m pip install PyInstaller==6.17.0 pyinstaller-hooks-contrib==2025.10
python scripts/build_portable.py
```

产物位于 `dist/portable`。脚本校验 `portable-inputs.json` 中的上游文件 SHA-256，并将输入缓存到 `build/portable-inputs`。保留完整缓存后，可用 `--offline` 复现组件 ZIP，或用 `--cache` 指定另一份缓存。分发启动器时附上 `launcher-licenses`，各运行环境的 wheel 许可证和第三方通知会保留在组件包内。GPU 输入约 2 GB，构建过程还需要解包空间，使用前应预留数 GB 磁盘空间。

```powershell
python scripts/build_portable.py --base-url http://127.0.0.1:8765 --components base ffmpeg cpu 320n
python -m http.server 8765 --bind 127.0.0.1 --directory dist/portable
```

这里的本地服务器用于验收下载。正式联网发行时，将 `--base-url` 改成存放 ZIP 的 HTTPS 目录地址。将生成的 ZIP 上传到该目录，随启动器分发同一次构建的清单。构建命令只在本地生成文件。省略 `--base-url` 会生成离线导入用的清单。`--components` 可指定本次构建包含的组件，空列表仅构建启动器。`--skip-launcher` 可只构建组件和清单。源码编辑器可设置 `QPIXELATE_HOME` 为构建输出目录以使用同一份清单和组件。

## 组件和上游许可

| 组件 | 上游与包内许可 |
| --- | --- |
| 基础运行环境 | [CPython 3.12.10](https://www.python.org/downloads/release/python-31210/) 的 `LICENSE.txt` 与 [PySide6 / Qt 6.11.2](https://pypi.org/project/PySide6/6.11.2/) 的 wheel 许可证。 |
| FFmpeg | [Gyan 的 8.0.1 Essentials ZIP](https://github.com/GyanD/codexffmpeg/releases/tag/8.0.1) 内的 `LICENSE` 和 `README.txt`。对应 [FFmpeg 8.0.1 源码](https://github.com/FFmpeg/FFmpeg/tree/n8.0.1)。 |
| CPU | [ONNX Runtime 1.22.0](https://github.com/microsoft/onnxruntime/releases/tag/v1.22.0)、NumPy、OpenCV 与依赖的 wheel 许可证。 |
| GPU | CPU 对应的依赖、ONNX Runtime GPU 1.22.0，以及 NVIDIA CUDA 12.8 系列和 cuDNN 9 的 wheel 许可证。要求 [兼容的 NVIDIA 驱动](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)。 |
| 320n、640m | [NudeNet v3.4 权重](https://github.com/notAI-tech/NudeNet/releases/tag/v3.4-weights)。320n 从 [官方 3.4.2 wheel](https://pypi.org/project/nudenet/3.4.2/) 提取。640m 使用 [固定版本的镜像](https://huggingface.co/Kalashnikov/NudeNet/tree/9f7923d6c0c434c445d5d9df62a8dcecd44b7292)，并核对锁定的 SHA-256。两个包均附带上游 v3.4 权重版本的许可证。 |
