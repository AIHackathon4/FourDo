"""프론트 입력 ↔ 설계 v3 엔진(card → strategy → roadmap) ↔ 프론트 응답(JSON). LLM을 부르지 않는 순수 코드.

흐름: 프론트 폼 → to_profile() → strategy.choose()(제출물별 방법 고르기·판정) → roadmap.build()(날짜 배치)
→ to_response()로 화면이 그리는 JSON. 설계: docs/백엔드_설계_v3.md
"""

import re
from datetime import date, datetime, timedelta

from aihack4.card import SKILLS, TIERS
from aihack4.feasibility import GOALS, LEVELS, STATUSES, Period, Profile
from aihack4.roadmap import FINAL_NAME, Roadmap, step_key, weeks
from aihack4.strategy import Choice, Strategy, can_do, option_hours, stretched

DAYS = "월화수목금토일"
DEFAULT_LEVEL = "가능"  # 수준 없이 이름만 온 경우
WEEKEND_RATIO = 2  # 주당 시간 → 평일·주말 하루 시간. 주말 하루 = 평일 하루의 2배(가정)

STATUS_TO_FRONT = {"ok": "met", "warn": "caution", "fail": "lacking"}
READINESS = {
    "참여 가능": "참여 가능 · 지금 시작해도 충분해요",
    "준비하면 가능": "준비하면 가능 · 아래 전략대로 하면 낼 수 있어요",
    "확인 필요": "확인 필요 · 참가 조건부터 확인해요",
    "어려움": "어려움 · 이번 회차는 쉽지 않아요",
}


# ---------- 입력 ----------

def parse_grade_major(text: str) -> tuple[int | None, str]:
    """'3학년 · 인공지능' → (3, '인공지능')."""
    text = text or ""
    found = re.search(r"([1-6])\s*학년", text)
    grade = int(found.group(1)) if found else None
    major = re.sub(r"[1-6]\s*학년", "", text)
    major = re.sub(r"^[\s·,/|-]+|[\s·,/|-]+$", "", major)
    return grade, major


def _resolve(month: int, day: int, today: date) -> date | None:
    """연도가 없는 날짜는 올해로 보고, 이미 한참 지났으면 내년으로 본다."""
    try:
        value = date(today.year, month, day)
    except ValueError:
        return None
    return value.replace(year=today.year + 1) if value < today - timedelta(days=60) else value


DATE = r"(?:(\d{4})[./-])?(\d{1,2})[./](\d{1,2})"
RANGE = re.compile(DATE + r"\s*(?:~|-|–|부터)\s*" + DATE + r"\s*(?:까지)?\s*([^,;\n]*)")
SINGLE = re.compile(DATE + r"\s*([^,;\n~]*)")


def parse_busy(text: str, today: date) -> tuple[list[Period], str]:
    """'11/16~11/20 중간고사, 주 2회 아르바이트' → 못 쓰는 기간 목록과 나머지 메모.
    날짜가 있는 일정만 기간으로 만들고, 반복 일정처럼 날짜가 없는 내용은 메모로 넘긴다."""
    periods, rest = [], []
    for part in re.split(r"[,;\n]", text or ""):
        part = part.strip()
        if not part:
            continue
        found = RANGE.search(part)
        if found:
            y1, m1, d1, y2, m2, d2, reason = found.groups()
            start = date(int(y1), int(m1), int(d1)) if y1 else _resolve(int(m1), int(d1), today)
            end = date(int(y2), int(m2), int(d2)) if y2 else _resolve(int(m2), int(d2), today)
            if start and end:
                if end < start:
                    end = end.replace(year=end.year + 1)
                periods.append(Period(start, end, reason.strip() or "바쁜 일정"))
                continue
        found = SINGLE.search(part)
        if found and found.group(2):
            y, m, d, reason = found.groups()
            day = date(int(y), int(m), int(d)) if y else _resolve(int(m), int(d), today)
            if day:
                periods.append(Period(day, day, reason.strip() or "바쁜 일정"))
                continue
        rest.append(part)
    return periods, ", ".join(rest)


