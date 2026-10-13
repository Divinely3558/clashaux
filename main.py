"""clashaux — mihomo 辅助管理面板。"""
import json
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import basic as basicm
from core import config as cfg
from core import service as svc
from core import subscription as subm
from core.config import ConfigError
from core.paths import BUNDLE_DIR, DATA_DIR

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


class SubBody(BaseModel):
    url: str = ""
    name: str = ""
    new_name: str = ""
    proxy: bool = False


class ProxyBody(BaseModel):
    proxy: bool = False


class TextBody(BaseModel):
    text: str = ""


class TokenBody(BaseModel):
    token: str = ""


class UpgradeBody(BaseModel):
    proxy: bool = False
    level: str = ""  # v1/v2/v3，空 = 按系统推荐档位


class BasicBody(BaseModel):
    external_controller: str = ""
    external_ui: str = ""
    secret: str = ""
    mixed_port: int = 7890


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时：释放内置 metacubexd 面板与 install-service.sh；剥离 base.yaml 基础设置键。"""
    from core import upgrade as up
    r = up.ensure_dashboard()
    if not r.get("ok"):
        logger.warning("内置面板释放失败: %s", r.get("error"))
    r = up.ensure_service_script()
    if not r.get("ok"):
        logger.warning("install-service.sh 释放失败: %s", r.get("error"))
    try:
        if basicm.strip_base_file(cfg.BASE_FILE):
            logger.info("base.yaml 中的基础设置键已迁移到 basic.json")
    except OSError as e:
        logger.warning("base.yaml 迁移失败: %s", e)
    yield


app = FastAPI(title="clashaux", docs_url=None, redoc_url=None, lifespan=lifespan)


async def _check_auth(request: Request):
    """验证 token，静态文件和登录接口除外。"""
    if request.url.path in ("/", "/login", "/api/login", "/style.css") or request.url.path.startswith("/static"):
        return None
    # SSE 端点通过 query param 传 token（EventSource 不支持自定义 Header）
    if request.url.path in ("/api/install/stream", "/api/logs/stream"):
        token = request.query_params.get("token", "")
        if token != TOKEN:
            return JSONResponse({"error": "未授权"}, status_code=401)
        return None
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {TOKEN}":
        return JSONResponse({"error": "未授权"}, status_code=401)
    return None


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    result = await _check_auth(request)
    if result is not None:
        return result
    return await call_next(request)


@app.post("/api/login")
def api_login(body: TextBody):
    """验证 token。"""
    if body.text == TOKEN:
        return {"ok": True, "token": TOKEN}
    return {"ok": False, "error": "token 错误"}


@app.get("/api/token")
def api_get_token():
    """获取当前 token（用于首次登录）。"""
    return {"token": TOKEN}


def _preview_text(text: str) -> dict:
    try:
        candidate = cfg.build_candidate(text)
    except ConfigError as e:
        return {"ok": False, "error": str(e)}
    ok, out = cfg.validate(candidate)
    result = {"ok": ok, "stats": cfg.stats(candidate)}
    if not ok:
        result["error"] = "mihomo -t 校验未通过"
        result["output"] = out
    return result


def _apply_text(text: str) -> dict:
    try:
        result = cfg.apply(text)
    except ConfigError as e:
        return {"ok": False, "stage": "apply", "error": str(e)}
    if not result["ok"]:
        return result
    ok, msg = svc.service_action("reload")
    if not ok:
        result.update(ok=False, stage="reload", error=f"配置已写入但重载失败: {msg}")
        return result
    result["message"] = "配置已生效"
    return result


async def _read_upload(file: UploadFile) -> str:
    raw = await file.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "文件不是 UTF-8 编码")


@app.get("/api/status")
def api_status():
    from core import upgrade as upg
    return {
        "service": svc.get_status(),
        "config": cfg.live_info(),
        "mihomo_version": upg.current_version(),
        "mihomo_installed": upg.is_installed(),
    }


@app.post("/api/mihomo/install")
def api_mihomo_install(body: ProxyBody):
    from core import upgrade as up
    try:
        return up.install(body.proxy)
    except up.UpgradeError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/mihomo/uninstall")
def api_mihomo_uninstall(body: TokenBody):
    """卸载 mihomo（需输入面板 token 二次确认）。"""
    if body.token != TOKEN:
        return {"ok": False, "error": "token 错误"}
    from core import upgrade as up
    return up.uninstall()


@app.post("/api/dashboard/install")
def api_dashboard_install():
    """重新释放内置 metacubexd 面板（覆盖现有目录）。"""
    from core import upgrade as up
    try:
        return up.redeploy_dashboard()
    except up.UpgradeError as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/basic")
def api_basic_get():
    """基础连接设置（external-controller/external-ui/secret/mixed-port）。"""
    return basicm.load()


@app.post("/api/basic")
def api_basic_save(body: BasicBody):
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
        result = cfg.apply_basic_settings(basics)
    except ConfigError as e:
        return {"ok": False, "error": str(e)}
    if not result["ok"]:
        return result
    basicm.save(basics)
    basicm.strip_base_file(cfg.BASE_FILE)
    if result.get("written"):
        rok, rmsg = svc.service_action("reload")
        if not rok:
            result["ok"] = False
            result["error"] = f"配置已写入但重载失败: {rmsg}"
            return result
        result["message"] = "基础设置已保存并写入配置最头部，mihomo 已重载"
    else:
        result["message"] = "基础设置已保存，下次换头/安装时生效"
    return result


@app.get("/api/install/stream")
def api_install_stream(proxy: bool = False, token: str = ""):
    """SSE 流式返回安装进度（token 通过 query 传递，EventSource 不支持自定义 Header）。"""
    if token != TOKEN:
        return JSONResponse({"error": "未授权"}, status_code=401)
    from core import upgrade as up

    def gen():
        for event in up.install_stream(use_proxy=proxy):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/service/{action}")
def api_service_action(action: str):
    ok, msg = svc.service_action(action)
    return {"ok": ok, "message": msg}


@app.get("/api/logs")
def api_logs(n: int = 200):
    return {"lines": svc.journal_tail(n)}


@app.get("/api/config/current")
def api_config_current():
    """读取 mihomo 当前运行的完整配置。"""
    try:
        path = cfg.live_file()
        text = path.read_text(encoding="utf-8")
        return {"ok": True, "text": text, "path": str(path)}
    except cfg.ConfigError as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": f"读取失败: {e}"}


@app.get("/api/logs/stream")
def api_logs_stream(token: str = ""):
    if token != TOKEN:
        return JSONResponse({"error": "未授权"}, status_code=401)

    def gen():
        yield ": connected\n\n"
        for line in svc.journal_follow():
            yield f"data: {line}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/config/preview")
async def api_config_preview(file: UploadFile):
    return _preview_text(await _read_upload(file))


@app.post("/api/config/apply")
async def api_config_apply(file: UploadFile):
    return _apply_text(await _read_upload(file))


@app.post("/api/config/preview-text")
def api_config_preview_text(body: TextBody):
    if not body.text.strip():
        return {"ok": False, "error": "配置内容为空"}
    return _preview_text(body.text)


@app.post("/api/config/apply-text")
def api_config_apply_text(body: TextBody):
    if not body.text.strip():
        return {"ok": False, "error": "配置内容为空"}
    return _apply_text(body.text)


@app.get("/api/backups")
def api_backups():
    return {"items": cfg.list_backups()}


@app.post("/api/backups/{name}/rollback")
def api_rollback(name: str):
    ok, msg = cfg.rollback(name)
    if not ok:
        return {"ok": False, "error": msg}
    rok, rmsg = svc.service_action("reload")
    if not rok:
        return {"ok": False, "error": f"备份已写入但重载失败: {rmsg}"}
    return {"ok": True, "message": f"已回滚到 {name}"}


@app.get("/api/base")
def api_base_get():
    text = cfg.BASE_FILE.read_text(encoding="utf-8")
    return {"path": str(cfg.BASE_FILE), "text": text}


@app.post("/api/base")
def api_base_save(body: TextBody):
    if not body.text.strip():
        return {"ok": False, "error": "base.yaml 内容不能为空"}
    cfg.BASE_FILE.write_text(body.text.rstrip("\n") + "\n", encoding="utf-8")
    return {"ok": True, "message": "base.yaml 已保存，下次换头时生效"}


@app.get("/api/subs")
def api_subs():
    return {"items": subm.list_subs()}


@app.post("/api/subs/save")
def api_subs_save(body: SubBody):
    ok, msg = subm.save_sub(body.name, body.url, body.proxy)
    return {"ok": ok, "message": msg}


@app.post("/api/subs/rename")
def api_subs_rename(body: SubBody):
    ok, msg = subm.rename_sub(body.name, body.new_name)
    return {"ok": ok, "message": msg}


@app.post("/api/subs/delete")
def api_subs_delete(body: SubBody):
    ok, msg = subm.remove_sub(body.name)
    return {"ok": ok, "message": msg}


@app.post("/api/sub/preview")
def api_sub_preview(body: SubBody):
    try:
        text = subm.fetch(body.url, body.proxy)
    except subm.SubscriptionError as e:
        return {"ok": False, "error": str(e)}
    result = _preview_text(text)
    if result["ok"]:
        result["fetched_chars"] = len(text)
    return result


@app.post("/api/sub/apply")
def api_sub_apply(body: SubBody):
    try:
        text = subm.fetch(body.url, body.proxy)
    except subm.SubscriptionError as e:
        return {"ok": False, "stage": "fetch", "error": str(e)}
    return _apply_text(text)


@app.get("/api/upgrade/check")
def api_upgrade_check(proxy: bool = False):
    from core import upgrade as up
    try:
        return up.check(proxy)
    except up.UpgradeError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/upgrade/apply")
def api_upgrade_apply(body: UpgradeBody):
    from core import upgrade as up
    try:
        return up.apply(body.proxy, body.level.strip())
    except up.UpgradeError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/upgrade/upload")
def api_upgrade_upload(file: UploadFile):
    """上传 .gz / .deb 安装包安装 mihomo（GitHub 不可用时的手动通道）。"""
    from core import upgrade as up
    try:
        return up.install_from_upload(file)
    except up.UpgradeError as e:
        return {"ok": False, "error": str(e)}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
