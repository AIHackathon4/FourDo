"""수집 결과를 SQLite에 저장한다.

공공누리 0·1유형이 확인된 문서만 본문을 저장하고, 나머지는 사실 항목과 링크만 남긴다.
"""

import json
import re
import sqlite3
from pathlib import Path

from aihack4.collector.agent import AgentResult
from aihack4.collector.models import Discovered, Listing

STATUS_EXTRACTED = "extracted"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
LICENSE_FACTS_ONLY = "facts_only"
OPEN_KOGL_TYPES = {0, 1}

SCHEMA = """
CREATE TABLE IF NOT EXISTS contests (
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL,
    status TEXT NOT NULL,
    skip_reason TEXT,
    error TEXT,
    title TEXT,
    organizer TEXT,
    start_date TEXT,
    end_date TEXT,
    end_time TEXT,
    license TEXT,
    notice TEXT,
    review_flags TEXT,
    dedupe_key TEXT,
    model TEXT,
    cost_usd REAL,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS contests_dedupe ON contests (dedupe_key);
CREATE INDEX IF NOT EXISTS contests_url ON contests (url);
CREATE TABLE IF NOT EXISTS documents (
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    kind TEXT NOT NULL,
    url TEXT NOT NULL,
    kogl_type INTEGER,
    text TEXT NOT NULL,
    PRIMARY KEY (source, source_id, position)
);
CREATE TABLE IF NOT EXISTS search_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    query TEXT NOT NULL,
    results INTEGER NOT NULL,
    ran_at TEXT NOT NULL
);
CREATE VIEW IF NOT EXISTS open_contests AS
SELECT source, source_id, url, title, organizer, start_date, end_date, end_time, license,
       notice, review_flags, collected_at
FROM contests
WHERE status = 'extracted'
  AND end_date >= date('now', 'localtime')
  AND review_flags NOT LIKE '%deadline_conflict%'
  AND review_flags NOT LIKE '%deadline_unverified%';
"""

COLUMNS = (
    "source", "source_id", "url", "status", "skip_reason", "error", "title", "organizer", "start_date",
    "end_date", "end_time", "license", "notice", "review_flags", "dedupe_key", "model", "cost_usd",
    "collected_at",
)


def dedupe_key(notice: dict | None) -> str | None:
    """공백과 문장부호를 뺀 제목과 마감일로 같은 공모전을 찾는다."""
    if not notice or not notice.get("deadline") or not notice.get("title"):
        return None
    title = re.sub(r"[^0-9a-z가-힣]", "", notice["title"].lower())
    return f"{title}|{notice['deadline']}"


def license_of(result: AgentResult | None) -> str:
    """읽은 웹페이지가 모두 같은 공공누리 0·1유형일 때만 개방 저작물로 본다."""
    pages = [doc for doc in result.documents if doc.kind == "official_page"] if result else []
    types = {doc.kogl_type for doc in pages}
    if len(types) == 1 and next(iter(types)) in OPEN_KOGL_TYPES:
        return f"kogl-{next(iter(types))}"
    return LICENSE_FACTS_ONLY


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


class ContestStore:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def has_url(self, url: str) -> bool:
        """이미 처리한 주소면 다시 열지 않는다. 실패한 주소는 다음 실행에서 재시도한다."""
        query = (
            "SELECT EXISTS(SELECT 1 FROM contests WHERE url = ? AND status != ?) "
            "OR EXISTS(SELECT 1 FROM documents WHERE url = ?)"
        )
        return bool(self.connection.execute(query, (url, STATUS_FAILED, url)).fetchone()[0])

    def find_duplicate(self, key: str | None, source: str, source_id: str) -> bool:
        if not key:
            return False
        row = self.connection.execute(
            "SELECT 1 FROM contests WHERE dedupe_key = ? AND status = ? AND NOT (source = ? AND source_id = ?)",
            (key, STATUS_EXTRACTED, source, source_id),
        ).fetchone()
        return row is not None

    def save(
        self,
        source: str,
        item: Discovered,
        status: str,
        collected_at: str,
        listing: Listing | None = None,
        result: AgentResult | None = None,
        model: str | None = None,
        skip_reason: str | None = None,
        error: str | None = None,
    ) -> None:
        notice = result.notice if result else None
        values = {
            "source": source,
            "source_id": item.source_id,
            "url": item.url,
            "status": status,
            "skip_reason": skip_reason,
            "error": error,
            "license": license_of(result),
            "notice": _dumps(notice) if notice else None,
            "review_flags": _dumps(result.flags) if result else None,
            "dedupe_key": dedupe_key(notice) if status == STATUS_EXTRACTED else None,
            "model": model,
            "cost_usd": result.cost if result else None,
            "collected_at": collected_at,
        }
        if listing:
            values.update(
                title=listing.title,
                organizer=listing.organizer,
                start_date=listing.start_date,
                end_date=listing.end_date,
                end_time=listing.end_time,
            )
        placeholders = ",".join("?" for _ in COLUMNS)
        with self.connection:
            self.connection.execute(
                f"INSERT OR REPLACE INTO contests ({','.join(COLUMNS)}) VALUES ({placeholders})",
                [values.get(column) for column in COLUMNS],
            )
            self.connection.execute(
                "DELETE FROM documents WHERE source = ? AND source_id = ?", (source, item.source_id)
            )
            if result:
                self.connection.executemany(
                    "INSERT INTO documents VALUES (?,?,?,?,?,?,?)",
                    [
                        (
                            source,
                            item.source_id,
                            position,
                            doc.kind,
                            doc.url,
                            doc.kogl_type,
                            doc.text if doc.kogl_type in OPEN_KOGL_TYPES else "",
                        )
                        for position, doc in enumerate(result.documents)
                    ],
                )

    def log_search(self, kind: str, query: str, results: int, ran_at: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO search_log (kind, query, results, ran_at) VALUES (?,?,?,?)",
                (kind, query, results, ran_at),
            )

    def last_search_at(self) -> str | None:
        return self.connection.execute("SELECT MAX(ran_at) FROM search_log").fetchone()[0]

    def query_last_runs(self) -> dict[str, str]:
        rows = self.connection.execute("SELECT query, MAX(ran_at) last FROM search_log GROUP BY query")
        return {row["query"]: row["last"] for row in rows}
