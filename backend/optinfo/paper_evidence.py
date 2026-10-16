"""Preserve source mathematics and enumerate traceable original figure candidates."""
from __future__ import annotations

import hashlib
from io import BytesIO
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

MAX_FIGURES = 32


def _tex(node) -> str:
    if isinstance(node, NavigableString):
        return str(node).strip()
    children = [child for child in node.children if isinstance(child, Tag)]
    parts = [_tex(child) for child in children]
    name = node.name.split(":")[-1]
    if name in {"annotation", "annotation-xml"}:
        return ""
    if name in {"mi", "mn", "mo", "mtext"}:
        text = node.get_text(strip=True)
        symbols = {"α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ε": r"\epsilon", "θ": r"\theta", "λ": r"\lambda", "μ": r"\mu", "π": r"\pi", "σ": r"\sigma", "τ": r"\tau", "ω": r"\omega", "Δ": r"\Delta", "Ω": r"\Omega", "∑": r"\sum", "∫": r"\int", "∞": r"\infty", "×": r"\times", "·": r"\cdot", "≤": r"\leq", "≥": r"\geq", "−": "-", "∂": r"\partial", "∇": r"\nabla"}
        text = " ".join(symbols.get(char, char) for char in text) if any(char in symbols for char in text) else text
        return r"\text{" + text.replace("{", "").replace("}", "") + "}" if name == "mtext" else text
    if name == "mfrac" and len(parts) == 2:
        return r"\frac{" + parts[0] + "}{" + parts[1] + "}"
    if name in {"msup", "msub"} and len(parts) == 2:
        return "{" + parts[0] + "}" + ("^" if name == "msup" else "_") + "{" + parts[1] + "}"
    if name in {"mover", "munder"} and len(parts) == 2:
        accents = {"→": r"\vec", "⃗": r"\vec", "¯": r"\bar", "‾": r"\bar", "̅": r"\bar", "^": r"\hat", "ˆ": r"\hat", "̂": r"\hat", "~": r"\tilde", "˜": r"\tilde", "̃": r"\tilde", ".": r"\dot", "˙": r"\dot", "̇": r"\dot", "¨": r"\ddot", "̈": r"\ddot"}
        if name == "mover" and node.get("accent") != "false" and parts[1] in accents:
            return accents[parts[1]] + "{" + parts[0] + "}"
        if name == "munder" and node.get("accentunder") != "false" and parts[1] in {"_", "¯", "‾", "̲"}:
            return r"\underline{" + parts[0] + "}"
        return (r"\overset{" if name == "mover" else r"\underset{") + parts[1] + "}{" + parts[0] + "}"
    if name in {"msubsup", "munderover"} and len(parts) == 3:
        return "{" + parts[0] + "}_{" + parts[1] + "}^{" + parts[2] + "}"
    if name == "msqrt":
        return r"\sqrt{" + " ".join(parts) + "}"
    if name == "mroot" and len(parts) == 2:
        return r"\sqrt[" + parts[1] + "]{" + parts[0] + "}"
    if name == "mfenced":
        return node.get("open", "(") + " , ".join(parts) + node.get("close", ")")
    if name == "mtable":
        return r"\begin{matrix}" + r" \\ ".join(parts) + r"\end{matrix}"
    if name == "mtr":
        return " & ".join(parts)
    return " ".join(parts)


def preserve_math(soup: BeautifulSoup | Tag) -> int:
    count = 0
    # Nature stores TeX directly in MathJax spans, already with delimiters.
    # Keep those exact strings instead of flattening their mathematical markup.
    for node in list(soup.select(".mathjax-tex")):
        latex = node.get_text("", strip=True)
        if latex:
            node.replace_with(NavigableString(latex))
            count += 1
    for node in list(soup.find_all(lambda tag: tag.name.split(":")[-1] == "math")):
        annotation = node.find(lambda tag: tag.name.split(":")[-1] == "annotation" and "tex" in str(tag.get("encoding", "")).lower())
        latex = annotation.get_text() if annotation else node.get("alttext") or node.get("data-tex") or _tex(node)
        latex = latex.strip()
        if latex:
            display = node.get("display") == "block"
            node.replace_with(NavigableString((r"\[" if display else r"\(") + latex + (r"\]" if display else r"\)")))
            count += 1
    for node in list(soup.select('script[type^="math/tex"]')):
        latex = node.get_text().strip()
        display = "display" in str(node.get("type", ""))
        node.replace_with(NavigableString((r"\[" if display else r"\(") + latex + (r"\]" if display else r"\)")))
        count += 1
    for node in list(soup.select('[data-tex], img.ltx_Math, img[role="math"]')):
        latex = node.get("data-tex") or node.get("alt")
        if latex:
            node.replace_with(NavigableString(r"\(" + latex.strip().strip("$") + r"\)"))
            count += 1
    return count


