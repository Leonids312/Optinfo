"""Industry history fills and durable source identity, without network or billing."""
from datetime import datetime
from uuid import uuid4

import pytest

from optinfo import cli, pipeline
from optinfo.config import Settings, SHANGHAI
from optinfo.db import Database, canonical_source_keys
from optinfo.sources import candidate


DAY = "2026-10-20"
NOW = datetime(2026, 10, 20, 12, tzinfo=SHANGHAI)


def news(company, number=1, published="2026-10-19"):
    value = candidate(f"{company} announces optical product number {number}",
                      f"https://example.org/{company}/{number}", company, "industry",
                      published + "T08:00:00+08:00", "Reported industrial specifications and product applications. " * 5)
    value.update(id=f"{company}-{number}", company_ids=[company], source_id=company)
    return value


def analysis(item, *args):
    return {"publish": True, "title": item["title"], "summary": "新品应用于光学测量。",
            "importance": 80, "content": {key: "公开的产品参数与应用。"
                                       for key in ("background", "approach", "results", "assessment")}}


@pytest.fixture
def editorial(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "is_configured", lambda: True)
    monkeypatch.setattr(pipeline, "enrich", lambda value: value)
    def analyze(value, *args):
        calls.append(value["id"])
        return analysis(value)
    monkeypatch.setattr(pipeline, "analyze_candidate", analyze)
    return calls


def test_industry_expands_windows_for_distinct_companies_before_second_release(monkeypatch, editorial):
    windows = []
    batches = {7: [news("a"), news("a", 2), news("b")],
               30: [news("a"), news("c", published="2026-10-01")],
               90: [news("a"), news("d", published="2026-08-01")]}
    def collect(day, sections=None, *, industry_days=7, topic_ids=None):
        assert day == DAY and sections == {"industry"} and topic_ids == ["semiconductor"]
        windows.append(industry_days)
        return batches[industry_days], [{"source": "fixture", "status": "ok"}]
    monkeypatch.setattr(pipeline, "collect", collect)
    result = pipeline.generate_edition(DAY, ["semiconductor"], industry_only=True)
    assert windows == [7, 30, 90]
    assert set(editorial) == {"a-1", "b-1", "c-1", "d-1"}
    assert len(editorial) == 4 and result["status"] == "ready"
    old = next(item for item in result["items"] if item["id"] == "d-1")
    assert old["published_at"].startswith("2026-08-01") and old["history_backfill"]


def test_existing_today_company_does_not_monopolize_fill(monkeypatch, editorial):
    windows = []
    def collect(day, sections=None, *, industry_days=7, **kwargs):
        windows.append(industry_days)
        data = [news("nvidia", 2)]
        if industry_days >= 30:
            data += [news("amd", published="2026-10-01")]
        if industry_days >= 90:
            data += [news("tsmc", published="2026-09-01")]
        if industry_days >= 365:
            data += [news("kla", published="2026-01-01")]
        return data, [{"source": "fixture", "status": "ok"}]
    monkeypatch.setattr(pipeline, "collect", collect)
    result = pipeline.generate_edition(DAY, [], industry_only=True, industry_limit=3,
                                       company_counts={"company:nvidia": 1})
    assert windows == [7, 30, 90, 365]
    assert set(editorial) == {"amd-1", "tsmc-1", "kla-1"}
    assert len(result["items"]) == 3


def test_second_company_release_only_after_history_exhausted_and_cost_is_bounded(monkeypatch, editorial):
    windows = []
    def collect(day, *, industry_days=7, **kwargs):
        windows.append(industry_days)
        return [news("a", number) for number in range(1, 9)], [{"source": "fixture", "status": "ok"}]
    monkeypatch.setattr(pipeline, "collect", collect)
    result = pipeline.generate_edition(DAY, [], industry_only=True)
    assert windows == [7, 30, 90, 365]
    assert len(editorial) == len(result["items"]) == 2  # No four-news padding from one company.
    assert len(result["reviews"]) == 2


def test_partial_archive_failure_preserves_recent_successes(monkeypatch, editorial):
    monkeypatch.setattr(pipeline, "collect", lambda *args, **kwargs: (
        [news("a")], [{"source": "fixture", "status": "partial", "count": 1, "error": "TimeoutError"}]))
    result = pipeline.generate_edition(DAY, [], industry_only=True, industry_limit=1)
    assert editorial == ["a-1"] and len(result["items"]) == 1
    assert result["status"] == "ready" and result["diagnostics"][0]["error"] == "TimeoutError"


