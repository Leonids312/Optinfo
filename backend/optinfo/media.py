"""Private, content-addressed copies of source images and original PDF pages."""
from __future__ import annotations

import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
import uuid

from PIL import Image

MAX_PIXELS = 20_000_000
MAX_IMAGE_BYTES = 10_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def media_root(db_path: Path | None = None) -> Path:
    data = Path(os.getenv("OPTINFO_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
    default = (db_path or Path(os.getenv("OPTINFO_DB_PATH", data / "optinfo.sqlite3"))).parent / "media"
    return Path(os.getenv("OPTINFO_MEDIA_DIR", default)).resolve()


def store_image(data: bytes, root: Path | None = None) -> str:
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Source image too large")
    with Image.open(BytesIO(data)) as original:
        if original.format not in {"PNG", "JPEG", "WEBP"} or original.width * original.height > MAX_PIXELS:
            raise ValueError("Unsupported source image")
        original.load()
        # Rasterization preserves the source pixels; no generated or inferred image.
        image = original.convert("RGBA" if "A" in original.getbands() else "RGB")
        output = BytesIO()
        image.save(output, format="PNG")
    encoded = output.getvalue()
    if len(encoded) > MAX_IMAGE_BYTES:
        raise ValueError("Normalized source image too large")
    name = hashlib.sha256(encoded).hexdigest() + ".png"
    directory = (root or media_root()).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / name
    if not path.exists():
        temporary = directory / ("." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_bytes(encoded)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return "/api/media/" + name


def media_path(name: str, root: Path) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}\.png", name):
        raise ValueError("Invalid media name")
    path = root / name
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise ValueError("Invalid media path")
    return path


def render_pdf_page(data: bytes, page_number: int) -> bytes:
    import pypdfium2 as pdfium
    if len(data) > 25_000_000 or not data.startswith(b"%PDF-"):
        raise ValueError("Invalid or oversized PDF")
    document = pdfium.PdfDocument(data)
    try:
        if len(document) > 80 or not 1 <= page_number <= len(document):
            raise ValueError("PDF page limit exceeded")
        page = document[page_number - 1]
        try:
            width, height = page.get_size()
            if not 0 < width <= 4000 or not 0 < height <= 4000:
                raise ValueError("Invalid PDF page dimensions")
            scale = min(2.0, 2200 / max(width, height))
            bitmap = page.render(scale=scale)
            try:
                output = BytesIO()
                bitmap.to_pil().save(output, format="PNG")
                return output.getvalue()
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()
