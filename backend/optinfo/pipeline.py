"""Evidence-first selection and DeepSeek Harness editorial pipeline."""
from __future__ import annotations

from datetime import datetime
import hashlib
import os
import re
from zoneinfo import ZoneInfo

from .catalog import COMPANIES, TOPICS, match_companies
from .harness import HarnessUnavailable, analyze_candidate, chinese_caption, is_configured, safe_error_details
from .sources import collect, enrich, materialize_figure


class SourceCollectionUnavailable(RuntimeError):
    stage = "collect"

class EvidencePending(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)

def require_paper_evidence(candidate: dict):
    if candidate["section"] != "research":
        return
    if candidate.get("evidence_level") not in {"fulltext", "fulltext_pdf"}:
        raise EvidencePending("pending_fulltext")
    if not candidate.get("figure_candidates") or candidate.get("figure_coverage") == "missing_main_figures":
        raise EvidencePending("pending_figures")

def selected_figures(candidate: dict, analysis: dict) -> list[dict]:
    if candidate["section"] != "research":
        return []
    require_paper_evidence(candidate)
    available = {figure["id"]: figure for figure in candidate["figure_candidates"]}
    chosen = analysis.get("selected_figures", [])
    if not isinstance(chosen, list) or not 1 <= len(chosen) <= 6:
        raise EvidencePending("pending_figures")
    result, seen = [], set()
    for selection in chosen:
        if not isinstance(selection, dict) or selection.get("id") not in available or selection["id"] in seen:
            raise EvidencePending("pending_figures")
        caption, reason = selection.get("caption", ""), selection.get("selection_reason", "")
        if (selection.get("role") != "result" or not isinstance(caption, str) or not isinstance(reason, str)
                or not chinese_caption(caption) or not chinese_caption(reason)):
            raise EvidencePending("pending_figures")
        original = available[selection["id"]]
        try:
            image_path = materialize_figure(candidate, original)
        except Exception:
            raise EvidencePending("pending_figures") from None
        figure = {key: original[key] for key in ("id", "url", "source_url", "kind", "page", "label", "original_caption") if key in original}
        if original.get("kind") == "pdf_page":
            caption = f"原文第{original['page']}页（含{original.get('label', '结果图')}）。" + caption
        figure.update(caption=caption[:2600], selection_reason=reason[:1000], image_path=image_path)
        result.append(figure)
        seen.add(selection["id"])
    return result

def score_candidate(item: dict, topic_ids: list[str]) -> float:
    haystack = " ".join(str(item.get(k, "")) for k in ("title", "evidence_text", "journal")).casefold()
    selected = set(topic_ids)
    score = 1.0
    for topic in TOPICS:
        if topic["section"] == item["section"] and any(keyword.casefold() in haystack for keyword in topic["keywords"]):
            score += 4 if topic["id"] in selected else 0.25
    if item.get("evidence_level") == "metadata":
        score -= 1
    return score

def sanitize_analysis(candidate: dict, analysis: dict, edition_date: str) -> dict | None:
    if analysis.get("publish") is not True:
        return None
    try:
        importance = max(0, min(100, int(analysis.get("importance", 0))))
    except (ValueError, TypeError):
        return None
    if candidate["section"] == "world" and importance < 80:
        return None
    content = analysis.get("content")
    if not isinstance(content, dict) or not all(isinstance(content.get(k), str) for k in ("background", "approach", "results", "assessment")):
        return None
    if not isinstance(analysis.get("summary"), str) or not isinstance(analysis.get("title"), str) or not analysis["summary"].strip() or not analysis["title"].strip():
        return None
    clean_content = {k: content[k][:9000] for k in ("background", "approach", "results", "assessment")}
    figures = selected_figures(candidate, analysis)
    return {"id": candidate["id"], "kind": candidate["kind"], "section": candidate["section"],
            "title": analysis["title"][:300], "summary": analysis["summary"][:1800], "published_at": candidate["published_at"],
            "briefing_date": edition_date, "source_url": candidate["source_url"], "source_name": candidate["source_name"],
            "doi": candidate.get("doi", ""), "journal": candidate.get("journal", ""), "company_ids": candidate.get("company_ids", []),
            "tags": [str(t)[:80] for t in analysis.get("tags", [])[:8]] if isinstance(analysis.get("tags"), list) else [],
            "importance": importance, "content": clean_content, "figures": figures,
            "content_revision": 2, "analysis_status": "complete", "math_status": "complete",
            "math_extraction": candidate.get("math_extraction", ""),
            "sources": [{"title": candidate["title"], "url": candidate["source_url"]}] + ([{"title": "原文正文与图表", "url": candidate["evidence_url"]}] if candidate.get("evidence_url") and candidate["evidence_url"] != candidate["source_url"] else []),
            "evidence_level": candidate["evidence_level"], "evidence_text": candidate.get("evidence_text", ""), "bookmarked": False}

