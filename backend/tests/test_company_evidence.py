import json

from bs4 import BeautifulSoup
import pytest

from optinfo import sources
from optinfo.industry_sources import INDUSTRY_SOURCES, parse_page


TEXT = ("The company introduced a wafer inspection platform with a new optical measurement method. "
        "The reported result applies to the specified production configuration, not every wafer. ") * 3
FOOTER = "Unrelated global navigation and recommended article. " * 20


@pytest.mark.parametrize(("url", "body"), [
    ("https://www.intel.com/content/www/us/en/newsroom/news/process.html",
     f'<div class="cmp-list-section__section-body"><p>{TEXT}</p></div>'),
    ("https://news.samsung.com/global/new-memory",
     f'<div class="single_contents"><p>{TEXT}</p></div>'),
    ("https://news.skhynix.com/en/new-memory/",
     f'<article class="type-post"><div class="post-contents"><p>{TEXT}</p></div></article>'),
    ("https://investors.micron.com/news/press-release/2026/new-memory/default.aspx",
     f'<div class="evergreen-news-body"><p>{TEXT}</p></div>'),
    ("https://www.zeiss.com/semiconductor-manufacturing-technology/news-and-events/smt-press-releases/2026/device.html",
     f'<main id="content"><div class="text-block"><p>{TEXT}</p></div></main>'),
    ("https://www.hamamatsu.com/us/en/news/announcements/2026/device.html",
     f'<div class="news-details"><p>{TEXT}</p></div>'),
    ("https://www.ipgphotonics.com/newsroom/news/laser",
     f'<div class="stories-disc"><div class="content"><p>{TEXT}</p></div></div>'),
    ("https://www.trumpf.com/en_US/newsroom/global-press-releases/press-release-detail-page/release/laser/",
     f'<div class="news"><div class="u-text-content"><p>{TEXT}</p></div></div>'),
])
def test_verified_company_templates_exclude_page_navigation(monkeypatch, url, body):
    html = f"<nav>{FOOTER}</nav>{body}<footer>{FOOTER}</footer>"
    monkeypatch.setattr(sources, "fetch", lambda *args, **kwargs: (html, url))
    candidate = sources.candidate("New platform", url, "Company", "industry", "2026-10-20", "")
    result = sources.enrich(candidate)
    assert result["evidence_level"] == "source_article"
    assert result["access_status"] == "accessible"
    assert TEXT.strip() in result["evidence_text"]
    assert "Unrelated" not in result["evidence_text"]
    assert result["evidence_url"] == url


def test_intel_body_sections_are_combined_without_related_story():
    body = (f'<div class="cmp-list-section__section-body"><p>{TEXT}</p></div>'
            '<div class="cmp-list-section__section-body"><p>Second technical condition.</p></div>'
            f'<div class="cmp-teaser">{FOOTER}</div>')
    article = sources.company_article(BeautifulSoup(body, "html.parser"),
                                      "https://www.intel.com/content/www/us/en/newsroom/news/device.html")
    assert article.get_text().endswith("Second technical condition.")
    assert "Unrelated" not in article.get_text()


def test_skhynix_media_download_and_related_news_are_not_article():
    html = (f'<main><article class="type-post"><div class="post-media-title">Image only</div>'
            '<div class="post-media-description"></div></article>'
            f'<div class="sec-bottom-contents"><article>{FOOTER}</article></div></main>')
    assert sources.company_article(BeautifulSoup(html, "html.parser"),
                                   "https://news.skhynix.com/en/media-photo-9/") is None


def test_skhynix_feed_skips_media_category_but_keeps_real_article():
    spec = next(s for s in INDUSTRY_SOURCES if s["id"] == "sk-hynix")
    body = """<rss version="2.0"><channel><title>Official</title>
    <item><title>Photo</title><link>https://news.skhynix.com/en/ocp-9/</link>
    <category>Media</category><pubDate>Tue, 20 Oct 2026 00:00:00 +0000</pubDate></item>
    <item><title>Article</title><link>https://news.skhynix.com/en/ocp/</link>
    <category>TECH&amp;AI</category><pubDate>Tue, 20 Oct 2026 00:00:00 +0000</pubDate></item>
    </channel></rss>"""
    assert [row["title"] for row in parse_page(spec, body, spec["url"])["rows"]] == ["Article"]


def jsonld_page(node):
    return BeautifulSoup('<script type="application/ld+json">' + json.dumps(node) + "</script>"
                         f"<main>{FOOTER}</main>", "html.parser")


def test_jsonld_article_body_requires_current_page_identity():
    url = "https://official.example/news/new"
    correct = {"@type": "NewsArticle", "url": url, "articleBody": TEXT}
    wrong = {"@type": "NewsArticle", "url": url + "-other", "articleBody": FOOTER}
    article = sources.company_article(jsonld_page({"@graph": [wrong, correct]}), url, "New")
    assert article.get_text().strip() == TEXT.strip()
    assert sources.company_article(jsonld_page(wrong), url, "New") is None


def test_jsonld_headline_match_supported_when_no_page_identifier():
    node = {"@type": ["Article"], "headline": "New optical platform", "articleBody": TEXT}
    assert sources.company_article(jsonld_page(node), "https://official.example/news/new",
                                   "New optical platform") is not None
    assert sources.company_article(jsonld_page(node), "https://official.example/news/new",
                                   "Unrelated story") is None


def test_jsonld_description_webpage_and_arbitrary_main_are_not_full_article():
    url = "https://official.example/news/new"
    for node in (
        {"@type": "NewsArticle", "url": url, "description": TEXT},
        {"@type": "WebPage", "url": url, "articleBody": TEXT},
        {"@type": "VideoObject", "url": url, "transcript": TEXT},
    ):
        assert sources.company_article(jsonld_page(node), url) is None
    assert sources.company_article(BeautifulSoup(f"<main>{TEXT}</main>", "html.parser"), url) is None


def test_jsonld_used_before_enrich_removes_scripts(monkeypatch):
    url = "https://official.example/news/new"
    body = str(jsonld_page({"@type": "NewsArticle", "mainEntityOfPage": {"@id": url},
                           "articleBody": "<p>" + TEXT + "</p>"}))
    monkeypatch.setattr(sources, "fetch", lambda *args, **kwargs: (body, url))
    item = sources.candidate("New device", url, "Official", "industry", "2026-10-20", "")
    result = sources.enrich(item)
    assert result["access_status"] == "accessible" and "Unrelated" not in result["evidence_text"]


def test_company_selector_does_not_upgrade_research_preview(monkeypatch):
    url = "https://news.samsung.com/global/preview"
    body = f'<div class="single_contents"><p>{TEXT * 4}</p></div>'
    monkeypatch.setattr(sources, "fetch", lambda *args, **kwargs: (body, url))
    item = sources.candidate("Paper preview", url, "Source", "research", "2026-10-20", "Abstract only")
    result = sources.enrich(item)
    assert result["evidence_level"] == "abstract" and result["access_status"] == "abstract_only"