def test_partial_industry_retry_uses_long_window_but_never_new_candidates(monkeypatch, editorial):
    windows = []
    def collect(day, *, industry_days=7, **kwargs):
        windows.append(industry_days)
        return [news("old", published="2026-01-01"), news("fresh")], [{"source": "fixture", "status": "ok"}]
    monkeypatch.setattr(pipeline, "collect", collect)
    result = pipeline.generate_edition(DAY, [], retry_ids={"old-1"})
    assert windows == [365] and editorial == ["old-1"] and result["status"] == "ready"


def test_fill_retry_stops_when_its_remaining_target_is_reached(monkeypatch, editorial):
    monkeypatch.setattr(pipeline, "collect", lambda *args, **kwargs: (
        [news("a"), news("b")], [{"source": "fixture", "status": "ok"}]))
    result = pipeline.generate_edition(DAY, [], industry_only=True, industry_limit=1,
                                       retry_ids={"a-1", "b-1"})
    assert editorial == ["a-1"] and len(result["items"]) == 1
    assert {"item": "b-1", "status": "target_reached"} in result["diagnostics"]


def test_history_windows_do_not_recollect_other_sections_and_keep_one_global_cost_limit(monkeypatch, editorial):
    calls = []
    def collect(day, sections=None, *, industry_days=7, **kwargs):
        calls.append((sections, industry_days))
        return [news(str(number)) for number in range(8)], [{"source": "fixture", "status": "ok"}]
    monkeypatch.setattr(pipeline, "collect", collect)
    monkeypatch.setenv("OPTINFO_MAX_CANDIDATES", "3")
    result = pipeline.generate_edition(DAY, [])
    assert calls == [(None, 7)]
    assert len(editorial) == len(result["items"]) == 3


def test_budget_audit_distinguishes_retrieval_attempts_and_unvisited_history(monkeypatch, editorial):
    windows = []
    entries = [news(f"company{number}") for number in range(18)]
    def collect(day, sections=None, *, industry_days=7, **kwargs):
        windows.append(industry_days)
        return entries[:6] if industry_days == 7 else entries, [{"source": "fixture", "status": "ok"}]
    def enrich(value):
        index = int(value["id"].split("-")[0].removeprefix("company"))
        return dict(value, evidence_text="short") if index % 3 != 0 else value
    monkeypatch.setattr(pipeline, "collect", collect)
    monkeypatch.setattr(pipeline, "enrich", enrich)
    def analyze(value, *args):
        editorial.append(value["id"])
        return {"publish": False}
    monkeypatch.setattr(pipeline, "analyze_candidate", analyze)
    result = pipeline.generate_edition(DAY, [], industry_only=True)
    assert windows == [7, 30] and len(editorial) == 6
    audit = next(entry for entry in result["diagnostics"] if entry.get("source") == "selection")
    assert audit == {"source": "selection", "status": "budget_exhausted",
                     "enrich_attempts": 18, "model_calls": 6, "candidate_limit": 18,
                     "last_industry_days": 30, "visited_industry_days": [7, 30],
                     "unvisited_industry_days": [90, 365]}
    assert len(result["reviewed_ids"]) == len(result["reviews"]) == 6
    assert result["status"] == "ready"  # The audit, not this status, describes selection completeness.
    assert all(entry["item"] in editorial for entry in result["reviews"])


def test_exact_target_at_budget_limit_does_not_claim_budget_stopped_selection(monkeypatch, editorial):
    entries = [news(f"company{number}") for number in range(3)]
    monkeypatch.setenv("OPTINFO_MAX_CANDIDATES", "3")
    monkeypatch.setattr(pipeline, "collect", lambda *args, **kwargs: (
        entries, [{"source": "fixture", "status": "ok"}]))
    monkeypatch.setattr(pipeline, "enrich", lambda value: value if value["id"] == "company2-1"
                        else dict(value, evidence_text="short"))
    result = pipeline.generate_edition(DAY, [], industry_only=True, industry_limit=1)
    audit = next(entry for entry in result["diagnostics"] if entry.get("source") == "selection")
    assert audit["enrich_attempts"] == 3 and audit["model_calls"] == 1
    assert audit["status"] == "selection_stats" and audit["last_industry_days"] == 7
    assert len(result["items"]) == 1


