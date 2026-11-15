"""backend/adapter.py: 프론트 입력 → 프로필, 설계 v3 엔진 결과 → 화면 JSON. LLM 없이 검사한다."""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import adapter  # noqa: E402
from aihack4.roadmap import build  # noqa: E402
from aihack4.strategy import choose, unpersonalized  # noqa: E402

TODAY = date(2026, 11, 16)  # 월요일


def opt(name, tier, steps, *skills):
    return {"name": name, "tier": tier, "skills": [{"skill": s, "level": l} for s, l in skills], "how": f"{name} 방법",
            "steps": [{"name": n, "hours": h} for n, h in steps], "hours": sum(h for _, h in steps)}


CARD = {
    "title": "AI 아이디어 공모전", "organizer": "테스트 재단", "deadline": "2026-12-14", "deadline_time": "18:00",
    "team_min": 1, "team_max": 4, "open_to_undergraduates": "yes", "forms": ["신청서"], "criteria": [],
    "conditions": [{"type": "other", "allowed": [], "min": None, "max": None, "quote": "대한민국 국민",
                    "question": "대한민국 국민인가요?", "typical": True}],
    "submissions": [
        {"name": "기획서", "requirement": "A4 5쪽", "options": [
            opt("서식 채우기", "최소", [("서식 작성", 4)]),
            opt("사례 조사 기획서", "표준", [("자료 조사", 3), ("초안", 5)], ("기획", "가능"))]},
        {"name": "시연 영상", "requirement": "3분 이내", "options": [
            opt("슬라이드+음성", "최소", [("슬라이드", 2), ("녹음", 1)]),
            opt("촬영+편집", "표준", [("촬영", 3), ("편집", 5)], ("영상", "가능"))]}],
}
FORM = {"major": "3학년 · 인공지능", "hours": 12, "busy": "11/23~11/27 기말고사, 주 2회 아르바이트",
        "skills": [["기획", "가능"], ["개발", "능숙"], ["없는역량", "가능"]], "exp": "해커톤 참가 1회",
        "team": "팀 없음", "goal": "경험 쌓기"}


def test_parse_inputs():
    assert adapter.parse_grade_major("3학년 · 인공지능") == (3, "인공지능")
    periods, rest = adapter.parse_busy("11/23~11/27 기말고사, 주 2회 아르바이트, 12.3 면접", TODAY)
    assert [(p.start, p.end, p.reason) for p in periods] == [
        (date(2026, 11, 23), date(2026, 11, 27), "기말고사"), (date(2026, 12, 3), date(2026, 12, 3), "면접")]
    assert rest == "주 2회 아르바이트"
    assert adapter.parse_experience("해커톤 참가") == "참가 경험" and adapter.parse_experience("공모전 경험 없음") == "처음"


def test_to_profile_keeps_only_known_skills_and_mates_only_with_team():
    profile, _ = adapter.to_profile(FORM, TODAY)
    assert profile.skills == {"기획": "가능", "개발": "능숙"} and profile.grade == 3
    assert profile.weekday_hours * 5 + profile.weekend_hours * 2 == pytest.approx(12, abs=0.1)
    solo, _ = adapter.to_profile({**FORM, "mate_skills": ["영상"]}, TODAY)
    team, _ = adapter.to_profile({**FORM, "team": "팀원 1~2명 있음", "mate_skills": ["영상", "엉뚱"]}, TODAY)
    assert solo.teammate_skills == [] and team.teammate_skills == ["영상"] and team.team_size == 2


CLASSES = [{"day": 0, "start": "18:00", "end": "20:00", "name": "자료구조"}]


def respond(form, picks=None, classes=CLASSES, progress=None):
    profile, assumed = adapter.to_profile(form, TODAY, classes)
    strategy = choose(CARD, profile, TODAY, picks=picks)
    roadmap = build(strategy, profile, progress)
    return adapter.to_response(CARD, strategy, unpersonalized(CARD, TODAY), roadmap, profile, TODAY,
                               classes=classes or [], timetable="ok" if classes is not None else "none",
                               assumed=assumed, trust=[{"name": "x"}], analysis_id="abc", picks=picks)


