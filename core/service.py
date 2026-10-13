"""mihomo systemd 服务操作封装。"""
import os
import subprocess
import time
from typing import Iterator

UNIT = "mihomo"
_PROPS = (
    "ActiveState",
    "SubState",
    "UnitFileState",
    "ExecMainPID",
    "ActiveEnterTimestamp",
    "MemoryCurrent",
)
_ACTIONS = ("enable", "start", "stop", "restart", "reload")


def _run(args: list[str], timeout: float = 30) -> tuple[int, str]:
    """执行命令，返回 (返回码, 合并输出)。"""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "命令执行超时"
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    return p.returncode, out


def require_root() -> str | None:
    """非 root 时返回错误提示，否则返回 None。"""
    if os.geteuid() != 0:
        return "需要 root 权限：请以系统服务方式运行面板（见 clashaux.service）"
    return None


def get_status() -> dict:
    """读取 mihomo 服务状态（只读，无需 root）。"""
    args = ["systemctl", "show", UNIT, "--no-pager"]
    args += [f"--property={p}" for p in _PROPS]
    _, out = _run(args)
    data: dict[str, str] = {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        data[k] = v
    mem = data.get("MemoryCurrent", "")
    return {
        "unit": UNIT,
        "active": data.get("ActiveState", "unknown"),
        "sub": data.get("SubState", ""),
        "enabled": data.get("UnitFileState", ""),
        "pid": data.get("ExecMainPID", "0"),
        "since": data.get("ActiveEnterTimestamp", ""),
        "memory": int(mem) if mem.isdigit() else 0,
    }


def service_action(action: str) -> tuple[bool, str]:
    """执行 enable / start / reload，需要 root。"""
    if action not in _ACTIONS:
        return False, f"不支持的操作: {action}"
    if err := require_root():
        return False, err
    rc, out = _run(["systemctl", action, UNIT])
    ok = rc == 0
    # start/reload 后短暂探活
    if ok and action in ("start", "restart", "reload"):
        time.sleep(1.0)
        _, state = _run(["systemctl", "is-active", UNIT])
        if state != "active":
            ok = False
            out = out or f"操作后服务状态: {state}"
    return ok, out or "成功"


def journal_tail(n: int) -> list[str]:
    """最近 n 行服务日志。"""
    n = max(1, min(n, 2000))
    _, out = _run(
        ["journalctl", "-u", UNIT, "-o", "cat", "-n", str(n), "--no-pager"],
        timeout=15,
    )
    return out.splitlines() if out else []


def journal_follow() -> Iterator[str]:
    """实时跟踪服务日志（journalctl -f）。"""
    p = subprocess.Popen(
        ["journalctl", "-u", UNIT, "-o", "cat", "-f", "-n", "0", "--no-pager"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:
        if p.stdout is None:
            return
        for line in p.stdout:
            yield line.rstrip("\n")
    finally:
        p.terminate()
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
