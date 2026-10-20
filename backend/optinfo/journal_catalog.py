"""Publisher catalog discovery with an offline, provenance-bearing fallback.

Catalog membership is not a CAS ranking. No paywalled partition table is
inferred from a journal title, impact factor, Crossref subject or JCR quartile.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import html
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlencode, urljoin, urlparse
import zipfile
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import httpx

SEED_PATH = Path(__file__).resolve().parents[2] / "content" / "journals.json"
NATURE_INDEX = "https://www.nature.com/siteindex"
NATURE_SERIES = [
    "https://www.nature.com/nature-portfolio/about-journals/research-journals",
    "https://www.nature.com/nature-portfolio/about-journals/nature-reviews",
    "https://www.nature.com/nature-portfolio/about-journals/npj-series",
    "https://www.nature.com/nature-portfolio/about-journals/communications-journals",
    "https://www.nature.com/nature-portfolio/about-journals/nature-progress",
]
SPRINGER_INDEX = "https://www.springernature.com/gp/librarians/licensing/journals-catalog"
# Public publisher files linked by the catalog above, observed 2026-10-20.
SPRINGER_FILES = {
    2026: "https://cms-resources.apps.public.k8s.springernature.io/springer-cms/rest/v1/content/27842206/data/v2",
    2027: "https://cms-resources.apps.public.k8s.springernature.io/springer-cms/rest/v1/content/52799420/data/v6",
}
SCIENCE_INDEX = "https://www.science.org/journals"
ALLOWED_HOSTS = {
    "www.nature.com", "nature.com", "www.springernature.com",
    "cms-resources.apps.public.k8s.springernature.io",
    "www.science.org", "science.org", "api.crossref.org",
}
PARTITION_SCOPE = "2025中科院期刊分区表升级版；材料科学、物理与天体物理大类1/2区及相关光学期刊待逐刊核验"
LEGACY_IDS = {
    "naturephotonics": "nphoton", "lightscienceapplications": "lsa",
    "optica": "optica", "opticsexpress": "oe", "physicalreviewb": "prb",
    "physicalreviewapplied": "prapplied", "appliedphysicsletters": "apl",
}


def title_key(name: str) -> str:
    return re.sub(r"[\W_]+", "", re.sub(r"\band\b", "", html.unescape(name).casefold().replace("&", " ")))


def valid_issn(value: str) -> bool:
    value = value.upper()
    if not re.fullmatch(r"\d{4}-\d{3}[\dX]", value):
        return False
    digits = value.replace("-", "")
    return (sum(int(c) * (8 - i) for i, c in enumerate(digits[:7])) +
            (10 if digits[-1] == "X" else int(digits[-1]))) % 11 == 0


def journal_id(name: str) -> str:
    key = title_key(name)
    return LEGACY_IDS.get(key, "j-" + re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:100])


def family_for(name: str) -> str:
    if name.startswith("npj "):
        return "npj"
    if name.startswith("Communications ") and not name.startswith(("Communications in ", "Communications on ")):
        return "nature-communications-series"
    if name == "Nature" or name.startswith("Nature "):
        return "nature"
    if name in {"Scientific Reports", "Scientific Data", "Scientific Reports Reviews"}:
        return "nature-scientific-series"
    if name in {"Science", "Science Advances", "Science Immunology", "Science Robotics",
                "Science Signaling", "Science Translational Medicine"}:
        return "science"
    return "optics-materials-core-candidate"


def catalog_entry(name: str, source: str, **extra) -> dict:
    return {
        "id": journal_id(name), "name": name, "family": family_for(name),
        "issns": [], "issn_status": "unverified",
        "partition_system": "CAS", "partition_year": 2025,
        "partition_status": "unverified", "partition_scope": PARTITION_SCOPE,
        "provenance": [source], **extra,
    }


def parse_nature_directory(body: str, source: str) -> list[dict]:
    """Only journal homepage links from the publisher's current directory."""
    result = {}
    for anchor in BeautifulSoup(body, "html.parser").select("a[href]"):
        name = " ".join(anchor.get_text(" ", strip=True).split())
        url = urljoin(source, anchor["href"])
        path = urlparse(url).path
        if urlparse(url).hostname not in {"www.nature.com", "nature.com"}:
            continue
        if not re.fullmatch(r"/[a-z0-9-]+/?", path) or not (
            name == "Nature" or name.startswith(("Nature ", "npj ", "Communications ")) or
            name in {"Scientific Reports", "Scientific Data", "Scientific Reports Reviews"}
        ):
            continue
        if name in {"Nature Portfolio", "Nature Index", "Nature Careers", "Nature Conferences",
                    "Nature Awards", "Nature Africa", "Nature India", "Nature Italy",
                    "Nature Middle East", "Nature China", "Nature Japan"}:
            continue
        result[title_key(name)] = catalog_entry(name, source, homepage=url, catalog_status="current_directory")
    return list(result.values())


