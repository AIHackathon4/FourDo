import json
from types import SimpleNamespace

import pytest

from aihack4.collector.agent import AgentResult, extract_notice, run_agent
from aihack4.collector.fetcher import BlockedUrl, FetchError, Fetcher, Page, is_safe_url, parse_robots
from aihack4.collector.models import Listing
from aihack4.collector.parsing import detect_kogl, extract_links, html_to_text

ROBOTS = """
User-agent: Googlebot
Disallow: /

User-agent: *
Allow: /
Disallow: /*.pdf$
Disallow: /login*
Crawl-delay: 5
"""

PUBLIC = lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))]


def notice(**overrides) -> dict:
    return {
        "is_contest": True,
        "source_is_organizer": "yes",
        "open_to_undergraduates": "yes",
        "title": "테스트 공모전",
        "organizer": "테스트기관",
        "application_start": "2026-11-04",
        "deadline": "2026-11-17",
        "deadline_time": "18:00",
        "eligibility": ["대학생"],
        "team_min": 1,
        "team_max": 3,
        "submissions": ["기획서"],
        "required_skills": [],
        "topics": [],
        "prize": None,
        "evidence": [{"field": "deadline", "quote": "2026-11-04 00:00 ~ 2026-11-17 18:00"}],
        **overrides,
    }


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=json.dumps(self.replies.pop(0), ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=SimpleNamespace(cost=0.001))


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    def get(self, url):
        self.requested.append(url)
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return page


def html_page(url, body):
    return Page(url, "text/html; charset=utf-8", body.encode("utf-8"))


def listing(**overrides) -> Listing:
    values = {
        "source": "web_search",
        "source_id": "7",
        "url": "https://example.org/notice",
        "title": "테스트 공모전",
        "organizer": "테스트기관",
        "start_date": "2026-11-04",
        "end_date": "2026-11-17",
        "end_time": "18:00",
        "participants": ["대학생"],
        "homepage": "https://example.org/notice",
        "poster_url": None,
        "image_urls": [],
        "fields": {"접수기간": "2026-11-04 00:00 ~ 2026-11-17 18:00"},
        "body_text": "팀은 최대 3명",
    }
    return Listing(**{**values, **overrides})


def test_robots_handles_wildcards_and_chooses_group():
    rules = parse_robots(ROBOTS, "PODO-collector/0.1")
    assert rules.allows("/thinkgood/view.do?contest_pk=1")
    assert not rules.allows("/files/a.pdf")
    assert rules.allows("/files/a.pdf?download=1")
    assert not rules.allows("/login.do")
    assert rules.crawl_delay == 5


def test_robots_longest_rule_wins():
    rules = parse_robots("User-agent: *\nDisallow: /a\nAllow: /a/b\n", "x")
    assert rules.allows("/a/b/c")
    assert not rules.allows("/a/c")


@pytest.mark.parametrize(
    "url,address,expected",
    [
        ("https://example.org/", "93.184.216.34", True),
        ("http://intranet/", "10.0.0.1", False),
        ("http://metadata/", "169.254.169.254", False),
        ("http://localhost/", "127.0.0.1", False),
        ("ftp://example.org/", "93.184.216.34", False),
        ("https://example.org:8080/", "93.184.216.34", False),
    ],
)
def test_is_safe_url(url, address, expected):
    assert is_safe_url(url, lambda host, port: [(2, 1, 6, "", (address, 0))]) is expected


class FakeStream:
    def __init__(self, status, headers, body):
        self.status_code, self.headers, self.body = status, headers, body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_bytes(self):
        yield self.body


class FakeHttp:
    def __init__(self, responses):
        self.responses = responses
        self.requested = []

    def stream(self, method, url):
        self.requested.append(url)
        return FakeStream(*self.responses[url])


def make_fetcher(responses, resolver=PUBLIC, sleeps=None):
    return Fetcher(
        client=FakeHttp(responses),
        delay=3,
        resolver=resolver,
        sleep=(sleeps if sleeps is not None else []).append,
        clock=lambda: 100.0,
    )


def test_fetcher_refuses_robots_disallowed_url():
    fetcher = make_fetcher({"https://a.org/robots.txt": (200, {}, b"User-agent: *\nDisallow: /x\n")})
    with pytest.raises(BlockedUrl):
        fetcher.get("https://a.org/x/1")
    assert fetcher.client.requested == ["https://a.org/robots.txt"]


def test_fetcher_treats_robots_server_error_as_disallowed():
    fetcher = make_fetcher({"https://a.org/robots.txt": (503, {}, b"")})
    with pytest.raises(BlockedUrl):
        fetcher.get("https://a.org/x")


def test_fetcher_rechecks_redirect_target():
    def resolver(host, port):
        return [(2, 1, 6, "", ("10.0.0.5" if host == "inner.org" else "93.184.216.34", 0))]

    fetcher = make_fetcher(
        {
            "https://a.org/robots.txt": (404, {}, b""),
            "https://a.org/go": (302, {"location": "http://inner.org/secret"}, b""),
        },
        resolver=resolver,
    )
    with pytest.raises(BlockedUrl):
        fetcher.get("https://a.org/go")
    assert "http://inner.org/secret" not in fetcher.client.requested


