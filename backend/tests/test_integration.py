"""Exercise real pipeline -> CLI -> SQLite -> API boundaries without network or billing."""
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from optinfo import cli, pipeline
from optinfo.api import create_app
from optinfo.config import Settings, SHANGHAI
from optinfo.db import Database
from optinfo.sources import candidate


def test_pipeline_cli_edition_and_api_contract(tmp_path, monkeypatch):
    settings = Settings(tmp_path / "data.db", tmp_path / "chats", "integration-token")
    now = datetime(2026, 10, 16, 4, tzinfo=SHANGHAI)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(cli, "now_shanghai", lambda: now)
    monkeypatch.setattr(pipeline, "is_configured", lambda: True)
    raw = candidate("Ultrafast research test fixture", "https://example.org/paper", "Test fixture", "research", "2026-10-15T10:00:00+00:00", "Evidence for a test fixture. " * 20, journal="Nature Photonics")
    monkeypatch.setattr(pipeline, "collect", lambda _: ([raw], [{"source": "test-fixture", "status": "ok", "count": 1}]))
    monkeypatch.setattr(pipeline, "enrich", lambda value: value)
    calls = []

    def analyze(value, labels, day):
        calls.append(day)
        return {"title": "超快研究测试样本", "summary": "仅用于自动化测试的证据摘要", "importance": 80, "publish": True, "content": {"background": "背景", "approach": "方案", "results": "结果", "assessment": "局限"}, "tags": ["ultrafast"]}

    monkeypatch.setattr(pipeline, "analyze_candidate", analyze)
    assert cli.main(["generate", "--date", "2026-10-16"]) == 0
    assert cli.main(["generate", "--date", "2026-10-16"]) == 0
    assert calls == ["2026-10-16"]
    app = create_app(settings, clock=lambda: now)
    with TestClient(app, headers={"Authorization": "Bearer integration-token"}) as client:
        result = client.get("/api/briefing").json()
        assert result["status"] == "ready" and len(result["items"]) == 1
        entry = result["items"][0]
        assert entry["source_url"] == "https://example.org/paper"
        assert "基于摘要" in entry["content"]["assessment"]
        assert entry["figures"] == []
        assert client.get("/api/briefing?journal=nphoton").json()["items"][0]["id"] == entry["id"]


def test_cli_cleanup_uses_database_queue_and_real_harness_cleanup(tmp_path, monkeypatch):
    settings = Settings(tmp_path / "data.db", tmp_path / "chats", "test")
    now = datetime(2026, 10, 16, 4, tzinfo=SHANGHAI)
    db = Database(settings.db_path).init()
    db.save_edition("2026-10-16", [{"id": "test", "title": "test"}])
    session_id = str(uuid4())
    db.ensure_chat_session("test", session_id, now - timedelta(days=8))
    directory = settings.chat_dir / session_id
    directory.mkdir(parents=True)
    (directory / "sdk-state").write_text("test", encoding="utf-8")
    monkeypatch.setattr(cli, "now_shanghai", lambda: now)
    assert cli.cleanup(db, settings) == 1
    assert not directory.exists() and db.pending_chat_cleanup() == []


def test_cli_partial_retry_keeps_successes_without_rebilling(tmp_path, monkeypatch):
    settings = Settings(tmp_path / "data.db", tmp_path / "chats", "test")
    now = datetime(2026, 10, 16, 4, tzinfo=SHANGHAI)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(cli, "now_shanghai", lambda: now)
    calls = []

    def generate(day, topics, major_only=False, exclude_ids=None):
        calls.append(set(exclude_ids or []))
        index = len(calls)
        return {"items": [{"id": f"success-{index}", "title": f"成功条目{index}", "section": "research", "importance": 90 - index}],
                "companies": [], "generated_at": now.isoformat(), "diagnostics": [], "status": "partial" if index == 1 else "ready"}

    monkeypatch.setattr(pipeline, "generate_edition", generate)
    assert cli.main(["generate", "--date", "2026-10-16"]) == 0
    db = Database(settings.db_path).init()
    db.set_item_bookmark("success-1", True)
    assert not db.edition_exists("2026-10-16")
    assert cli.main(["generate", "--date", "2026-10-16"]) == 0
    assert calls == [set(), {"success-1"}]
    assert db.edition_exists("2026-10-16")
    assert {item["id"] for item in db.query_items(day="2026-10-16")} == {"success-1", "success-2"}
    assert db.get_item("success-1")["bookmarked"] is True


def test_hourly_events_do_not_rebill_filtered_candidates_but_reset_next_day(tmp_path, monkeypatch):
    settings = Settings(tmp_path / "data.db", tmp_path / "chats", "test")
    now = datetime(2026, 10, 16, 4, tzinfo=SHANGHAI)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(cli, "now_shanghai", lambda: now)
    calls = []

    def generate(day, topics, major_only=False, exclude_ids=None):
        calls.append((day, set(exclude_ids or [])))
        return {"items": [], "companies": [], "generated_at": now.isoformat(), "diagnostics": [],
                "status": "ready", "reviewed_ids": ["low-importance"]}

    monkeypatch.setattr(pipeline, "generate_edition", generate)
    assert cli.main(["major-events", "--date", "2026-10-16"]) == 0
    assert cli.main(["major-events", "--date", "2026-10-16"]) == 0
    assert cli.main(["major-events", "--date", "2026-10-17"]) == 0
    assert calls == [("2026-10-16", set()), ("2026-10-16", {"low-importance"}), ("2026-10-17", set())]
