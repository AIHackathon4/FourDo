"""정답 데이터 없이 참여 가능성 판정을 검증한다.

정답(실제로 참여할 수 있었는지)을 모으기 어렵기 때문에, 입력을 일부러 바꿨을 때 결과가
바뀌어야 할 방향으로만 바뀌는지(관계 검사)와 같은 입력에 같은 답을 내는지(일관성)를 본다.

- 코드 관계 검사: LLM 출력은 고정하고 프로필·기준일만 바꿔 evaluate의 판정 논리를 본다.
- LLM 일관성: 같은 입력을 여러 번 넣었을 때 판정과 필요 시간이 흔들리는 정도.
- LLM 관계 검사: 무관한 변경에는 그대로인지, 역량을 채우면 부족 항목이 줄어드는지,
  제출물이 커지면 필요 시간이 늘어나는지.
- 참가 자격 통제 실험: 자격 문구와 프로필을 정해 두고 기대 상태(ok/warn/fail)와 비교한다.
  (LLM이 문구를 조건으로 추출 → 코드가 프로필과 비교하는 전체 경로를 본다)
- 자격 조건 추출 일관성: 같은 공고에서 조건을 여러 번 뽑았을 때 같은 조건이 나오는지.
"""

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Callable

from .feasibility import (CONDITIONS_SYSTEM, DECOMPOSE_SYSTEM, Feasibility, Period, Profile, conditions_input,
                          decompose, decompose_input, evaluate, extract_conditions)

VERDICT_RANK = {verdict: rank for rank, verdict in enumerate(("어려움", "확인 필요", "준비하면 가능", "참여 가능"))}
STATUS_RANK = {"fail": 0, "warn": 1, "ok": 2}
BASE_LEAD_DAYS = 21  # 평가 기준일 = 마감 21일 전. 이미 지난 공고도 같은 조건으로 평가한다.
GAP_OWNERS = ("부족", "수준 부족")


def load_notices(db_path: Path, synthetic_path: Path) -> list[dict]:
    """수집 DB의 실제 공고(추출 완료, 마감일 있음)와 합성 공고. 각 항목: id, source(real/synthetic), notice."""
    notices = []
    if db_path.exists():
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(
                "SELECT source_id, notice FROM contests WHERE status = 'extracted' ORDER BY source_id"
            ).fetchall()
        for source_id, text in rows:
            notice = json.loads(text)
            if notice.get("deadline"):
                notices.append({"id": f"R-{source_id[:8]}", "source": "real", "notice": notice})
    for item in json.loads(synthetic_path.read_text(encoding="utf-8")):
        notices.append({"id": item["id"], "source": "synthetic", "notice": item["notice"]})
    return notices


def decompose_job(client, model: str, name: str, notice: dict, profile: Profile, today: date, repeat: int = 0) -> dict:
    """llm_runner.Runner용 작업. 반복 번호까지 캐시 키에 넣어 같은 입력을 여러 번 부를 수 있게 한다."""
    return {"name": name,
            "key_text": f"{model}\n{DECOMPOSE_SYSTEM}\n{decompose_input(notice, profile, today)}\n{repeat}",
            "call": lambda: decompose(client, model, notice, profile, today)}


def conditions_job(client, model: str, name: str, notice: dict, repeat: int = 0) -> dict:
    """자격 조건 추출 작업. 응답은 {"conditions": [...]} 형태로 캐시한다."""
    def call():
        conditions, cost = extract_conditions(client, model, notice)
        return {"conditions": conditions}, cost
    return {"name": name, "key_text": f"{model}\n{CONDITIONS_SYSTEM}\n{conditions_input(notice)}\n{repeat}", "call": call}


def base_date(notice: dict, lead_days: int = BASE_LEAD_DAYS) -> date:
    return date.fromisoformat(notice["deadline"]) - timedelta(days=lead_days)


def make_profile(persona: dict, today: date) -> Profile:
    """페르소나의 못 쓰는 기간은 기준일로부터 며칠 뒤인지로 적혀 있다."""
    fields = dict(persona["profile"])
    fields["blocked"] = [
        Period(today + timedelta(days=start), today + timedelta(days=end), reason)
        for start, end, reason in persona.get("blocked_offsets", [])
    ]
    return Profile(**fields)


def gap_count(result: Feasibility) -> int:
    return sum(task.owner in GAP_OWNERS for task in result.tasks)


def need_mid(result: Feasibility) -> float:
    return (result.need_min + result.need_max) / 2


def time_status(result: Feasibility) -> str:
    return next((check.status for check in result.checks if check.item == "시간"), "warn")


# ---------- 코드 관계 검사 ----------

