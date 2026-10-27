"""clashaux — mihomo 辅助管理面板。"""
import asyncio
import json
import logging
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import basic as basicm
from core import config as cfg
from core import mihomo
from core import service as svc
from core import subscription as subm
from core.config import ConfigError
from core.paths import BUNDLE_DIR, DATA_DIR
from core.utils import atomic_write

logger = logging.getLogger("clashaux")

CONFIG_FILE = DATA_DIR / "clashaux.json"
DEFAULT_CONFIG = {"port": 8686, "token": ""}


def _load_config() -> dict:
    if not CONFIG_FILE.exists():
        config = dict(DEFAULT_CONFIG)
        config["token"] = secrets.token_hex(16)
        CONFIG_FILE.write_text(
            json.dumps(config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return config
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            if "token" not in data:
                data["token"] = secrets.token_hex(16)
                CONFIG_FILE.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
            return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("配置文件损坏，使用默认配置: %s", e)
    config = dict(DEFAULT_CONFIG)
    config["token"] = secrets.token_hex(16)
    return config


_app_config = _load_config()
HOST = "0.0.0.0"
PORT = _app_config.get("port", DEFAULT_CONFIG["port"])
TOKEN = _app_config.get("token", "")
STATIC_DIR = BUNDLE_DIR / "static"

# 短时效 ticket：SSE 日志流与备份下载链接通过 query 传 ticket 而非 token
# （EventSource / <a> 不支持自定义 Header），60 秒内可重复使用
_TICKET_TTL = 60.0
_tickets: dict[str, float] = {}


def _issue_ticket() -> str:
    now = time.monotonic()
    for t in [k for k, exp in _tickets.items() if exp <= now]:
        _tickets.pop(t, None)
    t = secrets.token_urlsafe(24)
    _tickets[t] = now + _TICKET_TTL
    return t


def _check_ticket(ticket: str) -> bool:
    exp = _tickets.get(ticket)
    return exp is not None and exp > time.monotonic()


class SubBody(BaseModel):
    url: str = ""
    name: str = ""
    new_name: str = ""
    proxy: bool = False


class TextBody(BaseModel):
    text: str = ""


class BasicBody(BaseModel):
    external_controller: str = ""
    external_ui: str = ""
    secret: str = ""
    mixed_port: int = 7890


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时：检测 mihomo 并释放内置面板；释放 install-service.sh；剥离 base.yaml 基础设置键。"""
    d = mihomo.detect()
    if d["installed"]:
        logger.info("检测到 mihomo v%s（%s，数据目录 %s）",
                    d["version"], d["bin"], d["home"])
        r = mihomo.ensure_dashboard()
        if not r.get("ok"):
            logger.warning("内置面板释放失败: %s", r.get("error"))
        r = mihomo.ensure_geo_files()
        if not r.get("ok"):
            logger.warning("内置 geo 数据释放失败: %s", r.get("error"))
        elif r.get("released"):
            logger.info("已释放 geo 数据到 %s: %s", r["dir"], "、".join(r["released"]))
        # 生效配置缺失时自动生成一份最小配置（基础设置 + base.yaml）
        try:
            r = cfg.ensure_live_config()
            if r.get("created"):
                logger.info("未找到生效配置，已自动生成: %s", r["path"])
        except OSError as e:
            logger.warning("自动生成生效配置失败: %s", e)
    else:
        logger.warning("未检测到 mihomo，用户自行安装后面板功能才可用")
    r = mihomo.ensure_service_script()
    if not r.get("ok"):
        logger.warning("install-service.sh 释放失败: %s", r.get("error"))
    try:
        if basicm.strip_base_file(cfg.BASE_FILE):
            logger.info("base.yaml 中的基础设置键已迁移到 basic.json")
    except OSError as e:
        logger.warning("base.yaml 迁移失败: %s", e)
    yield
    logger.info("面板已停止")


app = FastAPI(title="clashaux", docs_url=None, redoc_url=None, lifespan=lifespan)


async def _check_auth(request: Request):
    """验证 token，静态文件、登录与 mihomo 检测/上传安装接口除外。"""
    if request.url.path in ("/", "/index.html", "/style.css",
                            "/favicon.svg", "/favicon.png",
                            "/api/login", "/api/mihomo/detect",
                            "/api/mihomo/upload-install"):
        return None
    # 备份下载链接通过短时效 ticket 鉴权（避免 token 留在浏览器历史）
    if request.url.path.startswith("/api/backups/") and request.url.path.endswith("/download"):
        if _check_ticket(request.query_params.get("ticket", "")):
            return None
        return JSONResponse({"error": "未授权或票据已过期"}, status_code=401)
    auth = request.headers.get("Authorization", "")
    if not secrets.compare_digest(auth.encode("utf-8"),
                                  f"Bearer {TOKEN}".encode("utf-8")):
        return JSONResponse({"error": "未授权"}, status_code=401)
    return None


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    result = await _check_auth(request)
    if result is not None:
        return result
    return await call_next(request)


@app.post("/api/login")
async def api_login(body: TextBody):
    """验证 token；未安装 mihomo 时禁止登录。"""
    d = await asyncio.to_thread(mihomo.detect)
    if not d["installed"]:
        return {"ok": False, "blocked": True,
                "error": "你未安装 mihomo，请先自行安装后再登录",
                "level": d["level"]}
    if secrets.compare_digest(body.text.encode("utf-8"), TOKEN.encode("utf-8")):
        return {"ok": True, "token": TOKEN}
    return {"ok": False, "error": "token 错误"}


@app.post("/api/ticket")
async def api_ticket():
    """签发短时效 ticket（备份下载链接使用，避免 token 出现在 URL 中）。"""
    return {"ok": True, "ticket": _issue_ticket()}


class PanelBody(BaseModel):
    port: int
    token: str


def _reexec() -> None:
    """原地重启进程（systemd Type=simple 下 PID 不变），用于端口变更立即生效。"""
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])


@app.post("/api/panel/config")
async def api_panel_config(body: PanelBody):
    """修改面板端口与 Token（写 clashaux.json）。

    Token 即时更新；端口变更先做可用性检查，保存后 0.8 秒原地重启进程绑定新端口。
    """
    global TOKEN
    port = body.port
    new_token = body.token.strip()
    if not (1 <= port <= 65535):
        return {"ok": False, "error": "端口需在 1-65535 之间"}
    if not new_token:
        return {"ok": False, "error": "Token 不能为空"}
    port_changed = port != PORT
    if port_changed:
        s = socket.socket()
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            return {"ok": False, "error": f"端口 {port} 已被占用"}
        finally:
            s.close()
    _app_config["port"] = port
    _app_config["token"] = new_token
    CONFIG_FILE.write_text(
        json.dumps(_app_config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    TOKEN = new_token
    if port_changed:
        threading.Timer(0.8, _reexec).start()
        return {"ok": True, "restart": True, "port": port,
                "message": "端口已修改，面板将在新端口重启"}
    return {"ok": True, "restart": False, "message": "面板设置已更新"}


@app.get("/api/mihomo/detect")
async def api_mihomo_detect():
    """mihomo 安装检测（登录前可用）。

    用户自行安装完成后点击「重新检测」：检测通过即释放内置 metacubexd 面板。
    """
    d = await asyncio.to_thread(mihomo.detect)
    if d["installed"]:
        d["dashboard"] = await asyncio.to_thread(mihomo.ensure_dashboard)
        d["geo"] = await asyncio.to_thread(mihomo.ensure_geo_files)
    return d


@app.post("/api/mihomo/upload-install")
async def api_mihomo_upload_install(file: UploadFile, token: str = Form("")):
    """上传 .deb / .gz 安装 mihomo（阻断弹窗内使用，表单 token 校验）。"""
    if not secrets.compare_digest(token.encode("utf-8"), TOKEN.encode("utf-8")):
        return {"ok": False, "error": "token 错误"}
    name = file.filename or ""
    if not name.lower().endswith((".deb", ".gz")):
        return {"ok": False, "error": "仅支持 .deb 或 .gz 安装包"}
    data = await file.read()
    if not data:
        return {"ok": False, "error": "上传文件为空"}
    with tempfile.NamedTemporaryFile(suffix=Path(name).suffix, delete=False) as tf:
        tf.write(data)
        tmp = Path(tf.name)
    try:
        r = await asyncio.to_thread(mihomo.install_from_upload, tmp, name)
    finally:
        tmp.unlink(missing_ok=True)
    if r.get("ok"):
        d = await asyncio.to_thread(mihomo.detect)
        r["installed"] = d["installed"]
        r["dashboard"] = await asyncio.to_thread(mihomo.ensure_dashboard)
        r["geo"] = await asyncio.to_thread(mihomo.ensure_geo_files)
    return r


@app.post("/api/mihomo/upgrade")
async def api_mihomo_upgrade(file: UploadFile, token: str = Form("")):
    """上传更高版本的 mihomo 安装包执行升级。

    安装前校验全部通过才执行：新版本必须高于当前版本（语义化比较）、
    GOAMD64 档位相同（v1→v1/v2→v2/v3→v3）、CPU 架构相同（amd64/arm64）。
    """
    if not secrets.compare_digest(token.encode("utf-8"), TOKEN.encode("utf-8")):
        return {"ok": False, "error": "token 错误"}
    name = file.filename or ""
    if not name.lower().endswith((".deb", ".gz")):
        return {"ok": False, "error": "仅支持 .deb 或 .gz 升级包"}
    info = mihomo.parse_asset_name(name)
    if not info["arch"]:
        return {"ok": False, "error":
                "无法从文件名识别 CPU 架构，文件名需形如 mihomo-linux-amd64-v3-v1.19.32.deb"}
    if info["arch"] == "amd64" and not info["level"]:
        return {"ok": False, "error":
                "无法从文件名识别 GOAMD64 档位，请下载带 v1/v2/v3（或 compatible）标识的安装包"}
    cur = await asyncio.to_thread(mihomo.detect)
    if not cur["installed"]:
        return {"ok": False, "error": "当前未安装 mihomo，无法升级"}
    data = await file.read()
    if not data:
        return {"ok": False, "error": "上传文件为空"}
    with tempfile.NamedTemporaryFile(suffix=Path(name).suffix, delete=False) as tf:
        tf.write(data)
        tmp = Path(tf.name)
    try:
        new_ver = await asyncio.to_thread(mihomo.probe_package_version, tmp, name)
        if not new_ver:
            return {"ok": False, "error": "无法确定新包版本号（.gz 包需为有效的 mihomo 二进制）"}
        if not mihomo.version_newer(new_ver, cur["version"]):
            return {"ok": False, "error":
                    f"新包 v{new_ver} 不高于当前版本 v{cur['version']}，无需升级"}
        meta = mihomo.install_meta()
        cur_arch = meta.get("arch") or mihomo.host_arch()
        cur_level = meta.get("goamd64") or cur["level"]
        if info["arch"] != cur_arch:
            return {"ok": False, "error":
                    f"架构不符：新包 {info['arch']}，当前安装 {cur_arch}（不支持跨架构升级）"}
        if info["arch"] == "amd64" and info["level"] != cur_level:
            return {"ok": False, "error":
                    f"GOAMD64 档位不符：新包 {info['level']}，当前 {cur_level}（必须同档位升级）"}
        r = await asyncio.to_thread(mihomo.install_from_upload, tmp, name)
    finally:
        tmp.unlink(missing_ok=True)
    if r.get("ok"):
        d = await asyncio.to_thread(mihomo.detect)
        r["installed"] = d["installed"]
        r["version"] = d["version"] or new_ver
        r["dashboard"] = await asyncio.to_thread(mihomo.ensure_dashboard)
        r["geo"] = await asyncio.to_thread(mihomo.ensure_geo_files)
    return r


async def _preview_text(text: str) -> dict:
    try:
        candidate = cfg.build_candidate(text)
    except ConfigError as e:
        return {"ok": False, "error": str(e)}
    ok, out = await asyncio.to_thread(cfg.validate, candidate)
    result = {"ok": ok, "stats": cfg.stats(candidate)}
    if not ok:
        result["error"] = "mihomo -t 校验未通过"
        result["output"] = out
    return result


async def _apply_text(text: str) -> dict:
    try:
        result = await asyncio.to_thread(cfg.apply, text)
    except ConfigError as e:
        return {"ok": False, "stage": "apply", "error": str(e)}
    if not result["ok"]:
        return result
    ok, msg = await asyncio.to_thread(svc.service_action, "reload")
    if not ok:
        result.update(ok=False, stage="reload", error=f"配置已写入但重载失败: {msg}")
        return result
    result["message"] = "配置已生效（此前无生效配置，已自动生成）" if result.get("created") else "配置已生效"
    return result


async def _read_upload(file: UploadFile) -> str:
    raw = await file.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "文件不是 UTF-8 编码")


@app.get("/api/status")
async def api_status():
    d = await asyncio.to_thread(mihomo.detect)
    return {
        "service": await asyncio.to_thread(svc.get_status),
        "config": await asyncio.to_thread(cfg.live_info, auto_create=d["installed"]),
        "mihomo_version": d["version"],
        "mihomo_installed": d["installed"],
        "mihomo_home": d["home"],
        "mihomo_bin": d["bin"],
    }


@app.post("/api/dashboard/install")
async def api_dashboard_install():
    """重新释放内置 metacubexd 面板到 mihomo 数据目录（覆盖现有目录）。"""
    return await asyncio.to_thread(mihomo.redeploy_dashboard)


@app.get("/api/basic")
async def api_basic_get():
    """基础连接设置（external-controller/external-ui/secret/mixed-port）。"""
    return await asyncio.to_thread(basicm.load)


@app.post("/api/basic")
async def api_basic_save(body: BasicBody):
    """保存基础设置：写入配置文件最头部并重载 mihomo。"""
    basics = {
        "external-controller": body.external_controller.strip(),
        "external-ui": body.external_ui.strip(),
        "secret": body.secret.strip(),
        "mixed-port": body.mixed_port,
    }
    ok, err = basicm.validate(basics)
    if not ok:
        return {"ok": False, "error": err}
    try:
        result = await asyncio.to_thread(cfg.apply_basic_settings, basics)
    except ConfigError as e:
        return {"ok": False, "error": str(e)}
    if not result["ok"]:
        return result
    basicm.save(basics)
    basicm.strip_base_file(cfg.BASE_FILE)
    rok, rmsg = await asyncio.to_thread(svc.service_action, "reload")
    if not rok:
        result["ok"] = False
        result["error"] = f"配置已写入但重载失败: {rmsg}"
        return result
    result["message"] = ("生效配置不存在，已按基础设置自动生成并写入，mihomo 已重载"
                         if result.get("created")
                         else "基础设置已保存并写入配置最头部，mihomo 已重载")
    return result


@app.post("/api/service/{action}")
async def api_service_action(action: str):
    ok, msg = await asyncio.to_thread(svc.service_action, action)
    return {"ok": ok, "message": msg}


@app.get("/api/config/current")
async def api_config_current():
    """读取 mihomo 当前运行的完整配置（缺失时自动生成一份最小配置）。"""
    if not cfg.live_link().exists():
        await asyncio.to_thread(cfg.ensure_live_config)
    try:
        path = cfg.live_file()
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        return {"ok": True, "text": text, "path": str(path)}
    except cfg.ConfigError as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": f"读取失败: {e}"}


@app.post("/api/config/preview")
async def api_config_preview(file: UploadFile):
    return await _preview_text(await _read_upload(file))


@app.post("/api/config/apply")
async def api_config_apply(file: UploadFile):
    return await _apply_text(await _read_upload(file))


@app.post("/api/config/preview-text")
async def api_config_preview_text(body: TextBody):
    if not body.text.strip():
        return {"ok": False, "error": "配置内容为空"}
    return await _preview_text(body.text)


@app.post("/api/config/apply-text")
async def api_config_apply_text(body: TextBody):
    if not body.text.strip():
        return {"ok": False, "error": "配置内容为空"}
    return await _apply_text(body.text)


@app.get("/api/backups")
async def api_backups():
    return {"items": await asyncio.to_thread(cfg.list_backups)}


@app.get("/api/backups/{name}/download")
async def api_backup_download(name: str):
    path = await asyncio.to_thread(cfg.backup_file, name)
    if path is None:
        return {"ok": False, "error": "备份不存在"}
    return FileResponse(path, filename=path.name)


@app.post("/api/backups/upload")
async def api_backup_upload(file: UploadFile):
    text = await _read_upload(file)
    ok, msg = await asyncio.to_thread(cfg.save_uploaded_backup, file.filename or "", text)
    if not ok:
        return {"ok": False, "error": msg}
    return {"ok": True, "name": msg, "message": f"已保存为 {msg}"}


@app.post("/api/backups/{name}/rollback")
async def api_rollback(name: str):
    ok, msg = await asyncio.to_thread(cfg.rollback, name)
    if not ok:
        return {"ok": False, "error": msg}
    rok, rmsg = await asyncio.to_thread(svc.service_action, "reload")
    if not rok:
        return {"ok": False, "error": f"备份已写入但重载失败: {rmsg}"}
    return {"ok": True, "message": f"已回滚到 {name}"}


@app.get("/api/base")
async def api_base_get():
    text = await asyncio.to_thread(cfg.BASE_FILE.read_text, encoding="utf-8")
    return {"path": str(cfg.BASE_FILE), "text": text}


@app.post("/api/base")
async def api_base_save(body: TextBody):
    if not body.text.strip():
        return {"ok": False, "error": "base.yaml 内容不能为空"}
    # 基础连接设置由「基础设置」卡片管理，粘贴整份配置时自动剥离，避免写回模板造成重复
    text = basicm.strip_keys(body.text).strip("\n")
    if not text:
        return {"ok": False, "error": "base.yaml 内容不能只有基础连接设置"}
    await asyncio.to_thread(atomic_write, cfg.BASE_FILE, text + "\n")
    return {"ok": True, "message": "base.yaml 已保存，下次换头时生效"}


@app.post("/api/base/apply")
async def api_base_apply():
    """把当前 base.yaml 模板套用到生效配置（保留正文，只换头部）并重载 mihomo。"""
    try:
        result = await asyncio.to_thread(cfg.apply_base_template)
    except ConfigError as e:
        return {"ok": False, "stage": "apply", "error": str(e)}
    if not result["ok"]:
        return result
    rok, rmsg = await asyncio.to_thread(svc.service_action, "reload")
    if not rok:
        result.update(ok=False, stage="reload", error=f"配置已写入但重载失败: {rmsg}")
        return result
    result["message"] = ("生效配置不存在，已按 base.yaml 生成并写入，mihomo 已重载"
                         if result.get("created")
                         else "base.yaml 模板已套用到生效配置（正文保留），mihomo 已重载")
    return result


@app.get("/api/subs")
async def api_subs():
    return {"items": await asyncio.to_thread(subm.list_subs)}


@app.post("/api/subs/save")
async def api_subs_save(body: SubBody):
    ok, msg = await asyncio.to_thread(subm.save_sub, body.name, body.url, body.proxy)
    return {"ok": ok, "message": msg}


@app.post("/api/subs/rename")
async def api_subs_rename(body: SubBody):
    ok, msg = await asyncio.to_thread(subm.rename_sub, body.name, body.new_name)
    return {"ok": ok, "message": msg}


@app.post("/api/subs/delete")
async def api_subs_delete(body: SubBody):
    ok, msg = await asyncio.to_thread(subm.remove_sub, body.name)
    return {"ok": ok, "message": msg}


@app.post("/api/sub/preview")
async def api_sub_preview(body: SubBody):
    try:
        text = await asyncio.to_thread(subm.fetch, body.url, body.proxy)
    except subm.SubscriptionError as e:
        return {"ok": False, "error": str(e)}
    result = await _preview_text(text)
    if result["ok"]:
        result["fetched_chars"] = len(text)
    return result


@app.post("/api/sub/apply")
async def api_sub_apply(body: SubBody):
    try:
        text = await asyncio.to_thread(subm.fetch, body.url, body.proxy)
    except subm.SubscriptionError as e:
        return {"ok": False, "stage": "fetch", "error": str(e)}
    return await _apply_text(text)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def _lan_ip() -> str:
    """取本机局域网 IP（用于展示局域网访问地址，失败返回空串）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("223.5.5.5", 53))
            return s.getsockname()[0]
    except OSError:
        return ""


def _startup_banner() -> None:
    print("=" * 56)
    print("  clashaux · mihomo 辅助面板")
    print(f"  本机访问:   http://127.0.0.1:{PORT}")
    lan = _lan_ip()
    if lan:
        print(f"  局域网访问: http://{lan}:{PORT}")
    print(f"  登录 Token: {TOKEN}")
    print(f"  （Token 也保存在 {CONFIG_FILE}）")
    install_sh = DATA_DIR / "install-service.sh"
    if install_sh.exists():
        print(f"  安装系统服务: {install_sh} install")
    print("=" * 56, flush=True)


if __name__ == "__main__":
    import uvicorn

    if os.geteuid() != 0:
        print("请以 root 或使用 sudo 运行 clashaux", file=sys.stderr)
        install_sh = DATA_DIR / "install-service.sh"
        if install_sh.exists():
            print(f"安装系统服务: sudo {install_sh} install", file=sys.stderr)
        else:
            print("安装系统服务: sudo install-service.sh install", file=sys.stderr)
        sys.exit(1)

    _startup_banner()
    try:
        uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Ctrl+C 时 uvicorn 取消 lifespan 冒出的信号异常，属正常退出，不打堆栈
        print("\n面板已停止")
