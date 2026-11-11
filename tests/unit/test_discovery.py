from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx2
import pytest
from test_collector import FakeFetcher, FakeLLM, html_page, listing, notice

from aihack4.collector import discovery
from aihack4.collector.agent import AgentResult, Document, extract_notice, notice_skip_reason
from aihack4.collector.fetcher import BlockedUrl, FetchError
from aihack4.collector.models import Discovered
from aihack4.collector.search import SearchHit, SerperClient
from aihack4.collector.store import ContestStore, dedupe_key, license_of

NOW = datetime(2026, 11, 9, 12, 0)
PAGE_URL = "https://org.go.kr/contest"
PAGE_HTML = "<p>접수 마감 2026-11-17 18:00 까지 접수합니다</p>"
KOGL_HTML = PAGE_HTML + '<img alt="공공누리 제1유형">'
WEB_NOTICE = notice(evidence=[{"field": "deadline", "quote": "2026-11-17 18:00"}], title="웹 공모전")


class FakeSerper:
    def __init__(self, hits=None):
        self.hits = hits if hits is not None else [SearchHit("웹 공모전", PAGE_URL, "모집")]
        self.queries = []

    def search(self, query, recency=None):
        self.queries.append((query, recency))
        return list(self.hits)


def pick(*numbers):
    return {"numbers": list(numbers)}


def cycle(store, serper, llm, fetcher, max_searches=1, now=NOW, interval=24):
    return discovery.run_cycle(llm, "m", fetcher, serper, store, now, interval, max_searches)


def only_row(store):
    return store.connection.execute("SELECT * FROM contests").fetchone()


def fetcher_for(html=PAGE_HTML, url=PAGE_URL):
    return FakeFetcher({url: html_page(url, html)})


def test_serper_sends_key_in_header_and_parses_results():
    captured = {}

    def post(url, headers, json):
        captured.update(url=url, headers=headers, json=json)
        body = {"organic": [{"title": "t", "link": "https://a.org", "snippet": "s"}, {"title": "no link"}]}
        return SimpleNamespace(status_code=200, json=lambda: body)

    hits = SerperClient("secret", SimpleNamespace(post=post)).search("공모전", recency="qdr:w")
    assert hits == [SearchHit("t", "https://a.org", "s")]
    assert captured["headers"] == {"X-API-KEY": "secret"}
    assert captured["json"]["tbs"] == "qdr:w"
    assert captured["json"]["gl"] == "kr"


def test_serper_error_does_not_expose_key():
    post = lambda url, headers, json: SimpleNamespace(status_code=403)
    with pytest.raises(FetchError) as error:
        SerperClient("secret", SimpleNamespace(post=post)).search("q")
    assert "secret" not in str(error.value)


def test_serper_network_error_becomes_fetch_error():
    def post(url, headers, json):
        raise httpx2.ConnectError("boom")

    with pytest.raises(FetchError):
        SerperClient("secret", SimpleNamespace(post=post)).search("q")


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.wevity.com/a", True),
        ("https://m.instagram.com/p", True),
        ("https://www.thinkcontest.com/view", True),
        ("https://thinkyou.co.kr/mobile/contest/1/", True),
        ("https://notx.com/a", False),
        ("https://org.go.kr/notice", False),
    ],
)
def test_is_excluded(url, expected):
    assert discovery.is_excluded(url) is expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.mcst.go.kr/a", True),
        ("https://korea.kr/a", True),
        ("https://www.korea.kr/a", True),
        ("https://snu.ac.kr/a", True),
        ("https://corp.co.kr/a", False),
        ("https://fakego.kr/a", False),
    ],
)
def test_is_public_domain(url, expected):
    assert discovery.is_public_domain(url) is expected


def test_cycle_stores_new_contest_from_search():
    store = ContestStore(":memory:")
    summary = cycle(store, FakeSerper(), FakeLLM([pick(0), WEB_NOTICE]), fetcher_for())
    assert (summary.extracted, summary.searches) == (1, 1)
    row = only_row(store)
    assert (row["status"], row["title"], row["end_date"]) == ("extracted", "웹 공모전", "2026-11-17")


def test_facts_only_page_body_is_not_stored():
    store = ContestStore(":memory:")
    cycle(store, FakeSerper(), FakeLLM([pick(0), WEB_NOTICE]), fetcher_for())
    assert only_row(store)["license"] == "facts_only"
    assert store.connection.execute("SELECT text FROM documents").fetchone()[0] == ""


