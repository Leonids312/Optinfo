"""Authenticated private API; run with one Uvicorn worker behind Tailscale Serve."""
import asyncio
from contextlib import asynccontextmanager
from datetime import date as Date
import hmac
import importlib
import inspect
from pathlib import Path
import shutil
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .config import SHANGHAI, Settings, now_shanghai
from .db import Database
from .models import BookmarkRequest, PreferencesRequest, QuestionRequest


def _catalog():
    try:
        return importlib.import_module("optinfo.catalog")
    except ImportError:
        return None


def create_app(settings: Settings | None = None, *, clock=now_shanghai, harness=None, catalog=None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = Database(settings.db_path).init()
    catalog = catalog or _catalog()
    default_ids = list(getattr(catalog, "DEFAULT_TOPIC_IDS", []))
    sections = list(getattr(catalog, "SECTIONS", [
        {"id": "research", "label": "研究进展", "description": "光学与半导体研究"},
        {"id": "industry", "label": "产业动态", "description": "企业、工艺与工业应用"},
        {"id": "world", "label": "重大事件", "description": "政策、市场与公共事件"},
    ]))
    topics = list(getattr(catalog, "TOPICS", []))
    database.upsert_companies(list(getattr(catalog, "COMPANIES", [])), overwrite=False)
    locks: dict[str, asyncio.Lock] = {}
    active_sessions: set[str] = set()
    cleanup_lock = asyncio.Lock()

    def current_time():
        return clock().astimezone(SHANGHAI)

    def get_harness():
        if harness is not None:
            return harness
        try:
            return importlib.import_module("optinfo.harness")
        except ImportError:
            return None

    def configured() -> bool:
        adapter = get_harness()
        try:
            return bool(adapter and adapter.is_configured())
        except Exception:
            return False

    def session_path(session_id: str) -> Path:
        # IDs originate locally; validate again before any filesystem cleanup.
        if str(UUID(session_id)) != session_id:
            raise ValueError("Invalid session id")
        root = settings.chat_dir.resolve()
        path = (root / session_id).resolve()
        if path.parent != root:
            raise ValueError("Invalid session directory")
        return path

    async def cleanup():
        async with cleanup_lock:
            database.expire_chats(current_time(), exclude_ids=active_sessions)
            for session_id in database.pending_chat_cleanup():
                if session_id in active_sessions:
                    continue
                try:
                    directory = session_path(session_id)
                    adapter = get_harness()
                    if adapter is not None and hasattr(adapter, "cleanup_session"):
                        if inspect.iscoroutinefunction(adapter.cleanup_session):
                            await adapter.cleanup_session(directory)
                        else:
                            await asyncio.to_thread(adapter.cleanup_session, directory)
                    # Remove the app-owned session directory even if a hook leaves files behind.
                    if directory.exists():
                        await asyncio.to_thread(shutil.rmtree, directory)
                    database.complete_chat_cleanup(session_id)
                except Exception:
                    # Durable queue retries failed external cleanup on the next maintenance pass.
                    continue

    async def maintenance():
        while True:
            await asyncio.sleep(3600)
            await cleanup()

    @asynccontextmanager
    async def lifespan(app):
        settings.chat_dir.mkdir(parents=True, exist_ok=True)
        await cleanup()
        task = asyncio.create_task(maintenance())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="Optinfo", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.database = database
    app.state.cleanup_chats = cleanup
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["GET", "POST", "PUT"], allow_headers=["Authorization", "Content-Type"])

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(status_code=422, content={"detail": "请求参数格式不正确"})

    async def authenticate(authorization: str | None = Header(default=None)):
        if not settings.token:
            raise HTTPException(503, "服务尚未配置设备访问令牌")
        supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        if not hmac.compare_digest(supplied.encode("utf-8"), settings.token.encode("utf-8")):
            raise HTTPException(401, "设备访问令牌无效", headers={"WWW-Authenticate": "Bearer"})

    auth = [Depends(authenticate)]

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": __version__, "generator_ready": configured()}

    def bootstrap():
        return {"timezone": "Asia/Shanghai", "sections": sections, "topics": topics,
                **database.preference_state(current_time().date().isoformat(), default_ids),
                "latest_date": database.latest_date(), "journals": list(getattr(catalog, "JOURNALS", []))}

    @app.get("/api/bootstrap", dependencies=auth)
    async def get_bootstrap():
        return bootstrap()

    @app.put("/api/preferences", dependencies=auth)
    async def preferences(body: PreferencesRequest):
        valid = {topic["id"] for topic in topics}
        if any(topic_id not in valid for topic_id in body.topic_ids):
            raise HTTPException(422, "包含未知的兴趣领域")
        return database.save_preferences(body.topic_ids, current_time(), default_ids)

    @app.get("/api/briefing", dependencies=auth)
    async def briefing(date: str = "", q: str = Query(default="", max_length=300), section: str = "", journal: str = "", company: str = "", bookmarked: bool = False):
        try:
            day = Date.fromisoformat(date).isoformat() if date else current_time().date().isoformat()
        except ValueError:
            raise HTTPException(422, "日期格式应为 YYYY-MM-DD")
        if section and section not in {value["id"] for value in sections}:
            raise HTTPException(422, "未知资讯分区")
        # Accept catalog journal IDs and display names; stored items use the display name.
        journal_name = next((j["name"] for j in getattr(catalog, "JOURNALS", []) if j["id"] == journal), journal)
        historical = not date and bool(q.strip() or journal or company or bookmarked)
        return database.briefing(day, historical, q=q.strip(), section=section, journal=journal_name, company=company, bookmarked=bookmarked)

    def require_item(item_id: str):
        item = database.get_item(item_id)
        if item is None:
            raise HTTPException(404, "资讯不存在")
        return item

    @app.get("/api/items/{item_id}", dependencies=auth)
    async def get_item(item_id: str):
        return require_item(item_id)

    @app.put("/api/items/{item_id}/bookmark", dependencies=auth)
    async def bookmark_item(item_id: str, body: BookmarkRequest):
        if not database.set_item_bookmark(item_id, body.bookmarked):
            raise HTTPException(404, "资讯不存在")
        return {"bookmarked": body.bookmarked}

    @app.get("/api/companies", dependencies=auth)
    async def companies(q: str = Query(default="", max_length=300), bookmarked: bool = False):
        return {"items": database.list_companies(q.strip(), bookmarked)}

    @app.get("/api/companies/{company_id}", dependencies=auth)
    async def company(company_id: str):
        value = database.get_company(company_id)
        if value is None:
            raise HTTPException(404, "企业不存在")
        return {"company": value, "items": database.query_items(company=company_id)}

    @app.put("/api/companies/{company_id}/bookmark", dependencies=auth)
    async def bookmark_company(company_id: str, body: BookmarkRequest):
        if not database.set_company_bookmark(company_id, body.bookmarked):
            raise HTTPException(404, "企业不存在")
        return {"bookmarked": body.bookmarked}

    @app.get("/api/items/{item_id}/chat", dependencies=auth)
    async def get_chat(item_id: str):
        require_item(item_id)
        async with locks.setdefault(item_id, asyncio.Lock()):
            await cleanup()
            return database.get_chat(item_id)

    @app.post("/api/items/{item_id}/chat", dependencies=auth)
    async def ask(item_id: str, body: QuestionRequest):
        item = require_item(item_id)
        message = body.message.strip()
        if not message:
            raise HTTPException(422, "问题不能为空")
        if not configured():
            raise HTTPException(503, "DeepSeek Harness 尚未配置，请稍后重试；问题尚未提交")
        lock = locks.setdefault(item_id, asyncio.Lock())
        async with lock:
            await cleanup()
            chat = database.get_chat(item_id)
            session_id = chat["session_id"] or str(uuid4())
            database.ensure_chat_session(item_id, session_id, current_time())
            directory = session_path(session_id)
            directory.mkdir(parents=True, exist_ok=True)
            messages = [{"role": m["role"], "content": m["content"]} for m in chat["messages"]]
            messages.append({"role": "user", "content": message})
            active_sessions.add(session_id)
            database.acquire_chat_lease(session_id, current_time())
            try:
                adapter = get_harness()
                # The Harness SDK bounds initialization/request/shutdown itself.
                # Cancelling a to_thread wrapper would leave a live SDK process
                # writing into a session directory being deleted by cleanup.
                if inspect.iscoroutinefunction(adapter.answer_question):
                    answer = await adapter.answer_question(item, messages, directory)
                else:
                    answer = await asyncio.to_thread(adapter.answer_question, item, messages, directory)
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError("Empty Harness reply")
            except Exception:
                # Do not expose subprocess output, host paths, access tokens, or upstream bodies.
                active_sessions.discard(session_id)
                if not chat["session_id"]:
                    database.delete_chat_session(session_id, current_time())
                    await cleanup()
                raise HTTPException(503, "DeepSeek Harness 暂时无法回答，请保留问题后重试")
            finally:
                active_sessions.discard(session_id)
                database.release_chat_lease(session_id)
            # A seven-day thread that expired during generation must not be revived.
            existing = database.get_chat(item_id)
            if existing["expires_at"] and existing["expires_at"] <= current_time().isoformat():
                await cleanup()
                raise HTTPException(503, "本会话已满七天并自动清理，请重新提交问题")
            return database.append_chat_exchange(item_id, session_id, message, answer.strip(), current_time())

    return app


app = create_app()
