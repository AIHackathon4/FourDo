"""사용자가 가져온 공고(링크·파일·텍스트)를 구조화한다.

수집기와 같은 탐색·첨부 읽기·근거 검증을 쓰지만, 결과는 그 사용자에게만 돌려주고 공용 DB에 넣지 않는다.
"""

import hashlib
from dataclasses import dataclass
from datetime import date

from .agent import AgentResult, apply_notice_dates, explore_official_site, extract_notice, notice_skip_reason
from .attachments import attachment_to_text
from .models import Listing

SOURCE = "user"
MAX_INPUT_BYTES = 15 * 1024 * 1024
TEXT_EXTENSIONS = (".txt", ".md")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

SKIP_MESSAGES = {
    "공모전 아님": "공모전·경진대회 공고가 아닌 것 같습니다.",
    "대학생 참가 불가": "대학생이 참가할 수 없거나 특정 학교·대상으로 한정된 공고로 보입니다.",
    "마감됨": "이미 마감된 공고입니다.",
}

FLAG_MESSAGES = {
    "poster_only": "공고 내용이 짧아 빠진 조건이 있을 수 있습니다. 공고문 전문이나 첨부파일이 있다면 함께 넣어 주세요.",
    "deadline_unverified": "마감일 근거 문장을 원문에서 찾지 못했습니다. 원문에서 직접 확인해 주세요.",
    "evidence_not_in_source": "일부 근거 문장이 원문과 정확히 일치하지 않습니다.",
    "attachment_unparsed": "읽지 못한 첨부파일이 있습니다(스캔 PDF, 암호화 문서 등).",
    "attachment_fetch_failed": "일부 첨부파일을 내려받지 못했습니다.",
    "official_blocked": "사이트가 자동 접근을 허용하지 않아 링크된 페이지 일부를 열지 못했습니다.",
    "official_fetch_failed": "링크된 페이지 일부를 열지 못했습니다.",
}


class IntakeError(ValueError):
    """사용자에게 그대로 보여 줄 수 있는 입력 오류."""


@dataclass
class IntakeResult:
    listing: Listing
    agent: AgentResult
    warnings: list[str]

    def to_dict(self) -> dict:
        return {
            "source": self.listing.url,
            "notice": self.agent.notice,
            "warnings": self.warnings,
            "flags": self.agent.flags,
            "documents": [{"kind": doc.kind, "url": doc.url, "chars": len(doc.text)} for doc in self.agent.documents],
            "cost_usd": round(self.agent.cost, 6),
        }


def _listing(url: str, body_text: str = "", homepage: str | None = None) -> Listing:
    return Listing(
        source=SOURCE,
        source_id=hashlib.sha256((url + body_text[:2000]).encode()).hexdigest()[:16],
        url=url,
        title="",
        organizer="",
        start_date=None,
        end_date=None,
        end_time=None,
        participants=[],
        homepage=homepage,
        poster_url=None,
        image_urls=[],
        fields={},
        body_text=body_text,
    )


def _warnings(result: AgentResult, today: str) -> list[str]:
    warnings = []
    reason = notice_skip_reason(result.notice, today, result.flags)
    if reason:
        warnings.append(SKIP_MESSAGES[reason])
    if not result.notice["deadline"]:
        warnings.append("마감일을 찾지 못했습니다.")
    warnings += [FLAG_MESSAGES[flag] for flag in result.flags if flag in FLAG_MESSAGES]
    return warnings


def _finish(client, model: str, listing: Listing, result: AgentResult, today: str | None) -> IntakeResult:
    extract_notice(client, model, listing, result)
    apply_notice_dates(listing, result.notice)
    return IntakeResult(listing, result, _warnings(result, today or date.today().isoformat()))


def intake_text(client, model: str, text: str, today: str | None = None) -> IntakeResult:
    if not text.strip():
        raise IntakeError("공고 내용이 비어 있습니다.")
    return _finish(client, model, _listing("text:", text.strip()), AgentResult(), today)


def file_to_text(content: bytes, filename: str) -> str:
    name = filename.lower()
    if len(content) > MAX_INPUT_BYTES:
        raise IntakeError("파일이 너무 큽니다(15MB 이하).")
    if name.endswith(IMAGE_EXTENSIONS):
        raise IntakeError("이미지 공고는 아직 읽지 못합니다. 공고문 PDF·HWP나 텍스트를 넣어 주세요.")
    if name.endswith(TEXT_EXTENSIONS):
        for encoding in ("utf-8-sig", "cp949"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise IntakeError("텍스트 파일 인코딩을 알 수 없습니다.")
    text = attachment_to_text(content)
    if not text.strip():
        raise IntakeError("파일에서 글자를 읽지 못했습니다. PDF·HWP·HWPX만 지원하며 스캔본은 읽지 못합니다.")
    return text


def intake_file(client, model: str, content: bytes, filename: str, today: str | None = None) -> IntakeResult:
    text = file_to_text(content, filename)
    return _finish(client, model, _listing(f"file:{filename}", text.strip()), AgentResult(), today)


def intake_url(client, model: str, fetcher, url: str, today: str | None = None) -> IntakeResult:
    """사용자가 준 주소에서 시작해 모집요강 페이지와 첨부파일을 따라가 읽는다."""
    if not url.startswith(("http://", "https://")):
        raise IntakeError("http:// 또는 https:// 로 시작하는 주소를 넣어 주세요.")
    listing = _listing(url, homepage=url)
    result = AgentResult()
    explore_official_site(client, model, fetcher, listing, result)
    if not any(doc.text for doc in result.documents):
        if "official_blocked" in result.flags:
            raise IntakeError("이 사이트는 자동 접근을 허용하지 않습니다. 공고문을 복사해 붙여 넣거나 첨부파일을 올려 주세요.")
        if "official_fetch_failed" in result.flags:
            raise IntakeError("페이지를 열지 못했습니다. 주소를 확인하거나 공고문을 직접 붙여 넣어 주세요.")
        raise IntakeError("페이지에서 공고 내용을 읽지 못했습니다. 공고문을 직접 붙여 넣어 주세요.")
    return _finish(client, model, listing, result, today)

