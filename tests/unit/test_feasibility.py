import json
from datetime import date

import pytest
from test_collector import FakeLLM, notice

from aihack4.feasibility import Period, Profile, eligibility_check, evaluate, judge, normalize_region, work_days

TODAY = date(2026, 11, 10)  # 마감 2026-11-17 → 작업일 11/10~11/16 (7일)


def task(name="기획서 작성", skill="기획서 작성", level="가능", matched="기획서 작성", low=4, high=6):
    return {"name": name, "deliverable": name, "skill": skill, "level": level, "matched_skill": matched,
            "hours_min": low, "hours_max": high}


def raw(*tasks):
    return {"tasks": list(tasks)}


def profile(**overrides):
    values = {"skills": {"기획서 작성": "가능"}, "experience": "참가 경험", "weekday_hours": 1, "weekend_hours": 1}
    return Profile(**{**values, **overrides})


ADVICE = {"summary": "충분해요.", "this_week": ["주제 정하기"], "tips": ["예시 보기"]}


def test_work_days_skip_blocked_period_and_deadline_day():
    p = profile(blocked=[Period(date(2026, 11, 12), date(2026, 11, 13), "시험")])
    days = work_days(TODAY, date(2026, 11, 17), p)
    assert [d.day for d in days] == [10, 11, 14, 15, 16]


def test_weekend_only_profile_uses_weekends_and_weights_schedule():
    p = profile(weekday_hours=0, weekend_hours=5)  # 11/14(토), 11/15(일)만 작업일
    result = evaluate(notice(), p, raw(task(low=4, high=4)), TODAY)
    assert result.work_days == 2 and result.available_hours == 10
    assert result.tasks[0].start == "2026-11-14"  # 평일은 건너뛴다
    assert result.tasks[-1].start == "2026-11-15"


def test_weekday_weekend_split_in_hours_needed():
    p = profile(weekday_hours=1, weekend_hours=2)  # 평일 5일 + 주말 2일 → 9시간
    result = evaluate(notice(), p, raw(task(low=26, high=26)), TODAY)  # 최소 27시간 = 3배
    detail = next(c for c in result.checks if c.item == "시간").detail
    assert "평일 하루 3시간·주말 하루 6시간" in detail


def test_enough_time_and_skills_is_feasible_with_plan_inside_window():
    result = evaluate(notice(), profile(weekday_hours=2, weekend_hours=2), raw(task(), task("자료 조사", None, "입문", None, 1, 1)), TODAY)
    assert result.verdict == "참여 가능"
    assert result.target_hours == 7.5  # 5 + 1 + 최종 점검 1.5
    assert result.available_hours == 14.0  # 하루 2시간 × 7일
    assert result.tasks[-1].name == "최종 점검 및 제출"
    assert result.tasks[-1].end == "2026-11-16"
    assert all("2026-11-10" <= t.start <= t.end <= "2026-11-16" for t in result.tasks)


def test_missing_skill_doubles_hours_and_becomes_gap():
    result = evaluate(notice(), profile(weekday_hours=10, weekend_hours=10), raw(task("영상 편집", "영상 편집", matched=None)), TODAY)
    assert result.tasks[0].owner == "부족"
    assert (result.tasks[0].hours_min, result.tasks[0].hours_max) == (8, 12)
    assert result.verdict == "준비하면 가능"
    assert any("영상 편집" in gap for gap in result.gaps)


def test_lower_level_and_teammate_skill():
    p = profile(skills={"Python": "입문"}, team="팀 있음", team_size=2, teammate_skills=["디자인"], weekday_hours=10, weekend_hours=10)
    result = evaluate(notice(), p, raw(task("개발", "Python", "능숙", "Python"), task("디자인", "디자인", "가능", "디자인")), TODAY)
    assert [t.owner for t in result.tasks[:2]] == ["수준 부족", "팀원"]
    assert result.gaps == ["Python 수준 높이기(입문→능숙) — 개발"]


