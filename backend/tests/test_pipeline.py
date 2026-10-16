from datetime import date
import json

from optinfo import pipeline, sources, harness
from optinfo.harness import parse_json_response
import pytest
from optinfo.catalog import match_companies


def sample(section="research"):
    return sources.candidate("Ultrafast electron-phonon dynamics", "https://example.org/paper", "Journal", section,
                             "2026-10-15T12:00:00+00:00", "A real abstract with experimental evidence. " * 8, journal="Journal")


def analysis(**extra):
    return {"title": "超快动力学研究", "summary": "实验测得可重复的变化，具体条件见原文。", "importance": 85, "publish": True,
            "content": {k: "仅根据所提供证据解释。" for k in ("background", "approach", "results", "assessment")}, **extra}


def test_preference_changes_research_rank():
    item = sample()
    assert pipeline.score_candidate(item, ["ultrafast"]) > pipeline.score_candidate(item, ["quantum-optics"])


def test_dates_do_not_invent_current_publication_date():
    item = sample()
    item["published_at"] = ""
    assert not sources.recent(item, date(2026, 10, 16))
    item["published_at"] = "2026-10-17T00:00:00+08:00"
    assert not sources.recent(item, date(2026, 10, 16))
    assert sources.list_date("2026/10/16") == "2026-10-16T00:00:00+08:00"


def test_summary_cannot_acquire_model_invented_figure_or_source():
    item = sample()
    result = pipeline.sanitize_analysis(item, analysis(figures=[{"url": "https://fake.invalid/plot.png"}], source_url="https://fake.invalid"), "2026-10-16")
    assert result["figures"] == []
    assert result["source_url"] == item["source_url"]
    assert "未取得全文" in result["content"]["assessment"]


def test_world_low_importance_and_blank_analysis_not_published():
    assert pipeline.sanitize_analysis(sample("world"), analysis(importance=40), "2026-10-16") is None
    assert pipeline.sanitize_analysis(sample(), analysis(title="  "), "2026-10-16") is None


def test_new_company_requires_exact_evidence():
    item = sample("industry")
    item["evidence_text"] = "NewPhoton announced a new photodetector with independently reported conditions."
    found = pipeline.discovered_companies(item, analysis(company_mentions=[{"name": "NewPhoton", "evidence_quote": item["evidence_text"]}]))
    assert len(found) == 1
    assert found[0]["listed"] is None
    assert found[0]["compensation"]["masters"] is None
    assert not pipeline.discovered_companies(item, analysis(company_mentions=[{"name": "FictionCorp", "evidence_quote": "FictionCorp announced new technology."}]))


def test_model_failure_produces_retryable_partial(monkeypatch):
    one, two = sample(), sample()
    two["id"], two["title"] = "second", "Pump probe dynamics"
    monkeypatch.setattr(pipeline, "is_configured", lambda: True)
    monkeypatch.setattr(pipeline, "collect", lambda day: ([one, two], [{"source": "test", "status": "ok"}]))
    monkeypatch.setattr(pipeline, "enrich", lambda item: item)
    def run(item, *args):
        if item["id"] == "second":
            raise RuntimeError("model unavailable")
        return analysis()
    monkeypatch.setattr(pipeline, "analyze_candidate", run)
    result = pipeline.generate_edition("2026-10-16", ["ultrafast"])
    assert result["status"] == "partial"
    assert len(result["items"]) == 1


def test_known_items_do_not_trigger_model_calls(monkeypatch):
    item = sample()
    monkeypatch.setattr(pipeline, "is_configured", lambda: True)
    monkeypatch.setattr(pipeline, "collect", lambda day: ([item], [{"source": "test", "status": "ok"}]))
    def fail(*args):
        raise AssertionError("Known item must not be fetched or billed")
    monkeypatch.setattr(pipeline, "enrich", fail)
    result = pipeline.generate_edition("2026-10-16", [], exclude_ids={item["id"]})
    assert result["status"] == "ready" and result["items"] == []


def test_ssrf_local_and_credential_urls_rejected():
    assert not sources.public_url("http://127.0.0.1:8080/private")
    assert not sources.public_url("https://user:secret@example.com/")
    assert not sources.public_url("file:///etc/passwd")


def test_ndrc_parser_uses_displayed_date_not_url_date(monkeypatch):
    body = '<ul><li><a href="./202610/t20261016_123.html" title="政策原文">政策原文</a><span>2026/10/15</span></li></ul>'
    monkeypatch.setattr(sources, "fetch", lambda url: (body, url))
    items = sources.collect_html_list(sources.HTML_SOURCES[0], date(2026, 10, 16))
    assert items[0]["published_at"].startswith("2026-10-15")


def test_harness_json_fences_and_preamble():
    assert parse_json_response('```json\n{"publish":true}\n```') == {"publish": True}
    assert parse_json_response('Result: {"publish": false}') == {"publish": False}


def test_optics_coherent_word_is_not_company():
    assert "coherent" not in match_companies("Coherent and incoherent scattering in intelligent materials")
    assert "intel" not in match_companies("intelligent materials")
    assert "coherent" in match_companies("Coherent Corp announced a laser")


def complete_analysis():
    return analysis(tags=[], company_mentions=[])


def test_json_error_regenerates_once_with_same_evidence(monkeypatch, tmp_path):
    calls = []
    responses = iter(['{"title":"bad \\mu escape"}', json.dumps(complete_analysis(), ensure_ascii=False)])
    def run(prompt, home, **kwargs):
        calls.append((prompt, kwargs))
        return next(responses)
    monkeypatch.setattr(harness, "_run", run)
    monkeypatch.setattr(harness, "runtime_root", lambda: tmp_path)
    value = harness.analyze_candidate(sample(), ["超快动力学"], "2026-10-16")
    assert value["publish"] is True and len(calls) == 2
    assert "A real abstract" in calls[0][0] and "A real abstract" in calls[1][0]
    assert "bad" not in calls[1][0]  # Failed model text is never promoted to evidence.
    assert calls[0][1]["max_tokens"] == 8000


def test_truncated_output_never_filled_and_retry_bounded(monkeypatch, tmp_path):
    calls = []
    def run(*args, **kwargs):
        calls.append(1)
        return '{"title":"valid title","summary":"unfinished'
    monkeypatch.setattr(harness, "_run", run)
    monkeypatch.setattr(harness, "runtime_root", lambda: tmp_path)
    with pytest.raises(harness.StructuredOutputError) as raised:
        harness.analyze_candidate(sample(), [], "2026-10-16")
    assert len(calls) == 2
    assert harness.safe_error_details(raised.value) == {"error": "StructuredOutputError", "stage": "parse", "exception_type": "JSONDecodeError", "attempts": 2}


def test_schema_rejects_wrong_boolean_and_missing_fields():
    bad = complete_analysis()
    bad["publish"] = "true"
    with pytest.raises(ValueError):
        harness.validate_analysis(bad)
    bad = complete_analysis()
    del bad["content"]["assessment"]
    with pytest.raises(ValueError):
        harness.validate_analysis(bad)


def test_diagnostics_never_contains_exception_payload():
    error = harness.HarnessUnavailable("secret=sk-should-not-leak raw article", stage="run", exception_type="SdkProtocolError", finish_reason="error")
    value = harness.safe_error_details(error)
    assert "secret" not in json.dumps(value)
    assert value["stage"] == "run" and value["finish_reason"] == "error"
