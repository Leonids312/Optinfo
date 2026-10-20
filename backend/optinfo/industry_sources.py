"""Verified company-owned news adapters with bounded historical collection.

Only explicit publication dates are used. List-card dates belong to that card;
missing days are never derived from a URL, image filename, or collection time.
Verified against official sites on 2026-10-20. See docs/INDUSTRY_SOURCES.md.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
import re
import threading
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup
import feedparser

WINDOWS = (7, 30, 90, 365)
# Same-day widening reuses parsed metadata instead of repeatedly fetching feeds.
_CACHE: OrderedDict = OrderedDict()
_CACHE_LOCK = threading.Lock()
CACHE_TTL = 900
CACHE_PAGES = 96


def source(id, name, company, category, url, **options):
    return dict(id=id, name=name, company=company, category=category,
                url=url, kind="html", hosts=[urlparse(url).hostname], **options)


INDUSTRY_SOURCES = [
    source("nvidia", "NVIDIA Newsroom", "nvidia", "design", "https://nvidianews.nvidia.com/releases.xml",
           parser="rss", extra_hosts=["blogs.nvidia.com"]),
    source("amd", "AMD Newsroom", "amd", "design", "https://ir.amd.com/news-events/press-releases/rss", parser="rss"),
    source("intel", "Intel Newsroom", "intel", "design", "https://www.intel.com/content/www/us/en/newsroom/home.html",
           cards=".cmp-teaser", link="a.cmp-teaser__link", title=".cmp-teaser__title", published=".cmp-teaser__description"),
    source("arm", "Arm Newsroom", "arm", "design", "https://newsroom.arm.com/news/feed/", parser="rss", feed_paging=True),
    source("tsmc", "TSMC 官方新闻", "tsmc", "foundry", "https://pr.tsmc.com/english/latest-news",
           cards="article.articleItem-body", link="a[rel=bookmark]", title="h2", published="time",
           archive="https://pr.tsmc.com/english/news-archives", next=".pager a"),
    source("smic", "中芯国际官方公告", "smic", "foundry", "https://www.smics.com/en/site/news",
           cards="tr", link="a[href*='/site/news_read/']", title=".title .t", published="td.date", years="a[href*='news?year=']"),
    source("samsung-semiconductors", "Samsung 半导体官方新闻", "samsung", "memory",
           "https://news.samsung.com/global/category/technology/semiconductors/feed", parser="rss", feed_paging=True),
    source("sk-hynix", "SK hynix Newsroom", "sk-hynix", "memory", "https://news.skhynix.com/en/feed/", parser="rss", feed_paging=True),
    source("micron", "Micron Newsroom", "micron", "memory", "https://www.micron.com/about/press/news",
           cards=".cmp-teaser__content", link="a.cmp-teaser__action-link", title="h2", published=".cmp-teaser__description",
           extra_hosts=["investors.micron.com"]),
    source("kla", "KLA 官方公告", "kla", "equipment", "https://ir.kla.com/news-events/press-releases/rss", parser="rss"),
    source("lam-research", "Lam Research Newsroom", "lam-research", "equipment", "https://newsroom.lamresearch.com/press-releases",
           cards="li.wd_item", link=".wd_title a", title=".wd_title", published=".wd_date", excerpt=".wd_summary", next="a"),
    source("amec", "中微公司官方新闻", "amec", "equipment", "https://www.amec-inc.com/latestNews.html",
           cards=".mews_Eone_ul li", link="a[href^='/news/']", title="h3", published="time", detail_date="time", detail_limit=6),
    source("hamamatsu", "滨松光子学官方新闻", "hamamatsu", "photonics", "https://www.hamamatsu.com/us/en/news.html",
           cards=".List__Col.Type--News li", link="span.Title a", title="span.Title", published="span.Date"),
    source("zeiss-smt", "ZEISS SMT 官方新闻", "zeiss", "photonics",
           "https://www.zeiss.com/semiconductor-manufacturing-technology/news-and-events.html",
           cards="a.article-teaser-item__content-link", link=":self", title="h3", published=".article-teaser-item__eyebrow"),
    source("ipg", "IPG Photonics 官方新闻", "ipg", "photonics", "https://www.ipgphotonics.com/en-us/newsroom/news",
           cards="a.no-underline", link=":self", title="h4", published=".date-details", excerpt="p"),
    source("trumpf", "TRUMPF 官方新闻", "trumpf", "photonics", "https://www.trumpf.com/en_US/newsroom/global-press-releases/",
           cards=".block-download", link="a[href*='/release/']", title="[itemprop=headline]", published="time", next="a"),
]


def publication_date(value: str) -> str:
    """Parse a date element, never arbitrary prose or a URL. No month-only dates."""
    text = re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()
    if not text or len(text) > 140 or "http" in text.lower():
        return ""
    numeric = re.fullmatch(r"(20\d{2})[./年-](\d{1,2})[./月-](\d{1,2})日?", text)
    if numeric:
        try:
            return date(*map(int, numeric.groups())).isoformat()
        except ValueError:
            return ""
    # ISO timestamps retain the source timezone instead of being re-dated today.
    if re.match(r"^20\d{2}-\d\d-\d\dT", text):
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return ""
    text = re.sub(r",\s*", ", ", text)
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return parsedate_to_datetime(text).isoformat()
    except (ValueError, TypeError, OverflowError):
        return ""


def _official(url: str, spec: dict) -> bool:
    try:
        parsed = urlparse(url)
        return (parsed.scheme in {"https", "http"} and not parsed.username and not parsed.password
                and parsed.port in {None, 80, 443}
                and parsed.hostname in [*spec["hosts"], *spec.get("extra_hosts", [])])
    except ValueError:
        return False


def _node_text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _date_node(node) -> str:
    if node is None:
        return ""
    return str(node.get("datetime") or node.get("content") or _node_text(node))


def parse_page(spec: dict, body: str, final_url: str) -> dict:
    """Pure parser exposed for fixtures; output carries the original date evidence."""
    rows, links = [], []
    if spec.get("parser") == "rss":
        feed = feedparser.parse(body)
        if not feed.entries:
            raise ValueError("Company feed has no entries")
        for entry in feed.entries[:80]:
            url = entry.get("link", "")
            original_date = entry.get("published") or entry.get("dc_date") or ""
            if _official(url, spec):
                rows.append(dict(title=entry.get("title", ""), url=url,
                                 published=publication_date(original_date), date_evidence=original_date,
                                 text=(entry.get("summary") or entry.get("description") or "")[:10000]))
        for link in feed.feed.get("links", []):
            if link.get("rel") == "next" and _official(link.get("href", ""), spec):
                links.append(link["href"])
    else:
        soup = BeautifulSoup(body, "html.parser")
        cards = soup.select(spec["cards"])
        for card in cards[:100]:
            link = card if spec["link"] == ":self" else card.select_one(spec["link"])
            if link is None:
                continue
            url = urljoin(final_url, link.get("href", ""))
            title = _node_text(card.select_one(spec["title"]))
            original_date = _date_node(card.select_one(spec["published"]))
            if title and _official(url, spec):
                rows.append(dict(title=title, url=url, published=publication_date(original_date),
                                 date_evidence=original_date,
                                 text=_node_text(card.select_one(spec["excerpt"])) if spec.get("excerpt") else ""))
        if not rows:
            raise ValueError("Company news list has no recognized cards")
        if spec.get("next"):
            for link in soup.select(spec["next"]):
                if re.fullmatch(r"next|load more|下一页|›|»|next\s*[›»]?", _node_text(link), re.I) or "next" in link.get("rel", []):
                    url = urljoin(final_url, link.get("href", ""))
                    if _official(url, spec):
                        links.append(url)
        if spec.get("years"):
            for link in soup.select(spec["years"]):
                if re.fullmatch(r"20\d{2}", _node_text(link)):
                    links.append(urljoin(final_url, link.get("href", "")))
    return dict(rows=rows, next_urls=list(dict.fromkeys(links)))


def _cached(key, load, enabled: bool):
    now = time.monotonic()
    if enabled:
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached and now - cached[0] < CACHE_TTL:
                _CACHE.move_to_end(key)
                return cached[1]
    result = load()
    if enabled:
        with _CACHE_LOCK:
            _CACHE[key] = (now, result)
            _CACHE.move_to_end(key)
            while len(_CACHE) > CACHE_PAGES:
                _CACHE.popitem(last=False)
    return result


def collect_company(spec: dict, edition: date, days: int = 7, *, fetcher=None) -> tuple[list[dict], dict]:
    from .sources import candidate, fetch, recent
    if days not in WINDOWS:
        raise ValueError("Industry window must be 7, 30, 90 or 365 days")
    use_cache = fetcher is None
    fetcher = fetcher or fetch
    cutoff = edition - timedelta(days=days)
    max_pages = {7: 2, 30: 2, 90: 3, 365: 4}[days]
    deadline = time.monotonic() + 45
    queue, visited, found, seen = [spec["url"]], set(), [], set()
    if spec.get("archive") and days >= 90:
        queue.append(spec["archive"])
    diagnostics = dict(source=spec["id"], status="ok", count=0, window_days=days,
                       pages=0, skipped_missing_date=0, detail_requests=0)
    while queue and len(visited) < max_pages and len(found) < 60 and time.monotonic() < deadline:
        url = queue.pop(0)
        if url in visited or not _official(url, spec):
            continue
        visited.add(url)
        try:
            def load_page():
                body, final = fetcher(url, timeout=10)
                if not _official(final, spec):
                    raise ValueError("Company page redirected outside official hosts")
                return parse_page(spec, body, final)
            page = _cached((spec["id"], url), load_page, use_cache)
            diagnostics["pages"] += 1
            page_dates = []
            for original in page["rows"]:
                row = dict(original)
                if row["url"] in seen:
                    continue
                seen.add(row["url"])
                if not row["published"] and spec.get("detail_date"):
                    # A list's month can bound a request, but is never stored as a
                    # publication day. The day must come from the article itself.
                    month = re.fullmatch(r"(20\d{2})[.-](\d\d)", row["date_evidence"])
                    in_window = not month or (cutoff.strftime("%Y-%m") <= "-".join(month.groups()) <= edition.strftime("%Y-%m"))
                    if in_window and diagnostics["detail_requests"] < spec.get("detail_limit", 0) and time.monotonic() < deadline:
                        diagnostics["detail_requests"] += 1
                        try:
                            def load_date():
                                text, final = fetcher(row["url"], timeout=10)
                                if not _official(final, spec):
                                    raise ValueError("Article date redirected outside official hosts")
                                node = BeautifulSoup(text, "html.parser").select_one(spec["detail_date"])
                                return _date_node(node)
                            evidence = _cached((spec["id"], row["url"], "date"), load_date, use_cache)
                            row.update(published=publication_date(evidence), date_evidence=evidence)
                        except Exception as exc:
                            diagnostics["detail_error"] = type(exc).__name__
                if not row["published"]:
                    diagnostics["skipped_missing_date"] += 1
                    continue
                page_dates.append(row["published"][:10])
                item = candidate(row["title"], row["url"], spec["name"], "industry", row["published"], row["text"],
                                 source_id=spec["id"], industry_category=spec["category"],
                                 publication_date_evidence=row["date_evidence"],
                                 evidence_level="feed" if row["text"] else "metadata")
                item["company_ids"] = list(dict.fromkeys([spec["company"], *item["company_ids"]]))
                if recent(item, edition, days=days):
                    found.append(item)
            if page_dates and min(page_dates)[:10] < cutoff.isoformat():
                continue
            for next_url in page["next_urls"]:
                year = parse_qs(urlparse(next_url).query).get("year", [""])[0]
                if year and (not year.isdigit() or not cutoff.year <= int(year) <= edition.year):
                    continue
                if next_url not in queue and next_url not in visited:
                    queue.append(next_url)
            # Only WordPress feeds observed to support paged=2 use this route.
            if spec.get("feed_paging"):
                parts = urlparse(url)
                params = parse_qs(parts.query)
                params["paged"] = [str(int(params.get("paged", ["1"])[0]) + 1)]
                next_url = parts._replace(query=urlencode(params, doseq=True)).geturl()
                if next_url not in visited:
                    queue.append(next_url)
        except Exception as exc:
            diagnostics.update(status="partial" if found else "failed", error=type(exc).__name__)
            # A failed archive must not discard successfully fetched recent news.
    found = sorted(found, key=lambda item: (item["published_at"], item["source_url"]), reverse=True)[:60]
    diagnostics["count"] = len(found)
    diagnostics["bounded"] = bool(queue) or len(found) >= 60 or time.monotonic() >= deadline
    if diagnostics.get("detail_error") or diagnostics.get("error"):
        diagnostics["status"] = "partial" if found else "failed"
    return found, diagnostics


def collect_industry(edition: date, days: int = 7, *, specs=None, fetcher=None) -> tuple[list[dict], list[dict]]:
    if days not in WINDOWS:
        raise ValueError("Industry window must be 7, 30, 90 or 365 days")
    results, diagnostics = [], []
    specs = INDUSTRY_SOURCES if specs is None else specs
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(collect_company, spec, edition, days, fetcher=fetcher): spec["id"] for spec in specs}
        for future in as_completed(futures):
            try:
                items, detail = future.result()
                results.extend(items)
                diagnostics.append(detail)
            except Exception as exc:
                diagnostics.append(dict(source=futures[future], status="failed", error=type(exc).__name__, window_days=days))
    return results, sorted(diagnostics, key=lambda detail: detail["source"])
