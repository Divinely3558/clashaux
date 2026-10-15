"""共享工具函数。"""
import subprocess


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
