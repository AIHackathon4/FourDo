"""맞춤 전략: 공고 카드의 선택지 중에서 학생에게 맞는 방법을 코드로 고른다(LLM 없음). 설계: docs/백엔드_설계_v3.md

1. 자격·팀 조건 비교
2. 쓸 수 있는 시간(마감 전 여유 기간은 최종 점검·제출용으로 뺀다)
3. 제출물마다 할 수 있는 선택지 걸러내기(본인 또는 팀원의 역량·수준)
4. 목표로 고르기(수상 → 가장 높은 단계, 경험 → 표준)
5. 시간이 모자라면 아끼는 시간이 큰 것부터 한 단계씩 낮추기
6. 판정
"""

from dataclasses import dataclass, field
from datetime import date

from aihack4.card import TIERS
from aihack4.feasibility import (FIRST_TIMER_FACTOR, LEVELS, Check, Profile, decide, eligibility_check,
                                 hours_needed, team_check, work_days)

TEAMMATE_LEVEL = "가능"  # 팀원 역량은 수준 없이 받는다(가정)
BASE_SKILLS = {"기획": "입문", "글쓰기": "입문", "발표": "입문"}  # 대학생이면 누구나 이 정도는 한다고 본다(가정)
STRETCH_FACTOR = 1.5  # 필요한 수준보다 한 단계 낮으면 시간이 더 든다(가정, 예전 엔진의 LOWER_LEVEL_FACTOR와 같은 값)
BUFFER_SHARE = 0.1  # 마감 전 여유 기간: 작업일의 10%, 최소 1일
FINAL_BASE_HOURS = 1.0  # 최종 점검·제출
FORM_HOURS = 0.3  # 양식 서류 하나당
TIGHT_SHARE = 0.85  # 쓸 수 있는 시간의 85%를 넘게 쓰면 빠듯함(가정)


@dataclass
class Choice:
    submission: str
    requirement: str
    option: dict | None  # 고른 선택지(할 수 있는 게 없으면 None)
    hours: float  # 첫 참가 보정까지 한 시간
    who: dict[str, str]  # 필요 역량 → "본인"/"팀원"
    reasons: list[str]
    target_tier: str | None  # 시간을 생각하지 않았을 때 고를 단계
    feasible: list[dict] = field(default_factory=list)  # 할 수 있는 선택지(낮은 단계부터)


@dataclass
class Strategy:
    verdict: str
    checks: list[Check]
    questions: list[str]
    choices: list[Choice]
    need_hours: float  # 고른 방법들 + 최종 점검·제출
    work_capacity: float  # 여유 기간 전까지 쓸 수 있는 시간
    final_hours: float
    work_days: list[date]
    buffer_days: list[date]
    days_left: int | None
    personalized: bool = True
    notes: list[str] = field(default_factory=list)  # 판정에 넣지 않은 일반 조건(한 번 확인만)


# ---------- 역량 ----------

def level_of(profile: Profile, skill: str) -> tuple[str | None, str | None]:
    """본인과 팀원 중 더 높은 수준과 누가 맡는지."""
    mine = profile.skills.get(skill) or BASE_SKILLS.get(skill)
    mate = TEAMMATE_LEVEL if profile.team == "팀 있음" and skill in profile.teammate_skills else None
    candidates = [(lvl, who) for lvl, who in ((mine, "본인"), (mate, "팀원")) if lvl]
    if not candidates:
        return None, None
    return max(candidates, key=lambda c: LEVELS.index(c[0]))


def can_do(option: dict, profile: Profile) -> tuple[bool, dict[str, str], list[str]]:
    """선택지에 필요한 역량을 갖췄는지, 누가 맡는지, 모자란 것.
    한 단계 모자란 역량은 '보완'으로 허용한다(시간을 STRETCH_FACTOR배로 잡는다). 두 단계 이상이거나 없으면 막는다."""
    who, missing = {}, []
    for need in option["skills"]:
        level, person = level_of(profile, need["skill"])
        gap = LEVELS.index(need["level"]) - LEVELS.index(level) if level else None
        if gap is not None and gap <= 1:
            who[need["skill"]] = person + (" 보완" if gap == 1 else "")
        else:
            missing.append(f"{need['skill']} {need['level']}" + (f"(지금 {level})" if level else "(없음)"))
    return not missing, who, missing


