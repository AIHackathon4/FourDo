"""프론트 입력 ↔ PODO 엔진 ↔ 프론트 응답(JSON) 변환. LLM을 부르지 않는 순수 코드라 테스트할 수 있다.

흐름: 프론트 폼 → to_profile()로 엔진 Profile → feasibility.judge()(LLM은 추출·작업 분해·조언 문장만,
자격·시간·판정·일정은 코드) → to_response()로 프론트가 그리는 JSON.
"""

import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from aihack4 import baselines as bl
from aihack4.feasibility import GOALS, LEVELS, STATUSES, Feasibility, Period, Profile

DAYS = "월화수목금토일"

# 프론트 역량 칩 → 엔진에 넘길 역량 이름. 작업 분해 LLM이 이 이름 중에서 맞는 역량을 고른다.
SKILL_NAMES = {"기획": "기획(아이디어·기획서)", "디자인": "디자인", "개발": "개발(프로그래밍)",
               "영상": "영상 제작·편집", "발표": "발표·PPT", "글쓰기": "글쓰기"}
DEFAULT_LEVEL = "가능"  # 수준 없이 이름만 온 경우(예전 프론트)

# 주당 시간 → 평일·주말 하루 시간. 주말 하루를 평일 하루의 2배로 본다(가정): 평일 5h + 주말 2×2h = 9h 단위.
WEEKEND_RATIO = 2

