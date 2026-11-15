"""맞춤 전략: 공고 카드의 선택지 중에서 학생에게 맞는 방법을 코드로 고른다(LLM 없음). 설계: docs/백엔드_설계_v3.md

1. 자격·팀 조건 비교
2. 쓸 수 있는 시간(마감 전 여유 기간은 최종 점검·제출용으로 뺀다)
3. 제출물마다 할 수 있는 선택지 걸러내기(본인 또는 팀원의 역량·수준)
4. 목표로 고르기(완주 → 최소, 완성 → 표준, 수상 도전 → 차별화). 목표 단계를 못 하면 할 수 있는 방법으로 대신한다
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
# 카드의 단계(최소/표준/차별화)를 학생에게는 목표 기준 이름으로 보여 준다
TIER_LABEL = {"최소": "완주형", "표준": "완성형", "차별화": "수상 도전형"}
# 목표 → 단계. "경험 쌓기"/"수상 목표"는 예전 입력(저장된 프로필)과의 호환용
GOAL_TIER = {"완주": "최소", "완성": "표준", "수상 도전": "차별화", "경험 쌓기": "표준", "수상 목표": "차별화"}
DEFAULT_GOAL = "완성"


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
    substitute: bool = False  # 목표 단계 방법에 필요한 역량이 없어 대체 방법을 골랐는지


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


# ---------- 문장 ----------

def josa(word: str, pair: str) -> str:
    """받침에 맞는 조사를 붙인다. pair: '은는', '이가', '을를', '으로'. 한글이 없으면 '은(는)'처럼 둘 다 적는다."""
    last = next((c for c in reversed(word) if "가" <= c <= "힣"), None)
    if last is None:
        return word + {"은는": "은(는)", "이가": "이(가)", "을를": "을(를)", "으로": "(으)로"}[pair]
    final = (ord(last) - 0xAC00) % 28  # 0이면 받침 없음, 8이면 ㄹ 받침
    if pair == "으로":
        return word + ("로" if final in (0, 8) else "으로")
    return word + (pair[0] if final else pair[1])


def quoted(name: str, pair: str) -> str:
    return josa(f"'{name}'", pair)


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
            missing.append(f"{need['skill']} {need['level']}" + (f"(지금은 {level})" if level else ""))
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
    return GOAL_TIER.get(goal, GOAL_TIER[DEFAULT_GOAL])


def initial_choice(sub: dict, profile: Profile | None) -> Choice:
    """시간을 생각하지 않고, 역량과 목표로만 고른다. profile이 None이면 개인화 OFF(역량 무시, 표준)."""
    options = sub["options"]
    goal_tier = target_tier(profile.goal if profile else DEFAULT_GOAL)
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
        reasons = [f"{quoted(o['name'], '은는')} {', '.join(blocked[id(o)])} 수준이 필요해요" for o in options]
        return Choice(sub["name"], sub["requirement"], None, 0.0, {}, reasons, goal_tier, [])

    rank = TIERS.index(goal_tier)
    at_or_below = [o for o in feasible if TIERS.index(o["tier"]) <= rank]
    top = max((TIERS.index(o["tier"]) for o in at_or_below), default=TIERS.index(feasible[0]["tier"]))
    pick = best_fit([o for o in feasible if TIERS.index(o["tier"]) == top], profile)
    reasons, substitute = [], False
    if pick["tier"] != goal_tier:
        wanted = next((o for o in options if o["tier"] == goal_tier), None)
        if wanted and id(wanted) in blocked:
            substitute = True
            reasons.append(f"{TIER_LABEL[goal_tier]}인 {quoted(wanted['name'], '은는')} {', '.join(blocked[id(wanted)])} "
                           f"수준이 필요해요. 그래서 지금 할 수 있는 {quoted(pick['name'], '을를')} 추천해요")
    elif profile is not None:
        reasons.append(f"'{profile.goal}' 목표에 맞는 방법이에요")
    reasons += method_notes(pick, whos[id(pick)])
    return Choice(sub["name"], sub["requirement"], pick, option_hours(pick, profile), whos[id(pick)], reasons,
                  goal_tier, feasible, substitute=substitute)


def method_notes(option: dict, who: dict[str, str]) -> list[str]:
    """고른 방법에 딸린 설명: 팀원이 맡는 역량, 한 단계 모자라 시간을 더 잡은 역량."""
    notes = []
    mates = [skill for skill, person in who.items() if person.startswith("팀원")]
    if mates:
        notes.append(f"{josa(', '.join(mates), '은는')} 팀원이 맡아요")
    if stretched(who):
        need = {n["skill"]: n["level"] for n in option["skills"]}
        notes.append(f"{', '.join(f'{k} {need[k]}' for k in stretched(who))} 수준이 필요해요. "
                     f"지금보다 한 단계 높아서 익히는 시간까지 넉넉히 잡았어요")
    return notes


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
    """시간이 모자라면 아끼는 시간이 가장 큰 결과물부터 한 단계씩 낮춘다. 학생이 직접 고른 방법은 건드리지 않는다."""
    origin = {}  # 처음 고른 방법(이름, 시간): 여러 번 낮춰도 이유는 처음 방법과 비교해 쓴다
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
        name, hours = origin.setdefault(id(choice), (choice.option["name"], choice.hours))
        _, choice.who, _ = can_do(option, profile)
        choice.option, choice.hours = option, option_hours(option, profile)
        kept = choice.reasons[:1] if choice.substitute else []  # 역량 때문에 대신한 이유는 남긴다
        choice.reasons = kept + [f"시간이 모자라서 '{name}' 대신 {quoted(option['name'], '으로')} 낮췄어요. "
                                 f"{hours - choice.hours:g}시간을 아껴요"] + method_notes(option, choice.who)


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
    if deadline < today:  # 이미 마감: 작업일이 없다
        return [], [], (deadline - today).days
    days = work_days(today, deadline, profile)
    buffer = max(1, round(len(days) * BUFFER_SHARE)) if days else 0
    return days[: len(days) - buffer], days[len(days) - buffer:], (deadline - today).days


def select(card: dict, profile: Profile, capacity: float, open_: bool) -> list[Choice]:
    """목표·역량으로 제출물마다 방법을 하나 고르고, 시간이 모자라면 한 단계씩 낮춘다."""
    choices = [initial_choice(sub, profile) for sub in card["submissions"]]
    if open_:
        fit_to_time(choices, capacity, profile)
    return choices


def choose(card: dict, profile: Profile, today: date, answers: dict[str, bool] | None = None) -> Strategy:
    work, buffer, days_left = plan_days(card, profile, today)
    capacity = round(sum(profile.hours_on(d) for d in work), 1)
    final = round(FINAL_BASE_HOURS + FORM_HOURS * len(card["forms"]), 1)

    # 마감이 지났으면 범위를 줄이지 않는다(의미가 없다)
    open_ = days_left is not None and days_left >= 0
    choices = select(card, profile, capacity, open_)
    initial = [initial_choice(sub, profile) for sub in card["submissions"]]  # 시간을 생각하지 않았을 때
    wanted = sum(i.hours for i in initial)
    need = round(sum(c.hours for c in choices) + final, 1)

    eligibility, questions = eligibility_check(as_notice(card, profile), profile, answers)
    team, _ = team_check(as_notice(card, profile), profile)
    unmade = [c.submission for c in choices if not c.option]
    if unmade and profile.team == "팀원 구하는 중" and team.status == "ok":  # 맡을 팀원을 구해야 낼 수 있다 → 확인 필요
        team = Check("팀 구성", "warn", f"{josa(', '.join(unmade), '을를')} 맡을 팀원을 구해야 해요.")
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
    numbers = f"{used:g}시간이 필요하고 {capacity:g}시간을 쓸 수 있어요."
    if used > capacity:
        return Check("시간", "fail", f"가장 가벼운 방법으로도 {used:g}시간이 필요한데 쓸 수 있는 시간은 {capacity:g}시간이에요. "
                                    f"{hours_needed(used, capacity, profile)}은 써야 낼 수 있어요.")
    if used < wanted:
        return Check("시간", "warn", f"목표대로 하면 {wanted:g}시간이 필요한데 쓸 수 있는 시간이 {capacity:g}시간이라, "
                                    f"방법을 낮춰 {used:g}시간으로 맞췄어요.")
    if used > capacity * TIGHT_SHARE:
        return Check("시간", "warn", f"{numbers} 빠듯해서 밀리면 만회하기 어려워요.")
    return Check("시간", "ok", f"{numbers} 여유가 있어요.")


def skill_check(choices: list[Choice], profile: Profile) -> Check:
    missing = [c for c in choices if not c.option]
    if missing:
        names = ", ".join(c.submission for c in missing)
        if profile.team == "팀원 구하는 중":
            return Check("필요 역량", "warn", f"{josa(names, '을를')} 만들 역량이 아직 없어요. 맡아 줄 팀원을 구하면 할 수 있어요.")
        return Check("필요 역량", "fail", f"{josa(names, '을를')} 만들 역량이 없어요.")
    stretch = [c for c in choices if stretched(c.who)]
    if stretch:
        return Check("필요 역량", "warn", f"{josa(', '.join(c.submission for c in stretch), '은는')} 익히면서 해야 하는 부분이 "
                                         f"있어서 시간을 더 잡았어요.")
    lowered = [c for c in choices if c.substitute]
    if lowered:
        return Check("필요 역량", "warn", f"{josa(', '.join(c.submission for c in lowered), '은는')} 목표한 수준의 방법에 필요한 "
                                         f"역량이 없어서, 지금 할 수 있는 방법으로 바꿨어요.")
    who = "지금 역량과 팀원의 역량으로" if profile.team == "팀 있음" else "지금 역량으로"
    return Check("필요 역량", "ok", f"추천한 방법을 {who} 할 수 있어요.")


def unpersonalized(card: dict, today: date) -> Strategy:
    """개인화 OFF: 프로필을 모르는 채 모두 표준(완성형) 단계로 고른 결과(비교 화면용). 시간·역량·자격을 보지 않는다."""
    choices = [initial_choice(sub, None) for sub in card["submissions"]]
    final = round(FINAL_BASE_HOURS + FORM_HOURS * len(card["forms"]), 1)
    days_left = (date.fromisoformat(card["deadline"]) - today).days if card.get("deadline") else None
    return Strategy("알 수 없음", [], [], choices, round(sum(c.hours for c in choices) + final, 1), 0.0, final,
                    [], [], days_left, personalized=False)
