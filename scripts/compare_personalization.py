"""개인화 유무·역할 분담에 따른 계획 품질 비교.

사용법: uv run python scripts/compare_personalization.py --workers 8

세 조건(baselines.py 참고)의 계획을 페르소나의 실제 제약(못 쓰는 기간, 요일별 가능 시간, 마감)과 대조한다.
- generic: 공고만 보고 짠 계획 → 공고마다 한 번 부르고, 6명 모두에게 같은 계획이 간다.
- llm_only: 프로필까지 보고 LLM이 계산·판정·일정을 혼자 한 계획
- podo: LLM은 작업 분해만, 계산·판정·일정은 코드 (eval_feasibility와 같은 캐시를 쓴다)

결과는 data/results/personalization_<모델>_<시각>.{json,md}.
"""

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, median

from dotenv import load_dotenv
from openai import OpenAI

from aihack4 import baselines as bl
from aihack4 import feasibility_eval as fe
from aihack4.feasibility import evaluate
from aihack4.llm_runner import Cache, Runner

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "data/test/feasibility"
DB_PATH = ROOT / "data/contests/contests.db"
CACHE_PATH = ROOT / "data/contests/feasibility_cache.jsonl"
RESULT_DIR = ROOT / "data/results"
CONDITIONS = ("generic", "llm_only", "podo")
LABELS = {"generic": "A. 개인화 없음 (LLM)", "llm_only": "B. 개인화 + LLM 단독", "podo": "C. PODO (개인화 + LLM 추출 + 코드 계산)"}


def score(cases: list[dict]) -> dict:
    out = {}
    for condition in CONDITIONS:
        rows = [case[condition] for case in cases if case.get(condition)]
        if not rows:
            continue
        blocked_rows = [r for case, r in zip(cases, rows) if case["has_blocked"]]
        summary = {
            "cases": len(rows),
            # 핵심 지표: 실제로는 실행할 수 없는 계획을 '할 수 있다'고 내놓은 비율
            "infeasible_presented_as_ok": mean(r["check"]["violates"] and r["presented_ok"] for r in rows),
            "any_violation": mean(r["check"]["violates"] for r in rows),
            "overloaded": mean(r["check"]["overloaded_weeks"] > 0 for r in rows),
            "outside_window": mean(r["check"]["outside_hours"] > 0.05 for r in rows),
            "blocked_used": mean(r["check"]["blocked_hours"] > 0.05 for r in blocked_rows) if blocked_rows else None,
            "judged_hard": mean(not r["presented_ok"] for r in rows),
            "verdicts": dict(Counter(r["verdict"] for r in rows)),
        }
        if condition == "llm_only":
            errors = [r["available_error"] for r in rows if r.get("available_error") is not None]
            summary |= {
                "available_error_median": median(errors) if errors else None,
                "available_error_over_20pct": mean(e > 0.2 for e in errors) if errors else None,
                "self_contradiction": mean(r["self_contradiction"] for r in rows),
                "sum_mismatch": mean(r["sum_mismatch"] for r in rows),
            }
        out[condition] = summary
    return out


