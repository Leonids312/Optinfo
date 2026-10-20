from datetime import date, datetime, timezone
import io
import json
from pathlib import Path
import time
from urllib.parse import parse_qs, urlparse
import zipfile
from xml.sax.saxutils import escape

import pytest
import httpx

from optinfo import journal_catalog as catalog
from optinfo import journal_sources as sources


def workbook(rows):
    strings = list(dict.fromkeys(str(value) for row in rows for value in row))
    sheet_rows = []
    for index, row in enumerate(rows, 1):
        cells = "".join(f'<c r="{chr(65 + col)}{index}" t="s"><v>{strings.index(str(value))}</v></c>'
                        for col, value in enumerate(row))
        sheet_rows.append(f'<row r="{index}">{cells}</row>')
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("xl/sharedStrings.xml",
                         f'<sst xmlns="{namespace}">' +
                         "".join(f"<si><t>{escape(value)}</t></si>" for value in strings) + "</sst>")
        archive.writestr("xl/worksheets/sheet1.xml",
                         f'<worksheet xmlns="{namespace}"><sheetData>' +
                         "".join(sheet_rows) + "</sheetData></worksheet>")
    return data.getvalue()


def write_seed(path: Path, rows):
    path.write_text(json.dumps({"schema_version": 1, "journals": rows}), encoding="utf8")


def test_cas_scope_uses_2025_materials_major_or_optics_minor_not_jcr():
    data = workbook([
        ["刊名", "ISSN", "大类学科", "大类分区", "小类分区明细"],
        ["Optica", "2334-2536", "物理与天体物理", "3", "OPTICS 光学(2区)"],
        ["Nature Materials", "1476-1122", "材料科学", "1", "MATERIALS SCIENCE 材料(1区)"],
        ["Unrelated", "1094-4087", "材料科学", "3", "JCR Q1"],
    ])
    rows = catalog.parse_cas2025_xlsx(data, "https://university.example/2025.xlsx")
    assert {row["name"] for row in rows} == {"Optica", "Nature Materials"}
    assert all(row["partition_year"] == 2025 and
               row["partition_status"] == "verified_university_2025_table" for row in rows)
    assert rows[0]["partition_match"] == ["OPTICS光学小类1/2区"]


def test_publisher_directory_accepts_real_journal_links_and_not_external_lookalikes():
    body = """
    <a href="/nphoton/">Nature Photonics</a>
    <a href="/npjnew/">npj New Research</a>
    <a href="/commsmat/">Communications Materials</a>
    <a href="https://attacker.example/nature/">Nature Fake</a>
    <a href="/careers/">Nature Careers</a>
    <a href="/nature-portfolio/about-journals/">Nature Portfolio</a>
    """
    rows = catalog.parse_nature_directory(body, catalog.NATURE_INDEX)
    assert {row["name"] for row in rows} == {"Nature Photonics", "npj New Research", "Communications Materials"}
    assert next(row for row in rows if row["name"] == "Nature Photonics")["id"] == "nphoton"


def test_crossref_requires_title_and_issn_match_and_rejects_invalid_registry_issn():
    journal = catalog.catalog_entry("Nature Photonics", catalog.NATURE_INDEX, issns=["1749-4893"])
    def good(*args, **kwargs):
        return json.dumps({"message": {"title": "Nature Photonics",
                                      "ISSN": ["1749-4885", "1749-4893", "9999-9999"]}}).encode()
    checked = catalog.verify_crossref(journal, good, deadline=time.monotonic() + 1)
    assert checked["issn"] == "1749-4893" and checked["issn_status"] == "crossref_verified"
    assert "9999-9999" not in checked["issns"]
    def wrong(*args, **kwargs):
        return json.dumps({"message": {"title": "Another Journal", "ISSN": ["1749-4893"]}}).encode()
    with pytest.raises(ValueError):
        catalog.verify_crossref(journal, wrong, deadline=time.monotonic() + 1)


def test_failed_refresh_retains_catalog_and_partition_evidence_and_throttles(tmp_path, monkeypatch):
    seed = tmp_path / "seed.json"
    journal = catalog.catalog_entry("Optica", "publisher", issns=["2334-2536"],
                                    issn_status="crossref_verified",
                                    partition_status="verified_university_2025_table", partition_major=1)
    write_seed(seed, [journal])
    monkeypatch.setattr(catalog, "SEED_PATH", seed)
    calls = []
    def unavailable(url, **kwargs):
        calls.append(url)
        raise OSError("Unavailable publisher")
    cache = tmp_path / "cache.json"
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    rows, diagnostics = catalog.refresh_catalog(path=cache, now=now, fetcher=unavailable)
    assert len(rows) == 1 and rows[0]["name"] == "Optica"
    assert rows[0]["issns"] == journal["issns"] and rows[0]["partition_major"] == 1
    assert diagnostics[-1]["status"] == "stale"
    before = len(calls)
    again, _ = catalog.refresh_catalog(path=cache, now=now, fetcher=unavailable)
    assert again == rows and len(calls) == before
    fresh = catalog.catalog_entry("Optica", "another publisher")
    assert catalog.merge_journals(rows, [fresh])[0]["partition_major"] == 1
    assert catalog.merge_journals(rows, [fresh])[0]["issn_status"] == "crossref_verified"


