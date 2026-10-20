from datetime import date
import json
from pathlib import Path

import pytest

from optinfo import industry_sources as industry


EDITION = date(2026, 10, 20)


def spec(**options):
    return industry.source(
        "fixture", "Official fixture", "smic", "foundry", "https://example.com/news",
        cards="article", link="a", title="h2", published="time", **options,
    )


def card(title="New wafer process", day="2026-10-19", href="/release"):
    return f'<article><h2>{title}</h2><a href="{href}">Read</a><time>{day}</time></article>'


def fetch_pages(pages, calls):
    def fetcher(url, **kwargs):
        calls.append(url)
        value = pages[url]
        if isinstance(value, Exception):
            raise value
        return value, url
    return fetcher


@pytest.mark.parametrize(("raw", "expected"), [
    ("2026年10月9日", "2026-10-09"),
    ("2026.10.09", "2026-10-09"),
    ("October 9, 2026", "2026-10-09"),
    ("09 October 2026", "2026-10-09"),
    ("09/01/2026", "2026-09-01"),
    ("2026-10-19T23:00:00-04:00", "2026-10-19T23:00:00-04:00"),
    ("Tue, 20 Oct 26 09:00:00 +0000", "2026-10-20T09:00:00+00:00"),
    ("2026-10", ""), ("2026年10月", ""), ("2026-02-30", ""),
    ("https://example.com/2026/10/20/release", ""),
    ("Updated October 9, 2026", ""), ("", ""),
])
def test_only_explicit_publication_dates(raw, expected):
    assert industry.publication_date(raw) == expected


def test_card_dates_are_not_taken_from_neighbor_footer_or_url():
    body = card(day="", href="/2026/10/19/no-date") + card("Dated", "2026-10-17", "/dated")
    rows = industry.parse_page(spec(), body + "<footer>2026-10-20</footer>", spec()["url"])["rows"]
    assert rows[0]["published"] == ""
    assert rows[1]["published"] == "2026-10-17"


def test_rss_uses_publication_not_update_and_rejects_external_hosts():
    rss = """<rss version="2.0"><channel><title>News</title>
    <item><title>Published</title><link>https://example.com/a</link>
    <pubDate>Mon, 19 Oct 2026 00:00:00 +0000</pubDate></item>
    <item><title>Only updated</title><link>https://example.com/b</link>
    <updated>2026-10-20T00:00:00Z</updated></item>
    <item><title>External</title><link>https://attacker.test/2026-10-20</link>
    <pubDate>Mon, 19 Oct 2026 00:00:00 +0000</pubDate></item></channel></rss>"""
    rss_spec = industry.source("rss", "Official RSS", "arm", "design", "https://example.com/feed", parser="rss")
    rows = industry.parse_page(rss_spec, rss, rss_spec["url"])["rows"]
    assert len(rows) == 2
    assert rows[0]["published"] == "2026-10-19T00:00:00+00:00"
    assert rows[1]["published"] == ""
    with pytest.raises(ValueError):
        industry.parse_page(rss_spec, "<html>Access denied</html>", rss_spec["url"])


@pytest.mark.parametrize("url", [
    "https://example.com:invalid/a", "https://example.com.attacker.test/a",
    "https://user@example.com/a", "https://example.com:8443/a", "file:///a",
])
def test_official_host_filter_handles_malformed_links(url):
    assert not industry._official(url, spec())


@pytest.mark.parametrize(("days", "count"), [(7, 1), (30, 2), (90, 3), (365, 4)])
def test_history_windows_keep_original_dates_and_company_identity(days, count):
    body = "".join(card(f"Release {i} on process manufacturing", d, f"/release-{i}") for i, d in enumerate(
        ["2026-10-19", "2026-10-01", "2026-08-01", "2026-01-01", "2025-01-01", "2026-10-21"]))
    calls = []
    items, detail = industry.collect_company(spec(), EDITION, days, fetcher=fetch_pages({spec()["url"]: body}, calls))
    assert len(items) == count
    assert all(item["company_ids"][0] == "smic" and item["source_id"] == "fixture" for item in items)
    assert all(item["industry_category"] == "foundry" for item in items)
    assert items[0]["published_at"] == "2026-10-19"
    assert detail["status"] == "ok" and detail["count"] == count


def test_month_only_amec_list_fetches_detail_day_without_inventing_one():
    source = spec(detail_date="time", detail_limit=2)
    pages = {
        source["url"]: card(day="2026-10") + card("Missing day", "2026-10", "/unknown")
        + card("Old month", "2025-01", "/old"),
        "https://example.com/release": "<time>2026-10-16</time>",
        "https://example.com/unknown": "<h1>Not a dated article</h1>",
    }
    calls = []
    items, detail = industry.collect_company(source, EDITION, 7, fetcher=fetch_pages(pages, calls))
    assert len(items) == 1 and items[0]["published_at"] == "2026-10-16"
    assert items[0]["publication_date_evidence"] == "2026-10-16"
    assert "https://example.com/old" not in calls
    assert detail["detail_requests"] == 2 and detail["skipped_missing_date"] == 2


