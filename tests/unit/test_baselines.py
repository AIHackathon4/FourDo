from datetime import date

from test_collector import notice
from test_feasibility import profile, raw, task

from aihack4 import baselines as bl
from aihack4.feasibility import Period, evaluate

TODAY, DEADLINE = date(2026, 11, 10), date(2026, 11, 24)  # 월요일부터 2주


def plan(*items):
    return [{"name": n, "skill": None, "start": s, "end": e, "hours": h} for n, s, e, h in items]


def test_plan_within_capacity_passes():
    p = profile(weekday_hours=2, weekend_hours=2)
    check = bl.check_plan(plan(("조사", "2026-11-10", "2026-11-16", 10)), p, TODAY, DEADLINE)
    assert check == {"planned_hours": 10.0, "blocked_hours": 0, "outside_hours": 0, "overloaded_weeks": 0,
                     "invalid_tasks": 0, "violates": False}


def test_task_spanning_blocked_days_uses_only_free_days():
    p = profile(weekday_hours=2, weekend_hours=2, blocked=[Period(date(2026, 11, 12), date(2026, 11, 13), "시험")])
    check = bl.check_plan(plan(("조사", "2026-11-10", "2026-11-14", 6)), p, TODAY, DEADLINE)
    assert check["blocked_hours"] == 0 and not check["violates"]


def test_task_inside_blocked_period_is_violation():
    p = profile(weekday_hours=2, weekend_hours=2, blocked=[Period(date(2026, 11, 12), date(2026, 11, 13), "시험")])
    check = bl.check_plan(plan(("조사", "2026-11-12", "2026-11-13", 4)), p, TODAY, DEADLINE)
    assert check["blocked_hours"] == 4.0 and check["violates"]


def test_week_over_capacity_is_overload():
    p = profile(weekday_hours=1, weekend_hours=1)  # 첫 주 7시간
    check = bl.check_plan(plan(("제작", "2026-11-10", "2026-11-16", 20)), p, TODAY, DEADLINE)
    assert check["overloaded_weeks"] == 1 and check["violates"]


def test_after_deadline_and_bad_dates_are_violations():
    p = profile(weekday_hours=3, weekend_hours=3)
    check = bl.check_plan(plan(("제출", "2026-11-25", "2026-11-25", 1), ("?", "언젠가", "2026-11-12", 2)), p, TODAY, DEADLINE)
    assert check["outside_hours"] == 1.0 and check["invalid_tasks"] == 1 and check["violates"]


def test_true_available_matches_podo_calculation():
    p = profile(weekday_hours=1, weekend_hours=3, blocked=[Period(date(2026, 11, 12), date(2026, 11, 13), "시험")])
    result = evaluate(notice(deadline="2026-11-24"), p, raw(task()), TODAY)
    assert bl.true_available(p, TODAY, DEADLINE) == result.available_hours


def test_podo_plan_never_violates_when_feasible():
    p = profile(weekday_hours=2, weekend_hours=4, blocked=[Period(date(2026, 11, 12), date(2026, 11, 13), "시험")])
    result = evaluate(notice(deadline="2026-11-24"), p, raw(task(low=6, high=8)), TODAY)
    assert result.verdict != "어려움"
    assert not bl.check_plan(bl.podo_tasks(result), p, TODAY, DEADLINE)["violates"]


def test_llm_arithmetic_checks():
    tasks = plan(("a", "2026-11-10", "2026-11-11", 4), ("b", "2026-11-12", "2026-11-13", 4))
    assert bl.sum_mismatch({"needed_hours": 12, "tasks": tasks})
    assert not bl.sum_mismatch({"needed_hours": 8, "tasks": tasks})
    assert bl.self_contradiction({"verdict": "참여 가능", "needed_hours": 20, "available_hours": 10})
    assert not bl.self_contradiction({"verdict": "어려움", "needed_hours": 20, "available_hours": 10})
