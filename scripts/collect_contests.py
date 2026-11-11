"""공공기관과 주최 측 공식 페이지에서 공모전 공고를 찾아 DB에 쌓는다.

수집: uv run python scripts/collect_contests.py collect --max-searches 27 --recency month
현황: uv run python scripts/collect_contests.py status
"""

import argparse
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from aihack4.collector import discovery
from aihack4.collector.fetcher import Fetcher
from aihack4.collector.search import SerperClient
from aihack4.collector.store import ContestStore

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data/contests/contests.db"


def collect(args, store: ContestStore) -> None:
    summary = discovery.run_cycle(
        OpenAI(),
        os.environ["LLM_MODEL"],
        Fetcher(),
        SerperClient(os.environ["SERPER_API_KEY"]),
        store,
        datetime.now(),
        args.interval_hours,
        args.max_searches,
        args.recency,
        report=lambda message: print(message, flush=True),
    )
    if not summary.ran:
        print(f"검색 간격({args.interval_hours}시간)이 지나지 않아 건너뜀. 마지막 실행: {store.last_search_at()}")
        return
    print(
        f"검색 {summary.searches}회 / 저장 {summary.extracted} (공공누리 {summary.open_licensed}) "
        f"/ 제외 {summary.skipped} / 실패 {summary.failed} / LLM 비용 ${summary.cost_usd:.4f}"
    )
    for error in summary.errors:
        print(f"실패: {error}")


def status(args, store: ContestStore) -> None:
    connection = store.connection
    for row in connection.execute(
        "SELECT status, COALESCE(skip_reason, '') reason, COUNT(*) n, ROUND(SUM(COALESCE(cost_usd, 0)), 4) cost "
        "FROM contests GROUP BY 1, 2 ORDER BY 1, n DESC"
    ):
        print(f"{row['status']:10} {row['reason']:20} {row['n']:5}  ${row['cost']}")
    for row in connection.execute("SELECT license, COUNT(*) n FROM open_contests GROUP BY 1"):
        print(f"모집 중 [{row['license']}]: {row['n']}")
    print("현재 모집 중 합계:", connection.execute("SELECT COUNT(*) FROM open_contests").fetchone()[0])
    print("누적 검색 수:", connection.execute("SELECT COUNT(*) FROM search_log").fetchone()[0])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    commands = parser.add_subparsers(dest="command", required=True)
    collect_parser = commands.add_parser("collect")
    collect_parser.add_argument("--interval-hours", type=float, default=24)
    collect_parser.add_argument("--max-searches", type=int, default=27, help="한 번에 쓸 검색 크레딧 상한")
    collect_parser.add_argument("--recency", choices=discovery.RECENCY, default="week")
    collect_parser.set_defaults(handler=collect)
    commands.add_parser("status").set_defaults(handler=status)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    args.handler(args, ContestStore(args.db))


if __name__ == "__main__":
    main()
