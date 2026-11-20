"""Component storage, validation and executable discovery."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import contextmanager
from copy import copy
from functools import lru_cache
from pathlib import Path, PureWindowsPath
from urllib.request import urlopen


COMPONENTS = ('base', 'ffmpeg', 'cpu', 'gpu', '320n', '640m')
_MODELS = ('320n', '640m')
_DEVICES = ('cpu', 'gpu')
_TOOLS = ('ffmpeg', 'ffprobe')
_MARKER = '.q-pixelate-component.json'
_BLOCK_SIZE = 1024 * 1024
_ENCODERS = {'libx264', 'aac', 'ffv1'}
_FILTERS = {'alphaextract', 'alphamerge', 'apad', 'aresample', 'asetpts', 'asplit', 'atempo', 'atrim',
            'blend', 'boxblur', 'concat', 'format', 'fps', 'gblur', 'maskedmerge', 'null', 'overlay',
            'scale', 'scdet', 'select', 'setsar', 'setpts', 'settb', 'split', 'trim'}
_BASE_PROBE = '''
import struct, sys
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer
from q_pixelate.app import MainWindow

if sys.version_info[:2] != (3, 12) or struct.calcsize('P') != 8:
    raise RuntimeError('Expected 64-bit Python 3.12')
app = QApplication([])
player = QMediaPlayer()
if not player.isAvailable():
    raise RuntimeError('Qt Multimedia playback backend is unavailable')
print(sys.version.split()[0])
'''


class ComponentError(RuntimeError):
    pass


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return None


def _exists(path):
    return os.path.lexists(path)


def _linked(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def _setting_path(value, relative_to=None):
    if not isinstance(value, str) or not value or '\x00' in value:
        return None
    path = Path(value)
    if not path.is_absolute():
        if relative_to is None or path.anchor:
            return None
        path = relative_to / path
    try:
        # Resolve parent aliases while retaining a final link for the ownership checks.
        return path.parent.resolve() / path.name if _linked(path) else path.resolve()
    except (OSError, RuntimeError):
        return None


def _write_json(path, data, *, runner=None):
    with tempfile.TemporaryDirectory(dir=path.parent, prefix='.settings-', ignore_cleanup_errors=True) as workspace:
        pending = Path(workspace) / path.name
        with pending.open('x', encoding='utf-8') as output:
            json.dump(data, output, indent=2, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        if runner is not None:
            runner.check()
        pending.replace(path)


@contextmanager
def _directory_lock(directory, name='.change.lock'):
    try:
        if _linked(directory):
            raise ComponentError(f'目录不能是符号链接或目录联接：{directory}。')
        directory.mkdir(parents=True, exist_ok=True)
        lock = directory / name
        if _linked(lock):
            raise ComponentError('组件锁文件路径无效。')
        with lock.open('a+b') as handle:
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise ComponentError('已有组件变更任务正在运行。') from error
            yield
    except OSError as error:
        raise ComponentError(f'无法修改目录 {directory}，请确认有足够的磁盘空间和写入权限：{error}') from error


def _run_probe(args, runner=None, *, timeout=45):
    if runner is not None:
        runner.check()
    command = [str(arg) for arg in args]
    try:
        with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0) as process:
            deadline = time.monotonic() + timeout
            try:
                while True:
                    if runner is not None:
                        runner.check()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ComponentError(f'{Path(command[0]).name} 运行验证超时。')
                    try:
                        output, error = process.communicate(timeout=min(.2, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except BaseException:
                process.kill()
                process.communicate()
                raise
    except OSError as error:
        raise ComponentError(f'无法启动 {Path(command[0]).name}：{error}') from error
    if runner is not None:
        runner.check()
        if error.strip():
            runner.log(error)
    if process.returncode:
        detail = (error.strip() or output.strip() or f'退出代码 {process.returncode}')[-1500:]
        raise ComponentError(f'{Path(command[0]).name} 运行验证失败：{detail}')
    return output


def _capabilities(output):
    # FFmpeg 8 shortened the filter flags column to two characters.
    return set(re.findall(r'^ *[A-Z.]{2,6} +([A-Za-z0-9_]+) +', output, flags=re.MULTILINE))


def _probe_ffmpeg(tools, runner=None):
    if not tools:
        raise ComponentError('找不到完整的 FFmpeg 和 ffprobe。')
    ffmpeg, ffprobe = (tools[name] for name in _TOOLS)
    version = _run_probe([ffmpeg, '-version'], runner, timeout=10).splitlines()
    probe_version = _run_probe([ffprobe, '-version'], runner, timeout=10)
    if not version or not version[0].startswith('ffmpeg version ') or not probe_version.startswith('ffprobe version '):
        raise ComponentError('FFmpeg 或 ffprobe 未返回有效版本。')
    encoders = _capabilities(_run_probe([ffmpeg, '-hide_banner', '-encoders'], runner, timeout=10))
    filters = _capabilities(_run_probe([ffmpeg, '-hide_banner', '-filters'], runner, timeout=10))
    missing = sorted((_ENCODERS - encoders) | (_FILTERS - filters))
    if missing:
        raise ComponentError(f'当前 FFmpeg 缺少导出功能：{", ".join(missing)}。')
    return version[0].split()[2]


@lru_cache(maxsize=16)
def _cached_ffmpeg_probe(ffmpeg, ffprobe):
    try:
        return _probe_ffmpeg({'ffmpeg': ffmpeg[0], 'ffprobe': ffprobe[0]}), ''
    except ComponentError as error:
        return None, str(error)


def _tools_at(directory):
    result = {}
    for name in _TOOLS:
        candidates = [directory / f'{name}.exe']
        if os.name != 'nt':
            candidates.append(directory / name)
        tool = next((item for item in candidates if item.is_file()), None)
        if tool is None:
            return {}
        result[name] = str(tool.resolve())
    return result


def _tool_signature(path):
    state = Path(path).stat()
    return path, state.st_ino, state.st_size, state.st_mtime_ns


def _extract(zip_path, directory, runner):
    seen = {}
    with zipfile.ZipFile(zip_path) as archive:
        entries = archive.infolist()
        total = sum(entry.file_size for entry in entries)
        count = 0
        for entry in entries:
            runner.check()
            name = entry.orig_filename
            is_dir = entry.is_dir()
            parts = name.removesuffix('/').split('/')
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if ('\\' in name or any(not part or part.endswith(('.', ' ')) or
                                    PureWindowsPath(part).is_reserved() or
                                    re.search(r'[\x00-\x1f<>:"|?*]', part) for part in parts)
                    or parts[0].casefold() == _MARKER.casefold()
                    or mode not in (0, stat.S_IFDIR if is_dir else stat.S_IFREG)):
                raise ComponentError(f'离线包包含无效的路径：{name!r}。')
            for length in range(1, len(parts) + 1):
                prefix = tuple(parts[:length])
                key = tuple(part.casefold() for part in prefix)
                directory_entry = length < len(parts) or is_dir
                previous = seen.setdefault(key, (prefix, directory_entry))
                if previous != (prefix, directory_entry):
                    raise ComponentError(f'离线包包含冲突的路径：{name!r}。')
            destination = directory.joinpath(*parts)
            if is_dir:
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, destination.open('xb') as target:
                for block in iter(lambda: source.read(_BLOCK_SIZE), b''):
                    runner.check()
                    target.write(block)
                    count += len(block)
                    runner.report(50 + 28 * count / max(total, 1), '正在解压组件…')
    return total


def _copy_components(sources, stage, destination, runner):
    def raise_error(error):
        raise error

    files = []
    for source in sources:
        root = stage / source.name
        root.mkdir()
        for directory, folders, names in source.walk(on_error=raise_error):
            runner.check()
            target = root / directory.relative_to(source)
            for name in folders:
                if _linked(directory / name):
                    raise ComponentError(f'无法迁移包含目录联接的组件：{directory / name}。')
                (target / name).mkdir()
            for name in names:
                original = directory / name
                state = original.lstat()
                if _linked(original) or not stat.S_ISREG(state.st_mode):
                    raise ComponentError(f'无法迁移包含特殊文件的组件：{original}。')
                files.append((original, target / name, state.st_size))
    total = sum(size for _, _, size in files)
    if shutil.disk_usage(destination).free < total:
        raise ComponentError(f'目标目录没有足够的磁盘空间：{destination}。')

    copied = 0
    for original, target, size in files:
        runner.check()
        digest = hashlib.sha256()
        count = 0
        with original.open('rb') as source, target.open('xb') as output:
            for block in iter(lambda: source.read(_BLOCK_SIZE), b''):
                runner.check()
                output.write(block)
                digest.update(block)
                count += len(block)
                copied += len(block)
                runner.report(10 + 50 * copied / max(total, 1), '正在复制组件…')
        duplicate = hashlib.sha256()
        with target.open('rb') as output:
            for block in iter(lambda: output.read(_BLOCK_SIZE), b''):
                runner.check()
                duplicate.update(block)
        if count != size or duplicate.digest() != digest.digest():
            raise ComponentError(f'组件文件复制校验失败：{original}。')
        shutil.copymode(original, target)


class ComponentManager:
    def __init__(self, root: Path | None = None):
        default = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent
        self.root = Path(root if root is not None else os.environ.get('QPIXELATE_HOME') or default).resolve()
        self._location_file, self._directory, self._archive_directory, self.installer = self._locations()
        self._preferences_file = self._location_file.parent / 'component-preferences.json'
        catalog = _read_json(self.root / 'component-catalog.json')
        entries = catalog.get('components') if isinstance(catalog, dict) else None
        self.catalog = {key: value for key, value in entries.items()
                        if key in COMPONENTS and isinstance(value, dict)} if isinstance(entries, dict) else {}
        self._verified = {}

    def _locations(self):
        user_data = _setting_path(os.environ.get('LOCALAPPDATA'))
        if user_data:
            registration = user_data / 'Q-Pixelate' / 'installation.json'
            data = None if _linked(registration) else _read_json(registration)
            if isinstance(data, dict) and _setting_path(data.get('program_dir')) == self.root:
                directory = _setting_path(data.get('component_dir'))
                if directory and directory != self.root:
                    return (registration, directory, _setting_path(data.get('archive_dir')),
                            _setting_path(data.get('installer')))
        portable = self.root / 'component-location.json'
        data = None if _linked(portable) else _read_json(portable)
        directory = _setting_path(data.get('component_dir'), self.root) if isinstance(data, dict) else None
        return portable, directory if directory and directory != self.root else self.root / 'components', None, None

    @property
    def component_directory(self):
        return self._directory

    def path(self, component):
        if component not in COMPONENTS:
            raise ComponentError(f'未知组件：{component}。')
        return self._directory / component

    def _record(self, component, path):
        if _linked(self._directory) or _linked(path) or _linked(path / _MARKER):
            return None
        record = _read_json(path / _MARKER)
        if (isinstance(record, dict) and record.get('id') == component
                and isinstance(record.get('version'), str) and isinstance(record.get('sha256'), str)):
            return record
        return None

    @contextmanager
    def _mutation(self):
        with _directory_lock(self._directory):
            self._check_location()
            yield

    def _check_location(self):
        if self._locations()[:2] != (self._location_file, self._directory):
            raise ComponentError('组件安装位置已更改，请重新启动 Q-Pixelate。')

    def relocate_components(self, destination, runner, *, cleanup=False):
        runner.report(0, '正在检查组件的新位置…')
        destination = _setting_path(os.fspath(destination), Path.cwd())
        if destination is None or _linked(destination):
            raise ComponentError('请选择有效的组件目录。')
        original = self._directory
        if destination == original:
            return original
        if (destination == self.root or destination.is_relative_to(original)
                or original.is_relative_to(destination)):
            raise ComponentError('新组件目录不能与现有组件目录重叠，也不能是程序安装目录。')

        with self._mutation(), _directory_lock(destination):
            sources = {}
            for component in COMPONENTS:
                for name in (component, self._previous(component).name):
                    occupied = destination / name
                    if _exists(occupied):
                        raise ComponentError(f'目标组件目录已有文件：{occupied}。')
                source = self.path(component)
                if not _exists(source) and self._record(component, self._previous(component)) is not None:
                    self._recover(component)
                if self._record(component, source) is not None:
                    sources[component] = source

            candidate = copy(self)
            candidate._directory = destination
            source_ffmpeg = self._ffmpeg_selection(runner=runner)[0]
            with tempfile.TemporaryDirectory(dir=destination, prefix='.move-', ignore_cleanup_errors=True) as workspace:
                stage = Path(workspace)
                _copy_components(sources.values(), stage, destination, runner)
                promoted = []
                try:
                    for component in sources:
                        runner.check()
                        target = candidate.path(component)
                        (stage / component).rename(target)
                        promoted.append(target)
                    runner.report(75, '正在验证新位置的组件…')
                    candidate._verify_relocation(sources, source_ffmpeg, runner)
                    runner.report(95, '正在启用新的组件位置…')
                    self._check_location()
                    self._save_component_location(destination, runner)
                except BaseException:
                    for target in reversed(promoted):
                        target.rename(stage / target.name)
                    raise

            try:
                if cleanup:
                    configured = _setting_path(self.preferences()['ffmpeg_directory'])
                    for component, source in sources.items():
                        if self._record(component, source) is None:
                            continue
                        if configured and configured.is_relative_to(source):
                            runner.log(f'保留手动指定的 FFmpeg 目录：{configured}。')
                            continue
                        try:
                            self._discard(source)
                        except OSError as error:
                            runner.log(f'新位置已启用，无法清理旧组件目录 {source}：{error}')
            finally:
                self._directory = destination
                self._verified.clear()
        return destination

    def _save_component_location(self, destination, runner):
        if self._location_file.name == 'installation.json':
            data = _read_json(self._location_file)
            if not isinstance(data, dict) or _setting_path(data.get('program_dir')) != self.root:
                raise ComponentError('无法读取当前安装配置。')
            data['component_dir'] = str(destination)
        else:
            directory = destination.relative_to(self.root) if destination.is_relative_to(self.root) else destination
            data = {'component_dir': str(directory)}
        _write_json(self._location_file, data, runner=runner)

    def _verify_relocation(self, sources, source_ffmpeg, runner):
        for component in sources:
            runner.check()
            self._probe(component, self.path(component), runner)
        for component in (model for model in _MODELS if model in sources):
            for device in (runtime for runtime in _DEVICES if runtime in sources):
                self._detector_probe(self.path(device), device, runner, self.path(component) / 'weights.onnx')
        if source_ffmpeg in ('configured', 'PATH'):
            new_source = self._ffmpeg_selection(runner=runner)[0]
            if new_source != source_ffmpeg:
                raise ComponentError('新位置无法使用原来的 FFmpeg 和 ffprobe。')

    def preferences(self):
        stored = _read_json(self._preferences_file)
        if not isinstance(stored, dict):
            stored = {}
        model, device, location = (stored.get(key) for key in ('model', 'device', 'ffmpeg_directory'))
        return {'model': model if model in _MODELS else _MODELS[0],
                'device': device if device in _DEVICES else _DEVICES[0],
                'ffmpeg_directory': str(self.root / location) if isinstance(location, str) and location else None}

    def set_preferences(self, **fields):
        if not fields.keys() <= {'model', 'device', 'ffmpeg_directory'}:
            raise ComponentError('包含未知的组件设置。')
        with _directory_lock(self._preferences_file.parent, '.preferences.lock'):
            self._check_location()
            settings = self.preferences()
            settings.update(fields)
            if settings['model'] not in _MODELS or settings['device'] not in _DEVICES:
                raise ComponentError('识别模型或执行设备无效。')
            directory = settings['ffmpeg_directory']
            if directory:
                path = Path(directory).expanduser().resolve()
                if self._location_file.name != 'installation.json' and path.is_relative_to(self.root):
                    path = path.relative_to(self.root)
                settings['ffmpeg_directory'] = path.as_posix()
            else:
                settings['ffmpeg_directory'] = None
            _write_json(self._preferences_file, settings)

    def set_ffmpeg_directory(self, directory):
        self.set_preferences(ffmpeg_directory=directory)

    def _ffmpeg_selection(self, verify=False, runner=None):
        directory = self.path('ffmpeg')
        configured = self.preferences()['ffmpeg_directory']
        candidates = []
        reason = ''
        if configured:
            candidates.append(('configured', _tools_at(Path(configured))))
        if self._record('ffmpeg', directory) is not None:
            try:
                self._check_layout('ffmpeg', directory)
                candidates.append(('managed', _tools_at(directory / 'bin')))
            except (ComponentError, OSError) as error:
                reason = str(error)
        system = {name: shutil.which(name) for name in _TOOLS}
        if all(system.values()):
            candidates.append(('PATH', {name: str(Path(path).resolve()) for name, path in system.items()}))
        if verify:
            _cached_ffmpeg_probe.cache_clear()
        for source, tools in candidates:
            if not tools:
                reason = reason or '找不到完整的 FFmpeg 和 ffprobe。'
                continue
            try:
                if runner is not None:
                    version, error = _probe_ffmpeg(tools, runner), ''
                else:
                    ffmpeg, ffprobe = (_tool_signature(tools[name]) for name in _TOOLS)
                    version, error = _cached_ffmpeg_probe(ffmpeg, ffprobe)
            except (ComponentError, OSError) as error:
                reason = reason or str(error)
                continue
            if version:
                return source, tools, version, reason
            reason = reason or error
        return None, {}, None, reason

    def ffmpeg_tools(self):
        """Return a compatible, complete pair of absolute executable paths."""
        return self._ffmpeg_selection()[1]

    def local_archive(self, component):
        filename = self.catalog.get(component, {}).get('archive')
        if (not isinstance(filename, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*\.zip', filename)
                or PureWindowsPath(filename).is_reserved()):
            return None
        for directory in (self.root, self._archive_directory):
            if directory is not None:
                path = directory / filename
                if path.is_file() and not _linked(path):
                    return path
        return None

    def _check_layout(self, component, directory):
        files = {'base': ('python.exe', 'pythonw.exe', 'app/q_pixelate/__main__.py'),
                 'cpu': ('site-packages/onnxruntime/__init__.py',),
                 'gpu': ('site-packages/onnxruntime/__init__.py',),
                 '320n': ('weights.onnx',), '640m': ('weights.onnx',),
                 'ffmpeg': ('bin/ffmpeg.exe', 'bin/ffprobe.exe')}
        for name in files[component]:
            item = directory / name
            if not item.is_file() or not item.stat().st_size or not item.resolve().is_relative_to(directory):
                raise ComponentError(f'组件文件缺失或无效：{name}。')

    def _detector_probe(self, directory, device, runner, model=None):
        from .detection import detector_command

        python = self.path('base') / 'python.exe'
        if self._record('base', python.parent) is None or not python.is_file():
            if getattr(sys, 'frozen', False):
                raise ComponentError('请先安装基础运行环境，再验证识别组件。')
            python = Path(sys.executable)
        command = detector_command(python, directory / 'site-packages', model, device,
                                   probe=model is not None, runtime_probe=model is None)
        text = _run_probe(command, runner, timeout=90)
        try:
            provider = json.loads(text)['provider']
        except (TypeError, ValueError, KeyError) as error:
            raise ComponentError('识别环境未返回有效的推理设备。') from error
        expected = 'CUDAExecutionProvider' if device == 'gpu' else 'CPUExecutionProvider'
        if provider != expected:
            raise ComponentError(f'未能在 {expected} 完成推理。')
        return provider

    def _probe(self, component, directory, runner=None, *, installing=False):
        self._check_layout(component, directory)
        if component == 'base':
            version = _run_probe([directory / 'python.exe', '-I', '-c', _BASE_PROBE], runner).strip()
            return f'Python {version} / Qt Multimedia'
        if component == 'ffmpeg':
            return _probe_ffmpeg(_tools_at(directory / 'bin'), runner)
        if component in _DEVICES:
            return self._detector_probe(directory, component, runner)
        if installing:
            for device in _DEVICES:
                if self.status(device)['ready']:
                    return self._detector_probe(self.path(device), device, runner, directory / 'weights.onnx')
        return ''

    def _previous(self, component):
        return self._directory / f'.{component}.previous'

    def _discard(self, path):
        with tempfile.TemporaryDirectory(dir=self._directory, prefix='.cleanup-', ignore_cleanup_errors=True) as workspace:
            path.rename(Path(workspace) / path.name)

    def _recover(self, component):
        previous = self._previous(component)
        if not _exists(previous):
            return
        if self._record(component, previous) is None:
            raise ComponentError(f'无法恢复组件 {component}，备份目录属于其他文件。')
        target = self.path(component)
        if not _exists(target):
            previous.rename(target)
        elif self._record(component, target) is not None:
            self._discard(previous)

    def status(self, component, *, verify=False, runner=None):
        path = self.path(component)
        if not _exists(path) and _exists(self._previous(component)):
            try:
                with self._mutation():
                    self._recover(component)
            except ComponentError:
                pass
        record = self._record(component, path)
        entry = self.catalog.get(component, {})
        installed = record is not None
        source = 'managed' if installed else None
        version = (record or entry).get('version')
        ready = False
        message = '组件目录包含其他文件。' if not installed and _exists(path) else '未安装。'
        if component == 'ffmpeg':
            origin, tools, detected_version, reason = self._ffmpeg_selection(verify, runner)
            if origin:
                source, ready = origin, bool(tools)
                version = version if origin == 'managed' else detected_version
                message = reason or f'FFmpeg {detected_version}'
            elif reason:
                message = reason
            if tools and origin != 'managed':
                path = Path(tools['ffmpeg']).parent
        elif installed and component == 'base' and entry.get('version') and version != entry['version']:
            message = f'基础运行环境需要从 {version} 更新至 {entry["version"]}。'
        elif installed:
            try:
                self._check_layout(component, path)
                if verify:
                    try:
                        self._verified[component] = (record['sha256'], True, self._probe(component, path, runner))
                    except (ComponentError, OSError) as error:
                        self._verified[component] = (record['sha256'], False, str(error))
                verified = self._verified.get(component)
                if verified and verified[0] == record['sha256']:
                    _, ready, message = verified
                else:
                    ready, message = True, record.get('verification') or '已安装。'
            except (ComponentError, OSError) as error:
                message = str(error)
        return {'id': component, 'installed': installed, 'ready': ready, 'source': source, 'path': path,
                'version': version, 'download_size': entry.get('size'),
                'installed_size': record.get('installed_size', 0) if installed else 0,
                'message': message or '已安装。'}

    def _download(self, entry, source, path, runner):
        size, sha256 = entry['size'], entry['sha256']
        label = '正在导入离线包…' if source is not None else '正在下载组件…'
        runner.report(0, label)
        digest = hashlib.sha256()
        count = 0
        read_error = '无法读取离线组件包' if source is not None else '组件下载失败，请检查网络或导入离线包'
        try:
            stream = Path(source).open('rb') if source is not None else urlopen(entry['url'], timeout=15)
        except (OSError, ValueError) as error:
            raise ComponentError(f'{read_error}：{error}') from error
        with stream, path.open('xb') as target:
            while True:
                runner.check()
                try:
                    block = stream.read(_BLOCK_SIZE)
                except OSError as error:
                    raise ComponentError(f'{read_error}：{error}') from error
                if not block:
                    break
                count += len(block)
                if count > size:
                    raise ComponentError('组件包大小与发行清单不符。')
                digest.update(block)
                target.write(block)
                runner.report(45 * count / size, label)
        runner.report(48, '正在校验组件包…')
        if count != size or digest.hexdigest() != sha256.lower():
            raise ComponentError('组件包大小或 SHA-256 校验失败。')

    def install(self, component, runner, archive=None):
        target = self.path(component)
        entry = self.catalog.get(component, {})
        if (not isinstance(entry.get('version'), str) or not entry['version']
                or type(entry.get('size')) is not int or entry['size'] <= 0
                or not isinstance(entry.get('sha256'), str) or not re.fullmatch(r'[0-9a-fA-F]{64}', entry['sha256'])):
            raise ComponentError(f'发行清单中缺少 {component} 的有效版本、大小或 SHA-256。')
        if archive is None:
            archive = self.local_archive(component)
        if archive is None and (not isinstance(entry.get('url'), str) or
                                not entry['url'].lower().startswith(('https://', 'http://'))):
            raise ComponentError('发行清单缺少下载地址，可使用离线包安装。')
        with self._mutation():
            self._recover(component)
            if _exists(target) and self._record(component, target) is None:
                raise ComponentError(f'组件目录已有其他文件：{target}。')
            with tempfile.TemporaryDirectory(dir=self._directory, prefix='.install-', ignore_cleanup_errors=True) as workspace:
                package = Path(workspace) / 'package.zip'
                staged = Path(workspace) / 'payload'
                staged.mkdir()
                self._download(entry, archive, package, runner)
                runner.report(50, '正在解压组件…')
                try:
                    installed_size = _extract(package, staged, runner)
                except (zipfile.BadZipFile, RuntimeError, NotImplementedError, FileExistsError) as error:
                    if isinstance(error, ComponentError):
                        raise
                    raise ComponentError(f'无法解压组件包：{error}') from error
                runner.report(80, '正在验证组件…')
                verification = self._probe(component, staged, runner, installing=True)
                (staged / _MARKER).write_text(json.dumps({'id': component, 'version': entry['version'],
                    'sha256': entry['sha256'], 'installed_size': installed_size,
                    'verification': verification}, ensure_ascii=False), encoding='utf-8')
                runner.report(98, '正在启用组件…')
                runner.check()
                previous = self._previous(component)
                if target.exists():
                    target.rename(previous)
                try:
                    staged.rename(target)
                except OSError:
                    if previous.exists():
                        previous.rename(target)
                    raise
                if previous.exists():
                    try:
                        self._discard(previous)
                    except OSError as error:
                        runner.log(str(error))
                self._verified.pop(component, None)
        return self.status(component)

    def uninstall(self, component):
        target = self.path(component)
        with self._mutation():
            self._recover(component)
            if not _exists(target):
                return
            if self._record(component, target) is None:
                raise ComponentError(f'组件目录已有其他文件：{target}。')
            self._discard(target)
            self._verified.pop(component, None)
