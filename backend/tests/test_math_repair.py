"""Formatting corrections cannot rewrite facts or replace original image evidence."""
import asyncio
from datetime import datetime
import json
import re
from uuid import uuid4

import pytest

from optinfo import harness, math_repair, pipeline
from optinfo.config import SHANGHAI
from optinfo.db import Database


@pytest.mark.parametrize("text", ["Δ = 1.662 eV", "ν = 0.415", "T2 = 42 ± 11 fs", "|Ω(±K)| = 6.2 ± 1.4 Å²", "能量≈2 eV", "响应∝场强", "上限≤2", "下限≥1", "Eₓ", "xᵃ", "x < 3", "x > 0", "x ≪ 1"])
def test_raw_unicode_mathematical_notation_is_rejected(text):
    with pytest.raises(ValueError):
        harness.validate_math_format(text)
    harness.validate_math_format(r"\(" + text + r"\)")


def test_currency_and_web_query_are_not_mistaken_for_raw_math():
    harness.validate_math_format("公告价格 $ 100 million，详情 https://example.org/p?a=2&b=3。")
    harness.validate_math_format('示例标签 <div> 与 <span class="example">，不是数学不等式。')
    with pytest.raises(ValueError):
        harness.validate_math_format("https://example.org，测得x=2")


def test_only_exact_aa_command_inside_math_is_canonicalized():
    text = r"Outside \AA; \(a=1\,\mathrm{\AA}^{-1}\); \(\AAcustom\)"
    assert harness.canonicalize_math(text) == r"Outside \AA; \(a=1\,\mathrm{\mathring{A}}^{-1}\); \(\AAcustom\)"
    assert harness.canonicalize_math(r"\[a=2\AA\]") == r"\[a=2\mathring{A}\]"


def test_generation_and_answer_canonicalize_renderer_alias(monkeypatch, tmp_path):
    value = {"title": "原文实验", "summary": r"测得 \(a=1\mathrm{\AA}^{-1}\)。", "importance": 80, "publish": True,
             "content": {key: "依据原文证据。" for key in ("background", "approach", "results", "assessment")},
             "tags": [], "company_mentions": [], "selected_figures": []}
    assert r"\mathring{A}" in harness.validate_analysis(value)["summary"]
    monkeypatch.setattr(harness, "_run", lambda *args: r"\(a=1\AA\)")
    answer = asyncio.run(harness.answer_question({}, [], tmp_path))
    assert answer == r"\(a=1\mathring{A}\)"


def test_existing_math_and_facts_are_immutable_during_delimiter_insertion():
    original = r"已有 \(x=1\)，测得 Δ = 1.662 eV。"
    correct = r"已有 \(x=1\)，测得 \(Δ = 1.662 eV\)。"
    assert harness.validate_delimiter_only_repair(original, correct) == correct
    for changed in [correct.replace("1.662", "1.663"), correct.replace("x=1", "x=2"), correct.replace("测得", "推测"), correct.replace("x=1", "x = 1")]:
        with pytest.raises(ValueError):
            harness.validate_delimiter_only_repair(original, changed)
    alias = r"已有 \(a=1\AA\)，Δ = 2。"
    fixed = r"已有 \(a=1\mathring{A}\)，\(Δ = 2\)。"
    assert harness.validate_delimiter_only_repair(alias, fixed) == fixed


@pytest.mark.parametrize("original,repaired", [
    (r"\(x=1\)", r"\(x\)\(=1\)"),
    (r"x=1 https://example.org/a", r"\(x=1\) https://example.org/\(a\)"),
])
def test_existing_math_boundaries_and_original_urls_cannot_be_split(original, repaired):
    with pytest.raises(ValueError):
        harness.validate_delimiter_only_repair(original, repaired)


def test_chinese_url_boundary_does_not_protect_following_equation():
    original = "https://example.org，测得x=2"
    repaired = r"https://example.org，测得\(x=2\)"
    assert harness.validate_delimiter_only_repair(original, repaired) == repaired


def test_alias_only_repair_never_calls_model(monkeypatch):
    monkeypatch.setattr(harness, "_run", lambda *args, **kwargs: pytest.fail("Deterministic alias conversion must not bill the model"))
    assert harness.repair_math_fields({"caption": r"\(a=1\AA\)"}) == {"caption": r"\(a=1\mathring{A}\)"}


