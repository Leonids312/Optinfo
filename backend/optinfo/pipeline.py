"""Evidence-first selection and DeepSeek Harness editorial pipeline."""
from __future__ import annotations

from datetime import datetime
import hashlib
import os
import re
from zoneinfo import ZoneInfo

from .catalog import COMPANIES, TOPICS, match_companies
from .harness import HarnessUnavailable, analyze_candidate, is_configured, safe_error_details
from .sources import collect, enrich

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
    if candidate["section"] == "research" and candidate.get("evidence_level") != "fulltext":
        clean_content["assessment"] += "\n证据范围：基于摘要，未取得全文；未提供原文结果图时不展示推测图。"
    return {"id": candidate["id"], "kind": candidate["kind"], "section": candidate["section"],
            "title": analysis["title"][:300], "summary": analysis["summary"][:1800], "published_at": candidate["published_at"],
            "briefing_date": edition_date, "source_url": candidate["source_url"], "source_name": candidate["source_name"],
            "doi": candidate.get("doi", ""), "journal": candidate.get("journal", ""), "company_ids": candidate.get("company_ids", []),
            "tags": [str(t)[:80] for t in analysis.get("tags", [])[:8]] if isinstance(analysis.get("tags"), list) else [],
            "importance": importance, "content": clean_content, "figures": candidate.get("figures", []),
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

def generate_edition(edition_date: str, topic_ids: list[str], *, major_only: bool = False, exclude_ids: set[str] | None = None) -> dict:
    if not is_configured():
        raise HarnessUnavailable("官方 DeepSeek Harness 尚未就绪，未生成替代内容。")
    candidates, diagnostics = collect(edition_date, sections={"world"}) if major_only else collect(edition_date)
    candidates = [c for c in candidates if c["id"] not in (exclude_ids or set())]
    if not any(d["status"] == "ok" for d in diagnostics):
        raise RuntimeError("All sources failed; existing editions remain unchanged")
    limits = {"research": 6, "industry": 4, "world": 3}
    # Hard bound limits cost and article fetching; source failures remain visible.
    max_candidates = max(3, min(30, int(os.getenv("OPTINFO_MAX_CANDIDATES", "18"))))
    ranked = []
    for section in (("world",) if major_only else ("research", "industry", "world")):
        pool = sorted([c for c in candidates if c["section"] == section], key=lambda c: (score_candidate(c, topic_ids), c["published_at"]), reverse=True)
        ranked.extend(pool[: limits[section] + 2])
    ranked = ranked[:max_candidates]
    labels = [t["label"] for t in TOPICS if t["id"] in topic_ids]
    items, failures, companies, reviewed_ids = [], 0, {c["id"]: c for c in COMPANIES}, []
    seen_titles = set()
    for raw in ranked:
        if sum(i["section"] == raw["section"] for i in items) >= limits[raw["section"]]:
            continue
        title_key = re.sub(r"\W+", "", raw["title"]).casefold()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        candidate = enrich(raw)
        candidate["company_ids"] = list(dict.fromkeys([*candidate.get("company_ids", []), *match_companies(candidate["title"] + " " + candidate.get("evidence_text", ""))]))
        if len(candidate.get("evidence_text", "")) < 100:
            diagnostics.append({"item": candidate["id"], "status": "insufficient_evidence"})
            continue
        try:
            analysis = analyze_candidate(candidate, labels, edition_date)
            reviewed_ids.append(candidate["id"])
            item = sanitize_analysis(candidate, analysis, edition_date)
            if item:
                for company in discovered_companies(candidate, analysis):
                    companies[company["id"]] = company
                    item["company_ids"] = list(dict.fromkeys([*item["company_ids"], company["id"]]))
                items.append(item)
            diagnostics.append({"item": candidate["id"], "status": "published" if item else "filtered", "access": candidate.get("access_status", "")})
        except Exception as exc:
            failures += 1
            diagnostics.append({"item": candidate["id"], "status": "model_failed", **safe_error_details(exc)})
            # Avoid billing/retrying an outage for the whole candidate set.
            if failures >= 3 and not items:
                raise HarnessUnavailable("Harness 连续失败；保留已有简报，等待下一次重试。") from None
    if not items and failures:
        raise RuntimeError("No evidence-backed items produced; existing editions remain unchanged")
    return {"items": sorted(items, key=lambda i: (i["section"], -i["importance"])), "companies": list(companies.values()),
            "diagnostics": diagnostics, "status": "partial" if failures else "ready", "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "topic_ids": topic_ids, "reviewed_ids": reviewed_ids}