def text_with_math(value: str, limit: int = 26000) -> str:
    soup = BeautifulSoup(value or "", "html.parser")
    preserve_math(soup)
    return " ".join(soup.get_text(" ", strip=True).split())[:limit]


def figure_candidates(article: Tag, final_url: str) -> list[dict]:
    result, seen = [], set()
    for figure in article.select("figure, .c-article-section__figure, .fig, .figure"):
        caption = figure.select_one("figcaption, .ltx_caption, .c-article-section__figure-caption, .fig-caption, .caption")
        if not caption:
            continue
        original = " ".join(caption.get_text(" ", strip=True).split())[:2200]
        label = figure.select_one(".fig-label")
        if label:
            original = label.get_text(" ", strip=True) + " " + original
        if len(original) < 15:
            continue
        images = figure.select("img")
        urls = []
        for image in images:
            link = image.find_parent("a", href=True)
            linked = link.get("href", "") if link else ""
            srcset = image.get("srcset") or image.get("data-srcset") or ""
            src = linked if "/view-large/" not in linked and re.search(r"\.(?:png|jpe?g|webp)(?:\?|$)", linked, re.I) else image.get("data-src") or image.get("data-original") or image.get("src")
            if not src or str(src).startswith("data:"):
                src = srcset.split(",")[-1].strip().split(" ")[0] if srcset else ""
            url = urljoin(final_url, str(src))
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or url == final_url or url in seen:
                continue
            if any(marker in url.lower() for marker in ("logo", "icon", "tracking", "pixel.gif")):
                continue
            if parsed.path.lower().endswith(".svg"):
                continue  # Original PDF-page fallback safely preserves vector figures.
            urls.append(url)
        if not urls:
            continue
        anchor = figure.get("id", "")
        references = []
        if anchor:
            for reference in article.select('a[href="#' + anchor.replace('"', '') + '"]')[:5]:
                parent = reference.find_parent(["p", "div"])
                if parent:
                    references.append(parent.get_text(" ", strip=True)[:1300])
        for url in urls:
            seen.add(url)
            result.append({"id": "fig-" + hashlib.sha256(url.encode()).hexdigest()[:16], "url": url,
                "original_caption": original, "context": " ".join(dict.fromkeys(references))[:2600],
                "source_url": final_url, "kind": "original_image"})
            if len(result) >= MAX_FIGURES:
                return result
    return result


def pdf_evidence(data: bytes, source_url: str) -> dict:
    from pypdf import PdfReader
    if len(data) > 25_000_000 or not data.startswith(b"%PDF-"):
        raise ValueError("Invalid or oversized source PDF")
    reader = PdfReader(BytesIO(data), strict=False)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 80:
        raise ValueError("Protected or oversized PDF")
    text, figures = [], []
    for number, page in enumerate(reader.pages, 1):
        page_text = page.extract_text() or ""
        text.append(page_text)
        # Only caption-like paragraph starts; an in-body 'see Fig. 2' is not
        # sufficient evidence that a figure is located on this page.
        caption_start = r"(?:(?:fig(?:ure)?\.?\s*\d+[a-z]?|图\s*\d+)\s*[:.：]\s*|(?-i:FIG\.?\s*\d+[A-Za-z]?)\s+(?=[A-Z]))"
        for match in re.finditer(r"(?im)^\s*(" + caption_start + r"[^\n]+(?:\n(?!\s*\n)[^\n]+){0,8})", page_text):
            caption = " ".join(match[1].split())[:2200]
            label = re.match(r"(?i)(?:fig(?:ure)?\.?\s*\d+[a-z]?|图\s*\d+)", caption)[0]
            figure_id = "pdf-" + hashlib.sha256((source_url + f"#{number}:" + label).encode()).hexdigest()[:16]
            figures.append({"id": figure_id, "url": source_url + f"#page={number}", "source_url": source_url,
                "original_caption": caption, "context": page_text[:3200], "kind": "pdf_page", "page": number,
                "label": label})
            if len(figures) >= MAX_FIGURES:
                break
        if len(figures) >= MAX_FIGURES:
            break
    return {"evidence_text": "\n".join(text)[:42000], "figure_candidates": figures,
            "evidence_level": "fulltext_pdf", "evidence_url": source_url, "access_status": "accessible_pdf",
            "math_extraction": "pdf_text_requires_verification", "_pdf_bytes": data}