def parse_science_directory(body: str, source: str = SCIENCE_INDEX) -> list[dict]:
    result = {}
    for anchor in BeautifulSoup(body, "html.parser").select("a[href]"):
        name = " ".join(anchor.get_text(" ", strip=True).split())
        url = urljoin(source, anchor["href"])
        if (urlparse(url).hostname in {"www.science.org", "science.org"} and
                re.fullmatch(r"/journal/[a-z]+/?", urlparse(url).path) and
                (name == "Science" or name.startswith("Science ")) and len(name) < 100):
            result[title_key(name)] = catalog_entry(name, source, homepage=url, family="science",
                                                   catalog_status="current_directory")
    return list(result.values())


def parse_springer_xlsx(data: bytes, source: str, year: int) -> list[dict]:
    """Read title/ISSN fields without executing office content or adding a dependency."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(entry.file_size for entry in archive.infolist()) > 30_000_000:
            raise ValueError("Publisher catalog exceeds expansion limit")
        strings = ["".join(node.itertext()) for node in
                   ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("m:si", ns)]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        headers, found = {}, []
        for row in sheet.findall(".//m:sheetData/m:row", ns)[:12000]:
            cells = {}
            for cell in row.findall("m:c", ns):
                value = cell.find("m:v", ns)
                text = value.text if value is not None else ""
                text = strings[int(text)] if cell.get("t") == "s" and text else text
                cells[re.sub(r"\d", "", cell.get("r", ""))] = text or ""
            if "Title" in cells.values() and "ISSN electronic" in cells.values():
                headers = {value: key for key, value in cells.items()}
                continue
            name = cells.get(headers.get("Title", ""), "").strip()
            if not name or name == "Nature Digest":
                continue
            family = family_for(name)
            # Broad optics/materials candidates are deliberately not called CAS 1/2.
            if family == "optics-materials-core-candidate" and not re.search(
                    r"optics|optical|photon|laser|material|nanophoton|nanoscience|nanotechnology",
                    name, re.I):
                continue
            issns = [cells.get(headers.get(key, ""), "").strip()
                     for key in ("ISSN electronic", "ISSN print")]
            issns = list(dict.fromkeys(value for value in issns if valid_issn(value)))
            if not issns:
                continue
            comments = cells.get(headers.get("Comments", ""), "")
            found.append(catalog_entry(name, source, issns=issns, issn=issns[0], issn_status="publisher_verified",
                                       catalog_year=year, catalog_status="publisher_catalog",
                                       catalog_note=comments[:500]))
    return found


def parse_cas2025_xlsx(data: bytes, source: str) -> list[dict]:
    """Extract only relevant rows from the university's public 2025 republication."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(entry.file_size for entry in archive.infolist()) > 100_000_000:
            raise ValueError("CAS table exceeds expansion limit")
        strings = ["".join(node.itertext()) for node in
                   ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("m:si", ns)]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        headers, found = {}, []
        for row in sheet.findall(".//m:sheetData/m:row", ns)[:30000]:
            cells = {}
            for cell in row.findall("m:c", ns):
                value = cell.find("m:v", ns)
                text = value.text if value is not None else ""
                text = strings[int(text)] if cell.get("t") == "s" and text else text
                cells[re.sub(r"\d", "", cell.get("r", ""))] = text or ""
            if "刊名" in cells.values() and "大类分区" in cells.values():
                headers = {value: key for key, value in cells.items()}
                continue
            value = lambda key: cells.get(headers.get(key, ""), "").strip()
            category, zone, details = value("大类学科"), value("大类分区"), value("小类分区明细")
            material = category == "材料科学" and zone in {"1", "2"}
            optics = bool(re.search(r"\bOPTICS\s+光学[（(]([12])区[）)]", details))
            if not material and not optics:
                continue
            name, issn = value("刊名"), value("ISSN")
            if not name or not valid_issn(issn):
                continue
            found.append(catalog_entry(
                name, source, issns=[issn], issn=issn, issn_status="university_table",
                partition_status="verified_university_2025_table",
                partition_evidence_kind="university_republication",
                partition_evidence_url=source, partition_major_category=category,
                partition_major=int(zone), partition_categories=details,
                partition_match=[*(["材料科学大类1/2区"] if material else []),
                                 *(["OPTICS光学小类1/2区"] if optics else [])],
            ))
    if not headers:
        raise ValueError("Unexpected CAS table columns")
    return found


