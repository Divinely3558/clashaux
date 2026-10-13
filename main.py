"""clashaux — mihomo 辅助管理面板。"""
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import config as cfg
from core import service as svc
from core import subscription as subm
from core.config import ConfigError
from core.paths import BUNDLE_DIR, DATA_DIR

logger = logging.getLogger("clashaux")

CONFIG_FILE = DATA_DIR / "clashaux.json"
DEFAULT_CONFIG = {"port": 8686}


def _load_config() -> dict:
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(
            json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("配置文件损坏，使用默认配置: %s", e)
    return dict(DEFAULT_CONFIG)


_app_config = _load_config()
HOST = "0.0.0.0"
PORT = _app_config.get("port", DEFAULT_CONFIG["port"])
STATIC_DIR = BUNDLE_DIR / "static"

app = FastAPI(title="clashaux", docs_url=None, redoc_url=None)


class SubBody(BaseModel):
    url: str = ""
    name: str = ""
    new_name: str = ""
    proxy: bool = False


class TextBody(BaseModel):
    text: str = ""


def _preview_text(text: str) -> dict:
    try:
        candidate = cfg.build_candidate(text)
    except ConfigError as e:
        return {"ok": False, "error": str(e)}
    ok, out = cfg.validate(candidate)
    result = {"ok": ok, "stats": cfg.stats(candidate), "output": out if not ok else ""}
    if not ok:
        result["error"] = "mihomo -t 校验未通过"
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
    return {"service": svc.get_status(), "config": cfg.live_info(), "mihomo_version": upg.current_version()}


@app.post("/api/service/{action}")
def api_service_action(action: str):
    ok, msg = svc.service_action(action)
    return {"ok": ok, "message": msg}


@app.get("/api/logs")
def api_logs(n: int = 200):
    return {"lines": svc.journal_tail(n)}


@app.get("/api/logs/stream")
def api_logs_stream():
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
def api_upgrade_apply(body: SubBody):
    from core import upgrade as up
    try:
        return up.apply(body.proxy)
    except up.UpgradeError as e:
        return {"ok": False, "error": str(e)}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
