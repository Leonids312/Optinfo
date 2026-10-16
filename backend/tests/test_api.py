from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from optinfo.api import create_app
from optinfo.config import Settings, SHANGHAI
from optinfo.db import Database


CATALOG = SimpleNamespace(
    DEFAULT_TOPIC_IDS=["optics"],
    TOPICS=[{"id": "optics", "label": "光学", "section": "research", "kind": "direction", "keywords": []},
            {"id": "chips", "label": "芯片", "section": "industry", "kind": "direction", "keywords": []}],
    JOURNALS=[{"id": "nature-photonics", "name": "Nature Photonics"}],
    COMPANIES=[{"id": "example", "name": "测试企业", "description": "测试样本"}],
)


def item(item_id="paper-1", **kwargs):
    return {"id": item_id, "title": "光学芯片研究", "summary": "测试证据摘要", "journal": "Nature Photonics", "company_ids": ["example"], **kwargs}


class Harness:
    def __init__(self, ready=True, fail=False):
        self.ready, self.fail, self.deleted = ready, fail, []

    def is_configured(self):
        return self.ready

    async def answer_question(self, item, messages, session_dir):
        (session_dir / "test-resource").write_text("session resource", encoding="utf-8")
        if self.fail:
            raise RuntimeError("Private upstream secret must not escape")
        return "这是一条测试回答，依据所提供的资讯。"

    def cleanup_session(self, session_dir):
        self.deleted.append(session_dir.name)


@pytest.fixture
def service(tmp_path):
    clock = [datetime(2026, 10, 16, 23, 59, tzinfo=SHANGHAI)]
    adapter = Harness()
    config = Settings(tmp_path / "data.sqlite3", tmp_path / "chats", "test-token")
    app = create_app(config, clock=lambda: clock[0], harness=adapter, catalog=CATALOG)
    app.state.database.save_edition("2026-10-16", [item()])
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as client:
        yield client, app.state.database, clock, adapter, config


