"""robots.txt와 요청 간격을 지키며 웹 문서를 가져온다.

공고에 적힌 링크는 믿을 수 없는 입력이므로 내부망 주소로 가는 요청을 막는다.
"""

import ipaddress
import re
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx2

USER_AGENT = "PODO-collector/0.1 (university hackathon project)"
DEFAULT_DELAY_SECONDS = 3.0
MAX_BYTES = 15 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 20.0
REDIRECT_CODES = {301, 302, 303, 307, 308}
ALLOWED_PORTS = {None, 80, 443}


class FetchError(Exception):
    """문서를 가져오지 못했다."""


class BlockedUrl(FetchError):
    """robots.txt나 안전 규칙 때문에 요청하지 않았다."""


@dataclass
class RobotsRules:
    rules: list[tuple[bool, str]] = field(default_factory=list)
    crawl_delay: float | None = None

    def allows(self, path: str) -> bool:
        """가장 긴 패턴이 이기고, 길이가 같으면 허용이 이긴다."""
        matches = [(len(p), allow) for allow, p in self.rules if _pattern_matches(p, path)]
        return max(matches)[1] if matches else True


DISALLOW_ALL = RobotsRules(rules=[(False, "/")])


def _pattern_matches(pattern: str, path: str) -> bool:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = re.escape(body).replace(r"\*", ".*")
    return re.match(regex + ("$" if anchored else ""), path) is not None


def parse_robots(text: str, agent: str) -> RobotsRules:
    """표준 라이브러리 파서가 지원하지 않는 *와 $ 패턴까지 해석한다."""
    groups: list[dict] = []
    current: dict | None = None
    reading_agents = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if not reading_agents:
                current = {"agents": [], "rules": [], "delay": None}
                groups.append(current)
                reading_agents = True
            current["agents"].append(value.lower())
        elif current is not None and key in {"allow", "disallow", "crawl-delay"}:
            reading_agents = False
            if key == "crawl-delay":
                try:
                    current["delay"] = float(value)
                except ValueError:
                    pass
            elif value:
                current["rules"].append((key == "allow", value))

    token = agent.lower()
    specific = [g for g in groups if any(a != "*" and a in token for a in g["agents"])]
    chosen = specific or [g for g in groups if "*" in g["agents"]]
    delays = [g["delay"] for g in chosen if g["delay"] is not None]
    return RobotsRules(
        rules=[rule for group in chosen for rule in group["rules"]],
        crawl_delay=max(delays) if delays else None,
    )


def is_safe_url(url: str, resolver=socket.getaddrinfo) -> bool:
    """공개 인터넷의 http(s) 주소인지 확인한다."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False
    try:
        if parts.port not in ALLOWED_PORTS:
            return False
        infos = resolver(parts.hostname, None)
        addresses = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]
    except (OSError, ValueError):
        return False
    return bool(addresses) and all(address.is_global for address in addresses)


@dataclass
class Page:
    url: str
    content_type: str
    content: bytes

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type.lower()

    @property
    def text(self) -> str:
        declared = re.search(r"charset=([\w-]+)", self.content_type, re.I)
        encodings = [declared.group(1)] if declared else []
        for encoding in [*encodings, "utf-8", "cp949"]:
            try:
                return self.content.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
        return self.content.decode("utf-8", errors="replace")


class Fetcher:
    def __init__(
        self,
        client=None,
        delay: float = DEFAULT_DELAY_SECONDS,
        resolver=socket.getaddrinfo,
        sleep=time.sleep,
        clock=time.monotonic,
    ) -> None:
        self.client = client or httpx2.Client(
            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS, follow_redirects=False
        )
        self.delay = delay
        self.resolver = resolver
        self.sleep = sleep
        self.clock = clock
        self._robots: dict[str, RobotsRules] = {}
        self._last_request: dict[str, float] = {}

    def get(self, url: str) -> Page:
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(url)
            rules = self._rules_for(parts.scheme, parts.netloc)
            path = parts.path or "/"
            if parts.query:
                path = f"{path}?{parts.query}"
            if not rules.allows(path):
                raise BlockedUrl(f"robots.txt가 금지: {url}")
            status, headers, body = self._request(url, rules.crawl_delay)
            location = headers.get("location")
            if status in REDIRECT_CODES and location:
                url = urljoin(url, location)
                continue
            if status >= 400:
                raise FetchError(f"HTTP {status}: {url}")
            return Page(url=url, content_type=headers.get("content-type", ""), content=body)
        raise FetchError(f"리다이렉트가 너무 많음: {url}")

    def _rules_for(self, scheme: str, netloc: str) -> RobotsRules:
        origin = f"{scheme}://{netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        return self._robots[origin]

    def _load_robots(self, origin: str) -> RobotsRules:
        """robots.txt가 없으면 허용하고, 확인할 수 없으면 요청하지 않는다."""
        url = f"{origin}/robots.txt"
        try:
            for _ in range(MAX_REDIRECTS + 1):
                status, headers, body = self._request(url, None)
                location = headers.get("location")
                if status in REDIRECT_CODES and location:
                    url = urljoin(url, location)
                    continue
                if status == 404:
                    return RobotsRules()
                if status != 200:
                    return DISALLOW_ALL
                return parse_robots(body.decode("utf-8", errors="replace"), USER_AGENT)
        except FetchError:
            return DISALLOW_ALL
        return DISALLOW_ALL

    def _request(self, url: str, crawl_delay: float | None) -> tuple[int, dict, bytes]:
        if not is_safe_url(url, self.resolver):
            raise BlockedUrl(f"안전하지 않은 주소: {url}")
        self._wait(urlsplit(url).netloc, crawl_delay)
        try:
            with self.client.stream("GET", url) as response:
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise FetchError(f"파일이 너무 큼: {url}")
                return response.status_code, dict(response.headers), bytes(body)
        except httpx2.HTTPError as error:
            raise FetchError(f"요청 실패: {url}: {error}") from error

    def _wait(self, host: str, crawl_delay: float | None) -> None:
        delay = max(self.delay, crawl_delay or 0)
        last = self._last_request.get(host)
        if last is not None:
            remaining = delay - (self.clock() - last)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request[host] = self.clock()
