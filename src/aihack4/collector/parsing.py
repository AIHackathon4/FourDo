"""HTML에서 본문과 링크를 뽑는다."""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

ATTACHMENT_EXTENSIONS = {".pdf", ".hwp", ".hwpx", ".doc", ".docx", ".zip"}
ATTACHMENT_TEXT = re.compile(r"\.(pdf|hwpx?|docx?|zip)\b", re.I)
BLOCK_TAGS = ["p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table"]
NOISE_TAGS = ["script", "style", "noscript", "template", "iframe", "svg"]
KOGL_MARKER = re.compile(r"공공누리|공공저작물|kogl|opentype|opencode", re.I)
KOGL_TYPE_PATTERNS = (
    re.compile(r"([0-4])\s*유형"),
    re.compile(r"(?:open_?type|opencode|kogl)[_-]?(?:type)?[_-]?0?([0-4])(?!\d)", re.I),
)


@dataclass(frozen=True)
class Link:
    url: str
    text: str
    is_attachment: bool


def normalize_whitespace(text: str) -> str:
    lines = (re.sub(r"[ \t\r\f\v ​]+", " ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def element_text(element) -> str:
    """인라인 태그는 잇고 문단 단위로만 줄을 바꾼다."""
    fragment = BeautifulSoup(str(element), "html.parser")
    for tag in fragment(NOISE_TAGS):
        tag.decompose()
    for tag in fragment("br"):
        tag.replace_with("\n")
    for tag in fragment(BLOCK_TAGS):
        tag.append("\n")
    return normalize_whitespace(fragment.get_text())


def html_to_text(html: str) -> str:
    return element_text(BeautifulSoup(html, "html.parser"))


def extract_links(html: str, base_url: str) -> list[Link]:
    """http(s) 링크를 첨부파일 먼저, 문서 순서대로 돌려준다."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    links: list[Link] = []
    for anchor in soup.find_all("a", href=True):
        url = urljoin(base_url, anchor["href"]).split("#", 1)[0]
        if urlsplit(url).scheme not in {"http", "https"} or url in seen:
            continue
        seen.add(url)
        label = normalize_whitespace(anchor.get_text(" ")).replace("\n", " ")
        extension = PurePosixPath(urlsplit(url).path).suffix.lower()
        links.append(
            Link(url, label, extension in ATTACHMENT_EXTENSIONS or bool(ATTACHMENT_TEXT.search(label)))
        )
    return sorted(links, key=lambda link: not link.is_attachment)


def detect_kogl(html: str) -> int | None:
    """공공누리 유형을 찾는다. 여러 유형이 보이거나 애매하면 None을 돌려 보수적으로 처리한다."""
    source = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", html)
    if not KOGL_MARKER.search(source):
        return None
    types = {int(value) for pattern in KOGL_TYPE_PATTERNS for value in pattern.findall(source)}
    return types.pop() if len(types) == 1 else None
