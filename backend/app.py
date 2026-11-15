"""FourDo 백엔드 (Flask). 실행: python backend/app.py → http://localhost:5000

판정 엔진은 PODO 엔진(src/aihack4)을 그대로 쓴다. LLM은 읽기만 하고 계산은 코드가 한다.
  1) 공고 읽기·추출: collector.intake (링크면 모집요강 페이지·첨부파일까지 따라가 읽음)
  2) 자격 조건 추출 → 코드 비교, 작업 분해(LLM) → 시간·판정·일정 계산(코드), 조언 문장(LLM)
  3) 비교용 일반 계획(프로필 없음, 같은 모델) — 개인화 비교 화면용
  4) 시간표 이미지 → 수업 추출(Gemini, 선택). 키가 없으면 시간표 없이 진행
엔진 결과를 프론트가 그리는 JSON으로 옮기는 일은 adapter.py가 한다.
"""

import json
import logging
import os
import sys
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv(ROOT / ".env")

from aihack4 import baselines as bl  # noqa: E402
from aihack4.collector import intake  # noqa: E402
from aihack4.collector.fetcher import Fetcher  # noqa: E402
from aihack4.feasibility import advise, evaluate, judge  # noqa: E402

import adapter  # noqa: E402

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
KST = timezone(timedelta(hours=9))
IMG_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
TRUST = json.loads((Path(__file__).with_name("trust.json")).read_text(encoding="utf-8"))["metrics"]
TIMETABLE_MODELS = [os.getenv("TIMETABLE_MODEL", "gemini-4-hydrogen"), "gemini-4-hydrogen", "gemini-3.8-flash"]
TIMETABLE_PROMPT = """이 이미지는 대학생의 에브리타임 시간표야. 수업만 JSON 하나로 옮겨 적어(설명·코드블록 금지).
{"classes":[{"day":0,"start":"HH:MM","end":"HH:MM","name":str}]}  day는 월=0 … 일=6.
이미지에 없는 수업을 지어내지 마."""
log = logging.getLogger("fourdo")


def llm() -> tuple[OpenAI, str]:
    return OpenAI(), os.environ["LLM_MODEL"]


def read_notice(text: str, today: date) -> tuple[dict, list[str]]:
    """링크든 본문이든 엔진이 쓰는 구조화 공고(dict)로 만든다. 실패하면 IntakeError(사용자에게 보여 줄 문장)."""
    client, model = llm()
    text = text.strip()
    if text.startswith(("http://", "https://")) and " " not in text:
        result = intake.intake_url(client, model, Fetcher(), text, today.isoformat())
    else:
        result = intake.intake_text(client, model, text, today.isoformat())
    if not result.agent.notice:
        raise intake.IntakeError("공고 내용을 읽지 못했어요. 모집요강을 직접 붙여넣어 주세요.")
    return result.agent.notice, result.warnings


def timetable_classes(image: bytes, mime: str) -> list[dict] | None:
    """시간표 이미지에서 수업만 추출한다(읽기만 하는 LLM). 키가 없거나 실패하면 None."""
    if not os.getenv("GEMINI_API_KEY"):
        return None
    from google import genai
    from google.genai import types

    client = genai.Client()
    for model in dict.fromkeys(TIMETABLE_MODELS):
        try:
            resp = client.models.generate_content(
                model=model, contents=[types.Part.from_bytes(data=image, mime_type=mime), TIMETABLE_PROMPT],
                config=types.GenerateContentConfig(response_mime_type="application/json", max_output_tokens=4096))
            raw = resp.text or ""
            return json.loads(raw[raw.index("{"): raw.rindex("}") + 1]).get("classes", [])
        except Exception as error:  # 다음 모델로
            log.warning("timetable %s: %s", model, error)
    return None


def generic_plan(notice: dict, today: date) -> dict | None:
    """개인화 비교용: 프로필 없이 같은 모델이 짠 계획(평가의 비교 조건 A와 같은 프롬프트)."""
    try:
        client, model = llm()
        plan, _ = bl.generic_plan(client, model, notice, today)
        return plan
    except Exception as error:
        log.warning("generic plan: %s", error)
        return None


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "engine": "podo"}


# 분석 한 번의 재료(공고, 프로필, LLM 작업 분해 등)를 잠깐 보관한다. 재판정·다시 짜기는 이걸로 코드만 다시 돌린다.
# 데모용 메모리 보관이라 서버를 다시 켜면 사라진다(그때는 다시 분석).
SESSIONS: OrderedDict[str, dict] = OrderedDict()
MAX_SESSIONS = 200


def remember(state: dict) -> str:
    analysis_id = uuid.uuid4().hex
    SESSIONS[analysis_id] = state
    while len(SESSIONS) > MAX_SESSIONS:
        SESSIONS.popitem(last=False)
    return analysis_id


