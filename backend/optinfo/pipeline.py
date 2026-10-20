"""Evidence-first selection and DeepSeek Harness editorial pipeline."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import hashlib
import os
from zoneinfo import ZoneInfo

from .catalog import COMPANIES, TOPICS, match_companies
from .db import canonical_source_keys
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
            "title": analysis["title"][:300], "original_title": candidate["title"],
            "summary": analysis["summary"][:1800], "published_at": candidate["published_at"],
            "briefing_date": edition_date, "source_url": candidate["source_url"], "source_name": candidate["source_name"],
            "doi": candidate.get("doi", ""), "journal": candidate.get("journal", ""), "company_ids": candidate.get("company_ids", []),
            "source_id": candidate.get("source_id", ""),
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
                       "description": "原文提及：" + quote,
                       "businesses": [], "listed": None, "listing": "暂无公开数据", "compensation": {"masters": None, "phd": None, "currency": "CNY", "basis": "暂无公开数据", "as_of": "", "sources": []},
                       "sources": [{"title": candidate["title"], "url": candidate["source_url"]}], "bookmarked": False})
    return result

def company_group(candidate: dict) -> str:
    """Known publisher/company first; unknown companies share their source budget."""
    company_ids = candidate.get("company_ids") or match_companies(candidate.get("title", "") + " " + candidate.get("evidence_text", ""))
    return "company:" + company_ids[0] if company_ids else "source:" + (candidate.get("source_id") or candidate.get("source_name") or "unknown")


def generate_edition(edition_date: str, topic_ids: list[str], *, major_only: bool = False,
                     exclude_ids: set[str] | None = None, exclude_keys: set[str] | None = None,
                     retry_ids: set[str] | None = None, industry_only: bool = False,
                     industry_limit: int = 4, company_counts: dict[str, int] | None = None) -> dict:
    if not is_configured():
        raise HarnessUnavailable("官方 DeepSeek Harness 尚未就绪，未生成替代内容。")
    if major_only and industry_only or not 1 <= industry_limit <= 8:
        raise ValueError("Invalid generation scope")
    sections = {"world"} if major_only else {"industry"} if industry_only else None
    candidates, diagnostics = collect(edition_date, sections=sections, topic_ids=topic_ids,
                                      industry_days=365 if retry_ids is not None else 7)
    retry_scope = set(retry_ids) - (exclude_ids or set()) if retry_ids is not None else None
    limits = {"research": 6, "industry": industry_limit, "world": 3}
    max_candidates = max(3, min(30, int(os.getenv("OPTINFO_MAX_CANDIDATES", "18"))))
    labels = [t["label"] for t in TOPICS if t["id"] in topic_ids]
    items, failures, companies, reviewed_ids, reviews = [], 0, {c["id"]: c for c in COMPANIES}, [], []
    known_keys = set(exclude_keys or ()) | {"id:" + item_id for item_id in (exclude_ids or ())}
    seen_keys, counts, group_attempts = set(), Counter(company_counts or {}), Counter()
    attempted, model_calls, model_failures, stopped, budget_exhausted = 0, 0, 0, False, False
    industry_windows = [365 if retry_scope is not None else 7] if not major_only else []

    def eligible(raw):
        keys = canonical_source_keys(raw)
        return not (keys & known_keys or keys & seen_keys)

    def process(raw):
        nonlocal failures, attempted, model_calls, model_failures, stopped
        source_keys = canonical_source_keys(raw)
        if source_keys & known_keys or source_keys & seen_keys:
            return
        seen_keys.update(source_keys)
        attempted += 1
        group = company_group(raw)
        group_attempts[group] += 1
        try:
            candidate = enrich(raw)
            candidate["company_ids"] = list(dict.fromkeys([*candidate.get("company_ids", []), *match_companies(candidate["title"] + " " + candidate.get("evidence_text", ""))]))
            aliases = canonical_source_keys(candidate) - source_keys
            duplicate = bool(aliases & known_keys or aliases & seen_keys)
            source_keys.update(aliases)
            seen_keys.update(source_keys)
            if duplicate:
                diagnostics.append({"item": candidate["id"], "status": "already_reviewed"})
                return
            if len(candidate.get("evidence_text", "")) < 100:
                if retry_scope is not None:
                    failures += 1
                diagnostics.append({"item": candidate["id"], "status": "retry_missing" if retry_scope is not None else "insufficient_evidence",
                                    "error": "InsufficientEvidence"})
                return
            require_paper_evidence(candidate)
            # Count editorial analyses separately from article retrieval attempts.
            # The Harness's existing bounded output-repair retries are unchanged.
            model_calls += 1
            analysis = analyze_candidate(candidate, labels, edition_date)
            item = sanitize_analysis(candidate, analysis, edition_date)
            reviewed_ids.append(candidate["id"])
            reviews.append({"item": candidate["id"], "keys": sorted(source_keys), "decision": "published" if item else "filtered"})
            if item:
                for company in discovered_companies(candidate, analysis):
                    companies[company["id"]] = company
                    item["company_ids"] = list(dict.fromkeys([*item["company_ids"], company["id"]]))
                if item["section"] == "industry":
                    counts[group] += 1
                    # Keep the true publication date; this is an explicitly older
                    # addition, not a claim that the news happened today.
                    try:
                        age = (date.fromisoformat(edition_date) - datetime.fromisoformat(item["published_at"].replace("Z", "+00:00")).date()).days
                        item["history_backfill"] = age > 7
                    except ValueError:
                        item["history_backfill"] = False
                items.append(item)
            diagnostics.append({"item": candidate["id"], "status": "published" if item else "filtered", "access": candidate.get("access_status", "")})
        except EvidencePending as exc:
            failures += 1
            diagnostics.append({"item": raw["id"], "status": "evidence_pending", "reason": exc.reason,
                                "access": raw.get("access_status", "")})
        except Exception as exc:
            failures += 1
            model_failures += 1
            diagnostics.append({"item": raw["id"], "status": "model_failed", **safe_error_details(exc)})
            if model_failures >= 3 and not items:
                stopped = True

    def section_count(section):
        return sum(item["section"] == section for item in items)

    def process_section(section):
        nonlocal budget_exhausted
        pool = sorted((raw for raw in candidates if raw["section"] == section and eligible(raw)),
                      key=lambda raw: (score_candidate(raw, topic_ids), raw["published_at"]), reverse=True)
        planned = pool[:limits[section] + 2]
        for index, raw in enumerate(planned):
            if stopped or attempted >= max_candidates or section_count(section) >= limits[section]:
                if not stopped and attempted >= max_candidates and section_count(section) < limits[section]:
                    budget_exhausted = True
                if stopped:
                    diagnostics.extend({"item": entry["id"], "status": "retry_deferred"}
                                       for entry in planned[index:index + max_candidates - attempted])
                break
            process(raw)

    if retry_scope is not None:
        # Existing partial editions never admit fresh candidates, even when their
        # industry sources need a longer lookback to recover the original IDs.
        observed = {raw["id"] for raw in candidates}
        missing = retry_scope - observed
        failures += len(missing)
        diagnostics.extend({"item": item_id, "status": "retry_missing", "error": "CandidateUnavailable"} for item_id in sorted(missing))
        for raw in candidates:
            if raw["id"] not in retry_scope or (sections and raw["section"] not in sections):
                continue
            if not eligible(raw):
                diagnostics.append({"item": raw["id"], "status": "already_reviewed"})
            elif industry_only and section_count("industry") >= industry_limit:
                diagnostics.append({"item": raw["id"], "status": "target_reached"})
            elif stopped or attempted >= max_candidates:
                failures += 1
                if not stopped and attempted >= max_candidates:
                    budget_exhausted = True
                diagnostics.append({"item": raw["id"], "status": "retry_deferred"})
            else:
                process(raw)
    else:
        if not major_only and not industry_only:
            process_section("research")
        if not major_only:
            industry_pool = {}

            def select_industry(per_company):
                nonlocal budget_exhausted
                while not stopped and section_count("industry") < industry_limit:
                    pool = [raw for raw in industry_pool.values() if eligible(raw) and counts[company_group(raw)] < per_company]
                    if not pool:
                        break
                    if attempted >= max_candidates:
                        budget_exhausted = True
                        break
                    # A source with many releases cannot monopolize the budget.
                    # Within each company the most recent release is read first.
                    pool.sort(key=lambda raw: (raw["published_at"], score_candidate(raw, topic_ids)), reverse=True)
                    pool.sort(key=lambda raw: (counts[company_group(raw)], group_attempts[company_group(raw)]))
                    process(pool[0])

            for window in (7, 30, 90, 365):
                if stopped or section_count("industry") >= industry_limit:
                    break
                if attempted >= max_candidates:
                    budget_exhausted = True
                    break
                if window == 7:
                    batch = candidates
                else:
                    batch, audit = collect(edition_date, sections={"industry"}, industry_days=window, topic_ids=topic_ids)
                    industry_windows.append(window)
                    diagnostics.extend({**entry, "industry_days": window} for entry in audit)
                for raw in batch:
                    if raw["section"] == "industry":
                        industry_pool.setdefault(raw["id"], raw)
                select_industry(1)
            # Only after exhausting distinct companies across all four windows
            # can a company contribute a second item. Never pad with invented news.
            select_industry(2)
        if not industry_only:
            process_section("world")
    if not any(entry["status"] == "ok" or
               (entry["status"] == "partial" and entry.get("count", 0) > 0) for entry in diagnostics):
        raise SourceCollectionUnavailable("All sources failed; existing editions remain unchanged")
    diagnostics.append({
        "source": "selection", "status": "budget_exhausted" if budget_exhausted else "selection_stats",
        "enrich_attempts": attempted, "model_calls": model_calls, "candidate_limit": max_candidates,
        "last_industry_days": industry_windows[-1] if industry_windows else None,
        "visited_industry_days": industry_windows,
        "unvisited_industry_days": [window for window in (7, 30, 90, 365) if window not in industry_windows]
        if not major_only and retry_scope is None else [],
    })
    return {"items": sorted(items, key=lambda i: (i["section"], -i["importance"])), "companies": list(companies.values()),
            "diagnostics": diagnostics, "status": "partial" if failures else "ready", "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "topic_ids": topic_ids, "reviewed_ids": reviewed_ids, "reviews": reviews}


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
