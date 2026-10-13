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

# mihomo 安装布局：二进制与数据目录都在面板程序目录内
# （external-ui 等均位于 mihomo home 内，无需 SAFE_PATHS 放行）
MIHOMO_BIN = DATA_DIR / "mihomo"
MIHOMO_HOME = DATA_DIR

LOCAL_PROXY = "http://127.0.0.1:7890"
