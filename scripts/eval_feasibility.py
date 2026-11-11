"""참여 가능성 판정 검증을 일괄 실행한다.

사용법: uv run python scripts/eval_feasibility.py --repeats 3 --workers 8

- 공고: 수집 DB의 실제 공고(status=extracted) + data/test/feasibility/notices_synthetic.json의 합성 공고
- 프로필: data/test/feasibility/personas.json의 합성 페르소나
- 기준일: 공고 마감 21일 전(이미 마감된 공고도 같은 조건으로 평가)

LLM 작업 분해 응답은 data/contests/feasibility_cache.jsonl에 캐시한다(공고 원문에서 나온 내용이라
수집 DB와 같은 곳에 두고 커밋하지 않는다). 프롬프트·입력이 바뀌면 키가 바뀌어 새로 부른다.
결과는 data/results/feasibility_<모델>_<시각>.{json,md}로 실행마다 새로 저장한다.
"""

import argparse
import json
import os
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from statistics import mean

from dotenv import load_dotenv
from openai import OpenAI

from aihack4 import feasibility_eval as fe
from aihack4.feasibility import eligibility_check, evaluate
from aihack4.llm_runner import Cache, Runner

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "data/test/feasibility"
DB_PATH = ROOT / "data/contests/contests.db"
CACHE_PATH = ROOT / "data/contests/feasibility_cache.jsonl"
RESULT_DIR = ROOT / "data/results"


def by_source(items: list[dict], summarize) -> dict:
    groups = {"all": items}
    for source in ("real", "synthetic"):
        groups[source] = [item for item in items if item["source"] == source]
    return {name: summarize(group) for name, group in groups.items() if group}


def relation_summary(items: list[dict]) -> dict:
    names = sorted({item["relation"] for item in items})
    return {
        "checks": len(items),
        "pass_rate": mean(item["ok"] for item in items),
        "by_relation": {
            name: mean(item["ok"] for item in items if item["relation"] == name) for name in names
        },
    }