def test_formatting_repair_retries_fact_change_once_and_keeps_existing_math(monkeypatch, tmp_path):
    original = r"已有 \(x=1\)，Δ = 1.662 eV。"
    outputs = iter([r"已有 \(x=1\)，\(Δ = 9.999 eV\)。", r"已有 \(x=1\)，\(Δ = 1.662 eV\)。"])
    calls = []
    def run(prompt, *args, **kwargs):
        calls.append(prompt)
        return json.dumps({"fields": {"results": next(outputs)}}, ensure_ascii=False)
    monkeypatch.setattr(harness, "_run", run)
    monkeypatch.setattr(harness, "runtime_root", lambda: tmp_path)
    result = harness.repair_math_fields({"results": original, "summary": "原文摘要"})
    assert result["results"] == r"已有 \(x=1\)，\(Δ = 1.662 eV\)。" and result["summary"] == "原文摘要"
    assert len(calls) == 2 and "9.999" not in calls[1]


def test_formatting_repair_rejects_model_additions_after_two_attempts(monkeypatch, tmp_path):
    calls = []
    def run(*args, **kwargs):
        calls.append(1)
        return json.dumps({"fields": {"results": r"\(Δ = 2\)；新增结论"}})
    monkeypatch.setattr(harness, "_run", run)
    monkeypatch.setattr(harness, "runtime_root", lambda: tmp_path)
    with pytest.raises(harness.StructuredOutputError):
        harness.repair_math_fields({"results": "Δ = 2"})
    assert len(calls) == 2


def setup_versions(tmp_path):
    db = Database(tmp_path / "data.db").init()
    base = {"id": "paper", "title": "论文", "section": "research", "content_revision": 2, "analysis_status": "complete", "summary": "原文摘要",
            "figures": [{"id": "fig", "url": "https://example.org/real.png", "image_path": "/api/media/" + "a" * 64 + ".png", "caption": "原文结果图", "selection_reason": "支持主要实验结论", "original_caption": "Original measured result."}]}
    for day, value in [("2026-10-15", "1.662"), ("2026-10-16", "1.663")]:
        db.save_edition(day, [dict(base, content={"results": "Δ = " + value + " eV"})])
    db.set_item_bookmark("paper", True)
    now = datetime(2026, 10, 16, tzinfo=SHANGHAI)
    session = str(uuid4())
    db.ensure_chat_session("paper", session, now)
    db.append_chat_exchange("paper", session, "既有问题", "既有回答", now)
    return db


def test_math_only_preserves_each_snapshot_facts_original_images_and_user_data(tmp_path, monkeypatch):
    db = setup_versions(tmp_path)
    before_figures = db.get_item("paper")["figures"]
    before_chat = db.get_chat("paper")
    calls = []
    def run(prompt, *args, **kwargs):
        calls.append(prompt)
        fields = json.loads(prompt[prompt.rfind('{"fields":'):])["fields"]
        return json.dumps({"fields": {key: r"\(" + value + r"\)" for key, value in fields.items()}}, ensure_ascii=False)
    monkeypatch.setattr(harness, "_run", run)
    monkeypatch.setattr(harness, "runtime_root", lambda: tmp_path / "harness")
    monkeypatch.setattr(pipeline, "enrich", lambda *_: pytest.fail("Math repair must never re-fetch a paper"))
    result = math_repair.repair_math_only(db)
    assert result["status"] == "ready" and len(calls) == 1
    for day, value in [("2026-10-15", "1.662"), ("2026-10-16", "1.663")]:
        item = db.query_items(day=day)[0]
        assert item["content"]["results"] == r"\(Δ = " + value + r" eV\)"
        assert item["briefing_date"] == day and item["figures"] == before_figures
        assert item["analysis_status"] == "complete" and item["math_status"] == "complete"
    assert db.get_chat("paper") == before_chat and db.get_item("paper")["bookmarked"] is True
    assert math_repair.repair_math_only(db)["items"][0]["status"] == "already_formatted" and len(calls) == 1


def test_failed_math_only_keeps_readable_content_and_never_triggers_full_repair(tmp_path, monkeypatch):
    db = setup_versions(tmp_path)
    before = db.get_item("paper")
    def fail(*args, **kwargs):
        raise harness.HarnessUnavailable("Private exception must not escape")
    monkeypatch.setattr(math_repair, "repair_math_fields", fail)
    outcome = math_repair.repair_math_only(db)
    after = db.get_item("paper")
    assert outcome["status"] == "partial" and "Private" not in json.dumps(outcome)
    assert after["content"] == before["content"] and after["figures"] == before["figures"]
    assert after["analysis_status"] == "pending_math" and after["math_status"] == "pending"
    monkeypatch.setattr(pipeline, "is_configured", lambda: True)
    monkeypatch.setattr(pipeline, "enrich", lambda *_: pytest.fail("Full repair must skip pending-math items"))
    assert pipeline.repair_papers(db, [])["items"] == []
