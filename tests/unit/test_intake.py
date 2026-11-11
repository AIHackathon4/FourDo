import pytest
from test_collector import FakeFetcher, FakeLLM, html_page, notice

from aihack4.collector import intake
from aihack4.collector.fetcher import BlockedUrl, FetchError, Page

TODAY = "2026-11-10"
TEXT = "제1회 테스트 공모전\n접수기간 2026-11-04 00:00 ~ 2026-11-17 18:00\n대학생 누구나, 팀은 최대 3명\n" + "세부 안내 " * 80
URL = "https://org.go.kr/contest"
PDF_URL = "https://org.go.kr/files/guide.pdf"
NO_LINKS = {"open_link_number": None, "attachment_numbers": []}


def test_text_intake_structures_and_has_no_warnings_when_verified():
    llm = FakeLLM([notice()])
    result = intake.intake_text(llm, "m", TEXT, today=TODAY)
    assert result.agent.notice["title"] == "테스트 공모전"
    assert result.listing.end_date == "2026-11-17"
    assert result.warnings == []
    assert TEXT.strip() in llm.calls[0]["messages"][1]["content"]


def test_text_intake_warns_instead_of_rejecting():
    llm = FakeLLM([notice(deadline="2026-11-01", evidence=[], open_to_undergraduates="no")])
    result = intake.intake_text(llm, "m", TEXT, today=TODAY)
    assert result.agent.notice is not None
    assert any("대학생" in warning for warning in result.warnings)


def test_missing_deadline_is_warned():
    result = intake.intake_text(FakeLLM([notice(deadline=None, evidence=[])]), "m", TEXT, today=TODAY)
    assert "마감일을 찾지 못했습니다." in result.warnings


def test_empty_text_is_rejected_without_llm_call():
    llm = FakeLLM([])
    with pytest.raises(intake.IntakeError):
        intake.intake_text(llm, "m", "   ", today=TODAY)
    assert llm.calls == []


def test_text_file_decodes_cp949():
    assert intake.file_to_text("공모전".encode("cp949"), "notice.txt") == "공모전"


@pytest.mark.parametrize("name", ["poster.png", "scan.pdf"])
def test_unreadable_files_raise_user_message(name):
    with pytest.raises(intake.IntakeError):
        intake.file_to_text(b"not a document", name)


def test_url_intake_follows_chosen_attachment(monkeypatch):
    monkeypatch.setattr("aihack4.collector.agent.attachment_to_text", lambda content: "첨부 요강 " + TEXT)
    fetcher = FakeFetcher({
        URL: html_page(URL, f'<p>공모전 안내</p><a href="{PDF_URL}">요강.pdf</a>'),
        PDF_URL: Page(PDF_URL, "application/pdf", b"%PDF-"),
    })
    llm = FakeLLM([{"open_link_number": None, "attachment_numbers": [0]}, notice()])
    result = intake.intake_url(llm, "m", fetcher, URL, today=TODAY)
    assert fetcher.requested == [URL, PDF_URL]
    assert [doc.kind for doc in result.agent.documents] == ["official_page", "attachment"]
    assert result.to_dict()["notice"]["deadline"] == "2026-11-17"


@pytest.mark.parametrize("error", [BlockedUrl("robots"), FetchError("HTTP 500")])
def test_url_intake_explains_unreadable_page(error):
    llm = FakeLLM([])
    with pytest.raises(intake.IntakeError, match="붙여 넣"):
        intake.intake_url(llm, "m", FakeFetcher({URL: error}), URL, today=TODAY)
    assert llm.calls == []


def test_url_intake_rejects_non_http_scheme():
    with pytest.raises(intake.IntakeError):
        intake.intake_url(FakeLLM([]), "m", FakeFetcher({}), "file:///etc/passwd", today=TODAY)
