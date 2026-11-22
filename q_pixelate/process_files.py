"""App-owned workspaces for media previews, export and analysis."""

import re
import shutil
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4

from .components import ComponentError, _directory_lock, _exists, _linked, _read_json, _setting_path, _write_json
from .process import retry_windows_file_operation


_FOLDER = 'Q-Pixelate-Process'
_MARKER = '.q-pixelate-process'


def _remove_workspace(workspace):
    # Keep ownership metadata until every task file has been removed.
    for entry in workspace.iterdir():
        if entry.name == _MARKER:
            continue
        if entry.is_junction():
            entry.rmdir()
        elif not entry.is_symlink() and entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink(missing_ok=True)
    (workspace / _MARKER).unlink(missing_ok=True)
    workspace.rmdir()


class ProcessFiles:
    def __init__(self, state_directory):
        self.state = state_directory
        self.settings = state_directory / 'process-files.json'

    def _settings(self):
        if _linked(self.settings):
            raise ComponentError('过程文件设置路径不能是链接。')
        data = _read_json(self.settings)
        return data if isinstance(data, dict) else {}

    def _location(self, value):
        if not isinstance(value, str) or '\x00' in value:
            return None
        path = self.state / value
        if path.is_absolute() and path.name == _FOLDER and '..' not in path.parts:
            return path
        return None

    def _roots(self, data):
        previous = data.get('locations')
        values = [data.get('location'), *(previous if isinstance(previous, list) else [])]
        roots = [self.state / _FOLDER]
        for value in values:
            path = self._location(value)
            if path is not None and path not in roots:
                roots.append(path)
        return roots

    @property
    def directory(self):
        return self._location(self._settings().get('location')) or self.state / _FOLDER

    @staticmethod
    def _validate(root):
        if _linked(root) or root != root.resolve():
            raise ComponentError(f'过程文件目录不能使用符号链接或目录联接：{root}。')

    def set_directory(self, parent):
        selected = None
        if parent is not None:
            selected = _setting_path(str(parent))
            if selected is None or _linked(selected):
                raise ComponentError('请选择有效的过程文件目录。')
        root = (self.state if selected is None else selected) / _FOLDER
        with _directory_lock(self.state, '.preferences.lock'):
            self._validate(root)
            root.mkdir(parents=True, exist_ok=True)
            with self._workspace(root):
                pass  # Check that the editor can create and remove a work directory here.
            locations = self._roots(self._settings())
            if root not in locations:
                locations.append(root)
            settings = {'location': self._serialize(root),
                        'locations': [self._serialize(path) for path in locations if path != locations[0]]}
            _write_json(self.settings, settings)

    def _serialize(self, path):
        return str(path.relative_to(self.state)) if path.is_relative_to(self.state) else str(path)

    @contextmanager
    def _workspace(self, root):
        workspace = root / f'job-{uuid4().hex}'
        workspace.mkdir()
        try:
            (workspace / _MARKER).write_text(workspace.name, encoding='ascii')
            with _directory_lock(workspace, _MARKER):
                yield workspace
        finally:
            try:
                retry_windows_file_operation(lambda: _remove_workspace(workspace))
            except OSError:
                pass  # Uninstall can retry if a file stays open.

    @contextmanager
    def workspace(self):
        with ExitStack() as lease:
            with _directory_lock(self.state, '.preferences.lock'):
                root = self.directory
                self._validate(root)
                root.mkdir(parents=True, exist_ok=True)
                workspace = lease.enter_context(self._workspace(root))
            yield workspace

    def remove_abandoned(self):
        with _directory_lock(self.state, '.preferences.lock'):
            for root in self._roots(self._settings()):
                if not _exists(root):
                    continue
                self._validate(root)
                for entry in root.iterdir():
                    if not re.fullmatch(r'job-[0-9a-f]{32}', entry.name) or _linked(entry) or not entry.is_dir():
                        continue
                    marker = entry / _MARKER
                    try:
                        owned = not _linked(marker) and marker.read_bytes() == entry.name.encode('ascii')
                    except FileNotFoundError:
                        continue
                    if owned:
                        # An active job holds this lease. Keep the state lock until deletion finishes.
                        with _directory_lock(entry, _MARKER):
                            pass
                        try:
                            retry_windows_file_operation(lambda: _remove_workspace(entry))
                        except FileNotFoundError:
                            if entry.exists():
                                raise
