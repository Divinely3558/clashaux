"""共享工具函数。"""
import os
import subprocess
import tempfile
from pathlib import Path


def run_cmd(args: list[str], timeout: float = 30, silent: bool = False) -> tuple[int, str]:
    """执行命令，返回 (返回码, 合并输出)。

    silent=True 时 OSError/TimeoutExpired 返回 (1, "")，用于探测类调用。
    silent=False 时 TimeoutExpired 返回 (124, "命令执行超时")。
    """
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        if silent:
            return 1, ""
        return 124, "命令执行超时"
    except OSError:
        if silent:
            return 1, ""
        raise
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def atomic_write(path: Path, text: str) -> None:
    """原子写入文本文件：继承原文件的属主与权限位（不存在时 0644）。

    直接覆盖会把文件换成新 inode，属主与权限被重置为进程身份；这里先读旧
    文件的属主与权限位，写完后套用到临时文件再 os.replace。非 root 时
    chown 失败静默保持写入者身份。
    """
    path = Path(path)
    try:
        st = path.stat()
        uid, gid, mode = st.st_uid, st.st_gid, st.st_mode & 0o7777
    except OSError:
        uid, gid, mode = -1, -1, 0o644
    fd, tmp = tempfile.mkstemp(suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        if uid != -1:
            try:
                os.chown(tmp, uid, gid)
            except OSError:  # 非 root 无法改属主，保持写入者身份
                pass
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
