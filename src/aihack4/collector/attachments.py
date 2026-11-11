"""첨부파일(PDF, HWP, HWPX)에서 본문을 뽑는다.

형식은 확장자가 아니라 파일 내용으로 판별한다. 다운로드 주소에 확장자가 없는 경우가 많다.
"""

import io
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib

import olefile
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from aihack4.collector.parsing import normalize_whitespace

MAX_EXTRACTED_BYTES = 30 * 1024 * 1024
PDF_MAGIC = b"%PDF"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"
HWP_SIGNATURE = b"HWP Document File"
HWP_FLAGS_OFFSET = 36
HWP_COMPRESSED = 1
HWP_ENCRYPTED = 2
HWP_DISTRIBUTION = 4
HWP_TAG_PARA_TEXT = 67
HWP_EXTENDED_SIZE = 0xFFF
# 아래 코드의 제어 문자는 뒤따르는 데이터까지 합쳐 8글자(16바이트)를 차지한다.
HWP_WIDE_CONTROLS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}
PARSE_ERRORS = (
    OSError,
    ValueError,
    KeyError,
    IndexError,
    TypeError,
    AttributeError,
    struct.error,
    zlib.error,
    zipfile.BadZipFile,
    ET.ParseError,
    PyPdfError,
    RecursionError,
)


def _pdf_to_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _inflate(data: bytes) -> bytes:
    decompressor = zlib.decompressobj(-15)
    output = decompressor.decompress(data, MAX_EXTRACTED_BYTES)
    if decompressor.unconsumed_tail:
        raise ValueError("압축을 푼 크기가 너무 큼")
    return output


def _hwp_para_text(data: bytes) -> str:
    """UTF-16 문단 데이터에서 표·그림 같은 제어 코드를 건너뛰고 글자만 남긴다."""
    characters: list[str] = []
    position = 0
    while position + 2 <= len(data):
        code = int.from_bytes(data[position:position + 2], "little")
        if code in HWP_WIDE_CONTROLS:
            if code == 9:
                characters.append(" ")
            position += 16
            continue
        position += 2
        if code in (10, 13):
            characters.append("\n")
        elif code in (30, 31):
            characters.append(" ")
        elif code >= 32:
            characters.append(chr(code))
    return "".join(characters)


def parse_hwp_records(data: bytes) -> str:
    """HWP 본문 스트림의 레코드를 순서대로 읽어 문단을 잇는다."""
    paragraphs: list[str] = []
    position = 0
    while position + 4 <= len(data):
        header = int.from_bytes(data[position:position + 4], "little")
        position += 4
        tag, size = header & 0x3FF, header >> 20
        if size == HWP_EXTENDED_SIZE:
            size = int.from_bytes(data[position:position + 4], "little")
            position += 4
        if tag == HWP_TAG_PARA_TEXT:
            paragraphs.append(_hwp_para_text(data[position:position + size]))
        position += size
    return "\n".join(paragraphs)


def _section_number(stream_path: list[str]) -> int:
    match = re.search(r"(\d+)$", stream_path[-1])
    return int(match.group(1)) if match else 0


def _hwp_to_text(content: bytes) -> str:
    """암호화·배포용 문서는 본문이 암호화돼 있어 읽지 않는다."""
    with olefile.OleFileIO(io.BytesIO(content)) as ole:
        header = ole.openstream("FileHeader").read()
        if not header.startswith(HWP_SIGNATURE):
            return ""
        flags = int.from_bytes(header[HWP_FLAGS_OFFSET:HWP_FLAGS_OFFSET + 4], "little")
        if flags & (HWP_ENCRYPTED | HWP_DISTRIBUTION):
            return ""
        sections = sorted(
            (path for path in ole.listdir() if len(path) == 2 and path[0] == "BodyText"),
            key=_section_number,
        )
        texts = []
        for path in sections:
            data = ole.openstream("/".join(path)).read()
            texts.append(parse_hwp_records(_inflate(data) if flags & HWP_COMPRESSED else data))
    return "\n".join(texts)


def _hwpx_section_text(xml_bytes: bytes) -> str:
    parts: list[str] = []
    for element in ET.fromstring(xml_bytes).iter():
        name = element.tag.rsplit("}", 1)[-1]
        if name == "p":
            parts.append("\n")
        elif name == "t":
            parts.append("".join(element.itertext()))
    return "".join(parts)


def _hwpx_to_text(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        sections = sorted(
            (i for i in archive.infolist() if re.fullmatch(r"Contents/section\d+\.xml", i.filename, re.I)),
            key=lambda info: int(re.search(r"(\d+)\.xml$", info.filename, re.I).group(1)),
        )
        if sum(info.file_size for info in sections) > MAX_EXTRACTED_BYTES:
            raise ValueError("압축을 푼 크기가 너무 큼")
        return "\n".join(_hwpx_section_text(archive.read(info)) for info in sections)


def attachment_to_text(content: bytes) -> str:
    """읽을 수 없는 파일(스캔 PDF, 암호화 문서, 다른 형식)은 빈 문자열을 돌려준다."""
    if content.startswith(PDF_MAGIC):
        reader = _pdf_to_text
    elif content.startswith(OLE_MAGIC):
        reader = _hwp_to_text
    elif content.startswith(ZIP_MAGIC):
        reader = _hwpx_to_text
    else:
        return ""
    try:
        return normalize_whitespace(reader(content))
    except PARSE_ERRORS:
        return ""