def test_kogl_type1_page_body_is_stored():
    store = ContestStore(":memory:")
    summary = cycle(store, FakeSerper(), FakeLLM([pick(0), WEB_NOTICE]), fetcher_for(KOGL_HTML))
    assert summary.open_licensed == 1
    assert only_row(store)["license"] == "kogl-1"
    assert "접수 마감" in store.connection.execute("SELECT text FROM documents").fetchone()[0]


@pytest.mark.parametrize(
    "types,expected",
    [([1, 1], "kogl-1"), ([0], "kogl-0"), ([1, None], "facts_only"), ([2], "facts_only"), ([], "facts_only")],
)
def test_license_requires_every_page_open(types, expected):
    result = AgentResult(documents=[Document("official_page", "u", "t", kogl) for kogl in types])
    assert license_of(result) == expected


def test_attachment_body_is_not_stored_without_own_license():
    store = ContestStore(":memory:")
    result = AgentResult(
        documents=[Document("official_page", "u", "본문", 1), Document("attachment", "a", "첨부 본문")],
        notice=notice(),
    )
    store.save("s", Discovered("1", "u", "d"), "extracted", "now", listing(), result)
    texts = [row[0] for row in store.connection.execute("SELECT text FROM documents ORDER BY position")]
    assert texts == ["본문", ""]


def test_cycle_respects_interval_and_spends_no_credits():
    store = ContestStore(":memory:")
    store.log_search("discovery", "q", 1, (NOW - timedelta(hours=3)).isoformat())
    serper = FakeSerper()
    assert cycle(store, serper, FakeLLM([]), FakeFetcher({})).ran is False
    assert serper.queries == []


def test_cycle_runs_again_after_interval():
    store = ContestStore(":memory:")
    store.log_search("discovery", "q", 1, (NOW - timedelta(hours=25)).isoformat())
    serper = FakeSerper(hits=[])
    assert cycle(store, serper, FakeLLM([]), FakeFetcher({})).ran is True
    assert len(serper.queries) == 1


def test_cycle_never_exceeds_search_budget():
    serper = FakeSerper(hits=[])
    cycle(ContestStore(":memory:"), serper, FakeLLM([]), FakeFetcher({}), max_searches=3)
    assert len(serper.queries) == 3


def test_queries_rotate_to_least_recently_used():
    store = ContestStore(":memory:")
    first, second = FakeSerper(hits=[]), FakeSerper(hits=[])
    cycle(store, first, FakeLLM([]), FakeFetcher({}), max_searches=2)
    cycle(store, second, FakeLLM([]), FakeFetcher({}), max_searches=2, now=NOW + timedelta(days=2))
    assert not {query for query, _ in first.queries} & {query for query, _ in second.queries}


def test_search_failure_does_not_stop_cycle():
    class Flaky(FakeSerper):
        def search(self, query, recency=None):
            if not self.queries:
                self.queries.append((query, recency))
                raise FetchError("Serper 오류")
            return super().search(query, recency)

    summary = cycle(ContestStore(":memory:"), Flaky(hits=[]), FakeLLM([]), FakeFetcher({}), max_searches=2)
    assert (summary.failed, summary.searches) == (1, 1)


def test_excluded_hits_are_not_sent_to_llm_or_fetched():
    serper = FakeSerper(hits=[SearchHit("t", "https://www.wevity.com/x", "s")])
    llm, fetcher = FakeLLM([]), FakeFetcher({})
    cycle(ContestStore(":memory:"), serper, llm, fetcher)
    assert llm.calls == [] and fetcher.requested == []


def test_out_of_range_pick_is_ignored():
    fetcher = FakeFetcher({})
    cycle(ContestStore(":memory:"), FakeSerper(), FakeLLM([pick(7)]), fetcher)
    assert fetcher.requested == []


def test_known_url_is_not_reopened():
    store = ContestStore(":memory:")
    cycle(store, FakeSerper(), FakeLLM([pick(0), WEB_NOTICE]), fetcher_for())
    llm, fetcher = FakeLLM([]), FakeFetcher({})
    cycle(store, FakeSerper(), llm, fetcher, now=NOW + timedelta(days=2))
    assert llm.calls == [] and fetcher.requested == []


