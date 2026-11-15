"""실제 공고 카드 × 학생 한 명의 로드맵을 사람이 읽기 좋게 출력한다(LLM 없음).

사용법: uv run python scripts/try_roadmap.py data/results/cards_<시각>.json "<공고 제목 일부>" "<학생 이름 앞글자: A~D>"
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from aihack4.feasibility import Period, Profile  # noqa: E402
from aihack4.roadmap import build, weeks  # noqa: E402
from aihack4.strategy import choose  # noqa: E402
from try_strategy import STUDENTS  # noqa: E402

DAYS = "월화수목금토일"


def main() -> None:
    items = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    card = next(i["card"] for i in items if "card" in i and sys.argv[2] in i["card"]["title"])
    label, profile = next((k, v) for k, v in STUDENTS.items() if k.startswith(sys.argv[3]))
    today = date.fromisoformat(card["deadline"]) - timedelta(days=30)
    if label.startswith("C"):
        profile = Profile(**{**profile.__dict__, "blocked": [Period(today + timedelta(days=7), today + timedelta(days=20), "시험")]})
    s = choose(card, profile, today)
    r = build(s, profile)
    print(f"# {card['title']} × {label}\n기준일 {today} · 마감 {card['deadline']} · 판정 {s.verdict} · "
          f"필요 {s.need_hours:g}h / 가용 {s.work_capacity:g}h · 쓰는 비율 {r.pace:.0%}\n")
    for c in s.choices:
        print(f"- {c.submission}: [{c.option['tier']}] {c.option['name']} ({c.hours:g}h)" if c.option else f"- {c.submission}: 할 수 없음")
    print("\n## 주차별")
    for n, w in enumerate(weeks(r, today), 1):
        tasks = " · ".join(f"{k} {v:.1f}h" for k, v in w["tasks"].items()) or ("여유 기간(밀린 일 만회)" if w["buffer"] else "쉬어가요")
        goal = f"  ★ {', '.join(w['milestones'])} 완성" if w["milestones"] else ""
        print(f"{n}주차 ({w['start']:%m/%d}~{w['end']:%m/%d}): {tasks}{goal}")
    print("\n## 첫 10일")
    for day in r.days[:10]:
        what = ", ".join(f"{i.step} {i.hours:.1f}h" for i in day.items) or "-"
        print(f"{day.date:%m/%d}({DAYS[day.date.weekday()]}) 가능 {day.capacity:g}h → {what}{' [여유 기간]' if day.buffer else ''}")


if __name__ == "__main__":
    main()
