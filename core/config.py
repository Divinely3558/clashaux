"""配置换头、校验、备份与生效。

流程：新配置 → 去头(保留正文) → 拼接 base.yaml 头 → mihomo -t 校验
    → 备份当前生效配置 → 原子写入 → systemctl reload mihomo → 探活。
"""
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

from core import basic as basicm
from core import mihomo
from core.paths import BUNDLE_DIR, DATA_DIR
from core.utils import run_cmd

BASE_FILE = DATA_DIR / "base.yaml"
BACKUP_DIR = DATA_DIR / "backups"
KEEP_BACKUPS = 5

# 打包后 base.yaml 在 BUNDLE_DIR（sys._MEIPASS），首次运行时复制到 DATA_DIR
if not BASE_FILE.exists():
    _bundled = BUNDLE_DIR / "base.yaml"
    if _bundled.exists():
        BASE_FILE.write_bytes(_bundled.read_bytes())

# 生效配置路径（mihomo 数据目录下，动态检测）。若用户将其设为软链接（指向外部真实
# 配置），写入时会先 resolve 再写目标路径，避免 os.replace 把软链接整个替换掉。
def live_link() -> Path:
    return mihomo.home() / "config.yaml"

# 属于"正文"的顶层键：新文件从第一个命中的键开始保留到文件尾
BODY_KEYS = ("proxies", "proxy-groups", "proxy-providers", "rule-providers", "rules", "sub-rules")


class ConfigError(Exception):
    """配置处理失败。"""


def live_file() -> Path:
    """返回生效配置的真实路径（必须已存在）。"""
    link = live_link()
    if not link.exists():
        raise ConfigError(f"未找到生效配置: {link}")
    return link.resolve()


def live_target() -> Path:
    """生效配置的写入路径：软链接先解析到目标，目标不存在时返回其应写入位置。

    与 live_file() 的区别是允许文件尚不存在，用于首次自动生成生效配置
    （dangling 软链接也会被解析，写入目标后软链接本身不受影响）。
    """
    link = live_link()
    if link.is_symlink():
        return Path(os.path.realpath(link))
    return link


def strip_header(text: str) -> str:
    """去掉新配置文件的头部，返回从第一个正文键起的原文（保留注释）。"""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML 解析失败: {e}") from e
    if not isinstance(data, dict) or not data:
        raise ConfigError("配置文件不是有效的 YAML 映射")

    start = next((k for k in data if k in BODY_KEYS), None)
    if start is None:
        raise ConfigError(f"未在新配置中找到正文键（{'/'.join(BODY_KEYS)}），无法识别正文")

    pat = re.compile(rf"^{re.escape(start)}\s*:")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if pat.match(line):
            return "\n".join(lines[i:]) + "\n"
    raise ConfigError(f"未在文件中定位到顶层键 {start}:")


def build_candidate(new_text: str) -> str:
    """基础设置头 + base.yaml 头 + 新文件正文 = 候选配置。

    基础设置（external-controller/external-ui/secret/mixed-port）始终置于最头部；
    base.yaml 中如残留这些键会被剥离，避免重复定义。
    """
    basics = basicm.render(basicm.load())
    base = basicm.strip_keys(BASE_FILE.read_text(encoding="utf-8")).rstrip("\n")
    body = strip_header(new_text).strip("\n")
    return f"{basics}\n{base}\n\n{body}\n"


def minimal_candidate(basics: dict | None = None) -> str:
    """最小生效配置：基础设置头 + base.yaml 头（无正文）。"""
    header = basicm.render(basics if basics is not None else basicm.load())
    base = basicm.strip_keys(BASE_FILE.read_text(encoding="utf-8")).rstrip("\n")
    return f"{header}\n{base}\n" if base else f"{header}\n"


def ensure_live_config(basics: dict | None = None) -> dict:
    """生效配置缺失时自动生成一份最小配置（仅头部，无正文）。

    首次使用时 mihomo 数据目录下还没有 config.yaml，此前所有需要它的操作
    都会直接报「未找到生效配置」；现在按基础设置 + base.yaml 自动生成。
    """
    if live_link().exists():
        return {"ok": True, "created": False, "path": str(live_file())}
    target = live_target()
    candidate = minimal_candidate(basics)
    ok, out = validate(candidate)
    if not ok:
        return {"ok": False, "created": False,
                "error": "自动生成的生效配置未通过 mihomo -t 校验", "output": out}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, candidate)
    except (OSError, ConfigError) as e:
        return {"ok": False, "created": False, "error": f"自动生成生效配置失败: {e}"}
    return {"ok": True, "created": True, "path": str(target)}


def _atomic_write(path: Path, text: str) -> None:
    """原子写入配置：继承原文件的所有者与权限位。

    生效配置常常是用户自己维护的文件（权限/属主由用户设定），写入时若直接
    os.replace 会换成新 inode，属主与权限被重置为进程身份 + 0644，用户事后
    无法再按自己的权限修改。这里先读旧文件的属主与权限位，写完后套用到临时
    文件再替换；文件不存在（首次生成）时回退 0644。
    """
    try:
        st = path.stat()
        uid, gid, mode = st.st_uid, st.st_gid, st.st_mode & 0o7777
    except OSError:
        uid = gid = -1
        mode = 0o644
    fd, tmp = tempfile.mkstemp(suffix=".yaml", dir=str(path.parent))
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
    except PermissionError as e:
        os.unlink(tmp)
        raise ConfigError(f"写入 {path} 失败，需要 root 权限: {e}") from e
    except OSError:
        os.unlink(tmp)
        raise