@pytest.mark.parametrize(
    "fields,reason",
    [
        ({"is_contest": False}, "공모전 아님"),
        ({"deadline": "2026-11-01"}, "마감됨"),
        ({"open_to_undergraduates": "no"}, "대학생 참가 불가"),
        ({"deadline": None}, "마감일 확인 불가"),
        ({"evidence": [{"field": "deadline", "quote": "지어낸 문장"}]}, "마감일 확인 불가"),
        ({"source_is_organizer": "no"}, "주최 측 페이지 아님"),
    ],
)
def test_web_result_is_filtered(fields, reason):
    store = ContestStore(":memory:")
    cycle(store, FakeSerper(), FakeLLM([pick(0), {**WEB_NOTICE, **fields}]), fetcher_for())
    row = only_row(store)
    assert (row["status"], row["skip_reason"]) == ("skipped", reason)
    assert store.connection.execute("SELECT COUNT(*) FROM open_contests").fetchone()[0] == 0


def test_unknown_publisher_is_kept_only_on_public_domain():
    unknown = {**WEB_NOTICE, "source_is_organizer": "unknown"}
    public = ContestStore(":memory:")
    cycle(public, FakeSerper(), FakeLLM([pick(0), unknown]), fetcher_for())
    assert only_row(public)["status"] == "extracted"

    url = "https://corp.co.kr/contest"
    private = ContestStore(":memory:")
    serper = FakeSerper(hits=[SearchHit("t", url, "s")])
    cycle(private, serper, FakeLLM([pick(0), unknown]), fetcher_for(url=url))
    assert only_row(private)["skip_reason"] == "주최 측 페이지 아님"


def test_blocked_page_is_skipped_without_extraction():
    store = ContestStore(":memory:")
    llm = FakeLLM([pick(0)])
    cycle(store, FakeSerper(), llm, FakeFetcher({PAGE_URL: BlockedUrl("x")}))
    assert len(llm.calls) == 1
    assert only_row(store)["skip_reason"] == "본문 없음"


def test_duplicate_contest_from_other_url_is_skipped():
    store = ContestStore(":memory:")
    cycle(store, FakeSerper(), FakeLLM([pick(0), WEB_NOTICE]), fetcher_for())
    url = "https://other.go.kr/c"
    serper = FakeSerper(hits=[SearchHit("t", url, "s")])
    same = {**WEB_NOTICE, "title": "웹  공모전!"}
    cycle(store, serper, FakeLLM([pick(0), same]), fetcher_for(url=url), now=NOW + timedelta(days=2))
    row = store.connection.execute("SELECT skip_reason FROM contests WHERE url = ?", (url,)).fetchone()
    assert row[0] == "중복"


def test_failed_url_is_retried_on_next_cycle():
    store = ContestStore(":memory:")
    cycle(store, FakeSerper(), FakeLLM([pick(0), {"bad": "reply"}]), fetcher_for())
    assert only_row(store)["status"] == "failed"
    summary = cycle(store, FakeSerper(), FakeLLM([pick(0), WEB_NOTICE]), fetcher_for(), now=NOW + timedelta(days=2))
    assert summary.extracted == 1


def test_llm_retries_once_on_invalid_json():
    good = notice()
    llm = FakeLLM([good])
    original = llm.create
    responses = iter(["not json"])

    def flaky(**kwargs):
        reply = next(responses, None)
        if reply is not None:
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
                usage=SimpleNamespace(cost=0.001),
            )
        return original(**kwargs)

    llm.chat.completions.create = flaky
    result = AgentResult()
    extract_notice(llm, "m", listing(), result)
    assert result.notice["title"] == good["title"]
    assert result.cost == pytest.approx(0.002)


def test_dedupe_key_ignores_spacing_and_punctuation():
    first = dedupe_key({"title": "제1회 AI 공모전!", "deadline": "2026-11-17"})
    assert first == dedupe_key({"title": "제1회AI  공모전", "deadline": "2026-11-17"})
    assert dedupe_key({"title": "x", "deadline": None}) is None


def test_open_contests_view_hides_unverified_deadlines():
    store = ContestStore(":memory:")
    item = Discovered("1", "u", "2099-01-01")
    result = AgentResult(notice=notice(deadline="2099-01-01"), flags=["deadline_unverified"])
    store.save("s", item, "extracted", "now", listing(end_date="2099-01-01"), result)
    assert store.connection.execute("SELECT COUNT(*) FROM open_contests").fetchone()[0] == 0
    store.save("s", item, "extracted", "now", listing(end_date="2099-01-01"), AgentResult(notice=notice()))
    assert store.connection.execute("SELECT COUNT(*) FROM open_contests").fetchone()[0] == 1


def test_notice_skip_reason_allows_valid_notice():
    assert notice_skip_reason(notice(), "2026-11-09", []) is None