def test_not_enough_time_is_hard():
    result = evaluate(notice(), profile(weekday_hours=0.5, weekend_hours=0.5), raw(task(low=20, high=30)), TODAY)
    assert result.verdict == "어려움"
    check = next(c for c in result.checks if c.item == "시간")
    assert check.status == "fail"
    assert "최소 하루 3시간" in check.detail  # 최소 21시간 ÷ 지금 3.5시간 = 6배 → 하루 0.5 × 6


def test_blocked_whole_window_has_no_work_days():
    p = profile(blocked=[Period(date(2026, 11, 1), date(2026, 11, 30), "해외 연수")])
    result = evaluate(notice(), p, raw(task()), TODAY)
    assert result.work_days == 0 and result.verdict == "어려움"


def test_award_goal_uses_max_hours():
    goal = evaluate(notice(), profile(goal="수상 목표", weekday_hours=10, weekend_hours=10), raw(task()), TODAY)
    casual = evaluate(notice(), profile(weekday_hours=10, weekend_hours=10), raw(task()), TODAY)
    assert goal.target_hours == 8 and casual.target_hours == 6.5


@pytest.mark.parametrize("mode, expected", [("혼자", "어려움"), ("팀원 구하는 중", "확인 필요")])
def test_team_minimum(mode, expected):
    result = evaluate(notice(team_min=3, team_max=4), profile(team=mode, weekday_hours=10, weekend_hours=10), raw(task()), TODAY)
    assert result.verdict == expected
    assert result.gaps[0].startswith("팀원 2명")


def condition(kind, allowed=(), low=None, high=None, question="해당하나요?"):
    return {"type": kind, "allowed": list(allowed), "min": low, "max": high, "quote": f"{kind} 조건", "question": question}


def test_unclear_eligibility_needs_check():
    n = notice(eligibility_conditions=[condition("other", question="평생교육이용권 이용자인가요?")])
    result = evaluate(n, profile(weekday_hours=10, weekend_hours=10), raw(task()), TODAY)
    assert result.verdict == "확인 필요"
    assert result.gaps[0] == "참가 자격 확인: 평생교육이용권 이용자인가요?"
    assert result.questions == ["평생교육이용권 이용자인가요?"]


def test_answers_resolve_questions_without_llm():
    n = notice(eligibility_conditions=[condition("other", question="창업 1년 미만인가요?")])
    p = profile(weekday_hours=10, weekend_hours=10)
    assert evaluate(n, p, raw(task()), TODAY, {"창업 1년 미만인가요?": True}).verdict == "참여 가능"
    assert evaluate(n, p, raw(task()), TODAY, {"창업 1년 미만인가요?": False}).verdict == "어려움"


@pytest.mark.parametrize("region, expected", [("부산", "ok"), ("부산광역시 해운대구", "ok"), ("서울", "fail"), ("", "warn")])
def test_region_condition(region, expected):
    n = notice(eligibility_conditions=[condition("region", ["부산광역시"])])
    check, questions = eligibility_check(n, profile(region=region))
    assert check.status == expected and bool(questions) == (expected == "warn")


@pytest.mark.parametrize("text, short", [("강원특별자치도", "강원"), ("경기도 성남시", "경기"), ("전라북도", "전북"),
                                         ("세종특별자치시", "세종"), ("서울", "서울")])
def test_normalize_region(text, short):
    assert normalize_region(text) == short


@pytest.mark.parametrize("cond, overrides, expected", [
    (condition("school", ["한빛대학교"]), {"school": "한빛대"}, "ok"),
    (condition("school", ["한빛대학교"]), {"school": "서강대학교"}, "fail"),
    (condition("status", ["재학생", "졸업예정자"]), {"status": "휴학생"}, "fail"),
    (condition("status", ["재학생", "휴학생"]), {"status": "휴학생"}, "ok"),
    (condition("grade", low=3, high=4), {"grade": 2}, "fail"),
    (condition("grade", low=3, high=4), {"grade": 3}, "ok"),
    (condition("age", low=19, high=29), {}, "warn"),
    (condition("age", low=19, high=29), {"age": 31}, "fail"),
    (condition("major", ["디자인"]), {"major": "시각디자인학과"}, "ok"),
    (condition("major", ["소프트웨어"]), {"major": "컴퓨터공학"}, "warn"),
])
def test_condition_types(cond, overrides, expected):
    check, _ = eligibility_check(notice(eligibility_conditions=[cond]), profile(**overrides))
    assert check.status == expected