def test_new_publisher_title_is_discovered_and_registered_without_changing_defaults(tmp_path, monkeypatch):
    seed = tmp_path / "seed.json"
    write_seed(seed, [])
    monkeypatch.setattr(catalog, "SEED_PATH", seed)
    def fetch(url, **kwargs):
        if url == catalog.NATURE_INDEX:
            return b'<a href="/npjtest/">npj Test Journal</a>'
        if url.startswith("https://api.crossref.org/"):
            return json.dumps({"message": {"items": [{"title": "npj Test Journal", "ISSN": ["2334-2536"]}]}}).encode()
        raise OSError("Unavailable")
    rows, _ = catalog.refresh_catalog(path=tmp_path / "cache.json", fetcher=fetch)
    assert len(rows) == 1 and rows[0]["issn_status"] == "crossref_verified"
    assert catalog.get_journals(path=tmp_path / "cache.json") == rows


def test_batch_search_is_bound_to_verified_issns_and_real_publication_dates():
    journal = catalog.catalog_entry("Optica", "publisher", issns=["2334-2536"])
    observed = []
    def fetch(url, **kwargs):
        observed.append(parse_qs(urlparse(url).query))
        item = {"type": "journal-article", "title": ["A photonic device"], "DOI": "10.example/real",
                "ISSN": ["2334-2536"], "URL": "https://doi.org/10.example/real",
                "container-title": ["Optica"],
                "published": {"date-parts": [[2026, 10, 20]]}, "abstract": "Measured optical result"}
        return json.dumps({"message": {"total-results": 300, "items": [
            item, {**item, "DOI": "10.example/future", "published": {"date-parts": [[2027, 1, 1]]}},
            {**item, "DOI": "10.example/wrong", "ISSN": ["1749-4893"]},
            {**item, "DOI": "10.example/wrong-title", "container-title": ["Wrong merged registry journal"]},
        ]}}).encode()
    items, diagnostic = sources.query_batch([journal], date(2026, 10, 20), "ultrafast",
                                            deadline=time.monotonic() + 1, fetcher=fetch)
    assert [item["doi"] for item in items] == ["10.example/real"]
    assert diagnostic["bounded_results"] is True
    assert "issn:2334-2536" in observed[0]["filter"][0]
    assert observed[0]["query"] == ["ultrafast"]


def test_every_registered_title_enters_bounded_batches_and_dois_are_deduplicated(monkeypatch):
    rows = [catalog.catalog_entry(f"Nature Test {index}", "publisher", issns=["2334-2536"],
                                  issn_status="crossref_verified") for index in range(40)]
    seen = []
    def query(group, edition, query, **kwargs):
        seen.extend(row["name"] for row in group)
        return [{"doi": "10.example/same", "evidence_text": "Abstract"}], {"count": 1}
    monkeypatch.setattr(sources, "query_batch", query)
    def loader(**kwargs):
        return rows, []
    found, diagnostics = sources.collect_journals(date(2026, 10, 20), [], catalog_loader=loader)
    assert set(seen) == {row["name"] for row in rows}
    assert len(found) == 1 and all(row["status"] == "ok" for row in diagnostics)
    assert sources.interest_query(None)


def test_authoritative_table_issn_survives_registry_outage_but_uses_interest_query(monkeypatch):
    journal = catalog.catalog_entry("Optica", "university", issns=["2334-2536"],
                                    issn_status="university_table")
    observed = []
    def query(group, edition, text, **kwargs):
        observed.append((group, text))
        return [], {"count": 0}
    monkeypatch.setattr(sources, "query_batch", query)
    found, diagnostic = sources.collect_journals(
        date(2026, 10, 20), ["ultrafast"], catalog_loader=lambda **kwargs: ([journal], []))
    assert observed[0][0] == [journal]
    assert "ultrafast" in observed[0][1]
    assert any(row["source"] == "crossref-catalog-registry-unconfirmed" for row in diagnostic)


def test_interests_are_independent_short_queries_and_all_primary_topics_are_visited_first():
    queries = sources.interest_queries(["xfel", "ultrafast", "semiconductor-materials"])
    assert queries[:3] == ["xfel", "ultrafast", "silicon carbide"]
    assert "free electron laser" in queries and "diamond" in queries
    assert all(len(query.split()) <= 4 and " OR " not in query for query in queries)
    assert sources.interest_queries([]) == sources.DEFAULT_QUERIES