def test_selection_audit_cannot_turn_all_failed_sources_into_success(monkeypatch, editorial):
    monkeypatch.setattr(pipeline, "collect", lambda *args, **kwargs: (
        [], [{"source": "fixture", "status": "failed", "error": "TimeoutError"}]))
    with pytest.raises(pipeline.SourceCollectionUnavailable):
        pipeline.generate_edition(DAY, [], industry_only=True)
    assert editorial == []


def test_source_identity_aliases_keep_original_ids():
    first = news("company")
    first.update(source_url="https://www.example.org/news/release/?utm_source=feed#top",
                 doi="https://doi.org/10.1234/ABC", title="An important photonic product release",
                 id="original-id")
    duplicate = dict(first, id="changed-id", source_url="http://example.org/news/release",
                     title="A translated editorial title", doi="10.1234/abc")
    keys = canonical_source_keys(first)
    assert "doi:10.1234/abc" in keys
    assert keys & canonical_source_keys(duplicate)
    assert first["id"] == "original-id" and duplicate["id"] == "changed-id"
    assert canonical_source_keys({"source_url": "https://arxiv.org/abs/2610.12345v1"}) & canonical_source_keys(
        {"source_url": "https://arxiv.org/pdf/2610.12345v2.pdf"})
    title_duplicate = {"title": "Unrelated Chinese display title", "section": "industry",
                       "company_ids": ["company"], "published_at": "2026-10-20T00:00:00+08:00", "sources": [
        {"title": "AN IMPORTANT: photonic product release!", "url": "https://other.example/paper"}]}
    assert any(key.startswith("title:") for key in keys & canonical_source_keys(title_duplicate))


def test_repeating_industry_titles_do_not_hide_new_quarters_or_other_companies():
    first = news("company", published="2026-09-30")
    first["title"] = "Company announces quarterly financial results"
    nearby = dict(first, id="syndicated", source_url="https://other.example/release",
                  published_at="2026-10-01T00:00:00+08:00")
    next_quarter = dict(nearby, published_at="2026-12-31T00:00:00+08:00")
    another_company = dict(nearby, company_ids=["different-company"])
    keys = canonical_source_keys(first)
    assert keys & canonical_source_keys(nearby)
    assert not keys & canonical_source_keys(next_quarter)
    assert not keys & canonical_source_keys(another_company)
    paper = dict(first, section="research", doi="10.1234/original")
    other_paper = dict(nearby, section="research", doi="10.1234/another")
    assert not canonical_source_keys(paper) & canonical_source_keys(other_paper)


def test_review_identity_survives_reopen_and_failed_reviews_are_rejected(tmp_path):
    db = Database(tmp_path / "data.db").init()
    entry = news("filtered")
    db.mark_source_reviews([{"item": entry["id"], "keys": sorted(canonical_source_keys(entry)), "decision": "filtered"}])
    reopened = Database(db.path).init()
    changed = dict(entry, id="new-feed-id", source_url=entry["source_url"] + "?utm_medium=next-day")
    assert reopened.excluded_source_keys() & canonical_source_keys(changed)
    with pytest.raises(ValueError):
        db.mark_source_reviews([{"item": "failure", "keys": ["id:failure"], "decision": "model_failed"}])
    assert "id:failure" not in db.excluded_source_keys()


def test_enriched_redirect_to_known_original_does_not_bill_again(monkeypatch, editorial):
    known, alternate = news("known"), news("alternate")
    monkeypatch.setattr(pipeline, "collect", lambda *args, **kwargs: (
        [alternate], [{"source": "fixture", "status": "ok"}]))
    monkeypatch.setattr(pipeline, "enrich", lambda value: dict(value, evidence_url=known["source_url"]))
    result = pipeline.generate_edition(DAY, [], industry_only=True, exclude_keys=canonical_source_keys(known))
    assert editorial == [] and result["items"] == []
    assert any(entry.get("item") == alternate["id"] and entry["status"] == "already_reviewed"
               for entry in result["diagnostics"])


def test_legacy_source_titles_index_without_rewriting_snapshots(tmp_path):
    db = Database(tmp_path / "data.db").init()
    entry = news("legacy")
    entry["sources"] = [{"title": entry["title"], "url": entry["source_url"]}]
    entry["title"] = "旧中文标题"
    db.save_edition("2026-10-19", [entry])
    with db.connection() as conn:
        before = conn.execute("SELECT data_json FROM edition_items").fetchone()[0]
        conn.execute("DELETE FROM published_source_keys")
    Database(db.path).init()
    duplicate = news("legacy")
    duplicate.update(id="new-id", source_url="https://different.example/same-story")
    assert canonical_source_keys(duplicate) & db.excluded_source_keys()
    with db.connection() as conn:
        assert conn.execute("SELECT data_json FROM edition_items").fetchone()[0] == before