def stretched(who: dict[str, str]) -> list[str]:
    return [skill for skill, person in who.items() if person.endswith("보완")]


def option_hours(option: dict, profile: Profile | None) -> float:
    factor = 1.0
    if profile:
        if profile.experience == "처음":
            factor *= FIRST_TIMER_FACTOR
        if stretched(can_do(option, profile)[1]):
            factor *= STRETCH_FACTOR
    return round(option["hours"] * factor, 1)


# ---------- 고르기 ----------

def target_tier(goal: str) -> str:
    return "차별화" if goal == "수상 목표" else "표준"


def initial_choice(sub: dict, profile: Profile | None) -> Choice:
    """시간을 생각하지 않고, 역량과 목표로만 고른다. profile이 None이면 개인화 OFF(역량 무시, 표준)."""
    options = sub["options"]
    goal_tier = target_tier(profile.goal if profile else "경험 쌓기")
    if profile is None:
        feasible = options
        whos = {id(o): {} for o in options}
        blocked = {}
    else:
        feasible, whos, blocked = [], {}, {}
        for option in options:
            ok, who, missing = can_do(option, profile)
            if ok:
                feasible.append(option)
                whos[id(option)] = who
            else:
                blocked[id(option)] = missing
    if not feasible:
        reasons = [f"'{o['name']}'에는 {', '.join(blocked[id(o)])}이 필요해요" for o in options]
        return Choice(sub["name"], sub["requirement"], None, 0.0, {}, reasons, goal_tier, [])

    rank = TIERS.index(goal_tier)
    at_or_below = [o for o in feasible if TIERS.index(o["tier"]) <= rank]
    top = max((TIERS.index(o["tier"]) for o in at_or_below), default=TIERS.index(feasible[0]["tier"]))
    pick = best_fit([o for o in feasible if TIERS.index(o["tier"]) == top], profile)
    reasons = []
    if pick["tier"] != goal_tier:
        wanted = next((o for o in options if o["tier"] == goal_tier), None)
        if wanted and id(wanted) in blocked:
            reasons.append(f"목표 단계 '{wanted['name']}'에는 {', '.join(blocked[id(wanted)])}이 필요해서 "
                           f"'{pick['name']}'로 해요")
    elif profile is not None:
        reasons.append(f"{'수상 목표라' if goal_tier == '차별화' else '경험 쌓기 목표라'} '{pick['tier']}' 단계로 해요")
    mates = [skill for skill, who in whos[id(pick)].items() if who.startswith("팀원")]
    if mates:
        reasons.append(f"{', '.join(mates)}는 팀원이 맡아요")
    stretch = stretched(whos[id(pick)])
    if stretch:
        need = {n["skill"]: n["level"] for n in pick["skills"]}
        reasons.append(f"{', '.join(f'{k} {need[k]}' for k in stretch)} 수준이 필요한데 한 단계 낮아서, "
                       f"익히며 하도록 시간을 {STRETCH_FACTOR:g}배로 잡았어요")
    return Choice(sub["name"], sub["requirement"], pick, option_hours(pick, profile), whos[id(pick)], reasons,
                  goal_tier, feasible)


def best_fit(options: list[dict], profile: Profile | None) -> dict:
    """같은 단계에 방법이 여럿이면: 보완 없이 할 수 있는 것 → 학생이 잘하는 역량을 쓰는 것 → 시간이 적은 것."""
    if profile is None or len(options) == 1:
        return min(options, key=lambda o: o["hours"])

    def key(option):
        who = can_do(option, profile)[1]
        strength = sum(LEVELS.index(level_of(profile, n["skill"])[0]) for n in option["skills"]
                       if level_of(profile, n["skill"])[0])
        return (len(stretched(who)), -strength, option_hours(option, profile))
    return min(options, key=key)