def parse_experience(text: str) -> str:
    text = text or ""
    if "수상" in text and not re.search(r"수상\s*(경험)?\s*(없|X)", text):
        return "수상 경험"
    if re.search(r"공모전|대회|해커톤|경진", text) and not re.search(r"(공모전|대회|해커톤|경진)[^,]*(없|처음)", text):
        return "참가 경험"
    return "처음"


def team_of(choice: str) -> tuple[str, int]:
    """프론트 팀 칩 → (팀 상태, 인원)."""
    if choice.startswith("팀원"):  # "팀원 1~2명 있음": 적게 잡아 본인 포함 2명
        return "팀 있음", 2
    if choice.startswith("팀 구성"):  # "팀 구성 완료": 인원을 몰라 3명으로 본다(가정)
        return "팀 있음", 3
    return "혼자", 1


def parse_skills(items) -> dict[str, str]:
    """[["개발", "능숙"], ...] → {"개발": "능숙"}. 목록(SKILLS) 밖의 이름은 버린다."""
    skills = {}
    for item in items or []:
        name, level = (item[0], item[1]) if isinstance(item, (list, tuple)) and len(item) == 2 else (item, DEFAULT_LEVEL)
        if name in SKILLS:
            skills[name] = level if level in LEVELS else DEFAULT_LEVEL
    return skills


def parse_age(text) -> int | None:
    try:
        age = int(str(text).strip())
    except ValueError:
        return None
    return age if 10 <= age <= 80 else None


def to_profile(form: dict, today: date) -> tuple[Profile, dict]:
    """프론트 폼 → 엔진 프로필. 두 번째 값은 화면 메모에 쓰는 원래 입력."""
    grade, major = parse_grade_major(form.get("major", ""))
    weekly = float(form.get("hours") or 12)
    weekday = round(weekly / (5 + 2 * WEEKEND_RATIO), 2)
    blocked, busy_note = parse_busy(form.get("busy", ""), today)
    team, size = team_of(form.get("team", "팀 없음"))
    exp_text = (form.get("exp") or "").strip()
    profile = Profile(
        status=form.get("status") if form.get("status") in STATUSES else "재학생",
        grade=grade, school=(form.get("school") or "").strip(), major=major,
        region=(form.get("region") or "").strip(), age=parse_age(form.get("age")),
        skills=parse_skills(form.get("skills")), experience=parse_experience(exp_text), portfolio=exp_text,
        goal=form.get("goal") if form.get("goal") in GOALS else "경험 쌓기",
        team=team, team_size=size,
        teammate_skills=[s for s in form.get("mate_skills") or [] if s in SKILLS] if team == "팀 있음" else [],
        weekday_hours=weekday, weekend_hours=round(weekday * WEEKEND_RATIO, 2), blocked=blocked, note=busy_note,
    )
    return profile, {"weekly": weekly, "busy_note": busy_note}


# ---------- 하루별 할 일 (시간표가 있으면 수업을 피해서 배치) ----------

def half(hours: float) -> float:
    return max(0.5, round(hours * 2) / 2)


def _minutes(text: str) -> int:
    hour, minute = text.split(":")
    return int(hour) * 60 + int(minute)


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


DAY_KEYS = {**{i: i for i in range(7)}, **{str(i): i for i in range(7)}, **{d: i for i, d in enumerate(DAYS)}}
CLOCK = re.compile(r"\d{1,2}:\d{2}$")


def _free_slots(day: date, classes: list[dict]) -> list[tuple[int, int]]:
    """하루 중 할 일을 넣을 수 있는 구간(수업 시간 제외). 평일은 저녁 6시 이후부터, 주말은 오전 10시부터 채운다."""
    weekend = day.weekday() >= 5
    window = (_minutes("10:00"), _minutes("22:00")) if weekend else (_minutes("09:00"), _minutes("23:00"))
    busy = sorted((_minutes(c["start"]), _minutes(c["end"])) for c in classes)
    slots, cursor = [], window[0]
    for start, end in busy:
        if start > cursor:
            slots.append((cursor, min(start, window[1])))
        cursor = max(cursor, end)
    if cursor < window[1]:
        slots.append((cursor, window[1]))
    if not weekend:  # 18시를 걸치는 구간은 나눠서 저녁 구간을 먼저 쓴다
        evening = _minutes("18:00")
        split = []
        for start, end in slots:
            split += [(start, evening), (evening, end)] if start < evening < end else [(start, end)]
        slots = sorted(split, key=lambda s: (s[0] < evening, s[0]))
    return [s for s in slots if s[1] - s[0] >= 30]


