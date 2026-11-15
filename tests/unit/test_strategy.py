"""맞춤 전략: 같은 공고라도 학생에 따라 고르는 방법이 달라지는지."""

from datetime import date

from aihack4.feasibility import Period, Profile
from aihack4.strategy import choose, unpersonalized

TODAY = date(2026, 11, 2)  # 월요일, 마감 11/30 → 작업일 28일


def opt(name, tier, hours, *skills):
    return {"name": name, "tier": tier, "skills": [{"skill": s, "level": l} for s, l in skills], "how": "",
            "steps": [{"name": name, "hours": hours}], "hours": hours}


def card(**overrides):
    base = {
        "title": "AI 아이디어 공모전", "deadline": "2026-11-30", "deadline_time": "18:00", "team_min": 1, "team_max": 4,
        "open_to_undergraduates": "yes", "conditions": [], "criteria": [], "forms": ["참가신청서", "동의서"],
        "submissions": [
            {"name": "기획서", "requirement": "A4 5쪽", "options": [
                opt("서식 채우기", "최소", 4), opt("사례 조사 기획서", "표준", 8, ("기획", "가능")),
                opt("프로토타입 캡처 기획서", "차별화", 14, ("기획", "능숙"), ("디자인", "가능"))]},
            {"name": "시연 영상", "requirement": "3분 이내", "options": [
                opt("슬라이드+음성", "최소", 3), opt("화면녹화+자막", "표준", 6, ("영상", "입문")),
                opt("촬영+편집", "차별화", 12, ("영상", "능숙"))]},
        ]}
    return {**base, **overrides}


def student(**overrides):
    values = {"skills": {"기획": "가능"}, "experience": "참가 경험", "weekday_hours": 1, "weekend_hours": 3}
    return Profile(**{**values, **overrides})


def picked(strategy):
    return [c.option["name"] if c.option else None for c in strategy.choices]


def test_same_notice_different_methods_by_skill():
    no_video = choose(card(), student(), TODAY)
    video = choose(card(), student(skills={"기획": "가능", "영상": "입문"}), TODAY)
    assert picked(no_video) == ["사례 조사 기획서", "슬라이드+음성"]
    assert picked(video) == ["사례 조사 기획서", "화면녹화+자막"]
    assert any("'화면녹화+자막'은 영상 입문 수준이 필요해요. 그래서 지금 할 수 있는 '슬라이드+음성'을 추천해요" in r
               for r in no_video.choices[1].reasons)
    assert no_video.verdict == "준비하면 가능" and video.verdict == "참여 가능"


def test_goal_award_picks_highest_feasible():
    award = choose(card(), student(skills={"기획": "능숙", "디자인": "가능", "영상": "능숙"}, goal="수상 목표",
                                   weekday_hours=2, weekend_hours=5), TODAY)
    assert picked(award) == ["프로토타입 캡처 기획서", "촬영+편집"]


def test_short_time_lowers_scope_biggest_saving_first():
    busy = student(skills={"기획": "능숙", "디자인": "가능", "영상": "능숙"}, goal="수상 목표",
                   weekday_hours=0.5, weekend_hours=1, blocked=[Period(date(2026, 11, 9), date(2026, 11, 22), "시험")])
    s = choose(card(), busy, TODAY)
    assert s.verdict == "준비하면 가능"
    assert sum(c.hours for c in s.choices) <= s.work_capacity
    assert any("낮췄어요" in r for c in s.choices for r in c.reasons)


def test_nearly_full_time_is_tight():
    full = card(submissions=[{"name": "기획서", "requirement": "", "options": [opt("서식 채우기", "표준", 30)]}])
    s = choose(full, student(weekday_hours=0.9, weekend_hours=2.6), TODAY)  # 가용 약 33h
    assert s.work_capacity * 0.85 < 30 <= s.work_capacity and s.verdict == "준비하면 가능"


def test_no_time_even_for_lightest_is_hard():
    s = choose(card(), student(weekday_hours=0, weekend_hours=0.5), TODAY)
    assert s.verdict == "어려움" and picked(s) == ["서식 채우기", "슬라이드+음성"]


def test_code_required_needs_developer_or_teammate():
    code = card(submissions=[{"name": "탐지 도구", "requirement": "Windows 실행 파일", "options": [
        opt("정규식 도구", "최소", 30, ("개발", "가능")), opt("GUI 도구", "표준", 45, ("개발", "능숙"))]}])
    roomy = {"weekday_hours": 3, "weekend_hours": 6}
    assert choose(code, student(**roomy), TODAY).verdict == "어려움"
    with_mate = choose(code, student(team="팀 있음", team_size=2, teammate_skills=["개발"], **roomy), TODAY)
    assert picked(with_mate) == ["GUI 도구"] and with_mate.choices[0].who == {"개발": "팀원 보완"}  # 팀원 가능 → 능숙은 한 단계 보완
    searching = choose(code, student(team="팀원 구하는 중", **roomy), TODAY)
    assert searching.verdict == "확인 필요"


def test_first_timer_takes_longer():
    first = choose(card(), student(experience="처음"), TODAY)
    again = choose(card(), student(), TODAY)
    assert first.choices[0].hours > again.choices[0].hours