def fit_to_time(choices: list[Choice], capacity: float, profile: Profile) -> None:
    """시간이 모자라면 아끼는 시간이 가장 큰 결과물부터 한 단계씩 낮춘다."""
    while sum(c.hours for c in choices) > capacity:
        best, best_saving = None, 0.0
        for choice in choices:
            if not choice.option:
                continue
            current = TIERS.index(choice.option["tier"])
            lower = [o for o in choice.feasible if TIERS.index(o["tier"]) < current]
            if lower:
                below = max(TIERS.index(o["tier"]) for o in lower)
                option = best_fit([o for o in lower if TIERS.index(o["tier"]) == below], profile)
                saving = choice.hours - option_hours(option, profile)
                if saving > best_saving:
                    best, best_saving = (choice, option), saving
        if not best:
            return
        choice, option = best
        choice.reasons.append(f"시간이 모자라 '{choice.option['name']}' 대신 '{option['name']}'로 낮췄어요"
                              f"({best_saving:g}시간 절약)")
        _, choice.who, _ = can_do(option, profile)
        choice.option, choice.hours = option, option_hours(option, profile)


def known(condition: dict, profile: Profile) -> bool:
    """프로필에 그 조건을 비교할 값이 있는지."""
    value = {"region": profile.region, "school": profile.school, "grade": profile.grade, "age": profile.age,
             "status": profile.status, "major": profile.major}.get(condition["type"])
    return value not in (None, "")


def as_notice(card: dict, profile: Profile) -> dict:
    """엔진의 자격·팀 비교 함수가 쓰는 형태로. 대부분의 대학생이 충족하는 조건(typical)은 프로필로 비교할 수 있을 때만
    판정에 넣고, 아니면 '확인할 것'으로만 보여 준다(판정을 '확인 필요'로 바꾸지 않는다)."""
    conditions = [c for c in card["conditions"] if not c.get("typical") or known(c, profile)]
    return {"eligibility_conditions": conditions, "open_to_undergraduates": card["open_to_undergraduates"],
            "team_min": card["team_min"], "team_max": card["team_max"]}


def notes(card: dict, profile: Profile) -> list[str]:
    """판정에 넣지 않은 일반 조건: 학생이 한 번 확인만 하면 되는 것."""
    return list(dict.fromkeys(c["quote"] for c in card["conditions"] if c.get("typical") and not known(c, profile)))


def plan_days(card: dict, profile: Profile, today: date) -> tuple[list[date], list[date], int | None]:
    """작업일과 마감 전 여유일."""
    if not card.get("deadline"):
        return [], [], None
    deadline = date.fromisoformat(card["deadline"])
    days = work_days(today, deadline, profile)
    buffer = max(1, round(len(days) * BUFFER_SHARE)) if days else 0
    return days[: len(days) - buffer], days[len(days) - buffer:], (deadline - today).days


def choose(card: dict, profile: Profile, today: date, answers: dict[str, bool] | None = None) -> Strategy:
    work, buffer, days_left = plan_days(card, profile, today)
    capacity = round(sum(profile.hours_on(d) for d in work), 1)
    final = round(FINAL_BASE_HOURS + FORM_HOURS * len(card["forms"]), 1)

    choices = [initial_choice(sub, profile) for sub in card["submissions"]]
    wanted = sum(c.hours for c in choices)
    if days_left is not None and days_left >= 0:  # 마감이 지났으면 범위를 줄이지 않는다(의미가 없다)
        fit_to_time(choices, capacity, profile)
    need = round(sum(c.hours for c in choices) + final, 1)

    eligibility, questions = eligibility_check(as_notice(card, profile), profile, answers)
    team, _ = team_check(as_notice(card, profile), profile)
    unmade = [c.submission for c in choices if not c.option]
    if unmade and profile.team == "팀원 구하는 중" and team.status == "ok":  # 맡을 팀원을 구해야 낼 수 있다 → 확인 필요
        team = Check("팀 구성", "warn", f"{', '.join(unmade)}을(를) 맡을 팀원을 구해야 해요.")
    checks = [eligibility, team, time_check(choices, wanted, capacity, days_left, work, profile),
              skill_check(choices, profile)]
    return Strategy(decide(checks), checks, questions, choices, need, capacity, final, work, buffer, days_left,
                    notes=notes(card, profile))


