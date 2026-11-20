"""Build the per-user Windows installer from the portable launcher release."""

import argparse
from pathlib import Path
import os
import shutil
import subprocess
import sys


REPO = Path(__file__).resolve().parents[1]


def find_compiler():
    command = shutil.which('ISCC.exe')
    if command:
        return Path(command)
    for variable in ('LOCALAPPDATA', 'ProgramFiles(x86)', 'ProgramFiles'):
        if root := os.environ.get(variable):
            parent = Path(root)
            if variable == 'LOCALAPPDATA':
                parent /= 'Programs'
            for version in (7, 6):
                compiler = parent / f'Inno Setup {version}' / 'ISCC.exe'
                if compiler.is_file():
                    return compiler
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iscc', type=Path, help='Path to ISCC.exe from Inno Setup 6.3 or later')
    parser.add_argument('--portable', type=Path, default=REPO / 'dist' / 'portable',
                        help='Portable release containing the launcher and catalog')
    parser.add_argument('--output', type=Path, help='Installer output directory, defaults to --portable')
    args = parser.parse_args()
    compiler = args.iscc or find_compiler()
    if compiler is None or not compiler.is_file():
        parser.error('Inno Setup compiler not found. Provide its path via --iscc.')
    portable = args.portable.resolve()
    for name in ('Q-Pixelate.exe', 'component-catalog.json', 'README.md',
                 'launcher-licenses/PyInstaller-COPYING.txt'):
        if not (portable / name).is_file():
            parser.error(f'Missing portable release file: {portable / name}')
    output = (args.output or portable).resolve()
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO))
    from q_pixelate import __version__

    subprocess.run([str(compiler.resolve()), f'/DPortableDir={portable}',
                    f'/DProductVersion={__version__}', f'/O{output}',
                    str(Path(__file__).with_name('q-pixelate.iss').resolve())], check=True)


if __name__ == '__main__':
    main()