def test_response_has_every_screen_field():
    data = respond(FORM)
    assert set(data) >= {"meta", "posting", "requirements", "readiness", "time", "strategy", "today", "blockers",
                         "weeks", "compare", "trust", "daily", "session"}
    assert data["readiness"]["label"] == data["meta"]["verdict"] and data["readiness"]["summary"]
    assert data["posting"]["deadline"] == "2026-12-14T18:00:00+09:00"
    assert all(r["status"] in {"met", "caution", "lacking", "needs_check"} for r in data["requirements"])
    assert any("대한민국 국민" in r["criterion"] for r in data["requirements"])  # 흔한 조건은 '한 번 확인'
    assert data["session"]["id"] == "abc" and data["meta"]["timetable"] == "ok"


def test_strategy_differs_by_skill_and_compare_explains_why():
    no_video = respond(FORM)
    video = respond({**FORM, "skills": [["기획", "가능"], ["영상", "가능"]]})
    pick = lambda d: [s["method"] for s in d["strategy"]]  # noqa: E731
    assert pick(no_video) == ["사례 조사 기획서", "슬라이드+음성"]
    assert pick(video) == ["사례 조사 기획서", "촬영+편집"]
    note = no_video["compare"]["genericNote"]  # 개인화 OFF가 고른 '촬영+편집'은 지금 역량으로 어렵다
    assert "'촬영+편집'은 영상 가능 수준이 필요해서" in note and "기말고사 기간을 피하지 않아요" in note


def test_daily_todos_carry_step_keys_and_avoid_classes():
    monday = respond(FORM)["daily"][0]
    assert monday["classes"] == [["18:00–20:00", "자료구조"]] and monday["todos"]
    for slot, _, _, key, _ in monday["todos"]:
        assert " / " in key
        if slot != "시간 미정":
            start, end = slot.split("–")
            assert not (start < "20:00" and "18:00" < end)


def test_exam_week_has_little_or_no_work():
    data = respond(FORM)
    exam = data["weeks"][1]  # 11/23~11/29: 평일 시험, 주말만 작업
    assert data["weeks"][0]["tasks"] and sum(h for _, h in exam["tasks"]) <= 2 * 2.67 + 0.1


def test_goal_chips_and_tier_labels():
    profile, _ = adapter.to_profile({**FORM, "goal": "수상 도전"}, TODAY)
    unknown, _ = adapter.to_profile({**FORM, "goal": "아무거나"}, TODAY)
    assert profile.goal == "수상 도전" and unknown.goal == "완성"
    data = respond(FORM)
    assert {s["tier"] for s in data["strategy"]} <= {"완주형", "완성형", "수상 도전형"}
    video = data["strategy"][1]
    assert [o["tier"] for o in video["options"]] == ["완주형", "완성형"]
    blocked = next(o for o in video["options"] if not o["possible"])
    assert blocked["name"] == "촬영+편집" and "영상 가능" in blocked["why"]
    assert sum(o["chosen"] for o in video["options"]) == 1 and sum(o["recommended"] for o in video["options"]) == 1


def test_picked_method_is_shown_and_remembered():
    data = respond(FORM, picks={"기획서": "서식 채우기"})
    plan = data["strategy"][0]
    assert plan["method"] == "서식 채우기" and plan["picked"] and plan["tier"] == "완주형"
    assert next(o for o in plan["options"] if o["recommended"])["name"] == "사례 조사 기획서"
    assert data["session"]["picks"] == {"기획서": "서식 채우기"}


def test_daily_runs_from_today_to_deadline_with_notes():
    data = respond(FORM)
    days = data["daily"]
    assert len(days) == 29 and days[0]["date"] == "11/16" and days[-1]["date"] == "12/14"  # 오늘 ~ 마감일
    assert "마감일" in days[-1]["note"] and not days[-1]["todos"]
    assert any("완성 목표일" in d["note"] for d in days) and any("여유 기간" in d["note"] for d in days)
    exam = [d for d in days if d["date"] in {"11/23", "11/24", "11/25", "11/26", "11/27"}]
    assert all(d["free"] == 0 and not d["todos"] and ["종일", "기말고사"] in d["classes"] for d in exam)
    work = sum(hours for d in days for _, name, _, _, hours in d["todos"] if name != adapter.FINAL_NAME)
    assert work == pytest.approx(data["time"]["need"], abs=0.2)  # 하루별 할 일의 합 = 필요한 작업 시간


def test_today_card_points_to_first_work_day():
    assert respond(FORM)["today"]["when"] == "오늘" and respond(FORM)["today"]["hours"] > 0
    busy_now = respond({**FORM, "busy": "11/16~11/17 학회"})["today"]
    assert busy_now["when"] == "다음 작업일 11/18(수)"


