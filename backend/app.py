"""FourDo 백엔드 (Flask + Gemini). 실행: python app.py → http://localhost:5000"""
import datetime as dt, json, os, re, sqlite3, time
from functools import wraps
import requests
from flask import Flask, g, jsonify, request, session
from google import genai
from google.genai import types
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-only-change-me"),  # 배포 시 반드시 환경변수로 교체
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE") == "1",  # https 배포 시 COOKIE_SECURE=1
    PERMANENT_SESSION_LIFETIME=dt.timedelta(days=30),
)
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fourdo.db"))
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MODEL = os.getenv("MODEL", "gemini-4-hydrogen")
KST = dt.timezone(dt.timedelta(hours=9))
DAYS = "월화수목금토일"
IMG_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

SYSTEM = """너는 대학생의 공모전·대외활동 준비를 돕는 분석가야. 공고와 학생 프로필을 읽고 JSON 하나만 출력해(설명·코드블록 금지).
공고에 없는 내용은 지어내지 말고 status를 "needs_check"로 해. 모든 문장은 한국어 해요체.
{
 "posting":{"title":str,"organizer":str,"deadline":"ISO8601(+09:00) 또는 null"},
 "requirements":[{"label":str,"criterion":"학생 상황과 비교한 한 줄","status":"met|caution|lacking|needs_check"}],
 "time":{"min":int,"max":int},
 "readiness":{"label":"짧은 한 줄","reasons":[str,str]},
 "thisWeek":{"title":str,"hours":number,"note":str},
 "blockers":[{"d":"막는 요소","s":"해결 방법"}],
 "weeks":[{"w":1,"label":str,"rest":bool,"tasks":[[str,hours]]}],
 "fallbacks":[str],
 "compare":{"generic":[str,str,str],"genericNote":str,"mine":[[str,str]],"mineNote":str},
 "classes":[{"day":0,"start":"HH:MM","end":"HH:MM","name":str}],
 "todos":[{"date":"YYYY-MM-DD","start":"HH:MM","end":"HH:MM","title":str,"hours":number}]
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
    if not raw or "{" not in raw:
        raise ValueError(f"AI 응답에 JSON이 없어요: {raw!r}")
    return json.loads(raw[raw.index("{"): raw.rindex("}") + 1])

# ---------- DB ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(_):
    c = g.pop("db", None)
    if c is not None:
        c.close()

def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
          pw_hash TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS analyses(
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          title TEXT, organizer TEXT, deadline TEXT, result TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_analyses_user ON analyses(user_id, id DESC);
        """)

init_db()

def now_utc(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **k):
        if not session.get("uid"):
            return jsonify(error="로그인이 필요해요."), 401
        return fn(*a, **k)
    return wrapper

def public_user(row): return {"id": row["id"], "name": row["name"], "email": row["email"]}

def save_analysis(uid, result):
    p = result.get("posting") or {}
    result.setdefault("meta", {})
    cur = db().execute(
        "INSERT INTO analyses(user_id,title,organizer,deadline,result,created_at) VALUES(?,?,?,?,?,?)",
        (uid, str(p.get("title") or "제목 없는 공고")[:200], str(p.get("organizer") or "")[:200],
         p.get("deadline"), "{}", now_utc()))
    aid = cur.lastrowid
    result["meta"]["saved_id"] = aid
    db().execute("UPDATE analyses SET result=? WHERE id=?", (json.dumps(result, ensure_ascii=False), aid))
    db().commit()
    return aid

# ---------- 계정 ----------
@app.post("/api/signup")
def signup():
    j = request.get_json(silent=True) or {}
    email, name, pw = str(j.get("email", "")).strip().lower(), str(j.get("name", "")).strip(), str(j.get("password", ""))
    if not EMAIL_RE.match(email) or len(email) > 120:
        return jsonify(error="이메일 형식을 확인해 주세요."), 400
    if not 1 <= len(name) <= 20:
        return jsonify(error="이름은 1~20자로 입력해 주세요."), 400
    if len(pw) < 8 or len(pw) > 100:
        return jsonify(error="비밀번호는 8자 이상으로 입력해 주세요."), 400
    try:
        cur = db().execute("INSERT INTO users(email,name,pw_hash,created_at) VALUES(?,?,?,?)",
                           (email, name, generate_password_hash(pw), now_utc()))
        db().commit()
    except sqlite3.IntegrityError:
        return jsonify(error="이미 가입된 이메일이에요. 로그인해 주세요."), 409
    session.clear(); session.permanent = True; session["uid"] = cur.lastrowid
    return jsonify(user={"id": cur.lastrowid, "name": name, "email": email}), 201

