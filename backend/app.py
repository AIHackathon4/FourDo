"""FourDo 백엔드 (Flask). 실행: uv run python backend/app.py → http://localhost:5000

설계 v3(docs/백엔드_설계_v3.md): LLM은 선택지를 만들고, 코드가 고른다.
  1) 공고 읽기: 링크면 페이지와 첨부파일만, 아니면 붙여넣은 본문
  2) 공고 카드(LLM 1회, CARD_MODEL): 기본 정보·자격 조건·평가 기준·제출물별 만드는 방법 선택지.
     학생과 무관하므로 원문이 같으면 저장해 둔 카드를 다시 쓴다
  3) 맞춤 전략(코드): 역량·팀원·시간·목표로 방법을 고르고 판정
  4) 할 일 배치(LLM + 코드 검사, PLAN_MODEL): LLM이 날짜별로 배치하고 코드가 못 쓰는 날·하루 시간·순서·누락을 검사.
     어기면 한 번 고쳐 달라고 하고, 그래도 어기면 코드 배치(roadmap.build). 같은 입력이면 저장해 둔 배치를 다시 쓴다
  5) 시간표 이미지 → 수업 추출(읽기만 하는 LLM, 선택). 읽지 못하면 시간표 없이 진행하고 화면에 그렇다고 알린다
재판정·다시 짜기·기록 다시 열기는 카드를 다시 쓴다. 할 일이 바뀌면(다시 짜기) 배치만 LLM을 다시 부른다.
화면 JSON 변환은 adapter.py.
"""

import base64
import hashlib
import json
import logging
import os
import re
import sys
import uuid
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

from aihack4.card import CARD_VERSION, CardError, extract_card, read_url  # noqa: E402
from aihack4.collector.fetcher import Fetcher  # noqa: E402
from aihack4.roadmap import step_key  # noqa: E402
from aihack4.schedule import openai_complete, plan  # noqa: E402
from aihack4.strategy import choose, unpersonalized  # noqa: E402

import adapter  # noqa: E402

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
KST = timezone(timedelta(hours=9))
IMG_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
TRUST = json.loads((Path(__file__).with_name("trust.json")).read_text(encoding="utf-8"))["metrics"]
CARD_CACHE = Path(__file__).with_name(".card_cache")  # 공고 원문 해시 → 카드(커밋하지 않음)
SESSION_DIR = Path(__file__).with_name(".sessions")  # 분석 기록(커밋하지 않음)
MAX_SESSIONS = 200
SESSION_ID = re.compile(r"[0-9a-f]{32}")
GEMINI_MODELS = [os.getenv("TIMETABLE_MODEL", "gemini-4-hydrogen"), "gemini-4-hydrogen", "gemini-3.8-flash"]
TIMETABLE_PROMPT = """이 이미지는 대학생의 에브리타임 시간표야. 수업만 JSON 하나로 옮겨 적어(설명·코드블록 금지).
{"classes":[{"day":0,"start":"HH:MM","end":"HH:MM","name":str}]}  day는 월=0 … 일=6.
이미지에 없는 수업을 지어내지 마."""
PROFILE_FIELDS = ("major", "busy", "exp", "team", "school", "region", "status", "age", "goal")
log = logging.getLogger("fourdo")


def card_model() -> str:
    return os.getenv("CARD_MODEL") or os.environ["LLM_MODEL"]


def plan_complete():
    """할 일 배치용 LLM. PLAN_MODEL=code이거나 키가 없으면 LLM 없이 코드로 배치한다."""
    model = os.getenv("PLAN_MODEL") or card_model()
    if model == "code" or not os.getenv("OPENAI_API_KEY"):
        return None
    return openai_complete(OpenAI(), model)


def read_source(text: str) -> str:
    text = text.replace("\r\n", "\n").strip()  # 브라우저는 줄바꿈을 CRLF로 보낸다. 같은 원문이면 같은 카드를 쓰도록 맞춘다
    if text.startswith(("http://", "https://")) and " " not in text:
        return read_url(Fetcher(), text)
    return text


def notice_card(text: str, today) -> dict:
    """원문이 같으면 저장해 둔 카드를 쓴다(같은 공고에는 같은 선택지). 모델이나 프롬프트가 바뀌면 다시 만든다."""
    source = read_source(text)
    key = hashlib.sha256(f"{card_model()}\n{CARD_VERSION}\n{source}".encode()).hexdigest()[:20]
    path = CARD_CACHE / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    card, _ = extract_card(OpenAI(), card_model(), source, today)
    CARD_CACHE.mkdir(exist_ok=True)
    path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    return card


# ---------- 시간표 이미지 → 수업 (읽기만 하는 LLM) ----------

def _parse_classes(raw: str) -> list[dict]:
    return adapter.clean_classes(json.loads(raw[raw.index("{"): raw.rindex("}") + 1]).get("classes", []))


def _gemini_classes(image: bytes, mime: str) -> list[dict] | None:
    from google import genai
    from google.genai import types

    client = genai.Client()
    for model in dict.fromkeys(GEMINI_MODELS):
        try:
            resp = client.models.generate_content(
                model=model, contents=[types.Part.from_bytes(data=image, mime_type=mime), TIMETABLE_PROMPT],
                config=types.GenerateContentConfig(response_mime_type="application/json", max_output_tokens=4096))
            return _parse_classes(resp.text or "")
        except Exception as error:  # 다음 모델로
            log.warning("timetable %s: %s", model, error)
    return None


