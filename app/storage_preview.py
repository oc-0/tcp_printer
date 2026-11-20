from pathlib import Path
from typing import Optional, Tuple


IMAGE_PREVIEW_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
TEXT_PREVIEW_EXTENSIONS = {".txt", ".md"}
WORD_PREVIEW_EXTENSIONS = {".doc", ".docx"}
MAX_TEXT_PREVIEW_BYTES = 1024 * 1024


def preview_kind(filename: str) -> Optional[str]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in WORD_PREVIEW_EXTENSIONS:
        return "word"
    if suffix in TEXT_PREVIEW_EXTENSIONS:
        return "text"
    if suffix in IMAGE_PREVIEW_TYPES:
        return "image"
    return None


def read_text_preview(path: Path, max_bytes: int = MAX_TEXT_PREVIEW_BYTES) -> Tuple[str, bool]:
    with Path(path).open("rb") as source:
        data = source.read(max_bytes + 4)
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    truncated = len(data) > max_bytes
    return data[:max_bytes].decode("utf-8", errors="replace"), truncated
