"""Bounded Crossref searches across the verified journal catalog.

This is a candidate search, not an exhaustive download or a promise that every
published paper will be pushed. Editorial ranking and evidence gates run later.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import date, timedelta
import json
import threading
import time
from urllib.parse import urlencode

from .journal_catalog import fetch_public, refresh_catalog, title_key, valid_issn

DEFAULT_QUERIES = ["optics", "photonics", "ultrafast", "semiconductor", "materials", "laser"]


def interest_queries(topic_ids: list[str] | None, edition: date | None = None) -> list[str]:
    """Independent short searches, whose results are unioned, not one long query.

    Crossref has no Boolean OR syntax and its minimum term-match rule makes a
    concatenation of unrelated interests silently exclude single-topic papers.
    Visit each topic's primary phrase before spending budget on its aliases.
    """
    if topic_ids is None:
        queries = list(DEFAULT_QUERIES)
        offset = edition.toordinal() % len(queries) if edition else 0
        return queries[offset:] + queries[:offset]
    from .catalog import TOPICS
    selected = set(topic_ids)
    phrases = []
    for topic in TOPICS:
        if topic["id"] in selected and topic["section"] == "research" and topic["kind"] != "期刊":
            phrases.append([value.strip() for value in topic.get("keywords", [])[:2]
                            if value.isascii() and 0 < len(value.split()) <= 4])
    if phrases and edition:
        offset = edition.toordinal() % len(phrases)
        phrases = phrases[offset:] + phrases[:offset]
    result = list(dict.fromkeys(values[index] for index in range(2) for values in phrases
                                if len(values) > index))
    # No selected research direction means broad selection, not an empty feed.
    return result or interest_queries(None, edition)


def interest_query(topic_ids: list[str] | None) -> str:
    """Compatibility helper for callers needing a single short search."""
    return interest_queries(topic_ids)[0]


class CrossrefRequests:
    """Respect the public-array pool's observed 1 request/s, concurrency 1.

    The same gate covers catalog verification and candidate requests. One bounded
    retry is allowed for 429; it never causes recursive batch splitting.
    """

    def __init__(self, fetcher=fetch_public, *, interval=1.05,
                 clock=time.monotonic, sleep=time.sleep):
        self.fetcher, self.interval = fetcher, interval
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.next_request = 0.0

    def __call__(self, url, *, deadline, **kwargs):
        if not url.startswith("https://api.crossref.org/"):
            return self.fetcher(url, deadline=deadline, **kwargs)
        with self.lock:
            for attempt in range(2):
                delay = max(0, self.next_request - self.clock())
                if self.clock() + delay >= deadline:
                    raise TimeoutError("Crossref request budget exhausted")
                if delay:
                    self.sleep(delay)
                self.next_request = self.clock() + self.interval
                try:
                    return self.fetcher(url, deadline=deadline, **kwargs)
                except Exception as exc:
                    response = getattr(exc, "response", None)
                    if getattr(response, "status_code", None) != 429 or attempt:
                        raise
                    try:
                        retry_after = float(response.headers.get("Retry-After", 2))
                    except (TypeError, ValueError):
                        retry_after = 2.0
                    self.next_request = max(self.next_request,
                                            self.clock() + max(2, min(10, retry_after)))


def query_batch(journals: list[dict], edition: date, query: str, *,
                deadline: float, fetcher=fetch_public, rows: int = 100) -> tuple[list[dict], dict]:
    from .sources import candidate, parse_date, recent
    lookup = {}
    for journal in journals:
        for issn in journal.get("issns", [journal.get("issn", "")]):
            if valid_issn(issn):
                lookup.setdefault(issn, []).append(journal)
    # Repeated ISSN filters are an OR in Crossref; publication dates/type are AND.
    filters = [*(f"issn:{issn}" for issn in lookup),
               f"from-pub-date:{edition - timedelta(days=7)}",
               f"until-pub-date:{edition}", "type:journal-article"]
    params = {"filter": ",".join(filters), "rows": max(1, min(rows, 100)),
              "select": "DOI,title,URL,abstract,published,container-title,type,ISSN"}
    if query:
        params.update(query=query, sort="relevance")
    else:
        params.update(sort="published", order="desc")
    body = fetcher("https://api.crossref.org/works?" + urlencode(params), deadline=deadline)
    message = json.loads(body).get("message", {})
    found = []
    for entry in message.get("items", [])[:100]:
        if entry.get("type") != "journal-article":
            continue
        titles = {title_key(value) for value in entry.get("container-title", [])}
        matched = next((journal for issn in entry.get("ISSN", []) for journal in lookup.get(issn, [])
                        if titles.intersection({title_key(journal["name"]),
                                                *(title_key(value) for value in journal.get("aliases", [])),
                                                title_key(journal.get("crossref_title", ""))})), None)
        if not matched:
            continue
        parts = entry.get("published", {}).get("date-parts", [[]])[0]
        if len(parts) != 3:
            continue
        published = parse_date("-".join(str(number).zfill(2) for number in parts))
        abstract = entry.get("abstract", "")
        item = candidate(" ".join(entry.get("title", [])), entry.get("URL", ""), "Crossref",
                         "research", published, abstract, journal=matched["name"],
                         doi=entry.get("DOI", ""), evidence_level="abstract" if abstract else "metadata")
        if item["doi"] and item["title"] and recent(item, edition):
            found.append(item)
    return found, {"journal_count": len(journals), "count": len(found),
                   "total_matches": message.get("total-results", 0),
                   "bounded_results": message.get("total-results", 0) > params["rows"]}


def collect_journals(edition: date, topic_ids: list[str] | None = None, *,
                     fetcher=fetch_public, catalog_loader=refresh_catalog,
                     budget_seconds: float = 90) -> tuple[list[dict], list[dict]]:
    # Directory refresh has its own small budget and is never needed by bootstrap.
    requests = CrossrefRequests(fetcher)
    journals, diagnostics = catalog_loader(fetcher=requests, budget_seconds=25)
    unverified = [row for row in journals if row.get("issn_status") not in {"crossref_verified", "not_applicable"}]
    if unverified:
        diagnostics.append({"source": "crossref-catalog-registry-unconfirmed", "status": "partial",
                            "journal_count": len(unverified), "journal_ids": [row["id"] for row in unverified]})
    # Publisher/table ISSNs remain eligible if registry lookup is unavailable;
    # individual works must still match both an ISSN and the expected title.
    journals = [row for row in journals if row.get("issn_status") in
                {"crossref_verified", "publisher_verified", "university_table"} and
                any(valid_issn(value) for value in row.get("issns", []))]
    if not journals:
        return [], [*diagnostics, {"source": "crossref-catalog", "status": "failed", "error": "NoVerifiedJournals"}]
    queries = interest_queries(topic_ids, edition)
    # Every verified title enters a batch in each theme round. Large families use a
    # topic query; explicitly chosen journals receive a recent-publication search
    # without an extra subject restriction. Merely being CAS 1/2 does not bypass
    # the thesis interests (e.g. a biomaterial journal must still match the query).
    selected = set(topic_ids or [])
    focused, registered, fallback = [], [], []
    for journal in journals:
        target = focused if ("journal-" + journal["id"] in selected or
                             journal["id"] == "apl" and "journal-applied" in selected or
                             topic_ids is None and journal["id"] in
                             {"nphoton", "lsa", "optica", "oe", "prb", "prapplied", "apl"}) else (
                                 registered if journal["issn_status"] == "crossref_verified" else fallback)
        target.append(journal)
    # Larger OR-of-ISSN batches leave enough requests for independent themes.
    # Registry-unconfirmed titles cannot poison a registered-title request.
    groups = [group[offset:offset + 64] for group in (registered, fallback)
              for offset in range(0, len(group), 64)]
    if groups:
        rotation = edition.toordinal() % len(groups)
        groups = groups[rotation:] + groups[:rotation]
    jobs = [(group[offset:offset + 64], "")
            for group in ([row for row in focused if row["issn_status"] == "crossref_verified"],
                          [row for row in focused if row["issn_status"] != "crossref_verified"])
            for offset in range(0, len(group), 64)]
    # Finish a theme across the entire catalog before starting another theme.
    jobs.extend((group, query) for query in queries for group in groups)
    deadline = time.monotonic() + max(1, min(180, budget_seconds))
    found = []
    # Submit only three at once; a deadline never leaves a long queue that a
    # ThreadPoolExecutor context silently drains after the caller times out.
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending, cursor = {}, 0
        while cursor < len(jobs) or pending:
            while cursor < len(jobs) and len(pending) < 3 and time.monotonic() < deadline:
                group, text = jobs[cursor]
                future = pool.submit(query_batch, group, edition, text,
                                     deadline=deadline, fetcher=requests)
                pending[future] = cursor
                cursor += 1
            if not pending:
                break
            completed, _ = wait(pending, timeout=max(0.01, deadline - time.monotonic()),
                                return_when=FIRST_COMPLETED)
            if not completed:
                # In-flight calls share the deadline and have bounded socket IO.
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                index = pending.pop(future)
                name = f"crossref-catalog-{index + 1:02d}"
                try:
                    items, details = future.result()
                    found.extend(items)
                    diagnostics.append({"source": name, "status": "ok",
                                        "query": jobs[index][1], **details})
                except Exception as exc:
                    expired = time.monotonic() >= deadline
                    details = {"source": name, "status": "deferred" if expired else "failed",
                               "error": "BudgetExhausted" if expired else type(exc).__name__,
                               "query": jobs[index][1], "journal_count": len(jobs[index][0])}
                    response = getattr(exc, "response", None)
                    if response is not None:
                        details["http_status"] = response.status_code
                    diagnostics.append(details)
        if cursor < len(jobs):
            diagnostics.append({"source": "crossref-catalog-budget", "status": "deferred",
                                "batches": len(jobs) - cursor,
                                "journal_count": len({row["id"] for group, _ in jobs[cursor:]
                                                      for row in group}),
                                "queries": list(dict.fromkeys(query for _, query in jobs[cursor:]))})
    unique = {}
    for item in found:
        key = item["doi"].casefold()
        if key not in unique or len(item["evidence_text"]) > len(unique[key]["evidence_text"]):
            unique[key] = item
    return list(unique.values()), diagnostics
