from pathlib import Path

from itsdangerous import BadSignature, URLSafeSerializer
from litestar import Litestar, Request, Response, get, post
from litestar.exceptions import HTTPException
from litestar.response import Redirect
from litestar.static_files import create_static_files_router
from litestar.status_codes import HTTP_302_FOUND

from app.config import Config
from app.db import init_db
from app.services import (
    close_batch,
    get_batch,
    get_current_rule,
    list_batches,
    list_objects,
    list_reports,
    list_rules,
    register_object,
    run_visibility,
    switch_rule,
)
from app.services.objects import ServiceError

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
serializer = URLSafeSerializer(Config.SECRET, salt="cc-manifest-session")


def _read_session(request: Request) -> dict:
    raw = request.cookies.get("manifest_session")
    if not raw:
        return {}
    try:
        data = serializer.loads(raw)
        return data if isinstance(data, dict) else {}
    except BadSignature:
        return {}


def require_user(request: Request) -> str:
    user = _read_session(request).get("user")
    if user != Config.ADMIN_USER:
        raise HTTPException(status_code=401, detail="未登录")
    return user


def _on_startup() -> None:
    init_db()


@get("/")
async def root(request: Request) -> Redirect:
    if _read_session(request).get("user") == Config.ADMIN_USER:
        return Redirect("/app/", status_code=HTTP_302_FOUND)
    return Redirect("/login", status_code=HTTP_302_FOUND)


@get("/login")
async def login_page() -> Response:
    return Response(
        content=(STATIC / "login.html").read_bytes(),
        media_type="text/html; charset=utf-8",
    )


@get("/app/")
async def spa_page(request: Request) -> Response | Redirect:
    if _read_session(request).get("user") != Config.ADMIN_USER:
        return Redirect("/login", status_code=HTTP_302_FOUND)
    return Response(
        content=(STATIC / "index.html").read_bytes(),
        media_type="text/html; charset=utf-8",
    )


@post("/api/login")
async def api_login(request: Request) -> Response:
    body = await request.json()
    user = str(body.get("username", "")).strip()
    pwd = str(body.get("password", ""))
    if user != Config.ADMIN_USER or pwd != Config.ADMIN_PASSWORD:
        return Response(
            content={"ok": False, "error": "账号或密码错误"},
            status_code=400,
            media_type="application/json",
        )
    resp = Response(
        content={"ok": True, "user": user},
        status_code=200,
        media_type="application/json",
    )
    resp.set_cookie(
        "manifest_session",
        serializer.dumps({"user": user}),
        httponly=True,
        samesite="lax",
    )
    return resp


@post("/api/logout")
async def api_logout() -> Response:
    resp = Response(content={"ok": True}, status_code=200, media_type="application/json")
    resp.delete_cookie("manifest_session")
    return resp


@get("/api/meta")
async def api_meta(request: Request) -> dict:
    require_user(request)
    rule = get_current_rule()
    return {
        "lab_name": Config.LAB_NAME,
        "product_line": "cc-manifest-lab",
        "chain": ["落地批次", "对象登记", "清单闭合", "可见性对账"],
        "current_rule": rule,
    }


@get("/api/rule-tracks")
async def api_rule_tracks(request: Request) -> dict:
    require_user(request)
    return {"current": get_current_rule(), "items": list_rules()}


@post("/api/rule-tracks/switch")
async def api_switch_rule(request: Request) -> Response:
    require_user(request)
    body = await request.json()
    expected = body.get("expected_track_no")
    try:
        expected_no = int(expected) if expected is not None else None
    except (TypeError, ValueError):
        return Response(
            content={"ok": False, "error": "期望轨号必须是整数"},
            status_code=400,
            media_type="application/json",
        )
    try:
        rule = switch_rule(body, expected_track_no=expected_no)
    except ServiceError as exc:
        return Response(
            content={"ok": False, "error": exc.message},
            status_code=exc.status_code,
            media_type="application/json",
        )
    return Response(
        content={"ok": True, "rule": rule},
        status_code=200,
        media_type="application/json",
    )


@get("/api/batches")
async def api_batches(request: Request, status: str | None = None) -> dict:
    require_user(request)
    return {"items": list_batches(status=status), "current_rule": get_current_rule()}


@get("/api/batches/{batch_id:int}")
async def api_batch_detail(request: Request, batch_id: int) -> dict:
    require_user(request)
    batch = get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    return {
        "batch": batch,
        "objects": list_objects(batch_id),
        "current_rule": get_current_rule(),
    }


@post("/api/batches/{batch_id:int}/objects")
async def api_register(request: Request, batch_id: int) -> Response:
    require_user(request)
    body = await request.json()
    raw_track = body.get("rule_track_no")
    try:
        rule_track_no = int(raw_track) if raw_track is not None else None
    except (TypeError, ValueError):
        return Response(
            content={"ok": False, "error": "规则轨号必须是整数"},
            status_code=400,
            media_type="application/json",
        )
    try:
        obj = register_object(
            batch_id,
            str(body.get("object_key", "")),
            str(body.get("content_hash", "")),
            int(body.get("byte_size", 0)),
            rule_track_no=rule_track_no,
        )
    except ServiceError as exc:
        return Response(
            content={"ok": False, "error": exc.message},
            status_code=exc.status_code,
            media_type="application/json",
        )
    return Response(
        content={"ok": True, "object": obj},
        status_code=200,
        media_type="application/json",
    )


@post("/api/batches/{batch_id:int}/close")
async def api_close(request: Request, batch_id: int) -> Response:
    require_user(request)
    try:
        run = close_batch(batch_id)
    except ServiceError as exc:
        return Response(
            content={"ok": False, "error": exc.message},
            status_code=exc.status_code,
            media_type="application/json",
        )
    return Response(
        content={"ok": True, "run": run},
        status_code=200,
        media_type="application/json",
    )


@get("/api/visibility-reports")
async def api_reports(request: Request) -> dict:
    require_user(request)
    return {"items": list_reports()}


@post("/api/visibility-reports")
async def api_run_report(request: Request) -> dict:
    require_user(request)
    body = await request.json()
    report = run_visibility(str(body.get("as_of_date", "2026-11-16")))
    return Response(
        content={"ok": True, "report": report},
        status_code=200,
        media_type="application/json",
    )


app = Litestar(
    route_handlers=[
        root,
        login_page,
        spa_page,
        api_login,
        api_logout,
        api_meta,
        api_rule_tracks,
        api_switch_rule,
        api_batches,
        api_batch_detail,
        api_register,
        api_close,
        api_reports,
        api_run_report,
        create_static_files_router(path="/static", directories=[str(STATIC)]),
    ],
    on_startup=[_on_startup],
)
