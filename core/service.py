"""mihomo systemd 服务操作封装。"""
import os
import re
import time

from core.utils import run_cmd

from core.paths import MIHOMO_UNIT as UNIT
_PROPS = (
    "ActiveState",
    "SubState",
    "UnitFileState",
    "ExecMainPID",
    "ActiveEnterTimestamp",
    "MemoryCurrent",
)
_ACTIONS = ("enable", "disable", "start", "stop", "restart", "reload")


def require_root() -> str | None:
    """非 root 时返回错误提示，否则返回 None。"""
    if os.geteuid() != 0:
        return "需要 root 权限：请以系统服务方式运行面板（见 clashaux.service）"
    return None


def get_status() -> dict:
    """读取 mihomo 服务状态（只读，无需 root）。"""
    args = ["systemctl", "show", UNIT, "--no-pager"]
    args += [f"--property={p}" for p in _PROPS]
    _, out = run_cmd(args)
    data: dict[str, str] = {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        data[k] = v
    mem = data.get("MemoryCurrent", "")
    since = data.get("ActiveEnterTimestamp", "")
    # systemctl 输出形如 "Tue 2026-10-20 05:40:35 CST"，只保留日期时间部分
    m = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", since)
    return {
        "unit": UNIT,
        "active": data.get("ActiveState", "unknown"),
        "sub": data.get("SubState", ""),
        "enabled": data.get("UnitFileState", ""),
        "pid": data.get("ExecMainPID", "0"),
        "since": m.group(0) if m else since,
        "memory": int(mem) if mem.isdigit() else 0,
    }


def service_action(action: str) -> tuple[bool, str]:
    """执行 enable / start / reload，需要 root。"""
    if action not in _ACTIONS:
        return False, f"不支持的操作: {action}"
    if err := require_root():
        return False, err
    rc, out = run_cmd(["systemctl", action, UNIT])
    # reload 失败时（单元无 ExecReload）降级为 restart
    if action == "reload" and rc != 0 and "not applicable" in out:
        rc, out = run_cmd(["systemctl", "restart", UNIT])
    ok = rc == 0
    # start/reload/restart 后短暂探活
    if ok and action in ("start", "restart", "reload"):
        time.sleep(1.0)
        _, state = run_cmd(["systemctl", "is-active", UNIT])
        if state != "active":
            ok = False
            out = out or f"操作后服务状态: {state}"
    return ok, out or "成功"