def place(day: date, items: list[tuple[str, float, str]], classes: list[dict]) -> list[list]:
    """(할 일, 시간, 단계 키) → [시간대, 할 일, 시간, 단계 키]. 들어갈 자리가 없으면 '시간 미정'."""
    todos, slots = [], _free_slots(day, classes)
    for name, hours, key in items:
        need = int(half(hours) * 60)
        for index, (start, end) in enumerate(slots):
            if end - start >= need:
                todos.append([f"{_clock(start)}–{_clock(start + need)}", name, need / 60, key, hours])
                slots[index] = (start + need, end)
                break
        else:
            todos.append(["시간 미정", name, need / 60, key, hours])
    return todos


def classes_on(day: date, classes: list[dict]) -> list[dict]:
    return sorted((c for c in classes if DAY_KEYS.get(c.get("day")) == day.weekday()
                   and CLOCK.match(str(c.get("start", ""))) and CLOCK.match(str(c.get("end", "")))),
                  key=lambda c: _minutes(c["start"]))


def daily(roadmap: Roadmap, profile: Profile, today: date, classes: list[dict], has_timetable: bool) -> list[dict]:
    by_date = {d.date: d for d in roadmap.days}
    out = []
    for offset in range(7):
        day = today + timedelta(days=offset)
        blocked = [p.reason for p in profile.blocked if p.start <= day <= p.end]
        cls = classes_on(day, classes)
        busy = sum(_minutes(c["end"]) - _minutes(c["start"]) for c in cls) / 60
        plan = by_date.get(day)
        items = [(i.step, i.hours, step_key(i.submission, i.step)) for i in plan.items] if plan else []
        out.append({
            "d": DAYS[day.weekday()], "date": f"{day.month}/{day.day}",
            "free": 0 if blocked else (max(0, round(12 - busy)) if has_timetable else 12),
            "classes": [[f"{c['start']}–{c['end']}", c.get("name", "수업")] for c in cls] + [["종일", r] for r in blocked],
            "todos": place(day, items, cls),
        })
    return out


# ---------- 응답 ----------

def requirements(strategy: Strategy) -> list[dict]:
    items = []
    for check in strategy.checks:
        status = STATUS_TO_FRONT[check.status]
        if check.item in ("참가 자격", "팀 구성") and check.status == "warn":
            status = "needs_check"
        items.append({"label": check.item, "criterion": check.detail, "status": status, "source": "code"})
    for note in strategy.notes:
        items.append({"label": "한 번 확인할 조건", "criterion": f"대부분 충족하는 조건이에요: {note}",
                      "status": "needs_check", "source": "ai"})
    return items


def lower_option(choice: Choice, profile: Profile) -> tuple[dict, float] | None:
    """한 단계 낮은 방법과 줄어드는 시간."""
    if not choice.option:
        return None
    lower = [o for o in choice.feasible if TIERS.index(o["tier"]) < TIERS.index(choice.option["tier"])]
    if not lower:
        return None
    option = min(lower, key=lambda o: (-TIERS.index(o["tier"]), option_hours(o, profile)))
    return option, round(choice.hours - option_hours(option, profile), 1)