def weekly(tasks: list[dict], profile, today: date, deadline: date) -> dict:
    """주차별 계획 시간(작업 기간에 고르게 나눔)."""
    weeks: dict[str, float] = defaultdict(float)
    for task in tasks:
        for day, hours in (bl.spread(task, profile) or {}).items():
            weeks[str((day - today).days // 7 + 1)] += hours
    return weeks


def showcase(case: dict) -> list[str]:
    """한 케이스의 주차별 비교표. 발표 자료용."""
    profile, today, deadline = case["profile"], case["today"], case["deadline"]
    n_weeks = ((deadline - today).days + 6) // 7
    lines = [f"### 예시: {case['title']} × {case['persona_label']}", "",
             f"기준일 {today} · 마감 {deadline} · 못 쓰는 기간: "
             + (", ".join(f"{p.start}~{p.end} {p.reason}" for p in profile.blocked) or "없음"), "",
             "| 주차 | 쓸 수 있는 시간 | " + " | ".join(LABELS[c].split(" (")[0] for c in CONDITIONS) + " |",
             "|---|---|---|---|---|"]
    plans = {c: weekly(case[c]["tasks"], profile, today, deadline) for c in CONDITIONS}
    for week in range(1, n_weeks + 1):
        start = today + timedelta(days=7 * (week - 1))
        cap = sum(bl.capacity(start + timedelta(days=n), profile) for n in range(7)
                  if start + timedelta(days=n) < deadline)
        cells = []
        for c in CONDITIONS:
            hours = plans[c].get(str(week), 0)
            cells.append(f"{hours:.1f}" + (" ⚠" if hours > cap * 1.2 + 1 else ""))
        lines.append(f"| {week}주차 | {cap:.1f} | " + " | ".join(cells) + " |")
    lines.append("| 판정 | | " + " | ".join(case[c]["verdict"] for c in CONDITIONS) + " |")
    lines += ["", "⚠ = 그 주에 쓸 수 있는 시간을 20% 넘게 초과"]
    return lines


def pct(value) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def render(report: dict) -> str:
    s = report["summary"]
    lines = [f"# 개인화 유무·역할 분담 비교 — {report['model']}", "",
             f"실행 {report['run_at']} · 공고 {report['notices']}건 × 페르소나 {report['personas']}명 = {s['podo']['cases']}케이스", "",
             "| 지표 | " + " | ".join(LABELS[c] for c in CONDITIONS) + " |", "|---|---|---|---|"]
    rows = [
        ("실행 불가능한 계획을 '가능'으로 제시", "infeasible_presented_as_ok"),
        ("제약 위반이 하나라도 있는 계획", "any_violation"),
        ("쓸 수 있는 시간을 넘긴 주가 있음", "overloaded"),
        ("시험 기간에 작업 배치 (못 쓰는 기간이 있는 케이스)", "blocked_used"),
        ("기준일 전·마감 후에 작업 배치", "outside_window"),
        ("'어려움' 판정 비율", "judged_hard"),
    ]
    for label, key in rows:
        lines.append(f"| {label} | " + " | ".join(pct(s.get(c, {}).get(key)) for c in CONDITIONS) + " |")
    lo = s.get("llm_only", {})
    lines += ["", "B(LLM 단독)의 계산 오류:",
              f"- 가용 시간 계산 오차 중앙값 {pct(lo.get('available_error_median'))}, 20% 넘게 틀린 비율 {pct(lo.get('available_error_over_20pct'))} (혼자 하는 페르소나만)",
              f"- 자기 숫자로도 시간이 모자란데 '참여 가능' 판정 {pct(lo.get('self_contradiction'))}",
              f"- 필요 시간 합계가 자기 작업 시간 합과 다름 {pct(lo.get('sum_mismatch'))}",
              "", "A(개인화 없음)는 공고마다 계획이 하나라 6명의 서로 다른 학생에게 똑같은 계획을 준다.", ""]
    for block in report["showcases"]:
        lines += block + [""]
    lines.append(f"LLM 새 호출 {report['llm']['new_calls']}건(${report['llm']['new_cost_usd']}), 캐시 {report['llm']['cached_calls']}건, 오류 {report['llm']['errors']}건")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    model, client = os.environ["LLM_MODEL"], OpenAI()
    runner = Runner(Cache(CACHE_PATH))
    notices = fe.load_notices(DB_PATH, TEST_DIR / "notices_synthetic.json")
    personas = json.loads((TEST_DIR / "personas.json").read_text(encoding="utf-8"))

    # C(PODO)는 서비스와 같이 공고마다 자격 조건을 먼저 추출해 둔다(eval_feasibility의 반복 0번과 같은 캐시 키)
    condition_raws = runner.run_all([fe.conditions_job(client, model, f"conditions:{item['id']}:0", item["notice"])
                                     for item in notices], args.workers)
    for item, raw in zip(notices, condition_raws):
        item["notice"] = {**item["notice"], "eligibility_conditions": raw["conditions"] if raw else []}

    cases, jobs, slots = [], [], []
    for n_index, item in enumerate(notices):
        notice, today = item["notice"], fe.base_date(item["notice"])
        jobs.append({"name": f"generic:{item['id']}", "key_text": f"{model}\n{bl.GENERIC_SYSTEM}\n{bl.generic_input(notice, today)}",
                     "call": lambda notice=notice, today=today: bl.generic_plan(client, model, notice, today)})
        slots.append(("generic", n_index))
        for persona in personas:
            profile = fe.make_profile(persona, today)
            index = len(cases)
            cases.append({"id": f"{item['id']}×{persona['id']}", "source": item["source"], "notice_index": n_index,
                          "title": notice.get("title"), "persona": persona["id"], "persona_label": persona["label"],
                          "notice": notice, "profile": profile, "today": today,
                          "deadline": date.fromisoformat(notice["deadline"]), "has_blocked": bool(profile.blocked)})
            jobs.append({"name": f"llm_only:{index}",
                         "key_text": f"{model}\n{bl.LLM_ONLY_SYSTEM}\n{bl.llm_only_input(notice, profile, today)}",
                         "call": lambda notice=notice, profile=profile, today=today: bl.llm_only_plan(client, model, notice, profile, today)})
            slots.append(("llm_only", index))
            # eval_feasibility의 반복 0번과 같은 캐시 키
            jobs.append(fe.decompose_job(client, model, f"podo:{index}", notice, profile, today))
            slots.append(("podo", index))

    print(f"LLM 호출 {len(jobs)}건 (캐시 포함)")
    raws = runner.run_all(jobs, args.workers)
    generic_raws = {ref: raw for (kind, ref), raw in zip(slots, raws) if kind == "generic"}

    for (kind, ref), raw in zip(slots, raws):
        if kind == "generic" or not raw:
            continue
        case = cases[ref]
        profile, today, deadline = case["profile"], case["today"], case["deadline"]
        if kind == "llm_only":
            truth = bl.true_available(profile, today, deadline)
            case["llm_only"] = {
                "tasks": raw["tasks"], "verdict": raw["verdict"], "presented_ok": raw["verdict"] != "어려움",
                "check": bl.check_plan(raw["tasks"], profile, today, deadline),
                "available_llm": raw["available_hours"], "available_true": truth,
                # 팀 효율 가정(TEAM_EFFICIENCY)은 LLM이 알 수 없어 혼자 하는 경우만 계산 오차를 잰다.
                "available_error": abs(raw["available_hours"] - truth) / truth if profile.people() == 1 and truth else None,
                "self_contradiction": bl.self_contradiction(raw), "sum_mismatch": bl.sum_mismatch(raw),
            }
        else:
            result = evaluate(case["notice"], profile, raw, today)
            tasks = bl.podo_tasks(result)
            case["podo"] = {"tasks": tasks, "verdict": result.verdict, "presented_ok": result.verdict != "어려움",
                            "check": bl.check_plan(tasks, profile, today, deadline)}
    for case in cases:
        generic = generic_raws.get(case["notice_index"])
        if generic:
            case["generic"] = {"tasks": generic["tasks"], "verdict": "(판정 없음)", "presented_ok": True,
                               "check": bl.check_plan(generic["tasks"], case["profile"], case["today"], case["deadline"])}

    complete = [c for c in cases if all(c.get(k) for k in CONDITIONS)]
    # 발표용 예시: 시험 기간이 있는 페르소나, 시간이 적은 페르소나에서 A가 깨지고 C가 지킨 케이스
    picks = []
    for persona in ("P4", "P6"):
        candidates = [c for c in complete if c["persona"] == persona and c["source"] == "real"
                      and c["generic"]["check"]["violates"] and not (c["podo"]["check"]["violates"] and c["podo"]["presented_ok"])]
        # 자격 문제가 없는(일반 대학생 대상) 공고를 먼저 보여준다
        found = min(candidates, key=lambda c: c["podo"]["verdict"] == "확인 필요", default=None)
        if found:
            picks.append(showcase(found))

    report = {
        "model": model, "run_at": datetime.now().isoformat(timespec="seconds"),
        "notices": len(notices), "personas": len(personas),
        "summary": score(complete),
        "summary_by_source": {src: score([c for c in complete if c["source"] == src]) for src in ("real", "synthetic")},
        "showcases": picks, "llm": runner.stats(), "errors": runner.errors,
        "cases": [{k: v for k, v in c.items() if k not in ("notice", "profile")} for c in complete],
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    stem = RESULT_DIR / f"personalization_{model.replace('/', '-')}_{datetime.now():%Y%m%d-%H%M%S}"
    stem.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    stem.with_suffix(".md").write_text(render(report), encoding="utf-8")
    print(render(report))
    print(f"저장: {stem}.json / .md")


if __name__ == "__main__":
    main()