@dataclass(frozen=True)
class Relation:
    name: str
    description: str
    applies: Callable[[dict, Profile, date], bool]
    change: Callable[[Profile, date], tuple[Profile, date]]
    direction: int  # +1: 판정이 나빠지면 안 된다, -1: 좋아지면 안 된다
    metric: str  # 함께 보는 수치(Feasibility 속성)
    metric_direction: int  # +1: 커지거나 같아야 한다, -1: 작아지거나 같아야 한다


def _days_left(notice: dict, today: date) -> int:
    return (date.fromisoformat(notice["deadline"]) - today).days


def _team_allows(notice: dict, size: int) -> bool:
    return not notice.get("team_max") or notice["team_max"] >= size


RELATIONS = (
    Relation("more_time", "평일·주말 하루 1시간씩 더 쓸 수 있다",
             lambda n, p, t: True,
             lambda p, t: (replace(p, weekday_hours=p.weekday_hours + 1, weekend_hours=p.weekend_hours + 1), t),
             +1, "available_hours", +1),
    Relation("start_later", "5일 늦게 시작한다",
             lambda n, p, t: _days_left(n, t) > 6,
             lambda p, t: (p, t + timedelta(days=5)),
             -1, "available_hours", -1),
    Relation("blocked_week", "첫 주를 통째로 못 쓴다",
             lambda n, p, t: _days_left(n, t) > 8,
             lambda p, t: (replace(p, blocked=[*p.blocked, Period(t, t + timedelta(days=6), "검증")]), t),
             -1, "available_hours", -1),
    Relation("experienced", "공모전 참가 경험이 생긴다",
             lambda n, p, t: p.experience == "처음",
             lambda p, t: (replace(p, experience="참가 경험"), t),
             +1, "need_min", -1),
    Relation("award_goal", "목표를 수상으로 높인다",
             lambda n, p, t: p.goal == "경험 쌓기",
             lambda p, t: (replace(p, goal="수상 목표"), t),
             -1, "target_hours", +1),
    Relation("teammate_joins", "혼자 하다가 팀원 1명이 생긴다",
             lambda n, p, t: p.team == "혼자" and _team_allows(n, 2),
             lambda p, t: (replace(p, team="팀 있음", team_size=2), t),
             +1, "available_hours", +1),
)


def _respects(before: float, after: float, direction: int) -> bool:
    return after >= before if direction > 0 else after <= before


def check_relation(relation: Relation, notice: dict, profile: Profile, today: date, raw: dict) -> dict | None:
    """관계가 적용되지 않으면 None. 적용되면 판정·시간 상태·수치가 기대 방향을 지켰는지 돌려준다."""
    if not relation.applies(notice, profile, today):
        return None
    base = evaluate(notice, profile, raw, today)
    changed_profile, changed_today = relation.change(profile, today)
    variant = evaluate(notice, changed_profile, raw, changed_today)
    checks = {
        "verdict": _respects(VERDICT_RANK[base.verdict], VERDICT_RANK[variant.verdict], relation.direction),
        "time_status": _respects(STATUS_RANK[time_status(base)], STATUS_RANK[time_status(variant)], relation.direction),
        "metric": _respects(getattr(base, relation.metric), getattr(variant, relation.metric), relation.metric_direction),
    }
    return {
        "relation": relation.name,
        "ok": all(checks.values()),
        "failed": [name for name, passed in checks.items() if not passed],
        "base_verdict": base.verdict,
        "variant_verdict": variant.verdict,
        "base_metric": getattr(base, relation.metric),
        "variant_metric": getattr(variant, relation.metric),
    }


# ---------- LLM 일관성 ----------

def consistency(results: list[Feasibility]) -> dict:
    """같은 입력을 반복한 결과들."""
    verdicts = Counter(result.verdict for result in results)
    mids = [need_mid(result) for result in results]
    center = mean(mids)
    return {
        "runs": len(results),
        "verdicts": dict(verdicts),
        "verdict_agreement": verdicts.most_common(1)[0][1] / len(results),
        "hours_spread": (max(mids) - min(mids)) / center if center else 0.0,
        "task_count_range": [min(len(r.tasks) for r in results), max(len(r.tasks) for r in results)],
    }


def summarize_consistency(items: list[dict]) -> dict:
    if not items:
        return {}
    return {
        "cases": len(items),
        "mean_verdict_agreement": mean(item["verdict_agreement"] for item in items),
        "fully_consistent_rate": mean(item["verdict_agreement"] == 1 for item in items),
        "median_hours_spread": median(item["hours_spread"] for item in items),
        "max_hours_spread": max(item["hours_spread"] for item in items),
    }


# ---------- LLM 관계 검사 ----------

IRRELEVANT_NOTE = "작업할 때는 주로 학교 도서관을 이용해요."


def irrelevant_change(profile: Profile) -> Profile:
    """판정과 무관한 변경: 역량 입력 순서를 뒤집고 무관한 메모를 단다."""
    return replace(profile, skills=dict(reversed(list(profile.skills.items()))), note=IRRELEVANT_NOTE)