def discovered_companies(candidate: dict, analysis: dict) -> list[dict]:
    evidence = " ".join((candidate["title"] + " " + candidate.get("evidence_text", "")).split())
    result = []
    mentions = analysis.get("company_mentions", [])
    if not isinstance(mentions, list):
        return result
    for entity in mentions[:6]:
        if not isinstance(entity, dict):
            continue
        name = str(entity.get("name", "")).strip()[:100]
        quote = " ".join(str(entity.get("evidence_quote", "")).split())[:500]
        known = any(name.casefold() in [str(alias).casefold() for alias in (c["name"], c["name_en"], *c.get("aliases", []))] for c in COMPANIES)
        if len(name) < 2 or len(quote) < 12 or name not in quote or quote not in evidence or known:
            continue
        result.append({"id": "company-" + hashlib.sha256(name.casefold().encode()).hexdigest()[:16],
                       "name": name, "name_en": name if name.isascii() else "", "logo_url": "", "website": "",
                       "description": "从资讯中发现的企业，详细业务与企业身份待进一步核实。原文提及：" + quote,
                       "businesses": [], "listed": None, "listing": "待核实", "compensation": {"masters": None, "phd": None, "currency": "CNY", "basis": "暂无可靠公开数据", "as_of": "", "sources": []},
                       "sources": [{"title": candidate["title"], "url": candidate["source_url"]}], "bookmarked": False})
    return result