def fetch_public(url: str, *, deadline: float, max_bytes: int = 4_000_000) -> bytes:
    """Fixed publisher/Crossref hosts only, bounded time, redirects and body."""
    current = url
    for _ in range(4):
        parsed = urlparse(current)
        if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or
                parsed.username or parsed.password or parsed.port not in {None, 443}):
            raise ValueError("Unexpected catalog host")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Catalog budget exhausted")
        with httpx.Client(timeout=min(8, remaining), follow_redirects=False,
                          headers={"User-Agent": "Optinfo/0.1 (https://github.com/Leonids312/Optinfo)"}) as client:
            with client.stream("GET", current) as response:
                if response.is_redirect:
                    current = urljoin(current, response.headers["location"])
                    continue
                response.raise_for_status()
                result = bytearray()
                for chunk in response.iter_bytes():
                    result.extend(chunk)
                    if len(result) > max_bytes or time.monotonic() > deadline:
                        raise TimeoutError("Catalog response exceeds budget")
                return bytes(result)
    raise ValueError("Too many catalog redirects")


def verify_crossref(journal: dict, fetcher=fetch_public, *, deadline: float) -> dict:
    issns = [value for value in journal.get("issns", []) if valid_issn(value)]
    url = ("https://api.crossref.org/journals/" + issns[0] if issns else
           "https://api.crossref.org/journals?" + urlencode({"query": journal["name"], "rows": 8}))
    message = json.loads(fetcher(url, deadline=deadline))["message"]
    choices = message.get("items", []) if "items" in message else [message]
    wanted = {title_key(journal["name"]), *(title_key(x) for x in journal.get("aliases", []))}
    matched = next((entry for entry in choices if title_key(entry.get("title", "")) in wanted), None)
    if matched is None:
        raise ValueError("Crossref journal title does not match publisher catalog")
    verified = [value for value in matched.get("ISSN", []) if valid_issn(value)]
    if not verified or issns and not set(issns).intersection(verified):
        raise ValueError("Crossref ISSN mismatch")
    # Preserve the publisher's preferred electronic ISSN if Crossref confirms it.
    # A few registry journal records incorrectly combine neighbouring titles.
    # Never expand an authoritative publisher/table ISSN list with those values.
    merged = [value for value in issns if value in verified] if issns else verified
    return {**journal, "issns": merged, "issn": merged[0], "issn_status": "crossref_verified",
            "crossref_title": matched["title"], "crossref_source": url}


def cache_path() -> Path:
    default = (Path(os.environ["OPTINFO_DB_PATH"]).parent if os.environ.get("OPTINFO_DB_PATH")
               else Path(__file__).resolve().parents[1] / "data")
    data = Path(os.environ.get("OPTINFO_DATA_DIR", default))
    return data / "journal-catalog.json"


def _read_document(path: Path) -> dict:
    try:
        if path.stat().st_size > 3_000_000:
            return {}
        document = json.loads(path.read_text(encoding="utf-8"))
        return document if isinstance(document, dict) and document.get("schema_version") == 1 else {}
    except (OSError, ValueError, TypeError):
        return {}


def merge_journals(*groups: list[dict]) -> list[dict]:
    result = {}
    for group in groups:
        for entry in group[:1500]:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                continue
            key = title_key(entry["name"])
            previous = result.get(key, {})
            merged = {**previous, **entry}
            merged["id"] = previous.get("id") or entry.get("id") or journal_id(entry["name"])
            merged["provenance"] = list(dict.fromkeys(previous.get("provenance", []) + entry.get("provenance", [])))
            merged["issns"] = list(dict.fromkeys(previous.get("issns", []) + entry.get("issns", [])))
            if merged["issns"]:
                merged["issn"] = previous.get("issn") or entry.get("issn") or merged["issns"][0]
            if previous.get("family") and previous["family"] != "optics-materials-core-candidate":
                merged["family"] = previous["family"]
                merged["name"] = previous["name"]
            if previous.get("partition_status") == "verified_university_2025_table" and entry.get("partition_status") != "verified_university_2025_table":
                for field, value in previous.items():
                    if field.startswith("partition_"):
                        merged[field] = value
            if (previous.get("issn_status") == "crossref_verified" and entry.get("issn_status") != "crossref_verified" or
                    previous.get("issn_status") in {"publisher_verified", "university_table"} and entry.get("issn_status") == "unverified"):
                for field in ("issn", "issns", "issn_status", "crossref_title", "crossref_source"):
                    if field in previous:
                        merged[field] = previous[field]
            result[key] = merged
    return sorted(result.values(), key=lambda row: row["name"].casefold())[:1500]