def strategy_view(choice: Choice, profile: Profile, card: dict) -> dict:
    sub = next((s for s in card["submissions"] if s["name"] == choice.submission), {"options": []})
    alternatives = []
    for option in sub["options"]:
        if choice.option and option is choice.option:
            continue
        ok, _, missing = can_do(option, profile)
        alternatives.append({"name": option["name"], "tier": option["tier"], "hours": option_hours(option, profile),
                             "possible": ok, "why": "" if ok else f"{', '.join(missing)} 필요"})
    return {
        "submission": choice.submission, "requirement": choice.requirement,
        "method": choice.option["name"] if choice.option else None,
        "tier": choice.option["tier"] if choice.option else None,
        "how": choice.option["how"] if choice.option else "",
        "hours": choice.hours,
        "steps": [[s["name"], s["hours"]] for s in choice.option["steps"]] if choice.option else [],
        "who": choice.who, "reasons": choice.reasons, "alternatives": alternatives,
    }


def blockers(strategy: Strategy, profile: Profile) -> list[dict]:
    out = []
    for choice in strategy.choices:
        if not choice.option:
            out.append({"d": f"{choice.submission}을(를) 만들 역량이 없어요",
                        "s": " / ".join(choice.reasons[:2]) + " → 이 역량을 가진 팀원을 구하면 할 수 있어요"})
        elif stretched(choice.who):
            out.append({"d": f"{', '.join(stretched(choice.who))} 역량이 한 단계 모자라요 ({choice.submission})",
                        "s": "익히며 하도록 시간을 1.5배로 잡았어요. 막히면 한 단계 낮은 방법으로 바꿔도 돼요."})
    for check in strategy.checks:
        if check.item == "시간" and check.status != "ok":
            out.append({"d": "시간이 빠듯해요" if check.status == "warn" else "시간이 부족해요", "s": check.detail})
        if check.item == "팀 구성" and check.status != "ok":
            out.append({"d": "팀 구성을 확인해야 해요", "s": check.detail})
    for question in strategy.questions:
        out.append({"d": "참가 자격을 확인해야 해요", "s": question})
    return out or [{"d": "크게 막는 요소가 없어요", "s": "계획대로 진행하고, 밀리면 하루별 할 일에서 체크한 뒤 다시 짜기를 눌러요."}]


def fallbacks(strategy: Strategy, profile: Profile) -> list[str]:
    out = []
    for choice in strategy.choices:
        lower = lower_option(choice, profile)
        if lower and lower[1] > 0:
            out.append(f"{choice.submission}을(를) '{lower[0]['name']}'로 낮추면 {lower[1]:g}시간 줄어요")
    if strategy.buffer_days:
        out.append(f"마감 전 {len(strategy.buffer_days)}일은 여유 기간으로 비워 뒀어요. 밀린 일은 여기서 만회해요")
    return out or ["지금 계획이 가장 가벼운 방법이에요"]


def this_week(roadmap: Roadmap, today: date) -> dict:
    totals: dict[str, float] = {}
    for day in roadmap.days:
        if today <= day.date < today + timedelta(days=7):
            for item in day.items:
                totals[item.step] = totals.get(item.step, 0.0) + item.hours
    hours = sum(totals.values())
    upcoming = [(name, when) for name, when in roadmap.milestones if when >= today]
    goal = f"다음 중간 목표: {upcoming[0][1].month}/{upcoming[0][1].day}까지 {upcoming[0][0]} 완성" if upcoming else ""
    title = " → ".join(totals) if totals else "이번 주는 쉬어가요"
    return {"title": title, "hours": half(hours) if hours else 0, "note": goal or "마감 전 여유 기간이에요"}


def week_plan(roadmap: Roadmap, today: date) -> list[dict]:
    out = []
    for number, week in enumerate(weeks(roadmap, today), 1):
        tasks = [[name, round(hours, 1)] for name, hours in week["tasks"].items()]
        if week["milestones"]:
            label = f"★ {', '.join(week['milestones'])} 완성"
        elif tasks:
            label = " · ".join(name for name, _ in tasks[:2])
        else:
            label = "여유 기간" if week["buffer"] else "못 쓰는 기간"
        out.append({"w": number, "label": label, "rest": not tasks, "tasks": tasks})
    return out