def _vision_classes(image: bytes, mime: str) -> list[dict] | None:
    """공고 카드와 같은 제공사(OpenAI 호환)의 이미지 입력으로 읽는다."""
    model = os.getenv("TIMETABLE_VISION_MODEL") or card_model()
    url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
    try:
        resp = OpenAI().chat.completions.create(
            model=model, temperature=0, max_tokens=4096,
            messages=[{"role": "user", "content": [{"type": "text", "text": TIMETABLE_PROMPT},
                                                   {"type": "image_url", "image_url": {"url": url}}]}])
        return _parse_classes(resp.choices[0].message.content or "")
    except Exception as error:
        log.warning("timetable %s: %s", model, error)
        return None


def timetable_classes(image: bytes, mime: str) -> list[dict] | None:
    """시간표 이미지에서 수업만 추출한다. Gemini 키가 있으면 Gemini로, 없거나 실패하면 카드 모델로. 못 읽으면 None."""
    classes = _gemini_classes(image, mime) if os.getenv("GEMINI_API_KEY") else None
    return classes if classes is not None else _vision_classes(image, mime)


# ---------- 분석 기록 ----------
# 분석 한 번의 재료(카드, 입력한 프로필, 답변, 고른 방법, 진행)를 파일로 보관한다.
# 서버를 다시 켜도 재판정·다시 짜기·기록 다시 열기가 된다. 데모용 로컬 보관이라 커밋하지 않는다.

def save(analysis_id: str, state: dict) -> None:
    SESSION_DIR.mkdir(exist_ok=True)
    (SESSION_DIR / f"{analysis_id}.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def remember(state: dict) -> str:
    analysis_id = uuid.uuid4().hex
    save(analysis_id, state)
    files = sorted(SESSION_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
    for old in files[:-MAX_SESSIONS]:
        old.unlink(missing_ok=True)
    return analysis_id


def load(analysis_id) -> dict | None:
    if not isinstance(analysis_id, str) or not SESSION_ID.fullmatch(analysis_id):
        return None
    path = SESSION_DIR / f"{analysis_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def respond(analysis_id: str, state: dict):
    """카드 + 프로필 + 답변 + 고른 방법 + 진행 → 전략(코드)과 할 일 배치(LLM + 코드 검사)를 오늘 기준으로 계산해 화면 JSON으로."""
    today = datetime.now(KST).date()
    card = state["card"]
    profile, assumed = adapter.to_profile(state["form"], date.fromisoformat(state["created"]), state["classes"])
    strategy = choose(card, profile, today, state["answers"] or None)
    spent = {date.fromisoformat(day): hours for day, hours in state.get("spent", {}).items()}
    plans = state.setdefault("plans", {})
    known = set(plans)
    roadmap, planner = plan(strategy, profile, state["progress"] or None, spent, complete=plan_complete(), cache=plans)
    if set(plans) != known:  # 새로 받은 배치를 저장해 두고 다시 열 때 쓴다
        while len(plans) > 20:
            plans.pop(next(iter(plans)))
        save(analysis_id, state)
    return jsonify(adapter.to_response(
        card, strategy, unpersonalized(card, today), roadmap, profile, today, planner=planner,
        classes=state["classes"] or [], timetable=state["timetable"], assumed=assumed, spent=spent,
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


@app.get("/api/trust")
def trust():
    return jsonify(TRUST)


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
    form = {**{key: f.get(key, "") for key in PROFILE_FIELDS}, "hours": hours,
            "skills": json_list(f.get("skills")), "mate_skills": json_list(f.get("mate_skills"))}

    today = datetime.now(KST).date()
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

    state = {"card": card, "form": form, "created": today.isoformat(), "classes": classes,
             "timetable": "none" if not image else "ok" if classes is not None else "failed",
             "answers": {}, "progress": {}, "spent": {}}
    return respond(remember(state), state)


def session_or_error(analysis_id):
    state = load(analysis_id)
    if state is None:
        return None, (jsonify(error="분석 기록을 찾지 못했어요. 다시 분석해 주세요."), 410)
    return state, None


@app.get("/api/session/<analysis_id>")
def session(analysis_id):
    """내 도전 기록에서 다시 열기: 저장해 둔 재료로 오늘 기준 일정을 다시 계산한다(LLM 없음)."""
    state, error = session_or_error(analysis_id)
    return error or respond(analysis_id, state)


@app.post("/api/rejudge")
def rejudge():
    """자격 확인 질문에 예/아니오로 답하면 그 답을 프로필보다 우선해 다시 판정한다(LLM 없음)."""
    body = request.get_json(silent=True) or {}
    state, error = session_or_error(body.get("id"))
    if error:
        return error
    state["answers"] = {str(q): bool(a) for q, a in (body.get("answers") or {}).items() if a is not None}
    save(body["id"], state)
    return respond(body["id"], state)


@app.post("/api/replan")
def replan():
    """하루별 할 일에서 체크한 단계의 시간을 빼고 남은 일을 다시 배치한다(LLM 없음).
    done: [[단계 키, 시간], ...]  단계 키는 '제출물 / 단계'(roadmap.step_key).
    체크한 시간은 오늘 작업한 것으로 보고 오늘 쓸 수 있는 시간에서 뺀다."""
    body = request.get_json(silent=True) or {}
    state, error = session_or_error(body.get("id"))
    if error:
        return error
    today = datetime.now(KST).date().isoformat()
    spent = state.setdefault("spent", {})
    for item in body.get("done") or []:
        try:
            key, hours = item
            hours = max(0.0, float(hours))
        except (TypeError, ValueError):
            continue
        state["progress"][str(key)] = state["progress"].get(str(key), 0.0) + hours
        spent[today] = spent.get(today, 0.0) + hours
    save(body["id"], state)
    return respond(body["id"], state)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
