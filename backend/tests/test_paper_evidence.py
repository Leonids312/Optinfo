"""Evidence provenance, math fidelity, bounded PDF rendering and explicit repair."""
from datetime import datetime
from io import BytesIO
import json
from uuid import uuid4

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import pytest

from optinfo import harness, pipeline, sources
from optinfo.api import create_app
from optinfo.config import Settings, SHANGHAI
from optinfo.db import Database
from optinfo.media import media_path, render_pdf_page, store_image
from optinfo.paper_evidence import figure_candidates, pdf_evidence, text_with_math


def make_pdf(caption="FIG. 1. Experimental measured result.", pages=1):
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
    for _ in range(pages):
        page = writer.add_blank_page(width=400, height=500)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        content = DecodedStreamObject()
        content.set_data(("BT /F1 12 Tf 30 400 Td (" + caption + ") Tj ET").encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(content)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def paper():
    value = sources.candidate("Original paper", "https://example.org/paper", "Journal", "research", "2026-10-15T00:00:00Z", "Original evidence. " * 100)
    value.update(evidence_level="fulltext", figure_candidates=[{"id": "real-4", "url": "https://example.org/fig4.png", "source_url": value["source_url"], "original_caption": "Figure 4. Key experimental result.", "kind": "original_image"}])
    return value


def result(**kwargs):
    return {"title": "原始研究", "summary": r"测量得到 \(\tau = 2\,\mathrm{ps}\)。", "importance": 90, "publish": True,
            "content": {key: "依据原始论文中的实验结果。" for key in ("background", "approach", "results", "assessment")},
            "tags": [], "company_mentions": [], "selected_figures": [{"id": "real-4", "role": "result", "caption": "图四为测量所得的主要实验结果。", "selection_reason": "该图支持正文中的关键实验结论。"}], **kwargs}


def test_mathml_tex_annotation_fraction_and_scripts_preserved():
    text = text_with_math('<p>Test <math><semantics><mi>x</mi><annotation encoding="application/x-tex">E_g=2</annotation></semantics></math><math display="block"><mfrac><mi>a</mi><msup><mi>b</mi><mn>2</mn></msup></mfrac></math><script type="math/tex">\\Delta T</script></p>')
    assert r"\(E_g=2\)" in text
    assert r"\[\frac{a}{{b}^{2}}\]" in text
    assert r"\(\Delta T\)" in text


def test_nature_mathjax_preserves_original_latex_and_counts():
    from optinfo.paper_evidence import preserve_math
    soup = BeautifulSoup(r'<article><span class="mathjax-tex">\(V\sim\lambda^3/2500\)</span><div class="c-article-equation"><span class="mathjax-tex">$$E=mc^2$$</span></div></article>', "html.parser")
    assert preserve_math(soup) == 2
    assert r"\(V\sim\lambda^3/2500\)" in soup.get_text()
    assert "$$E=mc^2$$" in soup.get_text()


@pytest.mark.parametrize("accent, expected", [("→", r"\vec{E}"), ("¯", r"\bar{E}"), ("^", r"\hat{E}"), ("~", r"\tilde{E}")])
def test_mathml_accent_semantics_are_not_superscripts(accent, expected):
    text = text_with_math(f'<math><mover accent="true"><mi>E</mi><mo>{accent}</mo></mover></math>')
    assert expected in text and "^{" not in text


def test_nature_abstract_and_subscription_preview_is_not_fulltext(monkeypatch):
    html = '<article><section id="Abs1"><div class="c-article-section__content">' + 'Abstract only. ' * 100 + '</div></section><p>Access this article through your institution</p><figure><img src="https://example.org/fig.png"><figcaption>Figure 1. Graphical abstract preview.</figcaption></figure></article>'
    monkeypatch.setattr(sources, "fetch", lambda url, **kwargs: (html, url))
    value = sources.enrich(paper())
    assert value["evidence_level"] != "fulltext" and value["figure_candidates"] == []


def test_nature_fulltext_requires_real_sections(monkeypatch):
    html = '<article><section><h2>Results</h2><div class="c-article-section__content">' + 'Actual measured results. ' * 80 + '</div></section><section><h2>Methods</h2><p>Experimental methods.</p></section><figure><img src="https://example.org/fig.png"><figcaption>Figure 1. Measured results.</figcaption></figure></article>'
    monkeypatch.setattr(sources, "fetch", lambda url, **kwargs: (html, url))
    value = sources.enrich(paper())
    assert value["evidence_level"] == "fulltext" and len(value["figure_candidates"]) == 1


def test_repair_html_failure_uses_fresh_pdf_text_not_old_fulltext(monkeypatch):
    original = dict(paper(), source_url="https://arxiv.org/abs/2601.12345", evidence_text="OLD FLATTENED EVIDENCE", math_extraction="old")
    def fail(*args, **kwargs):
        raise RuntimeError("Unavailable HTML")
    monkeypatch.setattr(sources, "fetch", fail)
    monkeypatch.setattr(sources, "fetch_bytes", lambda *args, **kwargs: (b"%PDF-fixture", "https://arxiv.org/pdf/2601.12345", "application/pdf"))
    monkeypatch.setattr(sources, "pdf_evidence", lambda *args: {"evidence_text": "NEW PDF TEXT " * 150, "figure_candidates": paper()["figure_candidates"], "_pdf_bytes": b"%PDF-fixture", "evidence_level": "fulltext_pdf", "math_extraction": "pdf_text_requires_verification"})
    value = sources.enrich(original)
    assert value["evidence_level"] == "fulltext_pdf" and value["evidence_text"].startswith("NEW PDF TEXT")
    assert value["math_extraction"] == "pdf_text_requires_verification"


def test_all_figure_candidates_and_source_context_not_first_three():
    body = "<article>" + "".join(f'<p>Measured result in <a href="#f{i}">Figure {i}</a>.</p><figure id="f{i}"><img src="//images.example.org/f{i}.png"><figcaption>Figure {i}. Actual measured result.</figcaption></figure>' for i in range(1, 9)) + "</article>"
    values = figure_candidates(BeautifulSoup(body, "html.parser"), "https://example.org/paper")
    assert len(values) == 8 and len({value["id"] for value in values}) == 8
    assert "Measured result" in values[7]["context"]
    assert values[7]["source_url"] == "https://example.org/paper"


def test_svg_object_requires_original_pdf_fallback():
    soup = BeautifulSoup('<figure><object data="f.svg" type="image/svg+xml"></object><figcaption>Figure 1. Original graph.</figcaption></figure>', "html.parser")
    assert figure_candidates(soup, "https://arxiv.org/html/1234.56789") == []


@pytest.mark.parametrize("caption", ["FIG. 1. Measured result.", "FIG. 1 Measured result.", "FIG. 2 Linear and nonlinear response."])
def test_pdf_original_page_provenance_and_render(caption):
    data = make_pdf(caption)
    evidence = pdf_evidence(data, "https://example.org/paper.pdf")
    figure = evidence["figure_candidates"][0]
    assert figure["page"] == 1 and figure["kind"] == "pdf_page" and figure["url"].endswith("#page=1")
    with Image.open(BytesIO(render_pdf_page(data, figure["page"]))) as image:
        assert image.format == "PNG" and max(image.size) <= 2200
    assert pdf_evidence(make_pdf("See FIG. 2 for details."), "https://example.org/p.pdf")["figure_candidates"] == []


def test_pdf_rejects_nonpdf_and_page_limit():
    with pytest.raises(ValueError):
        pdf_evidence(b"<html>paywall</html>", "https://example.org/p.pdf")
    with pytest.raises(ValueError):
        pdf_evidence(make_pdf(pages=81), "https://example.org/p.pdf")


@pytest.mark.parametrize("text", [r"公式 \(x=2", r"公式 x^2", r"公式 E_g", r"公式 \frac{a}{b}", r"公式 \[x\)"])
def test_invalid_math_rejected(text):
    with pytest.raises(ValueError):
        harness.validate_analysis(result(summary=text))


def test_math_currency_company_names_and_roundtrip():
    for text in [r"公式 \(E_g=2\) 和 \[x^2\]。", "企业投入 $100 million。", "Open_AI 与 NVIDIA_GEFORCE 公告。"]:
        harness.validate_math_format(text)
    assert harness.validate_analysis(json.loads(json.dumps(result())))["summary"] == result()["summary"]


def test_chinese_prefix_cannot_disguise_english_caption():
    value = result()
    value["selected_figures"][0]["caption"] = "中文结果说明：The original measured experimental result shows a response to the increasing energy."
    with pytest.raises(ValueError):
        harness.validate_analysis(value)


@pytest.mark.parametrize("failure", ["math", "figure_id"])
def test_math_and_figure_validation_share_existing_bounded_retry(monkeypatch, tmp_path, failure):
    invalid = result(summary=r"未包裹公式 \tau=2") if failure == "math" else result()
    if failure == "figure_id":
        invalid["selected_figures"][0]["id"] = "invented"
    answers = iter([invalid, result()])
    calls = []
    def run(prompt, *_args, **_kwargs):
        calls.append(prompt)
        return json.dumps(next(answers), ensure_ascii=False)
    monkeypatch.setattr(harness, "_run", run)
    monkeypatch.setattr(harness, "runtime_root", lambda: tmp_path)
    assert harness.analyze_candidate(paper(), [], "2026-10-16")["selected_figures"][0]["id"] == "real-4"
    assert len(calls) == 2


def test_fabricated_figure_ids_and_english_caption_never_publish(monkeypatch):
    monkeypatch.setattr(pipeline, "materialize_figure", lambda *_: "/api/media/" + "a" * 64 + ".png")
    value = result()
    value["selected_figures"][0]["id"] = "made-up"
    with pytest.raises(pipeline.EvidencePending):
        pipeline.sanitize_analysis(paper(), value, "2026-10-16")
    valid = pipeline.sanitize_analysis(paper(), result(), "2026-10-16")
    assert valid["figures"][0]["url"] == "https://example.org/fig4.png"
    assert valid["figures"][0]["original_caption"].startswith("Figure 4")


def test_missing_paper_evidence_remains_partial_and_unreviewed_without_billing(monkeypatch):
    raw = paper()
    raw["evidence_level"] = "abstract"
    monkeypatch.setattr(pipeline, "is_configured", lambda: True)
    monkeypatch.setattr(pipeline, "collect", lambda *_: ([raw], [{"source": "fixture", "status": "ok"}]))
    monkeypatch.setattr(pipeline, "enrich", lambda value: value)
    monkeypatch.setattr(pipeline, "analyze_candidate", lambda *_: pytest.fail("Incomplete evidence must not be billed"))
    value = pipeline.generate_edition("2026-10-16", [])
    assert value["status"] == "partial" and value["items"] == [] and value["reviewed_ids"] == []
    assert value["diagnostics"][-1]["status"] == "evidence_pending"


def test_media_auth_content_type_and_path_validation(tmp_path, monkeypatch):
    monkeypatch.delenv("OPTINFO_MEDIA_DIR", raising=False)
    output = BytesIO()
    Image.new("RGB", (8, 8), color="red").save(output, format="PNG")
    path = store_image(output.getvalue(), tmp_path / "media")
    config = Settings(tmp_path / "data.db", tmp_path / "chats", "secret-test")
    with TestClient(create_app(config)) as client:
        assert client.get(path).status_code == 401
        response = client.get(path, headers={"Authorization": "Bearer secret-test"})
        assert response.status_code == 200 and response.headers["content-type"] == "image/png"
        assert response.content.startswith(b"\x89PNG")
        assert client.get("/api/media/other.png", headers={"Authorization": "Bearer secret-test"}).status_code == 404
    with pytest.raises(ValueError):
        media_path("../secret", tmp_path / "media")
    with pytest.raises(Exception):
        store_image(b"<svg>untrusted</svg>", tmp_path / "media")


def test_explicit_repair_updates_all_snapshots_and_preserves_user_data(tmp_path, monkeypatch):
    db = Database(tmp_path / "test.db").init()
    original = paper()
    db.save_edition("2026-10-15", [dict(original, importance=71)])
    db.save_edition("2026-10-16", [dict(original, importance=95)])
    db.set_item_bookmark(original["id"], True)
    now = datetime(2026, 10, 16, 10, tzinfo=SHANGHAI)
    session = str(uuid4())
    db.ensure_chat_session(original["id"], session, now)
    db.append_chat_exchange(original["id"], session, "原始问题", "原始回答", now)
    old_chat = db.get_chat(original["id"])
    monkeypatch.setattr(pipeline, "is_configured", lambda: True)
    monkeypatch.setattr(pipeline, "enrich", lambda value: dict(value, figure_candidates=paper()["figure_candidates"]))
    monkeypatch.setattr(pipeline, "analyze_candidate", lambda *_: result())
    monkeypatch.setattr(pipeline, "materialize_figure", lambda *_: "/api/media/" + "a" * 64 + ".png")
    repaired = pipeline.repair_papers(db, [])
    assert repaired["items"][0]["snapshots"] == 2
    assert db.get_chat(original["id"]) == old_chat
    assert db.get_item(original["id"])["bookmarked"] is True
    for day, importance in [("2026-10-15", 71), ("2026-10-16", 95)]:
        value = db.query_items(day=day)[0]
        assert value["briefing_date"] == day and value["importance"] == importance
        assert value["analysis_status"] == "complete" and value["content_revision"] == 2
    with pytest.raises(ValueError):
        db.repair_item_content(original["id"], {"id": "wrong"})