def respond(analysis_id: str, state: dict, result):
    return jsonify(adapter.to_response(
        state["notice"], result, state["profile"], state["today"], generic=state["generic"],
        classes=state["classes"] or [], has_timetable=state["classes"] is not None, assumed=state["assumed"],
        trust=TRUST, warnings=state["warnings"], analysis_id=analysis_id, answers=state["answers"],
        progress=state["progress"]))


@app.post("/api/analyze")
def analyze():
    f = request.form
    posting = (f.get("posting") or "").strip()
    if len(posting) < 20 and not posting.startswith(("http://", "https://")):
        return jsonify(error="공고 링크 또는 모집요강을 입력해 주세요."), 400
    try:
        hours = min(60, max(1, int(f.get("hours", 12))))
    except ValueError:
        hours = 12
    try:
        skills = json.loads(f.get("skills") or "[]")
    except json.JSONDecodeError:
        skills = []
    img = request.files.get("timetable")
    if img and img.mimetype not in IMG_TYPES:
        return jsonify(error="시간표는 PNG, JPG 이미지만 올릴 수 있어요."), 400
    image = img.read() if img else None

    today = datetime.now(KST).date()
    profile, assumed = adapter.to_profile(
        {"major": f.get("major", ""), "hours": hours, "busy": f.get("busy", ""), "skills": skills,
         "exp": f.get("exp", ""), "team": f.get("team", "팀 없음"), "school": f.get("school", ""),
         "region": f.get("region", ""), "status": f.get("status", ""), "age": f.get("age", ""),
         "goal": f.get("goal", "")}, today)

    with ThreadPoolExecutor(max_workers=3) as pool:
        classes_job = pool.submit(timetable_classes, image, img.mimetype) if image else None
        try:
            notice, warnings = read_notice(posting, today)
        except intake.IntakeError as error:
            return jsonify(error=str(error)), 400
        except Exception as error:
            log.exception(error)
            return jsonify(error="공고를 읽는 중에 문제가 생겼어요. 잠시 후 다시 시도해 주세요."), 502
        generic_job = pool.submit(generic_plan, dict(notice), today)  # judge가 notice에 자격 조건을 써 넣으므로 복사본
        try:
            client, model = llm()
            result = judge(client, model, notice, profile, today)
        except Exception as error:
            log.exception(error)
            return jsonify(error="분석에 실패했어요. 잠시 후 다시 시도해 주세요."), 502
        generic = generic_job.result()
        classes = classes_job.result() if classes_job else None

    state = {"notice": notice, "profile": profile, "raw": result.raw, "today": today, "generic": generic,
             "classes": classes, "assumed": assumed, "warnings": warnings, "answers": {}, "progress": {}}
    return respond(remember(state), state, result)


def recompute(state: dict):
    """LLM 작업 분해는 그대로 두고 코드로 판정·일정을 다시 계산한다. 조언 문장만 LLM으로 다시 쓴다."""
    today = datetime.now(KST).date()
    state["today"] = today
    if state["notice"].get("deadline") and date.fromisoformat(state["notice"]["deadline"]) < today:
        return judge(None, "", state["notice"], state["profile"], today)  # 마감 지남: LLM을 부르지 않고 끝난다
    result = evaluate(state["notice"], state["profile"], state["raw"], today, state["answers"] or None,
                      state["progress"] or None)
    try:
        client, model = llm()
        advise(client, model, state["notice"], state["profile"], result, today)
    except Exception as error:  # 조언 문장이 없어도 판정과 일정은 보여 준다
        log.warning("advice: %s", error)
    return result


def session_or_error(body: dict):
    state = SESSIONS.get(str(body.get("id", "")))
    if state is None:
        return None, (jsonify(error="분석 기록이 만료됐어요. 다시 분석해 주세요."), 410)
    return state, None


@app.post("/api/rejudge")
def rejudge():
    """자격 확인 질문에 예/아니오로 답하면 그 답을 프로필보다 우선해 다시 판정한다."""
    body = request.get_json(silent=True) or {}
    state, error = session_or_error(body)
    if error:
        return error
    answers = body.get("answers") or {}
    state["answers"] = {str(q): bool(a) for q, a in answers.items() if a is not None}
    return respond(body["id"], state, recompute(state))


@app.post("/api/replan")
def replan():
    """하루별 할 일에서 체크한 시간을 빼고 남은 작업으로 판정과 일정을 다시 짠다."""
    body = request.get_json(silent=True) or {}
    state, error = session_or_error(body)
    if error:
        return error
    for name, hours in body.get("done") or []:
        try:
            state["progress"][str(name)] = state["progress"].get(str(name), 0.0) + max(0.0, float(hours))
        except (TypeError, ValueError):
            continue
    return respond(body["id"], state, recompute(state))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
