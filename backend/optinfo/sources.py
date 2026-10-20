"""Bounded, independent public-source adapters. No login or CAPTCHA bypass.

Feeds/metadata are collected first. Only shortlisted articles are opened; failed
sources never stop other feeds, and fetched dates are never invented as publication dates.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
import ipaddress
import json
import os
import re
import socket
import time
from urllib.parse import urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import feedparser
import httpx

from .catalog import JOURNALS, match_companies
from .paper_evidence import figure_candidates, figure_key, merge_figure_candidates, pdf_evidence, preserve_math, text_with_math, vector_figure_keys
from .media import MAX_IMAGE_BYTES, render_pdf_page, store_image

SHANGHAI = ZoneInfo("Asia/Shanghai")
USER_AGENT = "Optinfo/0.1 (+https://github.com/Leonids312/Optinfo; personal research digest)"
# Publisher-owned alternate observed from the canonical DOI resolver. Never
# derive/guess subscription URLs or accept arbitrary alternates from model text.
PUBLISHER_ALTERNATES = {
    "10.1063/5.0351260": "https://aipp.silverchair-cdn.com/article-minimal/3406441",
}
FEEDS = [
    {"id": "nature-photonics", "name": "Nature Photonics", "url": "https://www.nature.com/nphoton.rss", "section": "research", "journal": "Nature Photonics"},
    {"id": "light", "name": "Light: Science & Applications", "url": "https://www.nature.com/lsa.rss", "section": "research", "journal": "Light: Science & Applications"},
    {"id": "fed", "name": "Federal Reserve", "url": "https://www.federalreserve.gov/feeds/press_all.xml", "section": "world"},
    {"id": "ecb", "name": "European Central Bank", "url": "https://www.ecb.europa.eu/rss/press.html", "section": "world"},
    {"id": "bbc-world", "name": "BBC World", "url": "https://feeds.bbci.co.uk/news/world/rss.xml", "section": "world"},
    {"id": "cnbc-markets", "name": "CNBC Markets", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html", "section": "world"},
]
HTML_SOURCES = [
    {"id": "ndrc", "name": "国家发展和改革委员会", "url": "https://www.ndrc.gov.cn/xxgk/zcfb/tz/", "section": "world", "selector": "li > a[href^='./']", "date_parent": "li"},
    {"id": "pbc", "name": "中国人民银行", "url": "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html", "section": "world", "selector": "a[istitle]", "date_parent": "td"},
]

def plain_text(value: str, limit: int = 20000) -> str:
    return text_with_math(value, limit)

def public_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
            return False
        ips = {r[4][0] for r in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
        return bool(ips) and all(ipaddress.ip_address(ip).is_global for ip in ips)
    except (OSError, ValueError):
        return False

def fetch(url: str, max_bytes: int = 3_000_000, timeout: float = 18) -> tuple[str, str]:
    data, final_url, content_type = fetch_bytes(url, max_bytes, timeout)
    charset = re.search(r"charset=([\w-]+)", content_type, re.I)
    return data.decode(charset[1] if charset else "utf-8", errors="replace"), final_url


def fetch_bytes(url: str, max_bytes: int = 3_000_000, timeout: float = 18) -> tuple[bytes, str, str]:
    # Validate each redirect so an untrusted feed cannot redirect into local services.
    for attempt in range(2):
        try:
            current = url
            with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=False, trust_env=not (urlparse(url).hostname or "").endswith(".gov.cn")) as client:
                for _ in range(5):
                    if not public_url(current):
                        raise ValueError("Source URL must resolve to a public host")
                    with client.stream("GET", current) as response:
                        if response.is_redirect:
                            current = urljoin(current, response.headers["location"])
                            continue
                        response.raise_for_status()
                        chunks = bytearray()
                        for chunk in response.iter_bytes():
                            chunks.extend(chunk)
                            if len(chunks) > max_bytes:
                                raise ValueError("Source response exceeds size limit")
                        return bytes(chunks), current, response.headers.get("content-type", "")
                raise ValueError("Too many source redirects")
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            if attempt or isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                raise
            time.sleep(0.5)
    raise RuntimeError("Source retrieval failed")

def parse_date(value) -> str:
    if not value:
        return ""
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return datetime(*list(value)[:6], tzinfo=timezone.utc).isoformat()
        except ValueError:
            return ""
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None).isoformat() + "+00:00" if re.fullmatch(r"\d{4}-\d\d-\d\d", text) else datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        try:
            return parsedate_to_datetime(text).isoformat()
        except (ValueError, TypeError):
            return ""

def candidate(title: str, url: str, source: str, section: str, published: str, text: str, **extras) -> dict:
    doi = extras.get("doi", "")
    stable = doi.lower().strip() or re.sub(r"v\d+$", "", url.split("?")[0].rstrip("/"))
    data = dict(id=hashlib.sha256(stable.encode()).hexdigest()[:24], title=plain_text(title, 400), source_url=url,
                source_name=source, section=section, kind={"research": "paper", "industry": "company", "world": "major"}[section],
                published_at=published, evidence_text=plain_text(text), evidence_level="abstract" if section == "research" else "feed",
                journal="", doi="", figures=[], company_ids=match_companies(title + " " + text))
    data.update(extras)
    return data

def recent(item: dict, edition: date, days: int = 7) -> bool:
    try:
        published = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return edition - timedelta(days=days) <= published.astimezone(SHANGHAI).date() <= edition
    except (ValueError, KeyError):
        # Missing dates do not become supposedly "latest" entries.
        return False

def collect_feed(spec: dict, edition: date) -> list[dict]:
    body, _ = fetch(spec["url"])
    feed = feedparser.parse(body)
    result = []
    for entry in feed.entries[:35]:
        link = entry.get("link", "")
        if not link.startswith(("https://", "http://")):
            continue
        text = entry.get("summary", "") or entry.get("description", "")
        published = parse_date(entry.get("published_parsed") or entry.get("updated_parsed") or entry.get("published") or entry.get("updated") or entry.get("dc_date"))
        doi = entry.get("prism_doi", "")
        item = candidate(entry.get("title", ""), link, spec["name"], spec["section"], published, text, journal=spec.get("journal", ""), doi=doi)
        if spec.get("company"):
            item["company_ids"] = list(set(item["company_ids"] + [spec["company"]]))
        if recent(item, edition) and item["title"]:
            result.append(item)
    return result

def collect_arxiv(edition: date) -> list[dict]:
    query = '(cat:physics.optics OR cat:cond-mat.mtrl-sci OR cat:cond-mat.mes-hall) AND (all:ultrafast OR all:"free-electron laser" OR all:"radiation damage" OR all:"silicon carbide" OR all:"electron-phonon" OR all:"pump-probe")'
    url = "https://export.arxiv.org/api/query?" + urlencode({"search_query": query, "sortBy": "submittedDate", "sortOrder": "descending", "max_results": 35})
    body, _ = fetch(url, timeout=30)
    result = []
    for entry in feedparser.parse(body).entries:
        link = entry.get("id", "").replace("http://", "https://")
        item = candidate(entry.get("title", ""), link, "arXiv", "research", parse_date(entry.get("published")), entry.get("summary", ""), journal="arXiv（预印本）", doi=entry.get("arxiv_doi", ""))
        if recent(item, edition):
            result.append(item)
    return result

def collect_crossref(journal: dict, edition: date) -> list[dict]:
    params = {"filter": f"from-pub-date:{edition - timedelta(days=7)},until-pub-date:{edition}", "rows": 6, "sort": "published", "order": "desc", "select": "DOI,title,URL,abstract,published,container-title,type"}
    url = f"https://api.crossref.org/journals/{journal['issn']}/works?" + urlencode(params)
    body, _ = fetch(url)
    result = []
    for entry in json.loads(body).get("message", {}).get("items", []):
        if entry.get("type") != "journal-article":
            continue
        parts = entry.get("published", {}).get("date-parts", [[]])[0]
        if len(parts) != 3:
            continue
        abstract = entry.get("abstract", "")
        item = candidate(" ".join(entry.get("title", [])), entry.get("URL", ""), "Crossref", "research", parse_date("-".join(str(n).zfill(2) for n in parts)), abstract,
                         journal=journal["name"], doi=entry.get("DOI", ""), evidence_level="abstract" if abstract else "metadata")
        if recent(item, edition):
            result.append(item)
    return result

def list_date(text: str) -> str:
    match = re.search(r"(20\d{2})[./年-](\d{1,2})[./月-](\d{1,2})", text)
    if match:
        y, m, d = map(int, match.groups())
        try:
            return datetime(y, m, d, tzinfo=SHANGHAI).isoformat()
        except ValueError:
            return ""
    match = re.search(r"(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, 20\d{2}", text)
    return datetime.strptime(match[0], "%B %d, %Y").replace(tzinfo=timezone.utc).isoformat() if match else ""

def collect_html_list(spec: dict, edition: date) -> list[dict]:
    body, final_url = fetch(spec["url"])
    soup = BeautifulSoup(body, "html.parser")
    result, seen = [], set()
    for link in soup.select(spec["selector"])[:35]:
        url = urljoin(final_url, link.get("href", ""))
        if url in seen or urlparse(url).hostname != urlparse(final_url).hostname:
            continue
        seen.add(url)
        container = link.find_parent(spec.get("date_parent", "article"))
        if not container:
            container = link.parent.parent
        published = list_date(container.get_text(" ", strip=True))
        title = link.get("title") or link.get_text(" ", strip=True)
        # Never extract dates from URL paths: publishing date may differ.
        item = candidate(title, url, spec["name"], spec["section"], published, "", evidence_level="metadata")
        if spec.get("company"):
            item["company_ids"] = [spec["company"]]
        if recent(item, edition):
            result.append(item)
    return result

def collect(edition_date: str, sections: set[str] | None = None, *, industry_days: int = 7,
            topic_ids: list[str] | None = None) -> tuple[list[dict], list[dict]]:
    from .industry_sources import collect_industry
    from .journal_sources import collect_journals
    edition = date.fromisoformat(edition_date)
    sections = sections or {"research", "industry", "world"}
    # Company adapters own their history windows and official-site pagination.
    # Keep public/news RSS separate so industry-only widening never recollects
    # research, policy or markets.
    jobs = [(spec["id"], collect_feed, (spec, edition)) for spec in FEEDS if spec["section"] in sections and spec["section"] != "industry"]
    jobs.extend((spec["id"], collect_html_list, (spec, edition)) for spec in HTML_SOURCES if spec["section"] in sections and spec["section"] != "industry")
    if "research" in sections:
        jobs.append(("arxiv", collect_arxiv, (edition,)))
        jobs.append(("journal-collector", collect_journals, (edition, topic_ids)))
    if "industry" in sections:
        jobs.append(("industry-collector", collect_industry, (edition, industry_days)))
    diagnostics, found = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fn, *args): name for name, fn, args in jobs}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
                if name in {"journal-collector", "industry-collector"}:
                    items, details = result
                    found.extend(items)
                    diagnostics.extend(details)
                    continue
                items = result
                found.extend(items)
                diagnostics.append({"source": name, "status": "ok", "count": len(items)})
            except Exception as exc:
                diagnostics.append({"source": name, "status": "failed", "error": type(exc).__name__})
    unique = {}
    for item in found:
        key = item.get("doi", "").lower() or item["id"]
        if key not in unique or len(item["evidence_text"]) > len(unique[key]["evidence_text"]):
            unique[key] = item
    return list(unique.values()), sorted(diagnostics, key=lambda d: d["source"])

def enrich(item: dict) -> dict:
    """Read one shortlisted HTML article; keep abstract on any access failure.

    arXiv HTML provides actual figures with captions. Publisher pages are accepted
    as fulltext only when a real article body is present (never a subscription preview).
    """
    result = dict(item, figure_candidates=[], figures=[], figure_coverage="complete")
    vector_keys = set()
    # Repair must prove access anew. An old full-text label is not evidence of
    # a successful retrieval today and must not suppress fresh PDF extraction.
    if result.get("evidence_level") in {"fulltext", "fulltext_pdf"}:
        result.update(evidence_level="metadata", evidence_text="", math_extraction="unavailable")
        result.pop("evidence_url", None)
        result.pop("pdf_source_url", None)
        result.pop("_pdf_bytes", None)
    url = item["source_url"]
    arxiv_match = re.search(r"arxiv\.org/abs/(\d{4}\.\d{4,5}(?:v\d+)?)", url)
    pdf_urls = ["https://arxiv.org/pdf/" + arxiv_match.group(1)] if arxiv_match else []
    if arxiv_match:
        url = "https://arxiv.org/html/" + arxiv_match.group(1)
    # DOI resolution and public article redirects are bounded by fetch().
    try:
        try:
            body, final_url = fetch(url, max_bytes=4_000_000, timeout=20)
        except Exception:
            alternate = PUBLISHER_ALTERNATES.get(item.get("doi", "").lower())
            if not alternate:
                raise
            body, final_url = fetch(alternate, max_bytes=4_000_000, timeout=20)
        soup = BeautifulSoup(body, "html.parser")
        for meta in soup.select('meta[name="citation_pdf_url"], meta[name="dc.identifier"]'):
            value = urljoin(final_url, meta.get("content", ""))
            if value.startswith("https://") and ("pdf" in value.lower() or meta.get("name") == "citation_pdf_url"):
                pdf_urls.append(value)
        math_count = preserve_math(soup)
        for node in soup.select("script,style,nav,header,footer,aside,form"):
            node.decompose()
        blocked = any(marker in soup.get_text(" ", strip=True)[:5000].lower() for marker in ("verify you are human", "access denied", "captcha", "checking your browser"))
        if blocked:
            result["access_status"] = "verification_required"
            raise ValueError("Article verification required")
        if arxiv_match:
            article = soup.select_one("article.ltx_document, .ltx_document")
        elif item["section"] == "research":
            article = soup.select_one("[data-title='Main'], #Main-content, .article-body, .hlFld-Fulltext")
            if article is None and soup.select_one("article .c-article-section__content"):
                nature_article = soup.select_one("article")
                headings = [node.get_text(" ", strip=True).casefold() for node in nature_article.select("h2, h3")]
                substantive = sum(bool(re.match(r"^(?:\d+[.\s]+)?(?:results?|discussion|methods?|materials? and methods?|conclusions?|introduction|main)\b", text)) for text in headings)
                preview = re.search(r"(?:preview of subscription content|access this article through your institution|subscribe to read|purchase this article)", nature_article.get_text(" ", strip=True), re.I)
                # Abstracts and graphical previews can be long and have figures.
                # Require real full-text section headings as independent evidence.
                if substantive >= 2 and not preview:
                    article = nature_article
            if article is None:
                article = soup.select_one(".widget-ArticleFulltext")
        else:
            article = soup.select_one("article, .article-body, .release-body, .main-body, #article, .TRS_Editor, #zoom, .news_detail, .news-detail, .detail_content, .content-area")
        if article and len(article.get_text(" ", strip=True)) > (1200 if item["section"] == "research" else 200):
            result["evidence_text"] = article.get_text(" ", strip=True)[:42000]
            result["evidence_level"] = "fulltext" if item["section"] == "research" else "source_article"
            result["access_status"] = "accessible"
            result["evidence_url"] = final_url
            result["figure_candidates"] = figure_candidates(article, final_url)
            vector_keys = vector_figure_keys(article)
            if any(key.startswith("main:") or key == "unlabelled" for key in vector_keys):
                result["figure_coverage"] = "missing_main_figures"
            result["math_extraction"] = "latex_preserved" if math_count else "no_embedded_math"
        else:
            # Abstract extraction is useful for Crossref metadata-only candidates.
            abstract = soup.select_one("#Abs1-content, .abstract, #abstract, .c-article-section__content")
            if abstract and len(abstract.get_text(" ", strip=True)) > len(result["evidence_text"]):
                result["evidence_text"] = abstract.get_text(" ", strip=True)[:8000]
                result["evidence_level"] = "abstract" if item["section"] == "research" else "source_excerpt"
            result["access_status"] = "abstract_only" if item["section"] == "research" else "feed_only"
    except Exception as exc:
        result["access_status"] = "unavailable_" + type(exc).__name__
    if item["section"] == "research" and (result.get("evidence_level") != "fulltext" or not result["figure_candidates"] or vector_keys):
        for pdf_url in list(dict.fromkeys(pdf_urls))[:2]:
            try:
                data, pdf_final, _ = fetch_bytes(pdf_url, max_bytes=25_000_000, timeout=30)
                evidence = pdf_evidence(data, pdf_final)
                if len(evidence["evidence_text"]) < 1200:
                    raise ValueError("PDF has insufficient extractable text")
                if result.get("evidence_level") == "fulltext":
                    result["figure_candidates"] = merge_figure_candidates(result["figure_candidates"], evidence["figure_candidates"], vector_keys)
                    result["_pdf_bytes"] = evidence["_pdf_bytes"]
                    result["pdf_source_url"] = pdf_final
                    pdf_keys = {figure_key(figure.get("label", "") or figure["original_caption"])
                                for figure in result["figure_candidates"] if figure.get("kind") == "pdf_page"}
                    required = {key for key in vector_keys if key.startswith("main:") or key == "unlabelled"}
                    result["figure_coverage"] = "complete" if required <= pdf_keys else "missing_main_figures"
                else:
                    result.update(evidence)
                if result["figure_candidates"]:
                    break
            except Exception as exc:
                result["pdf_status"] = "unavailable_" + type(exc).__name__
    return result


def materialize_figure(candidate: dict, figure: dict) -> str:
    if figure.get("kind") == "pdf_page":
        data = candidate.get("_pdf_bytes")
        if not isinstance(data, bytes):
            raise ValueError("Missing verified source PDF")
        return store_image(render_pdf_page(data, int(figure["page"])))
    data, _, content_type = fetch_bytes(figure["url"], max_bytes=MAX_IMAGE_BYTES, timeout=20)
    if not content_type.lower().startswith("image/"):
        raise ValueError("Original figure is not an image")
    return store_image(data)
