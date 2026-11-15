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


def respond(form):
    profile, assumed = adapter.to_profile(form, TODAY)
    strategy = choose(CARD, profile, TODAY)
    roadmap = build(strategy, profile)
    classes = [{"day": 0, "start": "18:00", "end": "20:00", "name": "자료구조"}]
    return adapter.to_response(CARD, strategy, unpersonalized(CARD, TODAY), roadmap, profile, TODAY,
                               classes=classes, has_timetable=True, assumed=assumed, trust=[{"name": "x"}],
                               analysis_id="abc")


def test_response_has_every_screen_field():
    data = respond(FORM)
    assert set(data) >= {"meta", "posting", "requirements", "readiness", "time", "strategy", "thisWeek", "blockers",
                         "weeks", "fallbacks", "compare", "trust", "daily", "session"}
    assert data["posting"]["deadline"] == "2026-12-14T18:00:00+09:00"
    assert all(r["status"] in {"met", "caution", "lacking", "needs_check"} for r in data["requirements"])
    assert any("대한민국 국민" in r["criterion"] for r in data["requirements"])  # 흔한 조건은 '한 번 확인'
    assert len(data["daily"]) == 7 and data["session"]["id"] == "abc"


def test_strategy_differs_by_skill_and_compare_explains_why():
    no_video = respond(FORM)
    video = respond({**FORM, "skills": [["기획", "가능"], ["영상", "가능"]]})
    pick = lambda d: [s["method"] for s in d["strategy"]]  # noqa: E731
    assert pick(no_video) == ["사례 조사 기획서", "슬라이드+음성"]
    assert pick(video) == ["사례 조사 기획서", "촬영+편집"]
    assert "영상 가능" in no_video["compare"]["genericNote"]  # 개인화 OFF가 고른 '촬영+편집'은 내가 못 한다
    assert "기말고사" in no_video["compare"]["genericNote"]


def test_daily_todos_carry_step_keys_and_avoid_classes():
    monday = respond(FORM)["daily"][0]
    assert monday["free"] == 10  # 수업 2시간
    for slot, _, _, key, _ in monday["todos"]:
        assert " / " in key
        if slot != "시간 미정":
            start, end = slot.split("–")
            assert not (start < "20:00" and "18:00" < end)


def test_exam_week_has_little_or_no_work():
    data = respond(FORM)
    exam = data["weeks"][1]  # 11/23~11/29: 평일 시험, 주말만 작업
    assert data["weeks"][0]["tasks"] and sum(h for _, h in exam["tasks"]) <= 2 * 2.67 + 0.1