def get_journals(*, path: Path | None = None) -> list[dict]:
    """Disk-only: safe in bootstrap; never make an API request wait on publishers."""
    return merge_journals(_read_document(SEED_PATH).get("journals", []),
                          _read_document(path or cache_path()).get("journals", []))


def refresh_catalog(*, path: Path | None = None, now: datetime | None = None,
                    fetcher=fetch_public, budget_seconds: float = 45) -> tuple[list[dict], list[dict]]:
    path = path or cache_path()
    now = now or datetime.now(timezone.utc)
    previous = _read_document(path)
    journals = get_journals(path=path)
    try:
        refresh_after = datetime.fromisoformat(previous.get("refresh_after", ""))
        if now < refresh_after:
            return journals, [{"source": "journal-catalog", "status": "cached", "count": len(journals)}]
    except (ValueError, TypeError):
        pass
    deadline = time.monotonic() + max(1, min(90, budget_seconds))
    diagnostics, discovered = [], []
    # Current A-Z gives new launches/renames; series pages are a fallback if its layout changes.
    nature_found = []
    for url in [NATURE_INDEX, *NATURE_SERIES]:
        if time.monotonic() >= deadline:
            break
        try:
            entries = parse_nature_directory(fetcher(url, deadline=deadline).decode("utf-8"), url)
            if not entries:
                raise ValueError("Publisher page has no journal links")
            nature_found.extend(entries)
            diagnostics.append({"source": url, "status": "ok", "count": len(entries)})
            if url == NATURE_INDEX and len(entries) >= 80:
                break
        except Exception as exc:
            diagnostics.append({"source": url, "status": "failed", "error": type(exc).__name__})
    discovered.extend(nature_found)
    for url, parser in [(SCIENCE_INDEX, parse_science_directory)]:
        if time.monotonic() >= deadline:
            break
        try:
            entries = parser(fetcher(url, deadline=deadline).decode("utf-8"), url)
            if not entries:
                raise ValueError("Publisher page has no journal links")
            discovered.extend(entries)
            diagnostics.append({"source": url, "status": "ok", "count": len(entries)})
        except Exception as exc:
            diagnostics.append({"source": url, "status": "failed", "error": type(exc).__name__})
    # Catalog JSON already contains a verified publisher XLSX snapshot. Discovery
    # refreshes new titles incrementally without rechecking hundreds of known ISSNs.
    merged = merge_journals(journals, discovered)
    unverified = [journal for journal in merged if journal.get("issn_status") not in {"crossref_verified", "not_applicable"}]
    old_cursor = previous.get("verification_cursor", 0)
    cursor = (old_cursor if isinstance(old_cursor, int) else 0) % max(1, len(unverified))
    pending_checks = (unverified[cursor:] + unverified[:cursor])[:12]
    for journal in pending_checks:
        if time.monotonic() >= deadline:
            break
        try:
            checked = verify_crossref(journal, fetcher, deadline=deadline)
            merged = merge_journals(merged, [checked])
        except Exception as exc:
            diagnostics.append({"source": "crossref-catalog-" + journal["id"], "status": "failed", "error": type(exc).__name__})
    success = bool(discovered)
    document = {
        "schema_version": 1, "checked_at": now.isoformat(),
        "refresh_after": (now + timedelta(days=7 if success else 1)).isoformat(),
        "verification_cursor": cursor + len(pending_checks),
        "journals": merged, "diagnostics": diagnostics,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            stream.write(json.dumps(document, ensure_ascii=False))
            temporary = Path(stream.name)
        temporary.replace(path)
    except OSError:
        diagnostics.append({"source": "journal-catalog-cache", "status": "failed", "error": "CacheWriteError"})
    diagnostics.append({"source": "journal-catalog", "status": "ok" if success else "stale",
                        "count": len(merged), "partition_year": 2025,
                        "cas_verified_count": sum(row.get("partition_status") == "verified_university_2025_table" for row in merged)})
    return merged, diagnostics