@app.post("/api/login")
def login():
    j = request.get_json(silent=True) or {}
    email, pw = str(j.get("email", "")).strip().lower(), str(j.get("password", ""))
    row = db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not row or not check_password_hash(row["pw_hash"], pw):
        return jsonify(error="이메일 또는 비밀번호가 맞지 않아요."), 401
    session.clear(); session.permanent = True; session["uid"] = row["id"]
    return jsonify(user=public_user(row))

@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)

@app.get("/api/me")
def me():
    uid = session.get("uid")
    row = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone() if uid else None
    if uid and not row: session.clear()
    return jsonify(user=public_user(row) if row else None)

# ---------- 내 도전 기록 ----------
@app.get("/api/history")
@login_required
def history_list():
    rows = db().execute("SELECT id,title,organizer,deadline,result,created_at FROM analyses "
                        "WHERE user_id=? ORDER BY id DESC LIMIT 100", (session["uid"],)).fetchall()
    items = []
    for r in rows:
        try: label = json.loads(r["result"]).get("readiness", {}).get("label", "")
        except ValueError: label = ""
        items.append({"id": r["id"], "title": r["title"], "organizer": r["organizer"],
                      "deadline": r["deadline"], "readiness": label, "created_at": r["created_at"]})
    return jsonify(items=items)

@app.get("/api/history/<int:aid>")
@login_required
def history_get(aid):
    r = db().execute("SELECT result FROM analyses WHERE id=? AND user_id=?", (aid, session["uid"])).fetchone()
    if not r: return jsonify(error="기록을 찾지 못했어요."), 404
    return app.response_class(r["result"], mimetype="application/json")

@app.post("/api/history")
@login_required
def history_save():
    """로그인 전에 분석한 결과를 로그인 후 저장할 때 사용"""
    res = (request.get_json(silent=True) or {}).get("result")
    if not isinstance(res, dict) or not isinstance(res.get("posting"), dict) or res.get("meta", {}).get("is_demo"):
        return jsonify(error="저장할 수 있는 분석 결과가 아니에요."), 400
    return jsonify(id=save_analysis(session["uid"], res)), 201

@app.delete("/api/history/<int:aid>")
@login_required
def history_delete(aid):
    c = db().execute("DELETE FROM analyses WHERE id=? AND user_id=?", (aid, session["uid"]))
    db().commit()
    return (jsonify(ok=True), 200) if c.rowcount else (jsonify(error="기록을 찾지 못했어요."), 404)

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
    contents = []
    if img:
        contents.append(types.Part.from_bytes(data=img.read(), mime_type=img.mimetype))
        contents.append("위 이미지는 학생의 에브리타임 시간표야. classes에 수업을 추출해.")
    contents.append(f"[공고]\n{posting}\n\n[학생 프로필]\n{profile}")

    try:
        client = genai.Client()  # GEMINI_API_KEY 환경변수 자동 사용
        ai = None
        last_err = None
        for m in dict.fromkeys([MODEL, "gemini-4-hydrogen", "gemini-3.8-flash"]):
            for _ in range(2):
                try:
                    resp = client.models.generate_content(
                        model=m,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM,
                            response_mime_type="application/json",
                            max_output_tokens=16384,
                            thinking_config=types.ThinkingConfig(thinking_level="LOW"),
                        ),
                    )
                    ai = parse_json(resp.text)
                    break
                except Exception as err:
                    last_err = err
                    if "503" in str(err) or "UNAVAILABLE" in str(err):
                        time.sleep(1.5)
                    else:
                        break
            if ai is not None:
                break
        if ai is None:
            raise last_err
    except Exception as e:
        app.logger.exception(e)
        return jsonify(error="분석에 실패했어요. 잠시 후 다시 시도해 주세요."), 502
    result = build(ai, now, hours, bool(img))
    if session.get("uid"):  # 로그인 상태면 자동 저장
        save_analysis(session["uid"], result)
    return jsonify(result)

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
