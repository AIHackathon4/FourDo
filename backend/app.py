"""FourDo 백엔드 (Flask). 실행: uv run python backend/app.py → http://localhost:5000

설계 v3(docs/백엔드_설계_v3.md): LLM은 선택지를 만들고, 코드가 고른다.
  1) 공고 읽기: 링크면 페이지와 첨부파일만, 아니면 붙여넣은 본문
  2) 공고 카드(LLM 1회, CARD_MODEL): 기본 정보·자격 조건·평가 기준·제출물별 만드는 방법 선택지.
     학생과 무관하므로 원문이 같으면 저장해 둔 카드를 다시 쓴다
  3) 맞춤 전략(코드): 역량·팀원·시간·목표로 방법을 고르고 판정
  4) 로드맵(코드): 고르게, 최소 1시간, 못 쓰는 날 건너뛰기, 여유 기간, 중간 목표
  5) 시간표 이미지 → 수업 추출(Gemini, 선택). 키가 없으면 시간표 없이 진행
재판정·다시 짜기·비교는 카드를 다시 쓰므로 LLM을 부르지 않는다. 화면 JSON 변환은 adapter.py.
"""

import hashlib
import json
import logging
import os
import sys
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv(ROOT / ".env")

from aihack4.card import CardError, extract_card, read_url  # noqa: E402
from aihack4.collector.fetcher import Fetcher  # noqa: E402
from aihack4.roadmap import build, step_key  # noqa: E402
from aihack4.strategy import choose, unpersonalized  # noqa: E402

import adapter  # noqa: E402

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
KST = timezone(timedelta(hours=9))
IMG_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
TRUST = json.loads((Path(__file__).with_name("trust.json")).read_text(encoding="utf-8"))["metrics"]
CARD_CACHE = Path(__file__).with_name(".card_cache")  # 공고 원문 해시 → 카드(커밋하지 않음)
TIMETABLE_MODELS = [os.getenv("TIMETABLE_MODEL", "gemini-4-hydrogen"), "gemini-4-hydrogen", "gemini-3.8-flash"]
TIMETABLE_PROMPT = """이 이미지는 대학생의 에브리타임 시간표야. 수업만 JSON 하나로 옮겨 적어(설명·코드블록 금지).
{"classes":[{"day":0,"start":"HH:MM","end":"HH:MM","name":str}]}  day는 월=0 … 일=6.
이미지에 없는 수업을 지어내지 마."""
log = logging.getLogger("fourdo")


def card_model() -> str:
    return os.getenv("CARD_MODEL") or os.environ["LLM_MODEL"]


def read_source(text: str) -> str:
    text = text.strip()
    if text.startswith(("http://", "https://")) and " " not in text:
        return read_url(Fetcher(), text)
    return text


def notice_card(text: str, today) -> dict:
    """원문이 같으면 저장해 둔 카드를 쓴다(같은 공고에는 같은 선택지)."""
    source = read_source(text)
    key = hashlib.sha256(f"{card_model()}\n{source}".encode()).hexdigest()[:20]
    path = CARD_CACHE / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    card, _ = extract_card(OpenAI(), card_model(), source, today)
    CARD_CACHE.mkdir(exist_ok=True)
    path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    return card


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


# 분석 한 번의 재료(카드, 프로필 등)를 잠깐 보관한다. 데모용 메모리 보관이라 서버를 다시 켜면 사라진다.
SESSIONS: OrderedDict[str, dict] = OrderedDict()
MAX_SESSIONS = 200


def remember(state: dict) -> str:
    analysis_id = uuid.uuid4().hex
    SESSIONS[analysis_id] = state
    while len(SESSIONS) > MAX_SESSIONS:
        SESSIONS.popitem(last=False)
    return analysis_id


def respond(analysis_id: str, state: dict):
    """카드 + 프로필 + 답변 + 진행 → 전략·로드맵을 코드로 계산해 화면 JSON으로."""
    today = datetime.now(KST).date()
    card, profile = state["card"], state["profile"]
    strategy = choose(card, profile, today, state["answers"] or None)
    roadmap = build(strategy, profile, state["progress"] or None)
    return jsonify(adapter.to_response(
        card, strategy, unpersonalized(card, today), roadmap, profile, today,
        classes=state["classes"] or [], has_timetable=state["classes"] is not None, assumed=state["assumed"],
        trust=TRUST, analysis_id=analysis_id, answers=state["answers"]))


def json_list(text: str | None) -> list:
    try:
        value = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "engine": "v3"}


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
    img = request.files.get("timetable")
    if img and img.mimetype not in IMG_TYPES:
        return jsonify(error="시간표는 PNG, JPG 이미지만 올릴 수 있어요."), 400
    image = img.read() if img else None

    today = datetime.now(KST).date()
    profile, assumed = adapter.to_profile(
        {"major": f.get("major", ""), "hours": hours, "busy": f.get("busy", ""), "skills": json_list(f.get("skills")),
         "mate_skills": json_list(f.get("mate_skills")), "exp": f.get("exp", ""), "team": f.get("team", "팀 없음"),
         "school": f.get("school", ""), "region": f.get("region", ""), "status": f.get("status", ""),
         "age": f.get("age", ""), "goal": f.get("goal", "")}, today)

    with ThreadPoolExecutor(max_workers=2) as pool:
        classes_job = pool.submit(timetable_classes, image, img.mimetype) if image else None
        try:
            card = notice_card(posting, today)
        except CardError as error:
            return jsonify(error=str(error)), 400
        except Exception as error:
            log.exception(error)
            return jsonify(error="공고를 분석하지 못했어요. 잠시 후 다시 시도해 주세요."), 502
        classes = classes_job.result() if classes_job else None

    state = {"card": card, "profile": profile, "classes": classes, "assumed": assumed, "answers": {}, "progress": {}}
    return respond(remember(state), state)


def session_or_error(body: dict):
    state = SESSIONS.get(str(body.get("id", "")))
    if state is None:
        return None, (jsonify(error="분석 기록이 만료됐어요. 다시 분석해 주세요."), 410)
    return state, None


@app.post("/api/rejudge")
def rejudge():
    """자격 확인 질문에 예/아니오로 답하면 그 답을 프로필보다 우선해 다시 판정한다(LLM 없음)."""
    body = request.get_json(silent=True) or {}
    state, error = session_or_error(body)
    if error:
        return error
    state["answers"] = {str(q): bool(a) for q, a in (body.get("answers") or {}).items() if a is not None}
    return respond(body["id"], state)


@app.post("/api/replan")
def replan():
    """하루별 할 일에서 체크한 단계의 시간을 빼고 남은 일을 다시 배치한다(LLM 없음).
    done: [[단계 키, 시간], ...]  단계 키는 '제출물 / 단계'(roadmap.step_key)."""
    body = request.get_json(silent=True) or {}
    state, error = session_or_error(body)
    if error:
        return error
    for key, hours in body.get("done") or []:
        try:
            state["progress"][str(key)] = state["progress"].get(str(key), 0.0) + max(0.0, float(hours))
        except (TypeError, ValueError):
            continue
    return respond(body["id"], state)


__all__ = ["app", "step_key"]

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
