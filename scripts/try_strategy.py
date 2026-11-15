"""실제 공고 카드 × 성격이 다른 학생으로 맞춤 전략이 얼마나 갈리는지 본다(LLM 없음).

사용법: uv run python scripts/try_strategy.py data/results/cards_<시각>.json
기준일은 공고마다 마감 30일 전으로 둔다.
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aihack4.card import check_card  # noqa: E402
from aihack4.feasibility import Period, Profile  # noqa: E402
from aihack4.strategy import choose, unpersonalized  # noqa: E402

STUDENTS = {
    "A 인문 1학년·처음·주 6h": Profile(grade=1, region="서울", skills={"기획": "입문", "글쓰기": "가능"},
                                    experience="처음", weekday_hours=0.6, weekend_hours=1.5),
    "B AI 3학년·수상·주 12h": Profile(grade=3, region="서울", skills={"개발": "능숙", "데이터·AI": "가능", "기획": "가능"},
                                   experience="참가 경험", goal="수상 목표", weekday_hours=1.2, weekend_hours=3),
    "C 디자인·팀(개발)·시험 2주": Profile(grade=2, region="서울", skills={"디자인": "능숙", "영상": "가능", "기획": "입문"},
                                    experience="참가 경험", team="팀 있음", team_size=2, teammate_skills=["개발"],
                                    weekday_hours=1, weekend_hours=2),
    "D 휴학·글쓰기·수상·주 20h": Profile(grade=3, region="서울", status="휴학생",
                                     skills={"기획": "가능", "글쓰기": "능숙", "발표": "가능"},
                                     experience="수상 경험", goal="수상 목표", weekday_hours=2, weekend_hours=5),
}


def main() -> None:
    items = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    for item in items:
        if "card" not in item:
            continue
        card = item["card"]
        today = date.fromisoformat(card["deadline"]) - timedelta(days=30)
        print(f"\n## {card['title']}  (기준일 {today}, 마감 D-30)")
        off = unpersonalized(card, today)
        print(f"- 개인화 OFF: " + " / ".join(f"{c.option['name']}({c.hours:g}h)" for c in off.choices))
        for label, base in STUDENTS.items():
            profile = base
            if label.startswith("C"):  # 시험 2주: 기준일 1주 뒤부터
                profile = Profile(**{**base.__dict__, "blocked": [Period(today + timedelta(days=7), today + timedelta(days=20), "시험")]})
            s = choose(card, profile, today)
            picks = " / ".join(f"[{c.option['tier']}] {c.option['name']}({c.hours:g}h)" if c.option else f"✗ {c.submission}"
                               for c in s.choices)
            print(f"- {label} → **{s.verdict}** · 필요 {s.need_hours:g}h / 가용 {s.work_capacity:g}h")
            print(f"    {picks}")
            reasons = [r for c in s.choices for r in c.reasons if "목표라" not in r]
            if reasons:
                print("    이유: " + " | ".join(reasons))


if __name__ == "__main__":
    main()
