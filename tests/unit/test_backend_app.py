"""backend/app.py: 분석 → 재판정 → 방법 바꾸기 → 다시 짜기 → 기록 다시 열기. LLM은 가짜 카드로 대신한다."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import app as backend  # noqa: E402
from test_backend_adapter import CARD  # noqa: E402

FORM = {"posting": "https://example.com/notice", "major": "3학년 · 인공지능", "hours": "12", "team": "팀 없음",
        "goal": "완성", "skills": '[["기획", "가능"], ["영상", "가능"]]', "exp": "해커톤 참가 1회"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(backend, "plan_complete", lambda: None)  # 배치는 코드로(LLM 없이)
    monkeypatch.setattr(backend, "notice_card", lambda text, today: {**CARD, "deadline": far_deadline(), "conditions": [
        {"type": "other", "allowed": [], "min": None, "max": None, "quote": "주최 기관 회원", "question": "회원인가요?",
         "typical": False}]})
    return backend.app.test_client()


def far_deadline():
    from datetime import datetime, timedelta
    return (datetime.now(backend.KST).date() + timedelta(days=28)).isoformat()


def test_analyze_then_recompute_without_llm(client, monkeypatch):
    first = client.post("/api/analyze", data=FORM).get_json()
    sid = first["session"]["id"]
    assert first["meta"]["verdict"] == "확인 필요" and first["meta"]["timetable"] == "none"
    assert [s["method"] for s in first["strategy"]] == ["사례 조사 기획서", "촬영+편집"]

    monkeypatch.setattr(backend, "notice_card", lambda *a: pytest.fail("다시 계산할 때는 공고 카드를 다시 만들지 않는다"))
    judged = client.post("/api/rejudge", json={"id": sid, "answers": {"회원인가요?": True}}).get_json()
    assert judged["meta"]["verdict"] in {"참여 가능", "준비하면 가능"}

    key, hours = first["daily"][0]["todos"][0][3], first["daily"][0]["todos"][0][4]
    replanned = client.post("/api/replan", json={"id": sid, "done": [[key, hours], "잘못된 값"]}).get_json()
    assert replanned["session"]["progress"]["hours"] == pytest.approx(hours, abs=0.1)
    today_hours = sum(t[4] for t in replanned["daily"][0]["todos"])  # 오늘 쓴 시간만큼 오늘 할 일이 줄어든다
    assert today_hours == 0

    picked = client.post("/api/pick", json={"id": sid, "picks": {"기획서": "서식 채우기"}}).get_json()
    assert picked["strategy"][0]["method"] == "서식 채우기" and picked["strategy"][0]["picked"]
    assert picked["session"]["progress"]["hours"] == 0  # 방법을 바꾸면 그 제출물의 진행 기록은 지운다
    back = client.post("/api/pick", json={"id": sid, "picks": {"기획서": None}}).get_json()
    assert back["strategy"][0]["method"] == "사례 조사 기획서" and not back["strategy"][0]["picked"]


def test_session_survives_restart_and_bad_ids_are_rejected(client):
    sid = client.post("/api/analyze", data=FORM).get_json()["session"]["id"]
    again = client.get(f"/api/session/{sid}").get_json()  # 기록은 파일에 있어 서버를 다시 켜도 열린다
    assert again["session"]["id"] == sid and again["posting"]["title"] == CARD["title"]
    assert client.get("/api/session/" + "0" * 32).status_code == 410
    assert client.post("/api/replan", json={"id": "../../etc/passwd", "done": []}).status_code == 410
    assert client.post("/api/rejudge", json={}).status_code == 410


def test_timetable_read_failure_is_reported_not_hidden(client, monkeypatch):
    import io
    monkeypatch.setattr(backend, "timetable_classes", lambda image, mime: None)
    failed = client.post("/api/analyze", data={**FORM, "timetable": (io.BytesIO(b"x"), "t.png", "image/png")},
                         content_type="multipart/form-data").get_json()
    assert failed["meta"]["timetable"] == "failed"
    monkeypatch.setattr(backend, "timetable_classes",
                        lambda image, mime: [{"day": 0, "start": "18:00", "end": "20:00", "name": "자료구조"}])
    ok = client.post("/api/analyze", data={**FORM, "timetable": (io.BytesIO(b"x"), "t.png", "image/png")},
                     content_type="multipart/form-data").get_json()
    assert ok["meta"]["timetable"] == "ok"
    monday = next(d for d in ok["daily"] if d["d"] == "월")
    assert monday["classes"] == [["18:00–20:00", "자료구조"]]


def test_bad_input_is_rejected(client):
    assert client.post("/api/analyze", data={"posting": "짧음"}).status_code == 400
    assert client.get("/api/trust").get_json()[0]["name"]
