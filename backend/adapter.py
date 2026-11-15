"""프론트 입력 ↔ 설계 v3 엔진(card → strategy → roadmap) ↔ 프론트 응답(JSON). LLM을 부르지 않는 순수 코드.

흐름: 프론트 폼 → to_profile() → strategy.choose()(제출물별 방법 고르기·판정) → roadmap.build()(날짜 배치)
→ to_response()로 화면이 그리는 JSON. 설계: docs/백엔드_설계_v3.md
"""

import re
from datetime import date, datetime, timedelta

from aihack4.card import SKILLS
from aihack4.feasibility import LEVELS, STATUSES, Period, Profile
from aihack4.roadmap import FINAL_NAME, Roadmap, step_key, weeks
from aihack4.strategy import (DEFAULT_GOAL, GOAL_TIER, TIER_LABEL, Choice, Strategy, can_do, josa, option_hours,
                              quoted, stretched)

DAYS = "월화수목금토일"
DEFAULT_LEVEL = "가능"  # 수준 없이 이름만 온 경우
WEEKEND_RATIO = 2  # 주당 시간 → 평일·주말 하루 시간. 주말 하루 = 평일 하루의 2배(가정)
MAX_DAILY = 180  # 하루별 할 일을 보여 주는 최대 일수

STATUS_TO_FRONT = {"ok": "met", "warn": "caution", "fail": "lacking"}
READINESS = {
    "참여 가능": "지금 시작하면 여유 있게 제출할 수 있어요.",
    "준비하면 가능": "아래 방법대로 준비하면 제출할 수 있어요.",
    "확인 필요": "참가 조건부터 확인해 주세요.",
    "어려움": "이번에는 제출까지 가기 어려워요.",
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


def to_profile(form: dict, today: date, classes: list[dict] | None = None) -> tuple[Profile, dict]:
    """프론트 폼 → 엔진 프로필. 두 번째 값은 화면 메모에 쓰는 원래 입력.
    classes(시간표에서 읽은 수업)가 있으면 요일별로 수업을 뺀 빈 시간을 작업 시간의 상한으로 둔다."""
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
        goal=form.get("goal") if form.get("goal") in GOAL_TIER else DEFAULT_GOAL,
        team=team, team_size=size,
        teammate_skills=[s for s in form.get("mate_skills") or [] if s in SKILLS] if team == "팀 있음" else [],
        weekday_hours=weekday, weekend_hours=round(weekday * WEEKEND_RATIO, 2), blocked=blocked, note=busy_note,
        daily_cap=timetable_caps(classes) if classes is not None else {},
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
    """(할 일, 시간, 단계 키) → [시간대, 할 일, 화면에 보일 시간, 단계 키, 실제 시간].
    빈 시간 하나에 다 안 들어가면 여러 빈 시간에 나눠 넣고, 그래도 남으면 '시간 미정'."""
    todos, slots = [], _free_slots(day, classes)
    for name, hours, key in items:
        need = int(half(hours) * 60)
        whole = next((i for i, (start, end) in enumerate(slots) if end - start >= need), None)
        parts = []  # (빈 시간 번호, 분)
        if whole is not None:
            parts.append((whole, need))
        else:
            left = need
            for i, (start, end) in enumerate(slots):
                if left <= 0:
                    break
                if end - start >= 30:
                    parts.append((i, min(left, end - start)))
                    left -= parts[-1][1]
        placed = 0
        for i, minutes in parts:
            start, end = slots[i]
            todos.append([f"{_clock(start)}–{_clock(start + minutes)}", name, minutes / 60, key,
                          round(hours * minutes / need, 2)])
            slots[i] = (start + minutes, end)
            placed += minutes
        if placed < need:
            todos.append(["시간 미정", name, (need - placed) / 60, key, round(hours * (need - placed) / need, 2)])
    return todos


def clean_classes(classes) -> list[dict]:
    """시간표에서 읽은 수업 중 요일·시각이 온전한 것만 남긴다."""
    out = []
    for c in classes or []:
        if not isinstance(c, dict):
            continue
        day = DAY_KEYS.get(c.get("day")) if isinstance(c.get("day"), (int, str)) else None
        start, end = str(c.get("start", "")), str(c.get("end", ""))
        if day is not None and CLOCK.match(start) and CLOCK.match(end) and _minutes(start) < _minutes(end):
            out.append({"day": day, "start": start, "end": end, "name": str(c.get("name") or "수업")})
    return out


def classes_on(day: date, classes: list[dict]) -> list[dict]:
    return sorted((c for c in clean_classes(classes) if c["day"] == day.weekday()), key=lambda c: _minutes(c["start"]))


def timetable_caps(classes: list[dict]) -> dict[int, float]:
    """요일(월=0)별로 수업을 뺀 빈 시간. 하루 작업 시간은 이 값을 넘지 않는다."""
    monday = date(2026, 1, 5)
    return {n: round(sum(end - start for start, end in
                         _free_slots(monday + timedelta(days=n), classes_on(monday + timedelta(days=n), classes))) / 60, 1)
            for n in range(7)}


def daily(roadmap: Roadmap, profile: Profile, today: date, classes: list[dict], deadline: date | None,
          spent: dict[date, float] | None = None) -> list[dict]:
    """오늘부터 마감일까지 하루씩. 마감을 모르거나 지났으면 7일. spent: 날짜별로 이미 작업한 시간."""
    spent = spent or {}
    by_date = {d.date: d for d in roadmap.days}
    goals: dict[date, list[str]] = {}
    for name, when in roadmap.milestones:
        goals.setdefault(when, []).append(name)
    last = deadline if deadline and deadline >= today else today + timedelta(days=6)
    out = []
    for offset in range(min((last - today).days + 1, MAX_DAILY)):
        day = today + timedelta(days=offset)
        blocked = [p.reason for p in profile.blocked if p.start <= day <= p.end]
        cls = classes_on(day, classes)
        plan = by_date.get(day)
        items = [(i.step, i.hours, step_key(i.submission, i.step)) for i in plan.items] if plan else []
        notes = [f"★ {name} 완성 목표일" for name in goals.get(day, [])]
        if plan and plan.buffer:
            notes.append("마감 전 여유 기간")
        if day == deadline:
            notes.append("마감일")
        out.append({
            "d": DAYS[day.weekday()], "date": f"{day.month}/{day.day}",
            "free": 0 if blocked else round(max(0.0, profile.hours_on(day) - spent.get(day, 0.0)), 1),
            "done": round(spent.get(day, 0.0), 1),
            "classes": [[f"{c['start']}–{c['end']}", c["name"]] for c in cls] + [["종일", r] for r in blocked],
            "todos": place(day, items, cls), "note": " · ".join(notes),
        })
    return out


# ---------- 응답 ----------

def requirements(strategy: Strategy) -> list[dict]:
    items = []
    for check in strategy.checks:
        status = STATUS_TO_FRONT[check.status]
        if check.item in ("참가 자격", "팀 구성") and check.status == "warn":
            status = "needs_check"
        items.append({"label": check.item, "criterion": check.detail, "status": status})
    for note in strategy.notes:
        items.append({"label": "확인할 조건", "criterion": f"대부분 해당하는 조건이에요: {note}", "status": "needs_check"})
    return items


def strategy_view(choice: Choice, profile: Profile, card: dict) -> dict:
    """제출물 하나에 추천한 방법과 이유."""
    picked = choice.option
    scale = choice.hours / picked["hours"] if picked and picked["hours"] else 1.0  # 단계 시간도 보정한 시간으로 보여 준다
    # 누가 맡는지는 팀일 때만 보여 준다. '보완'은 익히면서 한다는 뜻
    who = {skill: person.replace(" 보완", "(익히면서)") for skill, person in choice.who.items()} \
        if profile.team == "팀 있음" else {}
    return {
        "submission": choice.submission, "requirement": choice.requirement,
        "method": picked["name"] if picked else None,
        "tier": TIER_LABEL[picked["tier"]] if picked else None,
        "how": picked["how"] if picked else "",
        "hours": choice.hours,
        "steps": [[s["name"], round(s["hours"] * scale, 1)] for s in picked["steps"]] if picked else [],
        "who": who, "reasons": choice.reasons, "substitute": choice.substitute,
    }


def blockers(strategy: Strategy, profile: Profile) -> list[dict]:
    out = []
    for choice in strategy.choices:
        if not choice.option:
            out.append({"d": f"{josa(choice.submission, '을를')} 만들 역량이 부족해요",
                        "s": f"{choice.reasons[0]}. 이 역량이 있는 팀원과 함께하면 할 수 있어요."})
        elif stretched(choice.who):
            out.append({"d": f"{choice.submission}에 필요한 {', '.join(stretched(choice.who))} 역량이 조금 모자라요",
                        "s": "익히는 시간까지 넉넉히 잡았어요. 막히면 시간을 더 확보하거나 팀원과 나눠 보세요."})
    for check in strategy.checks:
        if check.item == "시간" and check.status != "ok":
            out.append({"d": "시간이 빠듯해요" if check.status == "warn" else "시간이 부족해요", "s": check.detail})
        if check.item == "팀 구성" and check.status != "ok":
            out.append({"d": "팀 구성을 확인해야 해요", "s": check.detail})
    for question in strategy.questions:
        out.append({"d": "참가 자격을 확인해야 해요", "s": question})
    return out or [{"d": "크게 막는 요소가 없어요", "s": "계획대로 진행하면 돼요. 끝낸 일을 체크하면 남은 일정을 다시 짜 드려요."}]


def today_card(roadmap: Roadmap, today: date) -> dict:
    """오늘(오늘 할 일이 없으면 다음 작업일)에 할 일."""
    upcoming = [(name, when) for name, when in roadmap.milestones if when >= today]
    goal = f"다음 중간 목표: {upcoming[0][1].month}/{upcoming[0][1].day}까지 {upcoming[0][0]} 완성" if upcoming else ""
    day = next((d for d in roadmap.days if d.date >= today and d.items), None)
    if day is None:
        return {"when": "오늘", "title": "남은 작업이 없어요", "hours": 0, "note": goal}
    when = "오늘" if day.date == today else f"다음 작업일 {day.date.month}/{day.date.day}({DAYS[day.date.weekday()]})"
    steps = list(dict.fromkeys(i.step for i in day.items))
    return {"when": when, "title": " → ".join(steps), "hours": half(day.hours), "note": goal}


def week_plan(roadmap: Roadmap, today: date, profile: Profile) -> list[dict]:
    """주차별 작업. 이름은 날짜 범위. 작업이 없는 주는 이유(시험 등)를 붙이고, 중간 목표는 goal에 따로 적는다."""
    out = []
    for number, week in enumerate(weeks(roadmap, today), 1):
        tasks = [[name, round(hours, 1)] for name, hours in week["tasks"].items()]
        label = f"{week['start'].month}/{week['start'].day}~{week['end'].month}/{week['end'].day}"
        if not tasks:
            busy = list(dict.fromkeys(p.reason for p in profile.blocked if p.start <= week["end"] and p.end >= week["start"]))
            label += f" · {', '.join(busy)}" if busy else ""
        goal = f"★ {', '.join(week['milestones'])} 완성" if week["milestones"] else ""
        out.append({"w": number, "label": label, "rest": not tasks, "tasks": tasks, "goal": goal})
    return out


def compare(on: Strategy, off: Strategy, profile: Profile, assumed: dict, timetable: str) -> dict:
    """같은 공고 카드, 같은 계산. 다른 건 프로필(개인화)뿐이다. 양쪽을 같은 항목(방법·시간·일정)으로 보여 주고 차이는 코드가 찾는다."""
    methods = lambda s: " / ".join(f"{c.submission}: {c.option['name']}" if c.option  # noqa: E731
                                   else f"{c.submission}: 할 수 있는 방법 없음" for c in s.choices) or "제출물 없음"
    days = f"마감까지 {off.days_left}일 동안" if off.days_left is not None and off.days_left >= 0 else "남은 기간에"
    work = lambda s: round(sum(c.hours for c in s.choices), 1)  # noqa: E731  결과 화면의 '시간'과 같은 기준(제출물 작업 시간)
    generic = [["방법", methods(off)],
               ["시간", f"{work(off):g}시간 필요 (내가 쓸 수 있는 시간은 모름)"],
               ["일정", f"{days} 고르게 (시험·수업 일정은 모름)"]]
    problems = []
    for c in off.choices:
        if c.option:
            ok, _, missing = can_do(c.option, profile)
            if not ok:
                problems.append(f"{quoted(c.option['name'], '은는')} {', '.join(missing)} 수준이 필요해서 지금은 하기 어려워요")
    if work(off) > on.work_capacity > 0:
        problems.append(f"{work(off):g}시간이 필요한데 내가 쓸 수 있는 시간은 {on.work_capacity:g}시간이에요")
    blocked = [p for p in profile.blocked if on.work_days and p.start <= on.work_days[-1]]
    if blocked:
        problems.append(f"{', '.join(p.reason for p in blocked)} 기간을 피하지 않아요")
    same = [c.option["name"] if c.option else None for c in on.choices] == \
           [c.option["name"] if c.option else None for c in off.choices]
    schedule = [f"작업일 {len(on.work_days)}일"] if on.work_days else ["작업할 수 있는 날 없음"]
    schedule += [f"{p.reason} 기간 비움" for p in blocked]
    if timetable == "ok":
        schedule.append("수업 시간 피함")
    if on.buffer_days:
        schedule.append(f"마감 전 {len(on.buffer_days)}일 여유")
    mine = [["방법", methods(on)],
            ["시간", f"{work(on):g}시간 필요 / {on.work_capacity:g}시간 쓸 수 있음"],
            ["일정", " · ".join(schedule)],
            ["결과", on.verdict]]
    skills = ", ".join(f"{k} {v}" for k, v in profile.skills.items()) or "역량 선택 없음"
    team = f"팀 {profile.team_size}명" + (f"(팀원 {', '.join(profile.teammate_skills)})" if profile.teammate_skills else "") \
        if profile.team == "팀 있음" else "혼자"
    note = "내 상황과 맞지 않는 점: " + " / ".join(problems) if problems else \
        "방법은 같지만, 시간과 일정은 내 상황에 맞춰 달라졌어요" if same else "내 상황과 크게 부딪히는 점은 없어요"
    return {"generic": generic, "genericNote": note, "mine": mine,
            "mineNote": f"반영한 내 정보: 주 {assumed['weekly']:g}시간 · {skills} · {team} · 목표 {profile.goal}"}


def deadline_iso(card: dict) -> str | None:
    if not card.get("deadline"):
        return None
    clock = card.get("deadline_time") or "23:59"
    try:
        return datetime.fromisoformat(f"{card['deadline']}T{clock}:00+09:00").isoformat()
    except ValueError:
        return f"{card['deadline']}T23:59:00+09:00"


def to_response(card: dict, strategy: Strategy, off: Strategy, roadmap: Roadmap, profile: Profile, today: date, *,
                classes: list[dict], timetable: str, assumed: dict, trust: list[dict],
                analysis_id: str = "", answers: dict[str, bool] | None = None,
                spent: dict[date, float] | None = None,
                planner: str = "code") -> dict:
    """timetable: 'ok'(시간표 반영) / 'failed'(올렸지만 읽지 못함) / 'none'(올리지 않음)."""
    deadline = date.fromisoformat(card["deadline"]) if card.get("deadline") else None
    reasons = [c.detail for c in strategy.checks if c.status != "ok"][:3] or ["참가 조건, 시간, 역량 모두 문제없어요."]
    questions = [{"q": q, "answer": None} for q in strategy.questions]
    questions += [{"q": q, "answer": a} for q, a in (answers or {}).items() if q not in strategy.questions]
    return {
        "meta": {"timetable": timetable, "engine": "v3", "planner": planner, "verdict": strategy.verdict, "issues": card.get("issues", []),
                 "team": profile.people()},
        "posting": {"title": card.get("title") or "제목 없는 공고", "organizer": card.get("organizer") or "주최 미상",
                    "deadline": deadline_iso(card)},
        "requirements": requirements(strategy),
        "readiness": {"label": strategy.verdict, "summary": READINESS[strategy.verdict], "reasons": reasons,
                      "disclaimer": "준비도는 합격 확률이 아니에요."},
        # 제출물 작업 시간과 여유 기간 전까지 쓸 수 있는 시간('시간' 판정과 같은 숫자). 최종 점검·제출은 여유 기간에 따로 한다
        "time": {"need": round(sum(c.hours for c in strategy.choices), 1), "available": round(strategy.work_capacity, 1)},
        "strategy": [strategy_view(c, profile, card) for c in strategy.choices],
        "criteria": card.get("criteria", []),
        "today": {"when": "오늘", "title": "이미 마감된 공고예요", "hours": 0, "note": ""}
        if strategy.days_left is not None and strategy.days_left < 0 else today_card(roadmap, today),
        "blockers": blockers(strategy, profile),
        "weeks": week_plan(roadmap, today, profile),
        "compare": compare(strategy, off, profile, assumed, timetable),
        "trust": trust,
        "daily": daily(roadmap, profile, today, classes, deadline, spent),
        "session": {"id": analysis_id, "questions": questions,
                    "progress": {"hours": round(sum(roadmap.done.values()), 1), "tasks": sorted(roadmap.done)}},
    }


__all__ = ["to_profile", "to_response", "FINAL_NAME"]
