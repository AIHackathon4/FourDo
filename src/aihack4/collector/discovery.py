"""웹 검색으로 공공기관과 주최 측의 공모전 공고를 찾아 DB에 쌓는다.

공고 모음 사이트의 데이터는 쓰지 않는다. 검색 크레딧을 아끼려고 정해진 간격 안에는 실행하지 않고,
오래 실행하지 않은 검색어부터 돌려 크레딧을 고르게 쓴다.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx2
import openai

from aihack4.collector.agent import (
    AgentResult,
    apply_notice_dates,
    choose_hits,
    explore_official_site,
    extract_notice,
    notice_skip_reason,
)
from aihack4.collector.fetcher import FetchError
from aihack4.collector.models import Discovered, Listing
from aihack4.collector.search import SearchHit
from aihack4.collector.store import (
    STATUS_EXTRACTED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    ContestStore,
    dedupe_key,
    license_of,
)

SOURCE = "web_search"
MAX_PICKS_PER_QUERY = 5
RECENCY = {"week": "qdr:w", "month": "qdr:m"}
EXCLUDED_DOMAINS = (
    "thinkcontest.com", "wevity.com", "contestkorea.com", "linkareer.com", "all-con.co.kr",
    "campuspick.com", "ssgsag.kr", "thinkyou.co.kr", "designjob.co.kr", "thedreamkorea.com",
    "moabol.co.kr", "momo365.net", "instagram.com", "facebook.com", "youtube.com", "tiktok.com",
    "x.com", "twitter.com", "namu.wiki",
)
PUBLIC_SUFFIXES = (".go.kr", ".or.kr", ".re.kr", ".ac.kr", ".korea.kr")
TOPICS = (
    "아이디어 공모전",
    "영상 공모전",
    "디자인 공모전",
    "슬로건 네이밍 공모전",
    "논문 공모전",
    "창업 경진대회",
    "데이터 AI 경진대회 해커톤",
    "사진 공모전",
    "수기 공모전",
)
SCOPES = ("site:go.kr", "site:or.kr", "")
ITEM_ERRORS = (FetchError, ValueError, KeyError, TypeError, openai.OpenAIError, httpx2.HTTPError)
TARGET = "주최 기관이 직접 게시한, 현재 모집 중인 단일 공모전의 공고문. 기준일 {today}."


@dataclass
class CycleSummary:
    ran: bool = True
    searches: int = 0
    extracted: int = 0
    skipped: int = 0
    failed: int = 0
    open_licensed: int = 0
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)


def all_queries() -> list[str]:
    return [f"{topic} 모집 공고 {scope}".strip() for scope in SCOPES for topic in TOPICS]


def is_excluded(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in EXCLUDED_DOMAINS)


def is_public_domain(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "korea.kr" or host.endswith(PUBLIC_SUFFIXES)


def interval_elapsed(last_run: str | None, now: datetime, interval_hours: float) -> bool:
    return last_run is None or now - datetime.fromisoformat(last_run) >= timedelta(hours=interval_hours)


def web_listing(hit: SearchHit) -> Listing:
    return Listing(
        source=SOURCE,
        source_id=hashlib.sha256(hit.url.encode()).hexdigest()[:16],
        url=hit.url,
        title=hit.title,
        organizer="",
        start_date=None,
        end_date=None,
        end_time=None,
        participants=[],
        homepage=hit.url,
        poster_url=None,
        image_urls=[],
        fields={},
        body_text="",
    )


def rejection_reason(url: str, result: AgentResult, today: str) -> str | None:
    """공고 내용과 게시 주체를 함께 보고 DB에 넣을지 판단한다."""
    reason = notice_skip_reason(result.notice, today, result.flags, require_verified_deadline=True)
    if reason:
        return reason
    publisher = result.notice["source_is_organizer"]
    if publisher == "no" or (publisher == "unknown" and not is_public_domain(url)):
        return "주최 측 페이지 아님"
    return None


def process_hit(client, model, fetcher, store: ContestStore, hit, today, stamp, summary, report) -> None:
    listing = web_listing(hit)
    item = Discovered(listing.source_id, hit.url, today)
    result = AgentResult()
    try:
        explore_official_site(client, model, fetcher, listing, result)
        if not any(document.text for document in result.documents):
            summary.cost_usd += result.cost
            store.save(SOURCE, item, STATUS_SKIPPED, stamp, listing, result, model, skip_reason="본문 없음")
            summary.skipped += 1
            report(f"  제외(본문 없음) {hit.url}")
            return
        extract_notice(client, model, listing, result)
        summary.cost_usd += result.cost
        apply_notice_dates(listing, result.notice)
        reason = rejection_reason(hit.url, result, today)
        if reason is None and store.find_duplicate(dedupe_key(result.notice), SOURCE, item.source_id):
            reason = "중복"
        status = STATUS_SKIPPED if reason else STATUS_EXTRACTED
        store.save(SOURCE, item, status, stamp, listing, result, model, skip_reason=reason)
        if reason:
            summary.skipped += 1
            report(f"  제외({reason}) {listing.title[:50]}")
            return
        summary.extracted += 1
        license_name = license_of(result)
        summary.open_licensed += license_name.startswith("kogl")
        report(f"  저장 [{license_name}] {listing.title[:50]} | 마감 {listing.end_date}")
    except ITEM_ERRORS as error:
        summary.cost_usd += result.cost
        store.save(SOURCE, item, STATUS_FAILED, stamp, listing, error=str(error))
        summary.failed += 1
        summary.errors.append(f"{hit.url}: {error}")
        report(f"  실패 {hit.url}")


def run_cycle(
    client,
    model: str,
    fetcher,
    serper,
    store: ContestStore,
    now: datetime,
    interval_hours: float,
    max_searches: int,
    recency: str = "week",
    report=lambda message: None,
) -> CycleSummary:
    summary = CycleSummary()
    if not interval_elapsed(store.last_search_at(), now, interval_hours):
        summary.ran = False
        return summary
    today, stamp = now.date().isoformat(), now.isoformat(timespec="seconds")
    last_runs = store.query_last_runs()
    queries = sorted(all_queries(), key=lambda query: last_runs.get(query, ""))[:max_searches]
    for query in queries:
        report(f"검색: {query}")
        try:
            hits = serper.search(query, recency=RECENCY[recency])
        except FetchError as error:
            summary.failed += 1
            summary.errors.append(f"{query}: {error}")
            continue
        store.log_search("discovery", query, len(hits), stamp)
        summary.searches += 1
        candidates = [hit for hit in hits if not is_excluded(hit.url) and not store.has_url(hit.url)]
        if not candidates:
            continue
        try:
            numbers, cost = choose_hits(client, model, candidates, TARGET.format(today=today))
        except ITEM_ERRORS as error:
            summary.failed += 1
            summary.errors.append(f"{query}: {error}")
            continue
        summary.cost_usd += cost
        for number in numbers[:MAX_PICKS_PER_QUERY]:
            if 0 <= number < len(candidates) and not store.has_url(candidates[number].url):
                process_hit(client, model, fetcher, store, candidates[number], today, stamp, summary, report)
    return summary
