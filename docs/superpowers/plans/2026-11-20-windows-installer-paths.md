# Windows 安装包与自定义路径实施记录

方案见 `../specs/2026-11-20-windows-installer-paths-design.md`。已获用户确认。直接复用当前检出和 `dist/portable` 中上一轮生成的组件输入，避免重建大型 GPU 包。任何新安装及卸载验证仅使用隔离的测试目录和测试配置。

- [x] 分离程序目录、组件目录、离线包来源和每用户设置。在切换组件目录前验证复制结果，并测试中断时继续使用旧目录。
- [x] 在编辑器设置显示和更改路径。将需退出编辑器的操作交接给独立启动器，由启动器等待当前进程退出。
- [x] 增加 Inno Setup 构建入口和每用户安装、维护、卸载流程。更新快捷方式和卸载记录，保留用户文件。
- [x] 刷新便携启动器和基础包，保留现有其它组件，生成安装 EXE。
- [x] 运行回归和隔离安装测试，记录成品、测试结果、可复现命令与未完成项。

接口约定：安装版路径配置为 `%LOCALAPPDATA%\Q-Pixelate\installation.json`，键为 `program_dir`、`component_dir`、`archive_dir`、`installer`。只在 `program_dir` 匹配当前启动器目录时应用此配置。编辑器与启动器通过同一组件管理器读取状态，便携版配置仍独立于安装版。安装程序及源码管理模块互不依赖 Qt。

接手入口：编辑器设置在 `q_pixelate/component_dialog.py`，进程交接在 `q_pixelate/app.py`，命令解析和操作调度在 `launcher.py`。`--move-components DIR` 支持 `--wait-pid PID`，加 `--remove-old-copy` 才清理旧组件。`--relocate-program` 打开已缓存安装程序的 `/RELOCATE=1`。`--verify-installation` 可在安装程序内无界面检查登记与清单，`--remove-installed-components` 供卸载程序在用户明确选择后调用。

重新构建时保留 `dist/portable` 现有产物和安装。用以下命令重建启动器和基础组件，并校验复用其它五个包。其余包通过硬链接或复制复用，无须重新下载或压缩 GPU 包。

```powershell
python scripts/build_portable.py --offline --components base --reuse-catalog dist/portable/component-catalog.json --output dist/windows-0.2.0
python scripts/build_installer.py --portable dist/windows-0.2.0 --iscc build/inno-tool/compiler/ISCC.exe
python -X utf8 -m unittest discover -s tests -v
python -m compileall -q q_pixelate launcher.py scripts
```

交付于 2026-11-21。安装包为 `dist/windows-0.2.0/Q-Pixelate-Setup.exe`，同目录包含便携启动器和全部配套 ZIP。旧目录 `dist/portable` 保留。安装包使用 `build/inno-tool/compiler/ISCC.exe` 构建通过，六个组件包的清单大小和 SHA-256 均已校验。

验证记录：阶段全量回归通过 101 项。最终定向回归通过 11 项路径测试和 5 项启动器测试。真实 FFmpeg 与 320n 组件通过 D 盘迁到 C 盘的验证。隔离 Windows 安装测试使用含中文和空格的自选程序及组件目录，检查首次安装登记、程序迁移后卸载记录更新、旧目录用户文件保留，以及卸载默认保留组件和记住其路径。测试脚本位于本机 `.test-artifacts/installer-lifecycle-smoke.py`，会使用唯一的临时安装项并自动卸载，执行时需要可写当前用户的开始菜单和卸载注册表。还未在全新 Windows 虚拟机验收。
