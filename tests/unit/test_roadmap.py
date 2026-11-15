"""로드맵 배치: 고르게, 최소 1시간, 못 쓰는 날 건너뛰기, 여유 기간, 중간 목표, 진행 반영."""

from datetime import date

from aihack4.feasibility import Period, Profile
from aihack4.roadmap import FINAL_NAME, build, step_key, weeks
from aihack4.strategy import choose

TODAY = date(2026, 11, 2)  # 월요일, 마감 11/30


def opt(name, tier, steps, *skills):
    return {"name": name, "tier": tier, "skills": [{"skill": s, "level": l} for s, l in skills], "how": "",
            "steps": [{"name": n, "hours": h} for n, h in steps], "hours": sum(h for _, h in steps)}


CARD = {"title": "t", "deadline": "2026-11-30", "deadline_time": None, "team_min": 1, "team_max": 4,
        "open_to_undergraduates": "yes", "conditions": [], "criteria": [], "forms": ["신청서"],
        "submissions": [
            {"name": "기획서", "requirement": "", "options": [opt("조사 기획서", "표준", [("자료 조사", 3), ("초안", 4), ("다듬기", 2)])]},
            {"name": "영상", "requirement": "", "options": [opt("화면녹화", "표준", [("대본", 2), ("녹화·편집", 4)])]}]}


def profile(**overrides):
    return Profile(**{"skills": {}, "experience": "참가 경험", "weekday_hours": 2, "weekend_hours": 4, **overrides})


def plan(p, **kw):
    return build(choose(CARD, p, TODAY), p, **kw)


def work_items(r):
    return [(d, i) for d in r.days if not d.buffer for i in d.items]


def test_everything_scheduled_in_order_with_milestones():
    r = plan(profile())
    steps = [i.step for _, i in work_items(r)]
    order = ["자료 조사", "초안", "다듬기", "대본", "녹화·편집"]
    assert [s for k, s in enumerate(steps) if k == 0 or steps[k - 1] != s] == order
    assert round(sum(i.hours for _, i in work_items(r)), 1) == 15
    (first, d1), (second, d2) = r.milestones
    assert (first, second) == ("기획서", "영상") and d1 <= d2


def test_even_pace_does_not_fill_every_day():
    r = plan(profile())
    assert r.pace < 0.5
    assert all(d.hours <= d.capacity + 1e-6 for d in r.days if not d.buffer)
    used = [d.hours for d in r.days if not d.buffer and d.hours]
    assert min(used) >= 1 - 1e-6  # 한 번 작업은 최소 1시간


def test_small_weekdays_still_used_without_slivers():
    r = plan(profile(weekday_hours=0.5, weekend_hours=2))
    used = [d for d in r.days if d.hours and not d.buffer]
    for day in used[:-1]:  # 마지막 날은 남은 조각을 마무리할 수 있다
        assert day.hours >= min(1, day.capacity) - 1e-6


def test_blocked_period_is_empty_and_buffer_has_only_final():
    p = profile(blocked=[Period(date(2026, 11, 9), date(2026, 11, 15), "시험")])
    r = plan(p)
    assert not any(date(2026, 11, 9) <= d.date <= date(2026, 11, 15) for d in r.days)
    buffer = [d for d in r.days if d.buffer]
    assert buffer and all(i.step == FINAL_NAME for d in buffer for i in d.items)
    assert max(d.date for d in r.days if not d.buffer) < min(d.date for d in buffer)


def test_overflow_when_time_is_short():
    r = plan(profile(weekday_hours=0, weekend_hours=0.5))
    assert r.overflow > 0


def test_progress_removes_done_work():
    done = {step_key("기획서", "자료 조사"): 3, step_key("기획서", "초안"): 1}
    r = plan(profile(), progress=done)
    steps = {i.step for _, i in work_items(r)}
    assert "자료 조사" not in steps and round(sum(i.hours for _, i in work_items(r)), 1) == 11


def test_weeks_group_by_seven_days():
    r = plan(profile())
    w = weeks(r, TODAY)
    assert w[0]["start"] == TODAY and sum(sum(x["tasks"].values()) for x in w) >= 15


def test_first_work_day_has_work_and_spent_time_today_is_not_reused():
    light = profile(weekday_hours=1, weekend_hours=2)  # 고른 속도의 하루 몫이 1시간보다 적어도
    r = plan(light)
    assert r.days[0].date == TODAY and r.days[0].hours >= 1  # 첫날부터 할 일이 있다
    done = {step_key("기획서", "자료 조사"): 1.0}
    again = plan(light, progress=done, spent={TODAY: 1.0})  # 오늘 1시간을 이미 썼다
    assert again.days[0].date == TODAY and again.days[0].hours == 0
    assert round(sum(i.hours for _, i in work_items(again)), 1) == 14
