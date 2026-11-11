"""사용자가 가져온 공고를 구조화해 JSON으로 출력한다. 결과는 DB에 저장하지 않는다.

uv run python scripts/intake_notice.py --url https://...
uv run python scripts/intake_notice.py --file 공고문.hwp
uv run python scripts/intake_notice.py --text "공고 내용..."   (또는 --text - 로 표준입력)
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from aihack4.collector import intake
from aihack4.collector.fetcher import Fetcher

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url")
    source.add_argument("--file", type=Path)
    source.add_argument("--text")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    client, model = OpenAI(), os.environ["LLM_MODEL"]
    try:
        if args.url:
            result = intake.intake_url(client, model, Fetcher(), args.url)
        elif args.file:
            result = intake.intake_file(client, model, args.file.read_bytes(), args.file.name)
        else:
            text = sys.stdin.read() if args.text == "-" else args.text
            result = intake.intake_text(client, model, text)
    except intake.IntakeError as error:
        sys.exit(f"입력 오류: {error}")
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