def test_pagination_is_bounded_and_duplicate_urls_not_republished():
    source = spec(next="a")
    pages = {}
    for page in range(1, 6):
        url = source["url"] if page == 1 else f"https://example.com/page{page}"
        pages[url] = card("Same official title", href="/duplicate") + card(
            f"Distinct item {page}", href=f"/unique-{page}") + (
            f'<a href="/page{page + 1}">Next</a><a href="https://attacker.test/">Next</a>')
    calls = []
    items, detail = industry.collect_company(source, EDITION, 365, fetcher=fetch_pages(pages, calls))
    assert len(calls) == 4 and len(items) == 5
    assert detail["bounded"] is True
    assert all("attacker" not in url for url in calls)


def test_same_title_different_dates_reaches_pipeline_identity_check():
    body = card("Quarterly results", "2026-10-19", "/quarter3") + card(
        "Quarterly results", "2026-07-19", "/quarter2")
    items, _ = industry.collect_company(spec(), EDITION, 365, fetcher=fetch_pages({spec()["url"]: body}, []))
    assert len(items) == 2
    assert {item["published_at"] for item in items} == {"2026-10-19", "2026-07-19"}


def test_failure_then_successful_archive_retains_partial_diagnostic():
    source = spec(archive="https://example.com/archive")
    pages = {source["url"]: TimeoutError("private body must not appear"),
             source["archive"]: card()}
    items, detail = industry.collect_company(source, EDITION, 365, fetcher=fetch_pages(pages, []))
    assert len(items) == 1 and detail["status"] == "partial"
    assert detail["error"] == "TimeoutError"
    assert "private" not in json.dumps(detail)


def test_failed_archive_keeps_successful_recent_page():
    source = spec(archive="https://example.com/archive")
    pages = {source["url"]: card(), source["archive"]: TimeoutError("timeout")}
    items, detail = industry.collect_company(source, EDITION, 365, fetcher=fetch_pages(pages, []))
    assert len(items) == 1 and detail["status"] == "partial"


def test_source_isolation_and_final_redirect_validation():
    good, bad = spec(), {**spec(), "id": "bad", "url": "https://example.com/bad"}
    def fetcher(url, **kwargs):
        return card(), "https://attacker.test/" if url.endswith("bad") else url
    items, details = industry.collect_industry(EDITION, 7, specs=[bad, good], fetcher=fetcher)
    assert len(items) == 1
    assert details[0]["status"] == "failed" and details[0]["error"] == "ValueError"
    assert details[1]["status"] == "ok"


def test_widening_reuses_parsed_source_metadata(monkeypatch):
    from optinfo import sources
    source = {**spec(), "id": "cache-fixture"}
    industry._CACHE.clear()
    calls = []
    monkeypatch.setattr(sources, "fetch", fetch_pages({source["url"]: card(day="2026-10-01")}, calls))
    narrow, _ = industry.collect_company(source, EDITION, 7)
    wide, _ = industry.collect_company(source, EDITION, 30)
    assert not narrow and len(wide) == 1 and len(calls) == 1
    industry._CACHE.clear()


def test_collect_industry_only_forwards_window_without_research(monkeypatch):
    from optinfo import sources, journal_sources
    calls = []
    def industry_collect(edition, days):
        calls.append((edition, days))
        return [], [{"source": "industry-test", "status": "ok", "count": 0}]
    def forbidden(*args, **kwargs):
        raise AssertionError("Research/world must not be fetched in industry widening")
    monkeypatch.setattr(industry, "collect_industry", industry_collect)
    monkeypatch.setattr(journal_sources, "collect_journals", forbidden)
    monkeypatch.setattr(sources, "collect_feed", forbidden)
    monkeypatch.setattr(sources, "collect_html_list", forbidden)
    monkeypatch.setattr(sources, "collect_arxiv", forbidden)
    items, details = sources.collect(EDITION.isoformat(), sections={"industry"}, industry_days=90, topic_ids=["chip-manufacturing"])
    assert items == [] and details[0]["source"] == "industry-test"
    assert calls == [(EDITION, 90)]


def test_catalog_contains_every_source_company_and_sector():
    companies = json.loads((Path(__file__).resolve().parents[2] / "content/companies.json").read_text(encoding="utf-8"))
    ids = {company["id"] for company in companies}
    assert {source["company"] for source in industry.INDUSTRY_SOURCES} <= ids
    assert {source["category"] for source in industry.INDUSTRY_SOURCES} == {
        "design", "foundry", "memory", "equipment", "photonics",
    }
    assert len({source["company"] for source in industry.INDUSTRY_SOURCES}) >= 16