def compare(on: Strategy, off: Strategy, profile: Profile, assumed: dict) -> dict:
    """같은 공고 카드, 같은 엔진. 다른 건 프로필(개인화)뿐이다. 차이는 코드가 찾는다."""
    generic = [f"{c.submission}: {c.option['name']} ({c.hours:g}h)" for c in off.choices if c.option]
    problems = []
    for c in off.choices:
        if c.option:
            ok, _, missing = can_do(c.option, profile)
            if not ok:
                problems.append(f"'{c.option['name']}'은 {', '.join(missing)}이 필요해 나는 못 해요")
    if off.need_hours > on.work_capacity > 0:
        problems.append(f"{off.need_hours:g}시간이 필요한데 내 시간은 {on.work_capacity:g}시간이에요")
    blocked = [p for p in profile.blocked if on.work_days and p.start <= on.work_days[-1]]
    if blocked:
        problems.append(f"{', '.join(p.reason for p in blocked)} 기간을 모르고 짠 계획이에요")
    mine = [[c.submission, f"{c.option['name']} ({c.hours:g}h)" if c.option else "할 수 없음 → 팀원 필요"]
            for c in on.choices]
    skills = ", ".join(f"{k} {v}" for k, v in profile.skills.items()) or "역량 선택 없음"
    team = f"팀 {profile.team_size}명" + (f"(팀원 {', '.join(profile.teammate_skills)})" if profile.teammate_skills else "") \
        if profile.team == "팀 있음" else "혼자"
    return {"generic": generic or ["제출물 없음"],
            "genericNote": "내 기준으로 보면: " + "; ".join(problems) if problems else "이 공고는 내 프로필과 크게 부딪히지 않아요",
            "mine": mine,
            "mineNote": f"주 {assumed['weekly']:g}시간 · {skills} · {team} · {profile.goal} 반영 (코드 계산)"}


def deadline_iso(card: dict) -> str | None:
    if not card.get("deadline"):
        return None
    clock = card.get("deadline_time") or "23:59"
    try:
        return datetime.fromisoformat(f"{card['deadline']}T{clock}:00+09:00").isoformat()
    except ValueError:
        return f"{card['deadline']}T23:59:00+09:00"


def to_response(card: dict, strategy: Strategy, off: Strategy, roadmap: Roadmap, profile: Profile, today: date, *,
                classes: list[dict], has_timetable: bool, assumed: dict, trust: list[dict],
                analysis_id: str = "", answers: dict[str, bool] | None = None) -> dict:
    lightest = sum(min((option_hours(o, profile) for o in c.feasible), default=0) for c in strategy.choices)
    reasons = [c.detail for c in strategy.checks if c.status != "ok"][:3] or ["모든 조건을 충족해요"]
    questions = [{"q": q, "answer": None} for q in strategy.questions]
    questions += [{"q": q, "answer": a} for q, a in (answers or {}).items() if q not in strategy.questions]
    return {
        "meta": {"is_demo": False, "timetable": has_timetable, "engine": "v3", "verdict": strategy.verdict,
                 "issues": card.get("issues", [])},
        "posting": {"title": card.get("title") or "제목 없는 공고", "organizer": card.get("organizer") or "주최 미상",
                    "deadline": deadline_iso(card)},
        "requirements": requirements(strategy),
        "readiness": {"label": READINESS[strategy.verdict], "reasons": reasons, "disclaimer": "준비도는 합격 확률이 아니에요."},
        "time": {"min": round(lightest + strategy.final_hours), "max": round(strategy.need_hours),
                 "available": round(strategy.work_capacity)},
        "strategy": [strategy_view(c, profile, card) for c in strategy.choices],
        "criteria": card.get("criteria", []),
        "thisWeek": this_week(roadmap, today),
        "blockers": blockers(strategy, profile),
        "weeks": week_plan(roadmap, today),
        "fallbacks": fallbacks(strategy, profile),
        "compare": compare(strategy, off, profile, assumed),
        "trust": trust,
        "daily": daily(roadmap, profile, today, classes, has_timetable),
        "session": {"id": analysis_id, "questions": questions,
                    "progress": {"hours": round(sum(roadmap.done.values()), 1), "tasks": sorted(roadmap.done)}},
    }


__all__ = ["to_profile", "to_response", "FINAL_NAME"]
