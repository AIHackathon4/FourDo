"""할 일 배치: LLM 답을 코드가 검사하고, 어기면 한 번 고쳐 달라고 하고, 그래도 어기면 코드 배치를 쓴다."""

from datetime import date, timedelta

from aihack4.feasibility import Period, Profile
from aihack4.roadmap import FINAL_NAME
from aihack4.schedule import check, plan, plan_input
from aihack4.strategy import choose

TODAY = date(2026, 11, 16)  # 월요일, 마감 12/4


def opt(name, tier, steps):
    return {"name": name, "tier": tier, "skills": [], "how": "", "steps": [{"name": n, "hours": h} for n, h in steps],
            "hours": sum(h for _, h in steps)}


CARD = {"title": "t", "deadline": "2026-12-04", "deadline_time": None, "team_min": 1, "team_max": 4,
        "open_to_undergraduates": "yes", "conditions": [], "criteria": [], "forms": ["신청서"],
        "submissions": [{"name": "숏폼", "requirement": "", "options": [
            opt("에셋 편집", "표준", [("에셋 정리", 1), ("AI 배경 생성", 2), ("영상 편집", 2)])]}]}


def setup(**overrides):
    profile = Profile(**{"skills": {}, "experience": "참가 경험", "weekday_hours": 1.3, "weekend_hours": 2.7, **overrides})
    return choose(CARD, profile, TODAY), profile


def good_answer(strategy, profile):
    """사람이 짤 법한 배치: 단계를 쪼개지 않고 앞쪽에, 큰 작업은 주말에."""
    _, _, work, data = plan_input(strategy, profile, None, None)
    weekend = [d["date"] for d in data["work_days"] if d["weekday"] in "토일"]
    return {"days": [{"date": "2026-11-16", "tasks": [{"id": "S1", "hours": 1}]},
                     {"date": weekend[0], "tasks": [{"id": "S2", "hours": 2}]},
                     {"date": weekend[1], "tasks": [{"id": "S3", "hours": 2}]}]}


def test_valid_llm_plan_is_used_as_is():
    strategy, profile = setup()
    roadmap, planner = plan(strategy, profile, complete=lambda s, u: good_answer(strategy, profile))
    assert planner == "llm"
    work = [(d.date, i.step, i.hours) for d in roadmap.days if not d.buffer for i in d.items]
    assert work == [(date(2026, 11, 16), "에셋 정리", 1.0), (date(2026, 11, 21), "AI 배경 생성", 2.0),
                    (date(2026, 11, 22), "영상 편집", 2.0)]
    assert roadmap.milestones == [("숏폼", date(2026, 11, 22))]
    assert any(i.step == FINAL_NAME for d in roadmap.days if d.buffer for i in d.items)


def test_rule_breaking_plan_is_retried_with_reasons_then_falls_back_to_code():
    strategy, profile = setup(blocked=[Period(date(2026, 11, 21), date(2026, 11, 22), "시험")])
    calls = []

    def bad(system, user):
        calls.append(user)
        return {"days": [{"date": "2026-11-21", "tasks": [{"id": "S1", "hours": 1}]},  # 시험 날
                         {"date": "2026-11-16", "tasks": [{"id": "S2", "hours": 5}]}]}  # 하루 시간 초과, 순서, 누락
    roadmap, planner = plan(strategy, profile, complete=bad)
    assert planner == "code" and len(calls) == 2
    assert "작업일이 아니에요" in calls[1] and "그날은" in calls[1] and "S3" in calls[1]
    assert not any(d.items for d in roadmap.days if d.date in (date(2026, 11, 21), date(2026, 11, 22)))


def test_check_catches_order_tiny_pieces_and_missing_hours():
    strategy, profile = setup()
    _, todo, work, _ = plan_input(strategy, profile, None, None)
    problems, _ = check({"days": [{"date": "2026-11-16", "tasks": [{"id": "S2", "hours": 1}]},
                                  {"date": "2026-11-17", "tasks": [{"id": "S1", "hours": 0.2}, {"id": "S2", "hours": 1}]},
                                  {"date": "2026-11-18", "tasks": [{"id": "S9", "hours": 1}]}]}, todo, work)
    text = " ".join(problems)
    assert "먼저 시작" in text and "잘게" in text and "알 수 없는" in text and "S1(에셋 정리)" in text and "S3" in text


def test_cache_reuses_plan_and_llm_errors_fall_back():
    strategy, profile = setup()
    cache, calls = {}, []

    def once(system, user):
        calls.append(1)
        return good_answer(strategy, profile)
    plan(strategy, profile, complete=once, cache=cache)
    roadmap, planner = plan(strategy, profile, complete=once, cache=cache)
    assert planner == "llm" and len(calls) == 1

    def broken(system, user):
        raise TimeoutError
    assert plan(strategy, profile, complete=broken)[1] == "code"
    assert plan(strategy, profile, complete=None)[1] == "code"


def test_not_enough_time_uses_code_without_calling_llm():
    strategy, profile = setup(weekday_hours=0.1, weekend_hours=0.1)
    roadmap, planner = plan(strategy, profile, complete=lambda s, u: (_ for _ in ()).throw(AssertionError("호출하면 안 됨")))
    assert planner == "code" and strategy.verdict == "어려움"


def test_progress_and_spent_time_shrink_what_llm_must_place():
    strategy, profile = setup()
    _, _, work, data = plan_input(strategy, profile, {"숏폼 / 에셋 정리": 1.0}, {TODAY: 1.0})
    assert [s["step"] for s in data["steps"]] == ["AI 배경 생성", "영상 편집"]
    assert TODAY.isoformat() not in [d["date"] for d in data["work_days"]] or work[0].capacity < 0.5
    assert all(date.fromisoformat(d["date"]) < date(2026, 12, 4) - timedelta(days=1) for d in data["work_days"])


def test_llm_gets_round_numbers_and_result_keeps_real_hours():
    strategy, profile = setup(experience="처음", weekday_hours=1.11, weekend_hours=2.22)  # 단계 1.2, 2.4, 2.4시간
    todo, asked, work, data = plan_input(strategy, profile, None, None)
    assert [s["hours"] for s in data["steps"]] == [1.0, 2.5, 2.5] and {d["hours"] for d in data["work_days"]} == {1.1, 2.2}
    answer = {"days": [{"date": "2026-11-16", "tasks": [{"id": "S1", "hours": 1}]},
                       {"date": "2026-11-21", "tasks": [{"id": "S2", "hours": 2.0}]},
                       {"date": "2026-11-22", "tasks": [{"id": "S2", "hours": 0.5}, {"id": "S3", "hours": 1.7}]},
                       {"date": "2026-11-23", "tasks": [{"id": "S3", "hours": 0.8}]}]}
    roadmap, planner = plan(strategy, profile, complete=lambda s, u: answer)
    assert planner == "llm"
    total = sum(i.hours for d in roadmap.days if not d.buffer for i in d.items)
    assert abs(total - sum(i.hours for i in todo)) < 0.05  # 배치한 시간의 합은 실제 필요 시간
