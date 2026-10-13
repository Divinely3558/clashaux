"""路径解析：兼容开发模式与 PyInstaller 打包后的可执行文件。

BUNDLE_DIR  — 只读资源目录（开发时 = 项目根目录，打包后 = sys._MEIPASS）
DATA_DIR    — 可写数据目录（开发时 = 项目根目录，打包后 = 可执行文件所在目录）
"""
import sys
from pathlib import Path


def _resolve() -> tuple[Path, Path]:
    if getattr(sys, "frozen", False):
        bundle = Path(sys._MEIPASS)
        data = Path(sys.executable).parent
    else:
        root = Path(__file__).resolve().parent.parent
        bundle = root
        data = root
    return bundle, data


BUNDLE_DIR, DATA_DIR = _resolve()