def test_fetcher_waits_between_requests_to_same_host():
    sleeps = []
    fetcher = make_fetcher(
        {
            "https://a.org/robots.txt": (404, {}, b""),
            "https://a.org/1": (200, {"content-type": "text/html"}, b"ok"),
        },
        sleeps=sleeps,
    )
    assert fetcher.get("https://a.org/1").content == b"ok"
    assert sleeps == [3.0]


def test_page_text_decodes_cp949_without_charset():
    assert Page("u", "text/html", "공모전".encode("cp949")).text == "공모전"


def test_html_to_text_joins_inline_and_drops_scripts():
    text = html_to_text("<p><span>팀은</span><span> 3명</span></p><script>bad()</script><p>끝</p>")
    assert text == "팀은 3명\n끝"


def test_extract_links_lists_attachments_first_and_drops_javascript():
    html = '<a href="/page">안내</a><a href="javascript:x()">x</a><a href="/f/a.hwp">요강</a><a href="/page#t">안내</a>'
    links = extract_links(html, "https://a.org/b")
    assert [link.url for link in links] == ["https://a.org/f/a.hwp", "https://a.org/page"]
    assert links[0].is_attachment


def test_agent_opens_only_listed_link_and_reads_chosen_pages():
    fetcher = FakeFetcher(
        {
            "https://example.org/notice": html_page(
                "https://example.org/notice", '<p>목록</p><a href="/real">모집요강 보기</a>'
            ),
            "https://example.org/real": html_page("https://example.org/real", "<p>마감은 11월 17일</p>"),
        }
    )
    llm = FakeLLM(
        [
            {"attachment_numbers": [], "open_link_number": 0},
            notice(),
        ]
    )
    result = run_agent(llm, "m", fetcher, listing())
    assert fetcher.requested == ["https://example.org/notice", "https://example.org/real"]
    assert [doc.kind for doc in result.documents] == ["official_page", "official_page"]
    assert result.cost == pytest.approx(0.002)


def test_agent_ignores_out_of_range_link_numbers():
    fetcher = FakeFetcher(
        {"https://example.org/notice": html_page("https://example.org/notice", '<a href="/a">a</a>')}
    )
    llm = FakeLLM([{"attachment_numbers": [5], "open_link_number": 9}, notice()])
    run_agent(llm, "m", fetcher, listing())
    assert fetcher.requested == ["https://example.org/notice"]


@pytest.mark.parametrize(
    "error,flag", [(BlockedUrl("x"), "official_blocked"), (FetchError("x"), "official_fetch_failed")]
)
def test_agent_still_extracts_when_official_site_fails(error, flag):
    fetcher = FakeFetcher({"https://example.org/notice": error})
    result = run_agent(FakeLLM([notice()]), "m", fetcher, listing())
    assert flag in result.flags
    assert result.notice is not None


def test_agent_flags_unparsed_attachment():
    fetcher = FakeFetcher(
        {
            "https://example.org/notice": html_page(
                "https://example.org/notice", '<p>안내</p><a href="/a.hwp">요강</a>'
            ),
            "https://example.org/a.hwp": Page("https://example.org/a.hwp", "application/x-hwp", b"HWP"),
        }
    )
    llm = FakeLLM([{"attachment_numbers": [0], "open_link_number": None}, notice()])
    assert "attachment_unparsed" in run_agent(llm, "m", fetcher, listing()).flags


def test_extract_marks_fabricated_evidence():
    llm = FakeLLM([notice(evidence=[{"field": "deadline", "quote": "마감은 12월 25일이다"}])])
    result = AgentResult()
    extract_notice(llm, "m", listing(), result)
    assert result.notice["evidence"][0]["found_in_source"] is False
    assert {"deadline_unverified", "evidence_not_in_source"} <= set(result.flags)


def test_extract_flags_deadline_conflict():
    result = AgentResult()
    extract_notice(FakeLLM([notice(deadline="2026-11-20")]), "m", listing(), result)
    assert "deadline_conflict" in result.flags


def test_extract_accepts_quote_with_different_spacing():
    quote = "2026-11-04 00:00~2026-11-17    18:00"
    result = AgentResult()
    extract_notice(FakeLLM([notice(evidence=[{"field": "deadline", "quote": quote}])]), "m", listing(), result)
    assert result.notice["evidence"][0]["found_in_source"] is True


@pytest.mark.parametrize(
    "html,expected",
    [
        ('<img src="/a.png" alt="공공누리 제1유형: 출처표시">', 1),
        ('<img src="/img/opentype01.png" alt="공공누리">', 1),
        ('<a href="/kogl">공공누리</a> 제0유형', 0),
        ("<p>공공누리 제4유형</p>", 4),
        ("<p>일반 공고입니다</p>", None),
        ("<p>공모 1유형과 2유형으로 나뉩니다</p>", None),
        ("<p>공공누리 안내: 제1유형, 제2유형, 제3유형</p>", None),
        ("<script>var a = '공공누리 제1유형';</script><p>본문</p>", None),
    ],
)
def test_detect_kogl(html, expected):
    assert detect_kogl(html) == expected


def test_long_evidence_quote_is_trimmed_after_verification():
    long_quote = "팀은 최대 3명" + "가" * 500
    body = long_quote
    result = AgentResult()
    reply = notice(evidence=[{"field": "team", "quote": long_quote}])
    extract_notice(FakeLLM([reply]), "m", listing(body_text=body), result)
    assert result.notice["evidence"][0]["found_in_source"] is True
    assert len(result.notice["evidence"][0]["quote"]) == 200