def generate_edition(edition_date: str, topic_ids: list[str], *, major_only: bool = False,
                     exclude_ids: set[str] | None = None, retry_ids: set[str] | None = None) -> dict:
    if not is_configured():
        raise HarnessUnavailable("官方 DeepSeek Harness 尚未就绪，未生成替代内容。")
    candidates, diagnostics = collect(edition_date, sections={"world"}) if major_only else collect(edition_date)
    candidates = [c for c in candidates if c["id"] not in (exclude_ids or set())]
    retry_scope = set(retry_ids) - (exclude_ids or set()) if retry_ids is not None else None
    missing = set()
    if retry_scope is not None:
        # A partial edition retries its original failures only. Otherwise one bad
        # item can cause fresh articles and charges on every subsequent retry.
        candidates = [c for c in candidates if c["id"] in retry_scope]
        missing = retry_scope - {c["id"] for c in candidates}
        diagnostics.extend({"item": item_id, "status": "retry_missing", "error": "CandidateUnavailable"}
                           for item_id in sorted(missing))
    if not any(d["status"] == "ok" for d in diagnostics):
        raise SourceCollectionUnavailable("All sources failed; existing editions remain unchanged")
    limits = {"research": 6, "industry": 4, "world": 3}
    # Hard bound limits cost and article fetching; source failures remain visible.
    max_candidates = max(3, min(30, int(os.getenv("OPTINFO_MAX_CANDIDATES", "18"))))
    ranked = []
    for section in (("world",) if major_only else ("research", "industry", "world")):
        pool = sorted([c for c in candidates if c["section"] == section], key=lambda c: (score_candidate(c, topic_ids), c["published_at"]), reverse=True)
        ranked.extend(pool if retry_scope is not None else pool[: limits[section] + 2])
    deferred = ranked[max_candidates:] if retry_scope is not None else []
    diagnostics.extend({"item": item["id"], "status": "retry_deferred"} for item in deferred)
    ranked = ranked[:max_candidates]
    labels = [t["label"] for t in TOPICS if t["id"] in topic_ids]
    items, failures, companies, reviewed_ids = [], len(missing) + len(deferred), {c["id"]: c for c in COMPANIES}, []
    seen_titles = set()
    model_failures = 0
    for index, raw in enumerate(ranked):
        if retry_scope is None and sum(i["section"] == raw["section"] for i in items) >= limits[raw["section"]]:
            continue
        title_key = re.sub(r"\W+", "", raw["title"]).casefold()
        if retry_scope is None and title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        candidate = enrich(raw)
        candidate["company_ids"] = list(dict.fromkeys([*candidate.get("company_ids", []), *match_companies(candidate["title"] + " " + candidate.get("evidence_text", ""))]))
        if len(candidate.get("evidence_text", "")) < 100:
            if retry_scope is not None:
                failures += 1
            diagnostics.append({"item": candidate["id"], "status": "retry_missing" if retry_scope is not None else "insufficient_evidence",
                                "error": "InsufficientEvidence"})
            continue
        try:
            require_paper_evidence(candidate)
            analysis = analyze_candidate(candidate, labels, edition_date)
            item = sanitize_analysis(candidate, analysis, edition_date)
            reviewed_ids.append(candidate["id"])
            if item:
                for company in discovered_companies(candidate, analysis):
                    companies[company["id"]] = company
                    item["company_ids"] = list(dict.fromkeys([*item["company_ids"], company["id"]]))
                items.append(item)
            diagnostics.append({"item": candidate["id"], "status": "published" if item else "filtered", "access": candidate.get("access_status", "")})
        except EvidencePending as exc:
            failures += 1
            diagnostics.append({"item": candidate["id"], "status": "evidence_pending", "reason": exc.reason,
                                "access": candidate.get("access_status", "")})
        except Exception as exc:
            failures += 1
            model_failures += 1
            diagnostics.append({"item": candidate["id"], "status": "model_failed", **safe_error_details(exc)})
            # Avoid billing/retrying an outage for the whole candidate set.
            if model_failures >= 3 and not items:
                diagnostics.extend({"item": item["id"], "status": "retry_deferred"} for item in ranked[index + 1:])
                break
    # Preserve successful filtered decisions even when nothing is published;
    # a single transient model failure must not rebill them on the next hourly run.
    return {"items": sorted(items, key=lambda i: (i["section"], -i["importance"])), "companies": list(companies.values()),
            "diagnostics": diagnostics, "status": "partial" if failures else "ready", "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "topic_ids": topic_ids, "reviewed_ids": reviewed_ids}


def repair_papers(db, topic_ids: list[str], *, item_ids: set[str] | None = None, limit: int = 20, force: bool = False) -> dict:
    """Re-read existing paper sources without adding articles or changing IDs."""
    if not is_configured():
        raise HarnessUnavailable("官方 DeepSeek Harness 尚未就绪，未改写论文内容。")
    labels = [topic["label"] for topic in TOPICS if topic["id"] in topic_ids]
    originals = {item["id"]: item for item in db.query_items() if item.get("section") == "research"}
    if item_ids is not None and item_ids - originals.keys():
        raise ValueError("Unknown research item requested")
    result = []
    for item_id in sorted(originals):
        if item_ids is not None and item_id not in item_ids:
            continue
        original = db.get_item(item_id)
        if not force and original.get("content_revision", 0) >= 2 and original.get("analysis_status") in {"complete", "pending_math"}:
            continue
        if len(result) >= max(1, min(limit, 50)):
            break
        at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
        # Even a crash must not leave old English-only/no-figure content labelled
        # as a complete current analysis. Identity and private user data stay put.
        db.repair_item_content(item_id, {"analysis_status": "pending_analysis", "figures": [], "repair_reason": "evidence_upgrade", "repaired_at": at})
        try:
            candidate = enrich(original)
            require_paper_evidence(candidate)
            analysis = analyze_candidate(candidate, labels, original["briefing_date"])
            # Editorial filtering cannot delete an already saved item; it remains
            # visibly pending until a valid evidence-backed repair is available.
            repaired = sanitize_analysis(candidate, analysis, original["briefing_date"])
            if repaired is None:
                raise EvidencePending("pending_analysis")
            patch = {key: repaired[key] for key in ("title", "summary", "content", "figures", "sources", "evidence_level", "evidence_text", "content_revision", "analysis_status", "math_extraction", "math_status")}
            patch.update(repair_reason="", repaired_at=at)
            snapshots = db.repair_item_content(item_id, patch)
            result.append({"item": item_id, "status": "repaired", "snapshots": snapshots, "figures": len(patch["figures"])})
        except EvidencePending as exc:
            db.repair_item_content(item_id, {"analysis_status": exc.reason, "repair_reason": exc.reason, "repaired_at": at})
            result.append({"item": item_id, "status": "evidence_pending", "reason": exc.reason})
        except Exception as exc:
            result.append({"item": item_id, "status": "model_failed", **safe_error_details(exc)})
    return {"status": "partial" if any(entry["status"] != "repaired" for entry in result) else "ready", "items": result}