def apply_base_template() -> dict:
    """把当前 base.yaml 模板套用到生效配置：保留正文，只替换头部。

    与换头流程一致（基础设置 + 新 base.yaml + 原正文），校验通过后备份并写入；
    生效配置不存在时直接生成一份最小配置。
    """
    try:
        text = live_file().read_text(encoding="utf-8")
    except (ConfigError, OSError):
        text = ""
    if text.strip():
        try:
            candidate = build_candidate(text)
        except ConfigError:
            # 当前配置只有头部、没有正文键，无从保留正文
            candidate = minimal_candidate()
    else:
        candidate = minimal_candidate()
    ok, out = validate(candidate)
    if not ok:
        return {"ok": False, "stage": "validate",
                "error": "mihomo -t 校验未通过", "output": out}
    backup = backup_current()
    _atomic_write(live_target(), candidate)
    return {"ok": True, "backup": backup, "created": not backup,
            "stats": stats(candidate)}


def apply_basic_settings(basics: dict) -> dict:
    """把基础设置应用到当前生效配置：置顶重写头部 → 校验 → 备份 → 原子写入。

    重载由调用方执行。尚无生效配置时按基础设置 + base.yaml 自动生成一份。
    """
    header = basicm.render(basics)
    target = live_target()
    try:
        text = target.read_text(encoding="utf-8") if target.exists() else ""
    except OSError as e:
        raise ConfigError(f"读取 {target} 失败: {e}") from e
    if text.strip():
        candidate = header + "\n" + basicm.strip_keys(text).rstrip("\n") + "\n"
        created = False
    else:
        candidate = minimal_candidate(basics)
        created = True
    ok, out = validate(candidate)
    if not ok:
        return {"ok": False, "error": "mihomo -t 校验未通过", "output": out}
    backup = backup_current()
    _atomic_write(target, candidate)
    return {"ok": True, "written": True, "created": created, "backup": backup}


def validate(text: str) -> tuple[bool, str]:
    """mihomo -t 校验候选配置，返回 (是否通过, 输出尾部)。"""
    fd, tmp = tempfile.mkstemp(suffix=".yaml", prefix="clashaux-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        bin_path = mihomo.detect_bin() or "mihomo"
        rc, out = run_cmd([bin_path, "-t", "-d", str(mihomo.home()), "-f", tmp], timeout=120)
    finally:
        os.unlink(tmp)
    tail = "\n".join(out.splitlines()[-30:])
    return rc == 0 and "test is successful" in out, tail


def stats(text: str) -> dict:
    """统计候选配置内容。"""
    data = yaml.safe_load(text) or {}

    def _count(k: str) -> int:
        return len(data.get(k) or [])

    return {
        "proxies": _count("proxies"),
        "groups": _count("proxy-groups"),
        "providers": _count("proxy-providers"),
        "rules": _count("rules"),
        "rule_providers": _count("rule-providers"),
    }


def live_info(auto_create: bool = False) -> dict:
    """当前生效配置概况（只读，无需 root）。

    auto_create=True 且生效配置缺失时，自动生成一份最小配置再返回概况。
    """
    created = False
    if auto_create and not live_link().exists():
        r = ensure_live_config()
        if not r.get("ok"):
            return {"exists": False, "error": r.get("error", ""),
                    "link": str(live_link())}
        created = bool(r.get("created"))
    try:
        path = live_file()
    except ConfigError as e:
        return {"exists": False, "error": str(e)}
    info: dict = {"exists": True, "path": str(path), "link": str(live_link()),
                  "created": created}
    st = path.stat()
    info["mtime"] = int(st.st_mtime)
    info["size"] = st.st_size
    try:
        info.update(stats(path.read_text(encoding="utf-8")))
    except (yaml.YAMLError, OSError):
        pass
    return info


def backup_current() -> str:
    """备份当前生效配置到 backups/，超出保留数时清理旧备份。

    尚无生效配置时返回空串（首次使用没有可备份的内容）。
    """
    try:
        path = live_file()
    except ConfigError:
        return ""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"config-{datetime.now():%Y%m%d-%H%M%S}.yaml"
    shutil.copy2(path, BACKUP_DIR / name)
    olds = sorted(BACKUP_DIR.glob("config-*.yaml"))
    for p in olds[:-KEEP_BACKUPS]:
        p.unlink()
    return name


def list_backups() -> list[dict]:
    items = []
    if BACKUP_DIR.exists():
        for p in sorted(BACKUP_DIR.glob("config-*.yaml"), reverse=True):
            st = p.stat()
            items.append({
                "name": p.name,
                "size": st.st_size,
                "mtime": int(st.st_mtime),
            })
    return items


def rollback(name: str) -> tuple[bool, str]:
    """回滚到指定备份：校验通过后覆盖当前配置（先再备份一次）。"""
    if not re.fullmatch(r"config-[\w.-]+\.yaml", name):
        return False, "非法备份名"
    path = BACKUP_DIR / name
    if not path.exists():
        return False, "备份不存在"
    text = path.read_text(encoding="utf-8")
    ok, out = validate(text)
    if not ok:
        return False, f"备份文件校验未通过: {out}"
    if live_link().exists():
        backup_current()
    _atomic_write(live_target(), text)
    return True, f"已回滚到 {name}"


def apply(new_text: str) -> dict:
    """完整生效流程：校验 → 备份 → 写入。重载由调用方执行。

    尚无生效配置（首次使用）时跳过备份，直接生成。
    """
    candidate = build_candidate(new_text)
    ok, out = validate(candidate)
    if not ok:
        return {"ok": False, "stage": "validate", "error": "mihomo -t 校验未通过", "output": out}
    backup = backup_current()
    _atomic_write(live_target(), candidate)
    return {"ok": True, "backup": backup, "created": not backup, "stats": stats(candidate)}
