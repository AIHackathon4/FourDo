import io
import zipfile
import zlib

import pytest

from aihack4.collector import attachments
from aihack4.collector.attachments import OLE_MAGIC, attachment_to_text, parse_hwp_records


def record(tag: int, payload: bytes) -> bytes:
    return (tag | (len(payload) << 20)).to_bytes(4, "little") + payload


def utf16(text: str) -> bytes:
    return text.encode("utf-16-le")


def wide_control(code: int) -> bytes:
    return code.to_bytes(2, "little") + b"\x00" * 14


def test_hwp_records_keep_only_paragraph_text():
    data = record(66, b"\x00" * 10) + record(67, utf16("마감은 11월")) + record(67, utf16("팀은 3명"))
    assert parse_hwp_records(data) == "마감은 11월\n팀은 3명"


def test_hwp_text_skips_wide_controls():
    payload = utf16("앞") + wide_control(11) + utf16("뒤") + (13).to_bytes(2, "little")
    assert parse_hwp_records(record(67, payload)) == "앞뒤\n"


def test_hwp_tab_control_becomes_space():
    assert parse_hwp_records(record(67, utf16("a") + wide_control(9) + utf16("b"))) == "a b"


def test_hwp_extended_record_size():
    payload = utf16("가" * 3000)
    data = (67 | (0xFFF << 20)).to_bytes(4, "little") + len(payload).to_bytes(4, "little") + payload
    assert parse_hwp_records(data) == "가" * 3000


class FakeOle:
    def __init__(self, flags: int, sections: dict[str, bytes], signature=b"HWP Document File"):
        self.streams = {"FileHeader": signature.ljust(36, b"\0") + flags.to_bytes(4, "little")}
        self.streams.update({f"BodyText/{name}": data for name, data in sections.items()})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def openstream(self, path):
        return io.BytesIO(self.streams[path])

    def listdir(self):
        return [path.split("/") for path in self.streams]


def compress(data: bytes) -> bytes:
    compressor = zlib.compressobj(wbits=-15)
    return compressor.compress(data) + compressor.flush()


def read_hwp(monkeypatch, ole: FakeOle) -> str:
    monkeypatch.setattr(attachments.olefile, "OleFileIO", lambda stream: ole)
    return attachment_to_text(OLE_MAGIC + b"rest")


def test_hwp_reads_compressed_sections_in_numeric_order(monkeypatch):
    sections = {
        "Section10": compress(record(67, utf16("셋째"))),
        "Section2": compress(record(67, utf16("둘째"))),
        "Section0": compress(record(67, utf16("첫째"))),
    }
    assert read_hwp(monkeypatch, FakeOle(1, sections)) == "첫째\n둘째\n셋째"


def test_hwp_reads_uncompressed(monkeypatch):
    assert read_hwp(monkeypatch, FakeOle(0, {"Section0": record(67, utf16("본문"))})) == "본문"


@pytest.mark.parametrize("flags", [2, 4])
def test_hwp_refuses_encrypted_and_distribution(monkeypatch, flags):
    assert read_hwp(monkeypatch, FakeOle(flags, {"Section0": record(67, utf16("본문"))})) == ""


def test_ole_file_that_is_not_hwp_returns_empty(monkeypatch):
    assert read_hwp(monkeypatch, FakeOle(0, {}, signature=b"Other")) == ""


def test_corrupt_hwp_returns_empty():
    assert attachment_to_text(OLE_MAGIC + b"broken") == ""


def test_oversized_hwp_section_is_rejected(monkeypatch):
    monkeypatch.setattr(attachments, "MAX_EXTRACTED_BYTES", 100)
    section = compress(b"\x00" * 100_000)
    assert read_hwp(monkeypatch, FakeOle(1, {"Section0": section})) == ""


SECTION_XML = """<hs:sec xmlns:hs="urn:s" xmlns:hp="urn:p">
<hp:p><hp:run><hp:t>마감은</hp:t><hp:t> 11월</hp:t></hp:run></hp:p>
<hp:p><hp:run><hp:tbl><hp:tc><hp:p><hp:run><hp:t>표 안의 글</hp:t></hp:run></hp:p></hp:tc></hp:tbl></hp:run></hp:p>
</hs:sec>"""


def make_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def test_hwpx_reads_paragraphs_and_tables():
    content = make_zip({"Contents/section0.xml": SECTION_XML, "Contents/header.xml": "<a>무시</a>"})
    assert attachment_to_text(content) == "마감은 11월\n표 안의 글"


def test_hwpx_orders_sections_numerically():
    body = '<s xmlns:hp="urn:p"><hp:p><hp:t>{}</hp:t></hp:p></s>'
    content = make_zip(
        {"Contents/section10.xml": body.format("끝"), "Contents/section2.xml": body.format("처음")}
    )
    assert attachment_to_text(content) == "처음\n끝"


def test_zip_without_hwpx_sections_returns_empty():
    assert attachment_to_text(make_zip({"a.txt": "내용"})) == ""


def test_hwpx_zip_bomb_is_rejected(monkeypatch):
    monkeypatch.setattr(attachments, "MAX_EXTRACTED_BYTES", 10)
    assert attachment_to_text(make_zip({"Contents/section0.xml": SECTION_XML})) == ""


def test_real_pdf_text_is_read():
    stream = b"BT /F1 24 Tf 100 700 Td (Deadline Nov 17) Tj ET"
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n%s\nendstream" % (len(stream), stream),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(pdf)
    pdf += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    pdf += b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % xref
    assert "Deadline Nov 17" in attachment_to_text(pdf)


@pytest.mark.parametrize("content", [b"", b"<html>error</html>", b"%PDF-broken"])
def test_unreadable_content_returns_empty(content):
    assert attachment_to_text(content) == ""
