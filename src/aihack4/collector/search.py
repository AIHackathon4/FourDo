"""Serper로 구글 검색 결과를 가져온다."""

from dataclasses import dataclass

import httpx2

from aihack4.collector.fetcher import FetchError

SERPER_URL = "https://google.serper.dev/search"
TIMEOUT_SECONDS = 20.0
RESULTS_PER_QUERY = 10


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str


class SerperClient:
    def __init__(self, api_key: str, client=None) -> None:
        self.api_key = api_key
        self.client = client or httpx2.Client(timeout=TIMEOUT_SECONDS)

    def search(self, query: str, recency: str | None = None) -> list[SearchHit]:
        """recency는 Serper의 tbs 값이다. 예: 최근 일주일은 qdr:w."""
        payload = {"q": query, "gl": "kr", "hl": "ko", "num": RESULTS_PER_QUERY}
        if recency:
            payload["tbs"] = recency
        try:
            response = self.client.post(SERPER_URL, headers={"X-API-KEY": self.api_key}, json=payload)
        except httpx2.HTTPError as error:
            raise FetchError(f"Serper 요청 실패: {type(error).__name__}") from error
        if response.status_code != 200:
            raise FetchError(f"Serper 오류: HTTP {response.status_code}")
        return [
            SearchHit(item.get("title", ""), item["link"], item.get("snippet", ""))
            for item in response.json().get("organic", [])
            if item.get("link")
        ]