def test_worst_condition_wins_and_no_conditions_is_ok():
    n = notice(eligibility_conditions=[condition("region", ["서울"]), condition("other")])
    assert eligibility_check(n, profile(region="부산"))[0].status == "fail"
    assert eligibility_check(notice(), profile())[0].status == "ok"
    assert eligibility_check(notice(open_to_undergraduates="no"), profile())[0].status == "warn"


def test_judge_calls_decompose_then_advice_with_computed_facts():
    llm = FakeLLM([{"conditions": []}, raw(task()), ADVICE])
    n = notice()
    result = judge(llm, "m", n, profile(weekday_hours=10, weekend_hours=10), TODAY)
    assert result.summary == "충분해요." and result.this_week == ["주제 정하기"]
    assert n["eligibility_conditions"] == []  # 공고에 저장해 다음 판정에서는 다시 부르지 않는다
    facts = json.loads(llm.calls[2]["messages"][1]["content"].removeprefix("<data>\n").removesuffix("\n</data>"))
    assert facts["판정"] == "참여 가능"
    assert facts["일정"][-1]["작업"] == "최종 점검 및 제출"


def test_judge_reuses_extracted_conditions():
    llm = FakeLLM([raw(task()), ADVICE])
    n = notice(eligibility_conditions=[condition("region", ["서울"])])
    result = judge(llm, "m", n, profile(region="서울", weekday_hours=10, weekend_hours=10), TODAY)
    assert len(llm.calls) == 2 and result.verdict == "참여 가능"
    task_prompt = llm.calls[0]["messages"][1]["content"]
    assert "region" not in task_prompt  # 작업 분해에는 자격 정보를 주지 않는다


def test_closed_notice_is_judged_without_llm():
    llm = FakeLLM([])
    result = judge(llm, "m", notice(deadline="2026-11-01"), profile(), TODAY)
    assert result.verdict == "어려움" and llm.calls == []


def test_tight_schedule_fits_available_hours():
    """빠듯함: 목표 시간이 가용 시간보다 많으면 작업 시간을 최소~목표 사이로 줄여 하루 시간을 넘지 않게 짠다."""
    p = profile(weekday_hours=1, weekend_hours=1)  # 7일 × 1시간 = 7시간
    result = evaluate(notice(deadline="2026-11-17"), p, raw(task(low=4, high=8)), TODAY)
    assert result.verdict == "준비하면 가능"
    assert result.target_hours > result.available_hours  # 판정 기준(목표 시간)은 그대로
    assert sum(t.hours for t in result.tasks) == pytest.approx(result.available_hours, abs=0.2)
    assert all(t.hours >= t.hours_min for t in result.tasks)


def test_progress_removes_done_work_and_replans():
    p = profile(weekday_hours=1, weekend_hours=1)
    first = evaluate(notice(deadline="2026-11-17"), p, raw(task(name="자료 조사", low=2, high=2), task(low=4, high=8)), TODAY)
    again = evaluate(notice(deadline="2026-11-17"), p, raw(task(name="자료 조사", low=2, high=2), task(low=4, high=8)), TODAY,
                     progress={"자료 조사": 2, "기획서 작성": 3})
    assert [t.name for t in again.tasks] == ["기획서 작성", "최종 점검 및 제출"]  # 다 한 작업은 빠진다
    assert again.need_min < first.need_min and again.target_hours < first.target_hours
    assert again.verdict == "참여 가능"  # 남은 일이 줄어 여유가 생긴다