def test_auth_health_and_no_secret_disclosure(service):
    client, *_ = service
    assert client.get("/api/health", headers={"Authorization": ""}).status_code == 200
    assert client.get("/api/bootstrap", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/bootstrap", headers={"Authorization": ""}).status_code == 401
    assert client.get("/api/bootstrap").status_code == 200
    assert "test-token" not in client.get("/api/health").text


def test_preference_rollover_and_explicit_empty_selection(service):
    client, db, clock, *_ = service
    response = client.put("/api/preferences", json={"topic_ids": ["chips"]})
    assert response.json() == {"active_topics": ["optics"], "pending_topics": ["chips"], "effective_date": "2026-10-17"}
    assert db.preferences_for_date("2026-10-16", ["optics"]) == ["optics"]
    assert client.get("/api/briefing").json()["items"][0]["title"] == "光学芯片研究"
    # A later save on the same day replaces tomorrow's selection, including empty.
    assert client.put("/api/preferences", json={"topic_ids": []}).json()["pending_topics"] == []
    clock[0] += timedelta(minutes=2)
    data = client.get("/api/bootstrap").json()
    assert data["active_topics"] == [] and data["effective_date"] is None
    assert client.put("/api/preferences", json={"topic_ids": ["unknown"]}).status_code == 422


def test_clock_uses_shanghai_calendar(service):
    client, _, clock, *_ = service
    clock[0] = datetime(2026, 10, 16, 17, 0, tzinfo=timezone.utc)
    assert client.get("/api/briefing").json()["date"] == "2026-10-17"
    assert client.put("/api/preferences", json={"topic_ids": []}).json()["effective_date"] == "2026-10-18"


def test_editions_are_idempotent_and_bookmarks_durable(service):
    client, db, *_ = service
    client.put("/api/items/paper-1/bookmark", json={"bookmarked": True})
    assert db.save_edition("2026-10-16", [item(title="replacement")]) is False
    db.upsert_items([item(title="updated source")], "2026-10-17")
    original = client.get("/api/briefing?date=2026-10-16").json()["items"][0]
    assert original["title"] == "光学芯片研究" and original["bookmarked"] is True
    reopened = Database(db.path).init()
    assert reopened.get_item("paper-1")["bookmarked"] is True
    assert client.get("/api/briefing?bookmarked=true").json()["items"][0]["id"] == "paper-1"


def test_historical_filters_and_empty_today(service):
    client, db, clock, *_ = service
    db.save_edition("2026-10-15", [item("paper-2", title="昨日光学", section="research")])
    clock[0] += timedelta(days=1)
    assert client.get("/api/briefing").json()["items"] == []
    assert client.get("/api/briefing").json()["status"] == "not_generated"
    assert len(client.get("/api/briefing?q=光学").json()["items"]) == 2
    assert len(client.get("/api/briefing?journal=nature-photonics").json()["items"]) == 2
    assert len(client.get("/api/briefing?company=example").json()["items"]) == 2
    assert len(client.get("/api/briefing?date=2026-10-15&q=光学").json()["items"]) == 1
    assert client.get("/api/briefing?date=invalid").status_code == 422


def test_company_bookmarks_and_unknown_salary(service):
    client, db, *_ = service
    assert client.put("/api/companies/example/bookmark", json={"bookmarked": True}).status_code == 200
    db.upsert_companies(CATALOG.COMPANIES)
    data = client.get("/api/companies?bookmarked=true").json()["items"]
    assert len(data) == 1 and data[0]["compensation"]["masters"] is None
    assert len(client.get("/api/companies/example").json()["items"]) == 1
    assert client.get("/api/items/missing").status_code == 404


def test_chat_one_session_expiry_removes_messages_and_resources(service):
    client, db, clock, adapter, config = service
    first = client.post("/api/items/paper-1/chat", json={"message": "什么是该研究的创新？"}).json()
    second = client.post("/api/items/paper-1/chat", json={"message": "还有哪些不足？"}).json()
    assert first["session_id"] == second["session_id"] and len(second["messages"]) == 4
    assert first["expires_at"] == second["expires_at"]
    assert (config.chat_dir / first["session_id"]).exists()
    client.put("/api/items/paper-1/bookmark", json={"bookmarked": True})
    clock[0] += timedelta(days=7)
    empty = client.get("/api/items/paper-1/chat").json()
    assert empty["session_id"] is None and empty["messages"] == []
    assert first["session_id"] in adapter.deleted
    assert not (config.chat_dir / first["session_id"]).exists()
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0
    assert db.get_item("paper-1")["bookmarked"] is True
    fresh = client.post("/api/items/paper-1/chat", json={"message": "重新提问"}).json()
    assert fresh["session_id"] != first["session_id"] and len(fresh["messages"]) == 2


def test_model_configuration_and_failure_never_invent_answer(service):
    client, db, _, adapter, config = service
    adapter.ready = False
    assert client.post("/api/items/paper-1/chat", json={"message": "test"}).status_code == 503
    adapter.ready, adapter.fail = True, True
    failed = client.post("/api/items/paper-1/chat", json={"message": "keep input"})
    assert failed.status_code == 503 and "secret" not in failed.text
    assert db.get_chat("paper-1")["messages"] == []
    assert list(config.chat_dir.iterdir()) == []


def test_expired_cleanup_retries_after_hook_failure(service):
    client, db, clock, adapter, config = service
    first = client.post("/api/items/paper-1/chat", json={"message": "hello"}).json()
    real_cleanup = adapter.cleanup_session
    adapter.cleanup_session = lambda _: (_ for _ in ()).throw(RuntimeError("temporary failure"))
    clock[0] += timedelta(days=8)
    assert client.get("/api/items/paper-1/chat").json()["messages"] == []
    assert first["session_id"] in db.pending_chat_cleanup()
    adapter.cleanup_session = real_cleanup
    client.get("/api/items/paper-1/chat")
    assert db.pending_chat_cleanup() == []
    assert not (config.chat_dir / first["session_id"]).exists()


def test_malformed_request_has_simple_error(service):
    client, *_ = service
    response = client.post("/api/items/paper-1/chat", json={"message": "", "extra": "ignored?"})
    assert response.status_code == 422 and isinstance(response.json()["detail"], str)


def test_partial_retry_replaces_incomplete_items_and_preserves_major_events(service):
    _, db, *_ = service
    db.upsert_items([item("major-1", kind="major", section="world", title="独立重大事件")], "2026-10-18")
    assert db.save_edition("2026-10-18", [item("partial-1", summary="未完成摘要")], status="partial")
    assert not db.edition_exists("2026-10-18")
    assert db.save_edition("2026-10-18", [item("complete-1", summary="完整证据分析")], status="ready")
    assert db.edition_exists("2026-10-18")
    edition = db.query_items(day="2026-10-18")
    assert {value["id"] for value in edition} == {"complete-1", "major-1"}


def test_registered_crash_session_expires_without_messages(service):
    from uuid import uuid4
    client, db, clock, adapter, config = service
    session_id = str(uuid4())
    db.ensure_chat_session("paper-1", session_id, clock[0])
    directory = config.chat_dir / session_id
    directory.mkdir()
    (directory / "orphan-resource").write_text("test", encoding="utf-8")
    clock[0] += timedelta(days=8)
    assert client.get("/api/items/paper-1/chat").json()["status"] == "empty"
    assert not directory.exists() and session_id in adapter.deleted


def test_seed_companies_does_not_overwrite_enriched_directory(service):
    _, db, *_ = service
    db.upsert_companies([{"id": "example", "name": "测试企业", "description": "Updated verified description"}])
    db.upsert_companies(CATALOG.COMPANIES, overwrite=False)
    assert db.get_company("example")["description"] == "Updated verified description"


def test_shared_cleanup_lease_protects_active_sdk_and_recovers_after_crash(service):
    from uuid import uuid4
    _, db, clock, *_ = service
    session_id = str(uuid4())
    db.ensure_chat_session("paper-1", session_id, clock[0])
    clock[0] += timedelta(days=7) - timedelta(minutes=1)
    db.acquire_chat_lease(session_id, clock[0])
    clock[0] += timedelta(minutes=2)
    assert db.expire_chats(clock[0]) == 0
    clock[0] += timedelta(minutes=15)
    assert db.expire_chats(clock[0]) == 1
    assert session_id in db.pending_chat_cleanup()