def test_daily_topic_rotation_keeps_primary_directions_ahead_of_aliases():
    selected = ["xfel", "ultrafast", "semiconductor-materials"]
    first = sources.interest_queries(selected, date(2026, 10, 20))
    following = sources.interest_queries(selected, date(2026, 10, 21))
    assert first[0] != following[0]
    assert set(first[:3]) == set(following[:3]) == {"xfel", "ultrafast", "silicon carbide"}
    assert set(first[3:]) == set(following[3:]) == {"free electron laser", "nonequilibrium", "diamond"}
    assert sources.interest_queries([], date(2026, 10, 20))[0] != sources.interest_queries(
        [], date(2026, 10, 21))[0]


def test_coverage_unions_short_themes_and_isolates_unregistered_journals(monkeypatch):
    journals = [catalog.catalog_entry(f"Nature Test {index}", "publisher", issns=["2334-2536"],
                                      issn_status="crossref_verified") for index in range(130)]
    fallback = catalog.catalog_entry("New Journal", "publisher", issns=["1749-4893"],
                                     issn_status="publisher_verified")
    calls = []
    def query(group, edition, text, **kwargs):
        calls.append((group, text))
        if text == "ultrafast":
            return [{"doi": "10.example/new-ultrafast", "evidence_text": "New paper"}], {"count": 1}
        if text == "silicon carbide":
            return [{"doi": "10.example/new-silicon", "evidence_text": "Other direction"}], {"count": 1}
        return [], {"count": 0}
    monkeypatch.setattr(sources, "query_batch", query)
    found, _ = sources.collect_journals(
        date(2026, 10, 20), ["ultrafast", "semiconductor-materials"],
        catalog_loader=lambda **kwargs: ([*journals, fallback], []))
    assert {row["doi"] for row in found} == {"10.example/new-ultrafast", "10.example/new-silicon"}
    assert {row["id"] for group, _ in calls[:4] for row in group} == {
        row["id"] for row in [*journals, fallback]}
    assert len({text for _, text in calls[:4]}) == 1
    assert all(len({row["issn_status"] for row in group}) == 1 for group, _ in calls)


def test_crossref_rate_limit_is_serial_and_recovers_once_without_splitting():
    now = [0.0]
    calls = []
    def sleep(seconds):
        now[0] += seconds
    def fetch(url, **kwargs):
        calls.append((url, now[0]))
        if len(calls) == 1:
            response = httpx.Response(429, headers={"Retry-After": "2"},
                                      request=httpx.Request("GET", url))
            response.raise_for_status()
        return b'{"message": {"items": []}}'
    requests = sources.CrossrefRequests(fetch, clock=lambda: now[0], sleep=sleep)
    requests("https://api.crossref.org/works?query=ultrafast", deadline=10)
    requests("https://api.crossref.org/works?query=photonics", deadline=10)
    assert [value[1] for value in calls] == pytest.approx([0, 2, 3.05])
    assert calls[0][0] == calls[1][0] and calls[2][0] != calls[1][0]
    with pytest.raises(TimeoutError):
        requests("https://api.crossref.org/works?query=laser", deadline=3.1)
    assert len(calls) == 3


def test_crossref_retry_is_bounded_and_non_rate_errors_are_not_split_or_retried():
    for status, expected in [(429, 2), (400, 1)]:
        now, calls = [0.0], []
        def fetch(url, **kwargs):
            calls.append(url)
            httpx.Response(status, request=httpx.Request("GET", url)).raise_for_status()
        requests = sources.CrossrefRequests(fetch, clock=lambda: now[0],
                                            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
        with pytest.raises(httpx.HTTPStatusError):
            requests("https://api.crossref.org/works", deadline=10)
        assert len(calls) == expected


def test_cache_uses_existing_db_directory_without_requiring_new_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("OPTINFO_DATA_DIR", raising=False)
    monkeypatch.setenv("OPTINFO_DB_PATH", str(tmp_path / "existing.sqlite3"))
    assert catalog.cache_path() == tmp_path / "journal-catalog.json"


def test_real_seed_preserves_legacy_ids_cas_scope_and_thesis_defaults():
    from optinfo.catalog import DEFAULT_TOPIC_IDS, JOURNALS, TOPICS
    by_id = {row["id"]: row for row in JOURNALS}
    assert by_id["nphoton"]["name"] == "Nature Photonics"
    assert by_id["lsa"]["name"] == "Light: Science & Applications"
    assert len(by_id) == len(JOURNALS)
    assert sum(row.get("partition_status") == "verified_university_2025_table" for row in JOURNALS) >= 149
    assert len(DEFAULT_TOPIC_IDS) < 40
    assert len(TOPICS) > len(DEFAULT_TOPIC_IDS) + 250


def test_url_fetch_rejects_nonpublisher_targets_before_network():
    with pytest.raises(ValueError):
        catalog.fetch_public("https://127.0.0.1/private", deadline=time.monotonic() + 1)
