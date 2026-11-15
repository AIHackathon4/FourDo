"""backend/adapter.py: 프론트 입력 → 엔진 프로필, 엔진 결과 → 프론트 JSON. LLM 없이 엔진의 evaluate()로 검사한다."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import adapter  # noqa: E402
from aihack4.feasibility import evaluate  # noqa: E402

TODAY = date(2026, 11, 16)  # 월요일
NOTICE = {
    "title": "AI 아이디어 공모전", "organizer": "테스트 재단", "deadline": "2026-12-10", "deadline_time": "18:00",
    "team_min": None, "team_max": 4, "submissions": ["기획서", "시연 영상"], "open_to_undergraduates": "yes",
    "eligibility_conditions": [],
}
RAW = {"tasks": [
    {"name": "주제 정하기", "deliverable": "주제 메모", "skill": None, "level": "입문", "matched_skill": None,
     "hours_min": 2, "hours_max": 4},
    {"name": "기획서 작성", "deliverable": "기획서", "skill": "기획", "level": "가능",
     "matched_skill": "기획(아이디어·기획서)", "hours_min": 6, "hours_max": 10},
    {"name": "시연 영상 제작", "deliverable": "영상", "skill": "영상 편집", "level": "가능", "matched_skill": None,
     "hours_min": 4, "hours_max": 8},
]}
FORM = {"major": "3학년 · 인공지능", "hours": 12, "busy": "11/23~11/27 기말고사, 주 2회 아르바이트",
        "skills": ["기획", "발표"], "exp": "팀 프로젝트 2회", "team": "팀 없음"}


def test_parse_inputs():
    assert adapter.parse_grade_major("3학년 · 인공지능") == (3, "인공지능")
    assert adapter.parse_grade_major("컴퓨터공학") == (None, "컴퓨터공학")
    periods, rest = adapter.parse_busy("11/23~11/27 기말고사, 주 2회 아르바이트, 12.3 면접", TODAY)
    assert [(p.start, p.end, p.reason) for p in periods] == [
        (date(2026, 11, 23), date(2026, 11, 27), "기말고사"), (date(2026, 12, 3), date(2026, 12, 3), "면접")]
    assert rest == "주 2회 아르바이트"
    periods, _ = adapter.parse_busy("1/5~1/9 계절학기", TODAY)  # 이미 지난 1월은 내년으로
    assert periods[0].start == date(2027, 1, 5)
    assert adapter.parse_experience("공모전 수상 1회") == "수상 경험"
    assert adapter.parse_experience("해커톤 참가") == "참가 경험"
    assert adapter.parse_experience("팀 프로젝트 2회") == "처음"
    assert adapter.parse_experience("공모전 경험 없음") == "처음"
    assert adapter.team_of("팀원 1~2명 있음") == ("팀 있음", 2)


def test_to_profile():
    profile, assumed = adapter.to_profile(FORM, TODAY)
    assert profile.grade == 3 and profile.major == "인공지능"
    assert profile.weekday_hours * 5 + profile.weekend_hours * 2 == pytest_approx(12)
    assert set(profile.skills) == {"기획(아이디어·기획서)", "발표·PPT"}
    assert profile.team == "혼자" and profile.blocked[0].reason == "기말고사"
    assert assumed["weekly"] == 12


def pytest_approx(value):
    import pytest
    return pytest.approx(value, abs=0.3)


def build():
    profile, assumed = adapter.to_profile(FORM, TODAY)
    result = evaluate(NOTICE, profile, RAW, TODAY)
    generic = {"needed_hours": 20, "tasks": [
        {"name": "기획서 작성", "skill": None, "start": "2026-11-23", "end": "2026-11-27", "hours": 12},
        {"name": "영상 제작", "skill": "영상", "start": "2026-11-30", "end": "2026-12-08", "hours": 8}]}
    classes = [{"day": 0, "start": "18:00", "end": "20:00", "name": "자료구조"}]
    return profile, result, adapter.to_response(
        NOTICE, result, profile, TODAY, generic=generic, classes=classes, has_timetable=True,
        assumed=assumed, trust=[{"name": "x"}], warnings=[])


def test_response_shape_matches_frontend():
    _, result, data = build()
    assert set(data) >= {"meta", "posting", "requirements", "readiness", "time", "thisWeek", "blockers", "weeks",
                         "fallbacks", "compare", "trust", "daily"}
    assert data["meta"]["verdict"] == result.verdict
    assert data["posting"]["deadline"] == "2026-12-10T18:00:00+09:00"
    assert all(r["status"] in {"met", "caution", "lacking", "needs_check"} for r in data["requirements"])
    assert all(r["source"] == "code" for r in data["requirements"])
    assert data["time"]["available"] == round(result.available_hours)
    assert len(data["daily"]) == 7 and data["daily"][0]["d"] == "월"
    for key in ("generic", "genericNote", "mine", "mineNote"):
        assert data["compare"][key]


def test_plan_respects_blocked_week_and_classes():
    _, _, data = build()
    exam = data["weeks"][1]  # 둘째 주 11/23~29: 기말고사 11/23~27이라 주말 이틀(하루 2.67시간)만 쓸 수 있다
    assert sum(h for _, h in exam["tasks"]) <= 2 * 2.67 + 0.5 * len(exam["tasks"])  # 작업마다 0.5시간 단위 반올림
    monday = data["daily"][0]
    for slot, _, _ in monday["todos"]:
        if slot != "시간 미정":
            start, end = slot.split("–")
            assert not (start < "20:00" and "18:00" < end)  # 수업(18~20시)과 겹치지 않는다
    # 일반 계획이 시험 주간에 배치되면 코드가 잡아낸다
    assert "바쁜 기간" in data["compare"]["genericNote"]


def test_blocked_day_has_no_free_time():
    profile, result, _ = build()
    days = adapter.daily(result, profile, date(2026, 11, 23), [], False)
    assert days[0]["free"] == 0 and days[0]["todos"] == [] and days[0]["classes"] == [["종일", "기말고사"]]


def test_blockers_from_gaps():
    _, result, data = build()
    assert any("영상" in b["d"] for b in data["blockers"])  # 영상 편집 역량이 없어 부족으로 잡힌다


def test_new_profile_inputs():
    form = {**FORM, "skills": [["개발", "능숙"], ["영상", "입문"]], "school": "서강대학교", "region": "서울",
            "status": "휴학생", "age": "23", "goal": "수상 목표"}
    profile, _ = adapter.to_profile(form, TODAY)
    assert profile.skills == {"개발(프로그래밍)": "능숙", "영상 제작·편집": "입문"}
    assert (profile.school, profile.region, profile.status, profile.age, profile.goal) == \
        ("서강대학교", "서울", "휴학생", 23, "수상 목표")
    profile, _ = adapter.to_profile({**form, "status": "외계인", "age": "abc", "goal": ""}, TODAY)
    assert (profile.status, profile.age, profile.goal) == ("재학생", None, "경험 쌓기")


def test_region_condition_fails_with_region_and_session_lists_questions():
    notice = {**NOTICE, "eligibility_conditions": [
        {"type": "region", "allowed": ["대전"], "min": None, "max": None, "quote": "대전 거주자", "question": "대전에 사나요?"},
        {"type": "other", "allowed": [], "min": None, "max": None, "quote": "OO 회원", "question": "OO 회원인가요?"}]}
    seoul, _ = adapter.to_profile({**FORM, "region": "서울"}, TODAY)
    assert evaluate(notice, seoul, RAW, TODAY).verdict == "어려움"  # 지역을 알면 바로 걸러진다
    unknown, assumed = adapter.to_profile(FORM, TODAY)
    result = evaluate(notice, unknown, RAW, TODAY, answers={"대전에 사나요?": True})
    data = adapter.to_response(notice, result, unknown, TODAY, generic=None, classes=[], has_timetable=False,
                               assumed=assumed, trust=[], warnings=[], analysis_id="abc",
                               answers={"대전에 사나요?": True}, progress={"기획서 작성": 2})
    qs = {q["q"]: q["answer"] for q in data["session"]["questions"]}
    assert qs == {"OO 회원인가요?": None, "대전에 사나요?": True}
    assert data["session"]["id"] == "abc" and data["session"]["progress"]["hours"] == 2