def test_timetable_caps_daily_work_time():
    # 평일 내내 수업(09~23시)이면 평일에는 작업을 넣지 않는다
    packed = [{"day": d, "start": "09:00", "end": "23:00", "name": "수업"} for d in range(5)]
    assert adapter.timetable_caps(packed)[0] == 0 and adapter.timetable_caps(packed)[5] == 12
    profile, _ = adapter.to_profile(FORM, TODAY, packed)
    assert profile.hours_on(date(2026, 11, 16)) == 0 and profile.hours_on(date(2026, 11, 21)) > 0
    days = respond(FORM, classes=packed)["daily"]
    assert all(not d["todos"] for d in days if d["d"] in "월화수목금")
    none, _ = adapter.to_profile(FORM, TODAY)  # 시간표가 없으면 상한도 없다
    assert none.daily_cap == {} and none.hours_on(date(2026, 11, 16)) > 0


def test_todo_is_split_across_free_slots_and_bad_classes_are_dropped():
    saturday = date(2026, 11, 21)
    classes = [{"day": 5, "start": "11:00", "end": "21:00", "name": "특강"}]  # 빈 시간 10~11시, 21~22시
    todos = adapter.place(saturday, [("초안", 2.0, "기획서 / 초안")], classes)
    assert [t[0] for t in todos] == ["10:00–11:00", "21:00–22:00"] and sum(t[4] for t in todos) == 2.0
    over = adapter.place(saturday, [("초안", 3.0, "기획서 / 초안")], classes)
    assert over[-1][0] == "시간 미정" and sum(t[4] for t in over) == 3.0
    assert adapter.clean_classes([{"day": "월", "start": "9:00", "end": "10:30"}, {"day": 9, "start": "9:00", "end": "10:00"},
                                  {"day": 1, "start": "12:00", "end": "11:00"}, "x", {"day": [1]}]) == [
        {"day": 0, "start": "9:00", "end": "10:30", "name": "수업"}]


def test_screen_text_is_plain_and_consistent():
    data = respond({**FORM, "exp": ""})  # 첫 참가: 시간 1.2배
    plan = data["strategy"][0]
    assert sum(hours for _, hours in plan["steps"]) == pytest.approx(plan["hours"], abs=0.2)  # 단계 시간의 합 = 방법 시간
    assert plan["who"] == {}  # 혼자면 누가 맡는지 보여 주지 않는다
    assert all("source" not in r for r in data["requirements"])
    weeks = data["weeks"]
    assert weeks[0]["label"] == "11/16~11/22" and any(w["goal"].startswith("★") for w in weeks)
    time_row = next(r for r in data["requirements"] if r["label"] == "시간")
    assert f"{data['time']['need']:g}시간이 필요하고" in time_row["criterion"]  # 시간 카드와 조건 표의 숫자가 같다
    text = str(data)
    assert "은(는)" not in text and "을(를)" not in text and "코드" not in text


def test_compare_shows_same_items_on_both_sides():
    compare = respond(FORM)["compare"]
    assert [row[0] for row in compare["mine"]] == ["방법", "시간", "일정", "결과"]
    assert [row[0] for row in compare["generic"]] == ["방법", "시간", "일정"]
    schedule = dict(compare["mine"])["일정"]
    assert "기말고사 기간 비움" in schedule and "수업 시간 피함" in schedule and "여유" in schedule
    same = respond({**FORM, "skills": [["기획", "가능"], ["영상", "가능"]], "busy": ""})["compare"]
    assert same["genericNote"] == "방법은 같지만, 시간과 일정은 내 상황에 맞춰 달라졌어요"


def test_closed_notice_has_no_work_to_do():
    from datetime import timedelta
    late = TODAY + timedelta(days=40)
    profile, assumed = adapter.to_profile(FORM, late)
    strategy = choose(CARD, profile, late)
    data = adapter.to_response(CARD, strategy, unpersonalized(CARD, late), build(strategy, profile), profile, late,
                               classes=[], timetable="none", assumed=assumed, trust=[])
    assert data["meta"]["verdict"] == "어려움" and data["today"]["title"] == "이미 마감된 공고예요"
    assert len(data["daily"]) == 7 and not any(d["todos"] for d in data["daily"])
