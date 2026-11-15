"""FourDo 백엔드 (Flask + Claude). 실행: python app.py → http://localhost:5000"""
import base64, datetime as dt, json, os, re
import requests
from anthropic import Anthropic
from flask import Flask, jsonify, request

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
client = Anthropic()  # ANTHROPIC_API_KEY 환경변수 사용
MODEL = os.getenv("MODEL", "claude-sonnet-4-6")  
KST = dt.timezone(dt.timedelta(hours=9))
DAYS = "월화수목금토일"
IMG_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

SYSTEM = """너는 대학생의 공모전·대외활동 준비를 돕는 분석가야. 공고와 학생 프로필을 읽고 JSON 하나만 출력해(설명·코드블록 금지).
공고에 없는 내용은 지어내지 말고 status를 "needs_check"로 해. 모든 문장은 한국어 해요체.
{
 "posting":{"title":str,"organizer":str,"deadline":"ISO8601(+09:00) 또는 null"},
 "requirements":[{"label":str,"criterion":"학생 상황과 비교한 한 줄","status":"met|caution|lacking|needs_check"}],
 "time":{"min":int,"max":int},            // 준비에 필요한 총 시간
 "readiness":{"label":"짧은 한 줄","reasons":[str,str]},
 "thisWeek":{"title":str,"hours":number,"note":str},
 "blockers":[{"d":"막는 요소","s":"해결 방법"}],
 "weeks":[{"w":1,"label":str,"rest":bool,"tasks":[[str,hours]]}],   // 최대 6주, 시험·바쁜 주는 rest
 "fallbacks":[str],
 "compare":{"generic":[str,str,str],"genericNote":str,"mine":[[str,str]],"mineNote":str},
 "classes":[{"day":0,"start":"HH:MM","end":"HH:MM","name":str}],     // 시간표 이미지가 있을 때만, 월=0
 "todos":[{"date":"YYYY-MM-DD","start":"HH:MM","end":"HH:MM","title":str,"hours":number}]  // 오늘 이후 7일, 수업과 겹치지 않게
}"""

def hm(t): h, m = t.split(":"); return int(h) * 60 + int(m)

def read_posting(text):
    text = text.strip()
    if re.match(r"https?://\S+$", text):
        try:
            html = requests.get(text, timeout=10, headers={"User-Agent": "Mozilla/5.0"}).text
        except requests.RequestException:
            raise ValueError("링크를 읽지 못했어요. 모집요강을 직접 붙여넣어 주세요.")
        html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
        text = re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html)).strip()
        if len(text) < 200:
            raise ValueError("링크에서 내용을 읽지 못했어요. 모집요강을 직접 붙여넣어 주세요.")
    return text[:12000]

def parse_json(raw):
    return json.loads(raw[raw.index("{"): raw.rindex("}") + 1])

@app.get("/")
def index(): return app.send_static_file("index.html")

@app.get("/api/health")
def health(): return {"ok": True}

@app.post("/api/analyze")
def analyze():
    f = request.form
    try:
        posting = read_posting(f.get("posting", ""))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    if len(posting) < 20:
        return jsonify(error="공고 링크 또는 모집요강을 입력해 주세요."), 400
    try: hours = min(60, max(1, int(f.get("hours", 12))))
    except ValueError: hours = 12
    img = request.files.get("timetable")
    if img and img.mimetype not in IMG_TYPES:
        return jsonify(error="시간표는 PNG, JPG 이미지만 올릴 수 있어요."), 400

    now = dt.datetime.now(KST)
    profile = (f"오늘: {now:%Y-%m-%d} ({DAYS[now.weekday()]})\n학년·전공: {f.get('major','')}\n주당 투자 가능 시간: {hours}시간\n"
               f"바쁜 일정: {f.get('busy','')}\n보유 역량: {f.get('skills','[]')}\n관련 경험: {f.get('exp','')}\n팀 구성: {f.get('team','')}")
    content = []
    if img:
        content.append({"type": "image", "source": {"type": "base64", "media_type": img.mimetype,
                        "data": base64.b64encode(img.read()).decode()}})
        content.append({"type": "text", "text": "위 이미지는 학생의 에브리타임 시간표야. classes에 수업을 추출해."})
    content.append({"type": "text", "text": f"[공고]\n{posting}\n\n[학생 프로필]\n{profile}"})

    try:
        resp = client.messages.create(model=MODEL, max_tokens=4000, system=SYSTEM,
                                      messages=[{"role": "user", "content": content}])
        ai = parse_json(resp.content[0].text)
    except Exception as e:  # API 오류·JSON 파싱 실패
        app.logger.exception(e)
        return jsonify(error="분석에 실패했어요. 잠시 후 다시 시도해 주세요."), 502
    return jsonify(build(ai, now, hours, bool(img)))

def build(ai, now, hours, has_tt):
    """AI 추출값을 코드로 검증·계산: D-day, 가용 시간, 하루별 빈 시간, 수업과 겹치는 할 일 제거"""
    DAY_MAP = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6,
           "0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6,
           "월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
    p = ai.get("posting", {})
    t = ai.get("time", {"min": 0, "max": 0})
    weeks_left = 1
    try:
        dl = dt.datetime.fromisoformat(p["deadline"])
        weeks_left = max(1, (dl - now).days // 7)
    except (KeyError, TypeError, ValueError):
        p["deadline"] = None
    classes = ai.get("classes", []) if has_tt else []
    todos = ai.get("todos", [])
    daily = []
    for i in range(1, 8):
        day = now + dt.timedelta(days=i)
        cls = sorted(
            (c for c in classes if DAY_MAP.get(c.get("day")) == day.weekday()),
            key=lambda c: c.get("start", "99:99")
        )
        busy = sum(hm(c["end"]) - hm(c["start"]) for c in cls) / 60
        mine = []
        for td in todos:
            if td.get("date") != f"{day:%Y-%m-%d}": continue
            s, e = hm(td["start"]), hm(td["end"])
            if any(s < hm(c["end"]) and hm(c["start"]) < e for c in cls): continue
            mine.append([f"{td['start']}–{td['end']}", td["title"], td["hours"]])
        daily.append({"d": DAYS[day.weekday()], "date": f"{day.month}/{day.day}", "free": max(0, round(12 - busy)),
                      "classes": [[f"{c['start']}–{c['end']}", c["name"]] for c in cls], "todos": mine})
    return {"meta": {"is_demo": False, "timetable": has_tt}, "posting": p,
            "requirements": [{**r, "source": "ai"} for r in ai.get("requirements", [])],
            "readiness": {**ai.get("readiness", {}), "disclaimer": "준비도는 합격 확률이 아니에요."},
            "time": {"min": t["min"], "max": t["max"], "available": hours * weeks_left},
            "thisWeek": ai.get("thisWeek", {}), "blockers": ai.get("blockers", []),
            "weeks": ai.get("weeks", []), "fallbacks": ai.get("fallbacks", []),
            "compare": ai.get("compare", {}), "trust": [], "daily": daily}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
