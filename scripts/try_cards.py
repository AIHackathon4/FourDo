"""공고 카드 추출을 실제 공고 몇 건으로 돌려 보고 사람이 읽기 좋은 요약을 만든다.

사용법: uv run python scripts/try_cards.py URL [URL ...]
출력: data/results/cards_<시각>.json, .md
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env")

from aihack4.card import CardError, extract_card, read_url  # noqa: E402
from aihack4.collector.fetcher import Fetcher  # noqa: E402


MODEL = os.getenv("CARD_MODEL") or os.environ["LLM_MODEL"]


def run(url: str) -> dict:
    started = time.time()
    try:
        source = read_url(Fetcher(), url)
        card, cost = extract_card(OpenAI(), MODEL, source, date.today())
        return {"url": url, "card": card, "cost": cost, "chars": len(source), "sec": round(time.time() - started, 1)}
    except (CardError, Exception) as error:  # 한 건 실패가 나머지를 막지 않게
        return {"url": url, "error": f"{type(error).__name__}: {error}", "sec": round(time.time() - started, 1)}


def markdown(item: dict) -> list[str]:
    if "error" in item:
        return [f"## 실패: {item['url']}", item["error"], ""]
    c = item["card"]
    lines = [f"## {c['title']}", f"{c['organizer']} · 마감 {c['deadline']} {c['deadline_time'] or ''} · 팀 {c['team_min']}~{c['team_max']}명 "
             f"· 원문 {item['chars']}자 · {item['sec']}초 · ${item['cost']:.4f}", ""]
    if c["conditions"]:
        lines.append("**자격 조건**: " + " / ".join(f"{x['type']}: {', '.join(x['allowed']) or x['quote'][:40]}" for x in c["conditions"]))
    if c["criteria"]:
        lines.append("**평가 기준**: " + ", ".join(f"{x['name']}({x['weight']:g})" if x["weight"] else x["name"] for x in c["criteria"]))
    if c["forms"]:
        lines.append("**서류**: " + ", ".join(c["forms"]))
    lines.append("")
    for sub in c["submissions"]:
        flag = " · ⚠ 역량 필수(누구나 방법 없음)" if sub["needs_skill"] else ""
        lines += [f"### {sub['name']} — {sub['requirement']}{flag}", f"> {sub['quote']}" + ("" if sub["quote_ok"] else " (원문 확인 실패)"), ""]
        for o in sub["options"]:
            skills = ", ".join(f"{s['skill']} {s['level']}" for s in o["skills"]) or "누구나"
            lines.append(f"- **[{o['tier']}] {o['name']}** ({skills}, {o['hours']:g}h): {o['how']}")
            lines.append("  - " + " → ".join(f"{s['name']} {s['hours']:g}h" for s in o["steps"]))
        lines.append("")
    if c["issues"]:
        lines += ["**검사에서 걸린 것**: " + "; ".join(c["issues"]), ""]
    return lines


def main() -> None:
    urls = sys.argv[1:]
    with ThreadPoolExecutor(max_workers=4) as pool:
        items = list(pool.map(run, urls))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = ROOT / "data/results"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"cards_{stamp}.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    md = out / f"cards_{stamp}.md"
    md.write_text(f"# 공고 카드 ({MODEL})\n\n" + "\n".join(line for item in items for line in markdown(item)), encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