def test_typical_condition_does_not_block_but_is_listed():
    korean = card(conditions=[{"type": "other", "allowed": [], "min": None, "max": None, "quote": "대한민국 국민",
                               "question": "대한민국 국민인가요?", "typical": True}])
    s = choose(korean, student(), TODAY)
    assert s.verdict != "확인 필요" and s.notes == ["대한민국 국민"]


def test_base_skills_count_as_entry_level():
    writing = card(submissions=[{"name": "기획서", "requirement": "", "options": [
        opt("간단히", "최소", 3), opt("조사 기획서", "표준", 8, ("글쓰기", "입문"))]}])
    assert picked(choose(writing, student(skills={}), TODAY)) == ["조사 기획서"]


def test_region_condition_filters_out():
    regional = card(conditions=[{"type": "region", "allowed": ["대전"], "min": None, "max": None,
                                 "quote": "대전 거주자", "question": "대전에 사나요?", "typical": False}])
    assert choose(regional, student(region="서울"), TODAY).verdict == "어려움"
    unknown = choose(regional, student(), TODAY)
    assert unknown.verdict == "확인 필요" and unknown.questions == ["대전에 사나요?"]
    assert choose(regional, student(), TODAY, answers={"대전에 사나요?": True}).verdict != "확인 필요"


def test_buffer_days_reserved_before_deadline():
    s = choose(card(), student(), TODAY)
    assert s.buffer_days and max(s.work_days) < min(s.buffer_days)
    assert s.final_hours == 1.6  # 최종 점검 1 + 서류 2개 × 0.3


def test_one_level_short_is_allowed_with_more_time():
    beginner = choose(card(), student(skills={"기획": "입문"}, weekday_hours=2, weekend_hours=4), TODAY)
    plan = beginner.choices[0]
    assert plan.option["name"] == "사례 조사 기획서" and plan.hours == 12  # 8h × 1.5
    assert plan.who == {"기획": "본인 보완"} and beginner.verdict == "준비하면 가능"
    assert any("익히는 시간까지" in r for r in plan.reasons)


def test_same_tier_picks_path_matching_strength():
    two_paths = card(submissions=[{"name": "기획서", "requirement": "", "options": [
        opt("서식 채우기", "최소", 4),
        opt("프로토타입형", "차별화", 20, ("개발", "가능")),
        opt("심층 리서치형", "차별화", 16, ("글쓰기", "능숙"))]}])
    roomy = {"goal": "수상 목표", "weekday_hours": 2, "weekend_hours": 5}
    assert picked(choose(two_paths, student(skills={"글쓰기": "능숙"}, **roomy), TODAY)) == ["심층 리서치형"]
    assert picked(choose(two_paths, student(skills={"개발": "능숙"}, **roomy), TODAY)) == ["프로토타입형"]


def test_unpersonalized_uses_standard_for_everyone():
    assert picked(unpersonalized(card(), TODAY)) == ["사례 조사 기획서", "화면녹화+자막"]


def test_closed_notice_is_hard_without_shrinking():
    s = choose(card(deadline="2026-10-01"), student(), TODAY)
    assert s.verdict == "어려움" and not any("낮췄어요" in r for c in s.choices for r in c.reasons)


def test_goal_levels_map_to_tiers():
    strong = {"기획": "능숙", "디자인": "가능", "영상": "능숙"}
    roomy = {"weekday_hours": 3, "weekend_hours": 6}
    finish = choose(card(), student(skills=strong, goal="완주", **roomy), TODAY)
    complete = choose(card(), student(skills=strong, goal="완성", **roomy), TODAY)
    award = choose(card(), student(skills=strong, goal="수상 도전", **roomy), TODAY)
    assert picked(finish) == ["서식 채우기", "슬라이드+음성"]
    assert picked(complete) == ["사례 조사 기획서", "화면녹화+자막"]
    assert picked(award) == ["프로토타입 캡처 기획서", "촬영+편집"]
    assert any("'수상 도전' 목표에 맞는" in r for r in award.choices[0].reasons)


def test_missing_skill_gives_substitute_and_says_so():
    s = choose(card(), student(goal="완성"), TODAY)  # 영상 없음
    video = s.choices[1]
    assert video.option["name"] == "슬라이드+음성" and video.substitute
    assert any("추천해요" in r for r in video.reasons)
    assert next(c for c in s.checks if c.item == "필요 역량").status == "warn"


def test_lowered_method_drops_reasons_of_the_method_it_replaced():
    busy = student(skills={"기획": "입문"}, goal="완성", weekday_hours=0.2, weekend_hours=0.4)  # 완성형은 기획 보완(1.5배)
    s = choose(card(), busy, TODAY)
    plan = s.choices[0]
    assert plan.option["name"] == "서식 채우기"
    assert len(plan.reasons) == 1 and "'사례 조사 기획서' 대신 '서식 채우기'로 낮췄어요" in plan.reasons[0]


def test_particles_follow_final_consonant():
    from aihack4.strategy import josa, quoted
    assert josa("기획서", "은는") == "기획서는" and josa("영상", "은는") == "영상은"
    assert josa("기획서(아이디어 부문)", "을를") == "기획서(아이디어 부문)을" and josa("PDF", "을를") == "PDF을(를)"
    assert quoted("서식 채우기", "으로") == "'서식 채우기'로" and quoted("촬영", "으로") == "'촬영'으로"
    assert quoted("연필", "으로") == "'연필'로"  # ㄹ 받침