def rate_summary(items: list[dict]) -> dict:
    return {"checks": len(items), "pass_rate": mean(item["ok"] for item in items)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3, help="같은 입력을 몇 번 넣어 일관성을 볼지")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--eligibility-notices", type=int, default=3, help="자격 통제 실험에 쓸 공고 수")
    parser.add_argument("--limit-notices", type=int, default=0, help="빠른 확인용. 0이면 전부")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    model = os.environ["LLM_MODEL"]
    client = OpenAI()
    runner = Runner(Cache(CACHE_PATH))
    notices = fe.load_notices(DB_PATH, TEST_DIR / "notices_synthetic.json")
    if args.limit_notices:
        notices = notices[: args.limit_notices]
    personas = json.loads((TEST_DIR / "personas.json").read_text(encoding="utf-8"))
    print(f"공고 {len(notices)}건(실제 {sum(n['source'] == 'real' for n in notices)}), 페르소나 {len(personas)}명, 모델 {model}")

    # 0단계: 공고마다 자격 조건 추출(반복해서 추출 일관성도 본다). 판정에는 반복 0번을 쓴다(서비스와 같음).
    condition_jobs = [fe.conditions_job(client, model, f"conditions:{item['id']}:{repeat}", item["notice"], repeat)
                      for item in notices for repeat in range(args.repeats)]
    print(f"0단계 자격 조건 추출 {len(condition_jobs)}건")
    condition_raws = runner.run_all(condition_jobs, args.workers)
    conditions_items = []
    for index, item in enumerate(notices):
        runs = [raw["conditions"] for raw in condition_raws[index * args.repeats:(index + 1) * args.repeats] if raw]
        if runs:
            item["notice"] = {**item["notice"], "eligibility_conditions": runs[0]}
            conditions_items.append({"case": item["id"], "source": item["source"], "runs": runs,
                                     "ok": fe.conditions_agreement(runs) == 1, "agreement": fe.conditions_agreement(runs)})

    cases = []
    for item in notices:
        today = fe.base_date(item["notice"])
        for persona in personas:
            cases.append({"id": f"{item['id']}×{persona['id']}", "source": item["source"], "notice": item["notice"],
                          "persona": persona["id"], "profile": fe.make_profile(persona, today), "today": today})

    # 1단계: 기본 반복 실행, 무관한 변경, 제출물 규모, 자격 통제 실험
    jobs, slots = [], []

    def add(kind, ref, notice, profile, today, repeat=0, **extra):
        jobs.append(fe.decompose_job(client, model, f"{kind}:{ref}:{repeat}", notice, profile, today, repeat))
        slots.append({"kind": kind, "ref": ref, "repeat": repeat, **extra})

    for index, case in enumerate(cases):
        for repeat in range(args.repeats):
            add("base", index, case["notice"], case["profile"], case["today"], repeat)
        add("invariance", index, case["notice"], fe.irrelevant_change(case["profile"]), case["today"])

    scale_persona = next(p for p in personas if p["id"] == "P2")
    for index, item in enumerate(notices):
        today = fe.base_date(item["notice"])
        profile = fe.make_profile(scale_persona, today)
        for size, submissions in (("small", fe.SMALL_SUBMISSIONS), ("large", fe.LARGE_SUBMISSIONS)):
            add("scale", index, {**item["notice"], "submissions": submissions}, profile, today, size=size)

    # 자격 통제 실험은 작업 분해가 아니라 조건 추출 → 코드 비교 경로를 본다
    eligibility_runs = []
    for index, item in enumerate(notices[: args.eligibility_notices]):
        today = fe.base_date(item["notice"])
        for name, eligibility, changes, expected in fe.ELIGIBILITY_CASES:
            profile = fe.make_profile({"profile": {**scale_persona["profile"], **changes}}, today)
            changed = fe.eligibility_notice(item["notice"], eligibility)
            eligibility_runs.append((name, expected, item["id"], changed, profile,
                                     fe.conditions_job(client, model, f"elig:{item['id']}:{name}", changed)))
    print(f"자격 통제 실험 조건 추출 {len(eligibility_runs)}건")
    eligibility_raws = runner.run_all([run[-1] for run in eligibility_runs], args.workers)

    print(f"1단계 LLM 호출 {len(jobs)}건")
    raws = runner.run_all(jobs, args.workers)
    results = list(zip(slots, raws))

    base_raws: dict[int, list[dict]] = {}
    for slot, raw in results:
        if slot["kind"] == "base" and raw:
            base_raws.setdefault(slot["ref"], []).append(raw)

    # 2단계: 부족한 역량 채우기(1단계의 첫 결과에 의존)
    fill_jobs, fill_refs = [], []
    for index, case in enumerate(cases):
        if index not in base_raws:
            continue
        first = evaluate(case["notice"], case["profile"], base_raws[index][0], case["today"])
        filled = fe.fill_gaps(case["profile"], first)
        if filled:
            fill_jobs.append(fe.decompose_job(client, model, f"fill:{index}", case["notice"], filled, case["today"]))
            fill_refs.append((index, first, filled))
    print(f"2단계 LLM 호출 {len(fill_jobs)}건")
    fill_raws = runner.run_all(fill_jobs, args.workers)

    # ---------- 채점 ----------
    report: dict = {"model": model, "run_at": datetime.now().isoformat(timespec="seconds"), "repeats": args.repeats,
                    "notices": [{"id": n["id"], "source": n["source"], "title": n["notice"].get("title")} for n in notices],
                    "personas": [{"id": p["id"], "label": p["label"]} for p in personas]}

    relation_items, consistency_items, verdict_table = [], [], []
    for index, case in enumerate(cases):
        raws_for_case = base_raws.get(index, [])
        if not raws_for_case:
            continue
        evaluated = [evaluate(case["notice"], case["profile"], raw, case["today"]) for raw in raws_for_case]
        verdict_table.append({"case": case["id"], "source": case["source"], "verdicts": [r.verdict for r in evaluated],
                              "need_hours": [fe.need_mid(r) for r in evaluated], "available": evaluated[0].available_hours})
        if len(evaluated) > 1:
            item = fe.consistency(evaluated)
            consistency_items.append({"case": case["id"], "source": case["source"], **item})
        for relation in fe.RELATIONS:
            checked = fe.check_relation(relation, case["notice"], case["profile"], case["today"], raws_for_case[0])
            if checked:
                relation_items.append({"case": case["id"], "source": case["source"], **checked})

    invariance_items = []
    for slot, raw in results:
        if slot["kind"] != "invariance" or not raw or slot["ref"] not in base_raws:
            continue
        case = cases[slot["ref"]]
        base_verdicts = [evaluate(case["notice"], case["profile"], r, case["today"]).verdict for r in base_raws[slot["ref"]]]
        variant = evaluate(case["notice"], fe.irrelevant_change(case["profile"]), raw, case["today"])
        invariance_items.append({"case": case["id"], "source": case["source"], "ok": fe.invariance_ok(base_verdicts, variant),
                                 "base_verdicts": base_verdicts, "variant_verdict": variant.verdict})

    fill_items = []
    for (index, first, filled), raw in zip(fill_refs, fill_raws):
        if raw:
            case = cases[index]
            variant = evaluate(case["notice"], filled, raw, case["today"])
            fill_items.append({"case": case["id"], "source": case["source"], **fe.skill_fill_ok(first, variant)})

    scale_pairs: dict[int, dict] = {}
    for slot, raw in results:
        if slot["kind"] == "scale" and raw:
            item = notices[slot["ref"]]
            today = fe.base_date(item["notice"])
            profile = fe.make_profile(scale_persona, today)
            submissions = fe.SMALL_SUBMISSIONS if slot["size"] == "small" else fe.LARGE_SUBMISSIONS
            scale_pairs.setdefault(slot["ref"], {})[slot["size"]] = evaluate(
                {**item["notice"], "submissions": submissions}, profile, raw, today)
    scale_items = [
        {"case": notices[ref]["id"], "source": notices[ref]["source"], **fe.submission_scale_ok(pair["small"], pair["large"])}
        for ref, pair in scale_pairs.items() if len(pair) == 2
    ]

    eligibility_items = []
    for (name, expected, notice_id, changed, profile, _), raw in zip(eligibility_runs, eligibility_raws):
        if raw:
            check, _ = eligibility_check({**changed, "eligibility_conditions": raw["conditions"]}, profile)
            eligibility_items.append({"case": name, "notice": notice_id, "expected": expected, "got": check.status,
                                      "detail": check.detail, "conditions": raw["conditions"]})

    report["summary"] = {
        "verdict_distribution": dict(Counter(v for row in verdict_table for v in row["verdicts"][:1])),
        "code_relations": by_source(relation_items, relation_summary),
        "consistency": by_source(consistency_items, fe.summarize_consistency),
        "invariance": by_source(invariance_items, rate_summary),
        "skill_fill": by_source(fill_items, lambda items: {**rate_summary(items),
                                                           "resolved_all_rate": mean(i["resolved_all"] for i in items)}),
        "submission_scale": by_source(scale_items, rate_summary),
        "conditions_consistency": by_source(conditions_items, rate_summary),
        "eligibility": fe.summarize_eligibility(eligibility_items),
        "llm": runner.stats(),
    }
    report["details"] = {
        "verdicts": verdict_table,
        "code_relation_failures": [i for i in relation_items if not i["ok"]],
        "consistency": consistency_items,
        "invariance_failures": [i for i in invariance_items if not i["ok"]],
        "skill_fill": fill_items,
        "submission_scale": scale_items,
        "eligibility": eligibility_items,
        "conditions": [{k: v for k, v in i.items()} for i in conditions_items],
        "errors": runner.errors,
    }

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    stem = RESULT_DIR / f"feasibility_{model.replace('/', '-')}_{datetime.now():%Y%m%d-%H%M%S}"
    stem.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    stem.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    print(f"저장: {stem}.json / .md")


def pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def render_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [f"# 참여 가능성 판정 검증 — {report['model']}", "",
             f"실행 {report['run_at']} · 공고 {len(report['notices'])}건 · 페르소나 {len(report['personas'])}명 · 반복 {report['repeats']}회", "",
             f"판정 분포(첫 실행): {s['verdict_distribution']}", "",
             "| 검사 | 전체 | 실제 공고 | 합성 공고 |", "|---|---|---|---|"]

    def row(label, group, key="pass_rate"):
        cells = [pct(group.get(source, {}).get(key)) for source in ("all", "real", "synthetic")]
        n = group.get("all", {}).get("checks") or group.get("all", {}).get("cases") or 0
        lines.append(f"| {label} (n={n}) | " + " | ".join(cells) + " |")

    row("코드 관계 검사 통과율", s["code_relations"])
    row("반복 판정 일치율", s["consistency"], "mean_verdict_agreement")
    row("반복 판정 완전 일치 케이스", s["consistency"], "fully_consistent_rate")
    row("반복 필요시간 흔들림(중앙값)", s["consistency"], "median_hours_spread")
    row("무관한 변경에 판정 유지", s["invariance"])
    row("역량 채우면 부족 감소·판정 유지", s["skill_fill"])
    row("제출물 커지면 필요시간 증가", s["submission_scale"])
    row("자격 조건 반복 추출이 모두 같은 공고", s["conditions_consistency"])
    elig = s["eligibility"]
    if elig:
        lines += ["", f"참가 자격 통제 실험: 정확도 {pct(elig['accuracy'])} (n={elig['runs']}), "
                      f"자격 미달을 ok로 본 경우 {elig['fail_judged_ok']}건",
                  "", "| 케이스 | 정확도 |", "|---|---|"]
        lines += [f"| {case} | {pct(rate)} |" for case, rate in elig["by_case"].items()]
    by_relation = s["code_relations"].get("all", {}).get("by_relation", {})
    if by_relation:
        lines += ["", "| 코드 관계 | 통과율 |", "|---|---|"]
        lines += [f"| {name} | {pct(rate)} |" for name, rate in by_relation.items()]
    llm = s["llm"]
    lines += ["", f"LLM 새 호출 {llm['new_calls']}건(${llm['new_cost_usd']}), 캐시 {llm['cached_calls']}건, 오류 {llm['errors']}건"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
