from datetime import date
import json
import uuid

from optinfo import pipeline, sources, harness
from optinfo.harness import parse_json_response
import pytest
from optinfo.catalog import match_companies


def sample(section="research"):
    value = sources.candidate("Ultrafast electron-phonon dynamics", "https://example.org/paper", "Journal", section,
                             "2026-10-15T12:00:00+00:00", "A real abstract with experimental evidence. " * 8, journal="Journal")
    if section == "research":
        value.update(evidence_level="fulltext", figure_candidates=[{"id": "fig-verified", "url": "https://example.org/real.png", "source_url": value["source_url"], "original_caption": "Figure 4. Experimental result.", "kind": "original_image"}])
    return value


def analysis(**extra):
    return {"title": "超快动力学研究", "summary": "实验测得可重复的变化，具体条件见原文。", "importance": 85, "publish": True,
            "selected_figures": [{"id": "fig-verified", "caption": "图四展示实验测量的原始结果。", "selection_reason": "对应正文中实验测量的主要结论。", "role": "result"}],
            "content": {k: "仅根据所提供证据解释。" for k in ("background", "approach", "results", "assessment")}, **extra}


@pytest.fixture(autouse=True)
def mock_figure_download(monkeypatch):
    monkeypatch.setattr(pipeline, "materialize_figure", lambda *args: "/api/media/" + "a" * 64 + ".png")


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
    assert result["figures"][0]["url"] == "https://example.org/real.png"
    assert result["source_url"] == item["source_url"]
    assert "fake.invalid" not in json.dumps(result)


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


def test_no_published_items_still_retains_filtered_review_after_model_failure(monkeypatch):
    filtered, failed = sample("world"), sample("world")
    failed["id"], failed["title"] = "failed", "One unavailable model result"
    monkeypatch.setattr(pipeline, "is_configured", lambda: True)
    monkeypatch.setattr(pipeline, "collect", lambda day, **kwargs: ([filtered, failed], [{"source": "test", "status": "ok"}]))
    monkeypatch.setattr(pipeline, "enrich", lambda value: value)
    def run(item, *args):
        if item["id"] == "failed":
            raise harness.HarnessUnavailable("private payload", stage="completion", finish_reason="max-tokens")
        return analysis(publish=False)
    monkeypatch.setattr(pipeline, "analyze_candidate", run)
    result = pipeline.generate_edition("2026-10-16", [], major_only=True)
    assert result["status"] == "partial" and result["items"] == []
    assert result["reviewed_ids"] == [filtered["id"]]
    assert "private payload" not in json.dumps(result["diagnostics"])


def test_all_source_failure_has_distinct_safe_error(monkeypatch):
    monkeypatch.setattr(pipeline, "is_configured", lambda: True)
    monkeypatch.setattr(pipeline, "collect", lambda day: ([], [{"source": "test", "status": "failed", "error": "TimeoutError"}]))
    with pytest.raises(pipeline.SourceCollectionUnavailable) as raised:
        pipeline.generate_edition("2026-10-16", [])
    assert harness.safe_error_details(raised.value) == {"error": "SourceCollectionUnavailable", "stage": "collect"}


def test_known_items_do_not_trigger_model_calls(monkeypatch):
    item = sample()
    monkeypatch.setattr(pipeline, "is_configured", lambda: True)
    monkeypatch.setattr(pipeline, "collect", lambda day: ([item], [{"source": "test", "status": "ok"}]))
    def fail(*args):
        raise AssertionError("Known item must not be fetched or billed")
    monkeypatch.setattr(pipeline, "enrich", fail)
    result = pipeline.generate_edition("2026-10-16", [], exclude_ids={item["id"]})
    assert result["status"] == "ready" and result["items"] == []


def test_partial_retry_only_analyzes_original_failed_candidates(monkeypatch):
    original, fresh = sample(), sample()
    fresh["id"], fresh["title"] = "fresh", "New unrelated candidate"
    monkeypatch.setattr(pipeline, "is_configured", lambda: True)
    monkeypatch.setattr(pipeline, "collect", lambda day: ([original, fresh], [{"source": "test", "status": "ok"}]))
    monkeypatch.setattr(pipeline, "enrich", lambda value: value)
    calls = []
    def run(item, *args):
        calls.append(item["id"])
        return analysis()
    monkeypatch.setattr(pipeline, "analyze_candidate", run)
    result = pipeline.generate_edition("2026-10-16", [], retry_ids={original["id"]})
    assert calls == [original["id"]]
    assert result["status"] == "ready" and len(result["items"]) == 1