def time_check(choices: list[Choice], wanted: float, capacity: float, days_left: int | None,
               work: list[date], profile: Profile) -> Check:
    used = sum(c.hours for c in choices)
    if days_left is None:
        return Check("시간", "warn", "마감일을 몰라 시간을 계산하지 못했어요.")
    if days_left < 0:
        return Check("시간", "fail", "이미 마감됐어요.")
    if not work or capacity <= 0:
        return Check("시간", "fail", "마감까지 작업할 수 있는 날이 없어요.")
    numbers = f"쓸 수 있는 시간 {capacity:g}시간"
    if used > capacity:
        return Check("시간", "fail", f"가장 가벼운 방법으로도 {used:g}시간이 필요한데 {numbers}이에요. "
                                    f"{hours_needed(used, capacity, profile)}이 필요해요.")
    if used < wanted:
        return Check("시간", "warn", f"목표대로 하면 {wanted:g}시간인데 {numbers}이라 범위를 줄여 {used:g}시간으로 짰어요.")
    if used > capacity * TIGHT_SHARE:
        return Check("시간", "warn", f"필요 {used:g}시간, {numbers}으로 빠듯해요. 밀리면 만회할 여유가 거의 없어요.")
    return Check("시간", "ok", f"필요 {used:g}시간, {numbers}으로 여유가 있어요.")


def skill_check(choices: list[Choice], profile: Profile) -> Check:
    missing = [c for c in choices if not c.option]
    if missing:
        names = ", ".join(c.submission for c in missing)
        if profile.team == "팀원 구하는 중":
            return Check("필요 역량", "warn", f"{names}을(를) 만들 역량이 아직 없어요. 맡아 줄 팀원을 구하면 할 수 있어요.")
        return Check("필요 역량", "fail", f"{names}을(를) 만들 역량이 없어요.")
    stretch = [c for c in choices if stretched(c.who)]
    if stretch:
        return Check("필요 역량", "warn", f"{', '.join(c.submission for c in stretch)}은(는) 역량을 한 단계 끌어올리며 해야 해서 "
                                         f"시간을 더 잡았어요.")
    lowered = [c for c in choices if TIERS.index(c.option["tier"]) < TIERS.index(c.target_tier)
               and any("필요해서" in r for r in c.reasons)]
    if lowered:
        return Check("필요 역량", "warn", f"{', '.join(c.submission for c in lowered)}은(는) 지금 역량에 맞춰 "
                                         f"낮은 단계로 해요.")
    return Check("필요 역량", "ok", "고른 방법을 본인이나 팀원이 할 수 있어요.")


def unpersonalized(card: dict, today: date) -> Strategy:
    """개인화 OFF: 프로필을 모르는 채 모두 표준 단계로 고른 결과(비교 화면용). 시간·역량·자격을 보지 않는다."""
    choices = [initial_choice(sub, None) for sub in card["submissions"]]
    final = round(FINAL_BASE_HOURS + FORM_HOURS * len(card["forms"]), 1)
    days_left = (date.fromisoformat(card["deadline"]) - today).days if card.get("deadline") else None
    return Strategy("알 수 없음", [], [], choices, round(sum(c.hours for c in choices) + final, 1), 0.0, final,
                    [], [], days_left, personalized=False)