def fill_gaps(profile: Profile, result: Feasibility) -> Profile | None:
    """부족하다고 나온 역량을 '능숙'으로 채운 프로필. 부족한 역량이 없으면 None."""
    names = [task.matched if task.owner == "수준 부족" else task.skill for task in result.tasks if task.owner in GAP_OWNERS]
    if not names:
        return None
    return replace(profile, skills={**profile.skills, **{name: "능숙" for name in names if name}})


def invariance_ok(base_verdicts: list[str], variant: Feasibility) -> bool:
    """반복 실행에서 나온 판정 중 하나와 같으면 통과(원래 흔들리는 만큼은 봐준다)."""
    return variant.verdict in base_verdicts


def skill_fill_ok(base: Feasibility, variant: Feasibility) -> dict:
    return {
        "ok": gap_count(variant) < gap_count(base) and VERDICT_RANK[variant.verdict] >= VERDICT_RANK[base.verdict],
        "resolved_all": gap_count(variant) == 0,
        "base_gaps": gap_count(base),
        "variant_gaps": gap_count(variant),
        "base_verdict": base.verdict,
        "variant_verdict": variant.verdict,
    }


SMALL_SUBMISSIONS = ["A4 1장 분량의 아이디어 요약서"]
LARGE_SUBMISSIONS = ["3분 이내 홍보 영상 1편", "기획서 10쪽 이내", "발표 자료(PPT) 15장 이내"]


def submission_scale_ok(small: Feasibility, large: Feasibility) -> dict:
    return {"ok": need_mid(large) > need_mid(small), "small_hours": need_mid(small), "large_hours": need_mid(large)}


# ---------- 참가 자격 통제 실험 ----------

ELIGIBILITY_CASES = (
    # 이름, 공고 자격 문구, 프로필 변경, 기대 상태
    ("open", ["전국 대학생 및 대학원생 누구나"], {}, "ok"),
    ("region_match", ["부산광역시에 주민등록상 주소를 둔 대학생"], {"region": "부산"}, "ok"),
    ("region_mismatch", ["부산광역시에 주민등록상 주소를 둔 대학생"], {"region": "서울"}, "fail"),
    ("region_unknown", ["부산광역시에 주민등록상 주소를 둔 대학생"], {"region": ""}, "warn"),
    ("school_match", ["한빛대학교 재학생"], {"school": "한빛대학교"}, "ok"),
    ("school_mismatch", ["한빛대학교 재학생"], {"school": "서강대학교"}, "fail"),
    ("leave_allowed", ["국내 대학 재학생 및 휴학생"], {"status": "휴학생"}, "ok"),
    ("enrolled_only", ["국내 대학 재학생(휴학생 제외)"], {"status": "휴학생"}, "fail"),
    ("grad_only", ["국내 대학원 석·박사과정 재학생"], {"status": "재학생"}, "fail"),
    ("age_unknown", ["만 19세 이상 29세 이하 청년"], {}, "warn"),
)


def conditions_signature(conditions: list[dict]) -> tuple:
    """판정에 영향을 주는 부분만 남긴 조건 목록(문구·질문 표현 차이는 무시).
    other는 코드가 항상 사용자에게 묻기만 하므로 allowed 문구가 판정에 쓰이지 않는다."""
    return tuple(sorted(
        (c["type"], () if c["type"] == "other" else tuple(sorted(c["allowed"])), c["min"], c["max"])
        for c in conditions
    ))


def conditions_agreement(runs: list[list[dict]]) -> float:
    signatures = Counter(conditions_signature(run) for run in runs)
    return signatures.most_common(1)[0][1] / len(runs)


def eligibility_notice(notice: dict, eligibility: list[str]) -> dict:
    """자격 문구만 바꾼 공고. 학부생 참가 가능 여부는 문구로만 판단하게 unknown으로 둔다."""
    changed = {**notice, "eligibility": eligibility, "open_to_undergraduates": "unknown", "evidence": []}
    changed.pop("eligibility_conditions", None)
    return changed


def summarize_eligibility(items: list[dict]) -> dict:
    """items: case, expected, got. 상태별 정확도와 혼동 행렬."""
    if not items:
        return {}
    confusion = Counter((item["expected"], item["got"]) for item in items)
    by_case: dict[str, list[bool]] = {}
    for item in items:
        by_case.setdefault(item["case"], []).append(item["expected"] == item["got"])
    return {
        "runs": len(items),
        "accuracy": mean(item["expected"] == item["got"] for item in items),
        # 자격이 안 되는데 ok라고 하는 실수가 가장 위험하다.
        "fail_judged_ok": sum(item["expected"] == "fail" and item["got"] == "ok" for item in items),
        "by_case": {case: mean(hits) for case, hits in by_case.items()},
        "confusion": {f"{expected}->{got}": count for (expected, got), count in sorted(confusion.items())},
    }