def test_missing_partial_retry_stays_partial_without_fresh_billing(monkeypatch):
    monkeypatch.setattr(pipeline, "is_configured", lambda: True)
    monkeypatch.setattr(pipeline, "collect", lambda day: ([sample()], [{"source": "test", "status": "ok"}]))
    def fail(*args):
        raise AssertionError("Fresh candidates must not be billed on partial retry")
    monkeypatch.setattr(pipeline, "analyze_candidate", fail)
    result = pipeline.generate_edition("2026-10-16", [], retry_ids={"missing-previous-failure"})
    assert result["status"] == "partial" and result["items"] == []
    assert {"item": "missing-previous-failure", "status": "retry_missing", "error": "CandidateUnavailable"} in result["diagnostics"]


def test_partial_retry_all_filtered_finishes_without_filler(monkeypatch):
    original = sample()
    monkeypatch.setattr(pipeline, "is_configured", lambda: True)
    monkeypatch.setattr(pipeline, "collect", lambda day: ([original], [{"source": "test", "status": "ok"}]))
    monkeypatch.setattr(pipeline, "enrich", lambda value: value)
    monkeypatch.setattr(pipeline, "analyze_candidate", lambda *args: analysis(publish=False))
    result = pipeline.generate_edition("2026-10-16", [], retry_ids={original["id"]})
    assert result["status"] == "ready" and result["items"] == []
    assert result["reviewed_ids"] == [original["id"]]


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


def test_max_tokens_retries_once_with_larger_bounded_budget(monkeypatch, tmp_path):
    calls = []
    def run(prompt, home, **kwargs):
        calls.append((prompt, kwargs["max_tokens"]))
        if len(calls) == 1:
            raise harness.HarnessUnavailable("private partial response", stage="completion", finish_reason="max-tokens")
        return json.dumps(complete_analysis(), ensure_ascii=False)
    monkeypatch.setattr(harness, "_run", run)
    monkeypatch.setattr(harness, "runtime_root", lambda: tmp_path)
    assert harness.analyze_candidate(sample(), [], "2026-10-16")["publish"]
    assert [call[1] for call in calls] == [8000, 12000]
    assert "A real abstract" in calls[1][0] and "private partial response" not in calls[1][0]


@pytest.mark.parametrize("first_failure", ["max-tokens", "json"])
def test_token_and_json_recovery_share_two_attempt_limit(monkeypatch, tmp_path, first_failure):
    calls = []
    def run(*args, **kwargs):
        calls.append(kwargs["max_tokens"])
        if len(calls) == 1 and first_failure == "json":
            return '{"title":"unfinished'
        raise harness.HarnessUnavailable("truncated", stage="completion", finish_reason="max-tokens")
    monkeypatch.setattr(harness, "_run", run)
    monkeypatch.setattr(harness, "runtime_root", lambda: tmp_path)
    with pytest.raises(harness.HarnessUnavailable) as raised:
        harness.analyze_candidate(sample(), [], "2026-10-16")
    assert len(calls) == 2 and max(calls) <= 12000
    assert harness.safe_error_details(raised.value)["attempts"] == 2


def test_non_token_runtime_failure_is_not_automatically_retried(monkeypatch, tmp_path):
    calls = []
    def run(*args, **kwargs):
        calls.append(1)
        raise harness.HarnessUnavailable("request timeout", stage="run", exception_type="TimeoutError")
    monkeypatch.setattr(harness, "_run", run)
    monkeypatch.setattr(harness, "runtime_root", lambda: tmp_path)
    with pytest.raises(harness.HarnessUnavailable):
        harness.analyze_candidate(sample(), [], "2026-10-16")
    assert len(calls) == 1


def test_cleanup_rejects_uuid_symlink_without_deleting_target(tmp_path):
    target = tmp_path / str(uuid.uuid4())
    target.mkdir()
    marker = target / "session.txt"
    marker.write_text("keep", encoding="utf-8")
    link = tmp_path / str(uuid.uuid4())
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit test symlink creation")
    with pytest.raises(ValueError, match="symlink"):
        harness.cleanup_session(link)
    assert marker.read_text(encoding="utf-8") == "keep" and link.is_symlink()