STATUS_TO_FRONT = {"ok": "met", "warn": "caution", "fail": "lacking"}
READINESS = {
    "참여 가능": "참여 가능 · 지금 시작해도 충분해요",
    "준비하면 가능": "준비하면 가능 · 계획대로 하면 낼 수 있어요",
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
    날짜가 있는 일정만 기간으로 만들고(코드), 반복 일정처럼 날짜가 없는 내용은 메모로 넘긴다."""
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
    """프론트 팀 칩 → (팀 상태, 인원). 팀원 역량은 입력받지 않는다."""
    if choice.startswith("팀원"):  # "팀원 1~2명 있음": 적게 잡아 본인 포함 2명
        return "팀 있음", 2
    if choice.startswith("팀 구성"):  # "팀 구성 완료": 인원을 몰라 3명으로 본다(가정)
        return "팀 있음", 3
    return "혼자", 1


def parse_skills(items) -> dict[str, str]:
    """[["개발", "능숙"], ...] 또는 ["개발", ...] → {"개발(프로그래밍)": "능숙", ...}"""
    skills = {}
    for item in items or []:
        name, level = (item[0], item[1]) if isinstance(item, (list, tuple)) and len(item) == 2 else (item, DEFAULT_LEVEL)
        skills[SKILL_NAMES.get(name, name)] = level if level in LEVELS else DEFAULT_LEVEL
    return skills


def parse_age(text) -> int | None:
    try:
        age = int(str(text).strip())
    except ValueError:
        return None
    return age if 10 <= age <= 80 else None


def to_profile(form: dict, today: date) -> tuple[Profile, dict]:
    """프론트 폼 → 엔진 프로필. 두 번째 값은 가정한 내용(응답의 compare 메모에 쓴다)."""
    grade, major = parse_grade_major(form.get("major", ""))
    weekly = float(form.get("hours") or 12)
    weekday = round(weekly / (5 + 2 * WEEKEND_RATIO), 2)
    blocked, busy_note = parse_busy(form.get("busy", ""), today)
    team, size = team_of(form.get("team", "팀 없음"))
    exp_text = (form.get("exp") or "").strip()
    status = form.get("status") if form.get("status") in STATUSES else "재학생"
    profile = Profile(
        status=status, grade=grade, school=(form.get("school") or "").strip(), major=major,
        region=(form.get("region") or "").strip(), age=parse_age(form.get("age")),
        skills=parse_skills(form.get("skills")), experience=parse_experience(exp_text), portfolio=exp_text,
        goal=form.get("goal") if form.get("goal") in GOALS else "경험 쌓기",
        team=team, team_size=size, weekday_hours=weekday, weekend_hours=round(weekday * WEEKEND_RATIO, 2),
        blocked=blocked, note=busy_note,
    )
    return profile, {"weekly": weekly, "busy_note": busy_note}


# ---------- 일정 쪼개기 ----------

def task_days(result: Feasibility, profile: Profile) -> dict[date, list[tuple[str, float]]]:
    """엔진이 정한 작업 기간 안에서, 쓸 수 있는 시간에 비례해 날짜별 작업 시간을 나눈다."""
    out: dict[date, list[tuple[str, float]]] = defaultdict(list)
    for task in bl.podo_tasks(result):
        for day, hours in (bl.spread(task, profile) or {}).items():
            if hours >= 0.05:
                out[day].append((task["name"], hours))
    return out


def half(hours: float) -> float:
    return max(0.5, round(hours * 2) / 2)


def week_plan(result: Feasibility, profile: Profile, today: date, deadline: date | None) -> list[dict]:
    per_day = task_days(result, profile)
    if not per_day:
        return []
    last = max(per_day)
    if deadline:
        last = max(last, deadline - timedelta(days=1))
    weeks = []
    start, number = today, 1
    while start <= last:
        end = start + timedelta(days=6)
        totals: dict[str, float] = defaultdict(float)
        for day, items in per_day.items():
            if start <= day <= end:
                for name, hours in items:
                    totals[name] += hours
        tasks = [[name, half(hours)] for name, hours in totals.items()]
        reasons = sorted({p.reason for p in profile.blocked if p.start <= end and p.end >= start})
        if tasks:
            label = " · ".join(name for name, _ in tasks[:2])
        else:
            label = reasons[0] if reasons else "여유 주간"
        weeks.append({"w": number, "label": label, "rest": not tasks, "tasks": tasks})
        start, number = end + timedelta(days=1), number + 1
    return weeks


# ---------- 하루별 할 일 (시간표가 있으면 수업을 피해서 배치) ----------

def _minutes(text: str) -> int:
    hour, minute = text.split(":")
    return int(hour) * 60 + int(minute)


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


DAY_KEYS = {**{i: i for i in range(7)}, **{str(i): i for i in range(7)}, **{d: i for i, d in enumerate(DAYS)}}


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


def place(day: date, items: list[tuple[str, float]], classes: list[dict]) -> list[list]:
    todos, slots = [], _free_slots(day, classes)
    for name, hours in items:
        need = int(half(hours) * 60)
        for index, (start, end) in enumerate(slots):
            if end - start >= need:
                todos.append([f"{_clock(start)}–{_clock(start + need)}", name, need / 60])
                slots[index] = (start + need, end)
                break
        else:
            todos.append(["시간 미정", name, need / 60])
    return todos


def daily(result: Feasibility, profile: Profile, today: date, classes: list[dict], has_timetable: bool) -> list[dict]:
    per_day = task_days(result, profile)
    out = []
    for offset in range(7):
        day = today + timedelta(days=offset)
        blocked = [p.reason for p in profile.blocked if p.start <= day <= p.end]
        cls = sorted((c for c in classes if DAY_KEYS.get(c.get("day")) == day.weekday()
                      and re.match(r"\d{1,2}:\d{2}$", str(c.get("start", ""))) and re.match(r"\d{1,2}:\d{2}$", str(c.get("end", "")))),
                     key=lambda c: _minutes(c["start"]))
        busy = sum(_minutes(c["end"]) - _minutes(c["start"]) for c in cls) / 60
        free = 0 if blocked else (max(0, round(12 - busy)) if has_timetable else 12)
        out.append({
            "d": DAYS[day.weekday()], "date": f"{day.month}/{day.day}", "free": free,
            "classes": [[f"{c['start']}–{c['end']}", c.get("name", "수업")] for c in cls] + [["종일", r] for r in blocked],
            "todos": place(day, per_day.get(day, []), cls),
        })
    return out


# ---------- 응답 ----------

def requirements(result: Feasibility) -> list[dict]:
    items = []
    for check in result.checks:
        status = STATUS_TO_FRONT[check.status]
        if check.item in ("참가 자격", "팀 구성") and check.status == "warn":
            status = "needs_check"
        items.append({"label": check.item, "criterion": check.detail, "status": status, "source": "code"})
    for question in result.questions:
        items.append({"label": "확인할 자격 조건", "criterion": question, "status": "needs_check", "source": "code"})
    return items


def blockers(result: Feasibility) -> list[dict]:
    out = []
    for gap in result.gaps:
        if gap.startswith("참가 자격 확인:"):
            out.append({"d": "참가 자격을 확인해야 해요", "s": gap.split(":", 1)[1].strip()})
        elif " — " in gap:  # "영상 제작·편집(가능) — 영상 촬영: 팀원으로 보완하거나 배우기"
            skill, rest = gap.split(" — ", 1)
            task, _, way = rest.partition(": ")
            out.append({"d": f"{skill} 역량이 부족해요 ({task})", "s": way or "시간을 더 잡았어요"})
        else:
            out.append({"d": "팀 인원이 부족해요", "s": gap})
    time = next((c for c in result.checks if c.item == "시간" and c.status != "ok"), None)
    if time:
        out.append({"d": "시간이 빠듯해요" if time.status == "warn" else "시간이 부족해요", "s": time.detail})
    return out


def this_week(result: Feasibility, profile: Profile, today: date) -> dict:
    per_day = task_days(result, profile)
    totals: dict[str, float] = defaultdict(float)
    for offset in range(7):
        for name, hours in per_day.get(today + timedelta(days=offset), []):
            totals[name] += hours
    hours = sum(totals.values())
    title = result.this_week[0] if result.this_week else (next(iter(totals), "공고 다시 읽고 계획 세우기"))
    note = " · ".join(result.this_week[1:]) or ("이번 주 일정: " + ", ".join(totals) if totals else "이번 주는 쉬어가요")
    return {"title": title, "hours": half(hours) if hours else 0, "note": note}


def compare(result: Feasibility, profile: Profile, today: date, deadline: date | None,
            generic: dict | None, weeks: list[dict], assumed: dict) -> dict:
    """왼쪽: 프로필 없이 같은 모델이 짠 실제 일반 계획(조건 A). 오른쪽: 엔진 계획.
    일반 계획이 내 일정과 얼마나 부딪히는지는 코드(check_plan)로 센다."""
    if generic and generic.get("tasks"):
        tasks = generic["tasks"]
        lines = [f"{t['name']} ({t['start'][5:]}~{t['end'][5:]}, {t['hours']:g}시간)" for t in tasks[:3]]
        note = "내 일정·역량·팀이 반영되지 않았어요"
        if deadline:
            check = bl.check_plan(tasks, profile, today, deadline)
            problems = []
            if check["blocked_hours"] > 0.05:
                problems.append(f"바쁜 기간에 {check['blocked_hours']:g}시간 배치")
            if check["overloaded_weeks"]:
                problems.append(f"내 주당 시간을 넘는 주 {check['overloaded_weeks']}개")
            if problems:
                note = "내 시간으로 확인하면: " + ", ".join(problems)
    else:
        lines, note = ["비교용 일반 계획을 만들지 못했어요"], "잠시 후 다시 분석하면 볼 수 있어요"

    mine = []
    for week in weeks[:3]:
        text = "쉬어가요(" + week["label"] + ")" if week["rest"] else \
            " · ".join(f"{name} {hours:g}h" for name, hours in week["tasks"][:2])
        mine.append([f"{week['w']}주차", text])
    gaps = [g for g in result.gaps if " — " in g]
    if gaps:
        mine.append(["부족한 역량", gaps[0].split(" — ")[0] + " 부족 → 그만큼 시간을 더 잡았어요"])
    team = f"팀 {profile.team_size}명" if profile.team == "팀 있음" else "혼자"
    skills = ", ".join(f"{name.split('(')[0]} {level}" for name, level in profile.skills.items()) or "역량 선택 없음"
    grade = f"{profile.grade}학년 · " if profile.grade else ""
    return {"generic": lines, "genericNote": note, "mine": mine,
            "mineNote": f"{grade}주 {assumed['weekly']:g}시간 · {skills} · {team} 반영 (코드 계산)"}


def deadline_iso(notice: dict) -> str | None:
    if not notice.get("deadline"):
        return None
    clock = notice.get("deadline_time") or "23:59"
    try:
        return datetime.fromisoformat(f"{notice['deadline']}T{clock}:00+09:00").isoformat()
    except ValueError:
        return f"{notice['deadline']}T23:59:00+09:00"


def session(analysis_id: str, result: Feasibility, answers: dict[str, bool], progress: dict[str, float]) -> dict:
    """재판정·다시 짜기에 쓰는 정보. 아직 답하지 않은 자격 질문과 이미 답한 질문을 함께 보낸다."""
    questions = [{"q": q, "answer": None} for q in result.questions]
    questions += [{"q": q, "answer": a} for q, a in answers.items() if q not in result.questions]
    return {"id": analysis_id, "questions": questions,
            "progress": {"hours": round(sum(progress.values()), 1), "tasks": sorted(progress)}}


def to_response(notice: dict, result: Feasibility, profile: Profile, today: date, *, generic: dict | None,
                classes: list[dict], has_timetable: bool, assumed: dict, trust: list[dict], warnings: list[str],
                analysis_id: str = "", answers: dict[str, bool] | None = None,
                progress: dict[str, float] | None = None) -> dict:
    deadline = date.fromisoformat(notice["deadline"]) if notice.get("deadline") else None
    weeks = week_plan(result, profile, today, deadline)
    reasons = [result.summary] if result.summary else []
    reasons += [c.detail for c in result.checks if c.status != "ok"][: max(0, 3 - len(reasons))]
    return {
        "meta": {"is_demo": False, "timetable": has_timetable, "engine": "podo", "warnings": warnings,
                 "verdict": result.verdict},
        "posting": {"title": notice.get("title") or "제목 없는 공고", "organizer": notice.get("organizer") or "주최 미상",
                    "deadline": deadline_iso(notice)},
        "requirements": requirements(result),
        "readiness": {"label": READINESS[result.verdict], "reasons": reasons or ["모든 조건을 충족해요"],
                      "disclaimer": "준비도는 합격 확률이 아니에요."},
        "time": {"min": round(result.need_min), "max": round(result.target_hours or result.need_max),
                 "available": round(result.available_hours)},
        "thisWeek": this_week(result, profile, today),
        "blockers": blockers(result),
        "weeks": weeks,
        "fallbacks": result.tips or ["마감 전에 할 수 있는 만큼 범위를 줄여요"],
        "compare": compare(result, profile, today, deadline, generic, weeks, assumed),
        "trust": trust,
        "daily": daily(result, profile, today, classes, has_timetable),
        "session": session(analysis_id, result, answers or {}, progress or {}),
    }