def test_fill_cli_preserves_partial_research_snapshots_bookmarks_and_chat(tmp_path, monkeypatch, editorial):
    settings = Settings(tmp_path / "data.db", tmp_path / "chats", "fixture-token")
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(cli, "now_shanghai", lambda: NOW)
    db = Database(settings.db_path).init()
    metadata = {"topics": ["ultrafast"], "diagnostics": [{"item": "pending-paper", "status": "model_failed"}]}
    existing = [dict(news("nvidia"), title="既有产业快照"),
                {"id": "paper", "title": "既有论文", "section": "research", "content": {"results": "原始结果"}}]
    db.save_edition(DAY, existing, status="partial", generated_at=NOW.isoformat(), metadata=metadata)
    db.set_item_bookmark("paper", True)
    session_id = str(uuid4())
    db.ensure_chat_session("paper", session_id, NOW)
    db.append_chat_exchange("paper", session_id, "既有问题", "既有回答", NOW)
    with db.connection() as conn:
        snapshots = [tuple(row) for row in conn.execute("SELECT item_id,position,data_json FROM edition_items ORDER BY position")]
    chat = db.get_chat("paper")
    def collect(day, sections=None, **kwargs):
        assert sections == {"industry"}
        return [news("nvidia", 2), news("amd"), news("tsmc"), news("kla")], [{"source": "fixture", "status": "ok"}]
    monkeypatch.setattr(pipeline, "collect", collect)
    assert cli.main(["fill-industry", "--date", DAY]) == 0
    assert set(editorial) == {"amd-1", "tsmc-1", "kla-1"}
    edition = db.get_edition(DAY)
    assert edition["status"] == "partial" and edition["generated_at"] == NOW.isoformat()
    assert edition["metadata"]["diagnostics"] == metadata["diagnostics"]
    assert edition["metadata"]["topics"] == metadata["topics"]
    assert len(edition["metadata"]["industry_fill"]["added"]) == 3
    saved_audit = next(entry for entry in edition["metadata"]["industry_fill"]["diagnostics"]
                       if entry.get("source") == "selection")
    assert saved_audit["enrich_attempts"] == saved_audit["model_calls"] == 3
    with db.connection() as conn:
        assert [tuple(row) for row in conn.execute("SELECT item_id,position,data_json FROM edition_items ORDER BY position LIMIT 2")] == snapshots
    assert db.get_item("paper")["bookmarked"] and db.get_chat("paper") == chat
    assert cli.main(["fill-industry", "--date", DAY]) == 0
    assert len(editorial) == 3
    with pytest.raises(ValueError, match="today"):
        cli.main(["fill-industry", "--date", "2026-10-19"])


def test_append_industry_enforces_scope_and_cross_day_duplicates(tmp_path):
    db = Database(tmp_path / "data.db").init()
    old = news("old")
    db.save_edition("2026-10-19", [old])
    db.save_edition(DAY, [{"id": "paper", "title": "研究", "section": "research"}])
    duplicate = dict(old, id="new-id", source_url=old["source_url"] + "?utm_source=feed")
    assert db.append_industry(DAY, [duplicate, news("other")], target=1) == ["other-1"]
    with pytest.raises(ValueError):
        db.append_industry(DAY, [{"id": "unexpected", "title": "研究", "section": "research"}])


def test_successful_filtered_review_is_not_rebilled_by_alternate_source_next_day(tmp_path, monkeypatch):
    settings = Settings(tmp_path / "data.db", tmp_path / "chats", "test-token")
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(cli, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(pipeline, "is_configured", lambda: True)
    monkeypatch.setattr(pipeline, "enrich", lambda value: value)
    calls = []
    def collect(day, **kwargs):
        entry = news("filtered")
        entry.update(section="world", id="first" if day == DAY else "changed",
                     source_url=entry["source_url"] + ("?utm_source=next-day" if day != DAY else ""))
        return [entry], [{"source": "fixture", "status": "ok"}]
    def analyze(value, *args):
        calls.append(value["id"])
        return {"publish": False}
    monkeypatch.setattr(pipeline, "collect", collect)
    monkeypatch.setattr(pipeline, "analyze_candidate", analyze)
    assert cli.main(["major-events", "--date", DAY]) == 0
    assert cli.main(["major-events", "--date", "2026-10-21"]) == 0
    assert calls == ["first"]
