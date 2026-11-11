"""PODO 데모의 포도 테마: 로고, CSS, 판정·일정용 HTML 조각."""

from html import escape

GRAPE_POSITIONS = [(30, 46), (50, 46), (70, 46), (90, 46), (40, 63), (60, 63), (80, 63), (50, 80), (70, 80), (60, 97)]

GRAPE_SVG = f"""<svg viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="포도">
<defs>
  <radialGradient id="berry" cx="35%" cy="30%" r="75%">
    <stop offset="0%" stop-color="#B58BE0"/><stop offset="55%" stop-color="#7B4DB5"/><stop offset="100%" stop-color="#4A2580"/>
  </radialGradient>
</defs>
<path d="M60 38 C60 28 62 20 68 12" stroke="#7A5230" stroke-width="5" fill="none" stroke-linecap="round"/>
<path d="M66 24 C76 10 98 12 104 22 C92 34 74 34 66 24 Z" fill="#7DBB57"/>
<path d="M68 24 C80 22 92 21 101 22" stroke="#5E9A3E" stroke-width="2" fill="none"/>
{''.join(f'<circle cx="{x}" cy="{y}" r="11" fill="url(#berry)"/><circle cx="{x - 4}" cy="{y - 4}" r="3" fill="#fff" opacity=".55"/>' for x, y in GRAPE_POSITIONS)}
</svg>"""

CSS = """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css');
:root {
  --grape: #6B3FA0; --grape-deep: #3E1F66; --grape-soft: #F2ECFA; --grape-line: #E2D6F3;
  --leaf: #5E9A3E; --leaf-soft: #EAF5E2; --amber: #B7791F; --amber-soft: #FDF3E1; --rose: #B83B5E; --rose-soft: #FBE9EE;
}
html, body, [class*="css"], .stMarkdown, button, input, textarea { font-family: 'Pretendard', -apple-system, sans-serif !important; }
.block-container { padding-top: 3.5rem; max-width: 820px; }
header[data-testid="stHeader"] { background: transparent; }

.podo-hero {
  display: flex; align-items: center; gap: 18px; padding: 22px 26px; border-radius: 22px; margin-bottom: 18px;
  background: linear-gradient(135deg, #3E1F66 0%, #6B3FA0 60%, #8C5CC7 100%); color: #fff;
  box-shadow: 0 10px 30px rgba(62, 31, 102, .25);
}
.podo-hero svg { width: 76px; height: 76px; flex: none; filter: drop-shadow(0 4px 8px rgba(0,0,0,.25)); }
.podo-hero h1 { margin: 0; padding: 0; color: #fff; font-size: 2.1rem; font-weight: 800; letter-spacing: .04em; }
.podo-hero p { margin: 4px 0 0; color: #E9DDFA; font-size: .98rem; }
.podo-hero b { color: #fff; }

[data-testid="stMetric"] {
  background: #fff; border: 1px solid var(--grape-line); border-radius: 16px; padding: 12px 16px;
}
[data-testid="stMetricLabel"] p { color: #7A6A93; font-weight: 600; }
[data-testid="stMetricValue"] { color: var(--grape-deep); font-size: 1.35rem; font-weight: 700; }
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 18px; }
.stButton button[kind="primary"] { border-radius: 999px; padding: 0 22px; font-weight: 700; }
[data-testid="stSidebar"] { background: var(--grape-soft); }
section[data-testid="stSidebar"][aria-expanded="true"] { width: 340px !important; min-width: 340px !important; }
[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] { background: #fff; }
[data-testid="stSidebar"] .podo-chip { font-size: .88rem; padding: 4px 12px; }
[data-testid="stButtonGroup"] button[aria-pressed="true"], [data-testid="stButtonGroup"] button[aria-checked="true"] {
  background: var(--grape) !important; border-color: var(--grape) !important; color: #fff !important; }
[data-testid="stButtonGroup"] button[aria-pressed="true"] *, [data-testid="stButtonGroup"] button[aria-checked="true"] * { color: #fff !important; }
[data-testid="stSidebar"] h2 { color: var(--grape-deep); }

.podo-chip { display: inline-block; padding: 3px 10px; margin: 2px 4px 2px 0; border-radius: 999px;
  background: var(--grape-soft); color: var(--grape-deep); font-size: .85rem; }
.podo-section { font-weight: 700; color: var(--grape-deep); margin: 10px 0 4px; }

.podo-verdict { border-radius: 18px; padding: 18px 22px; margin: 6px 0 14px; border: 1px solid; }
.podo-verdict .label { font-size: .8rem; font-weight: 700; letter-spacing: .08em; opacity: .8; }
.podo-verdict .title { font-size: 1.5rem; font-weight: 800; margin: 2px 0 6px; }
.podo-verdict.ok { background: var(--leaf-soft); border-color: #CFE6C0; color: #2F5A1C; }
.podo-verdict.prep { background: var(--grape-soft); border-color: var(--grape-line); color: var(--grape-deep); }
.podo-verdict.warn { background: var(--amber-soft); border-color: #F2DDB5; color: #7A4E10; }
.podo-verdict.fail { background: var(--rose-soft); border-color: #F2C9D4; color: #7E2140; }

.podo-check { display: flex; gap: 10px; padding: 8px 0; border-bottom: 1px dashed var(--grape-line); }
.podo-check:last-child { border-bottom: none; }
.podo-check .dot { flex: none; width: 22px; height: 22px; border-radius: 50%; color: #fff; font-size: .75rem;
  display: flex; align-items: center; justify-content: center; font-weight: 700; margin-top: 1px; }
.podo-check .dot.ok { background: var(--leaf); } .podo-check .dot.warn { background: var(--amber); } .podo-check .dot.fail { background: var(--rose); }
.podo-check .item { font-weight: 700; }

.podo-plan { position: relative; margin: 8px 0 4px 10px; padding-left: 26px; border-left: 3px solid var(--grape-line); }
.podo-step { position: relative; padding: 4px 0 14px; }
.podo-step::before { content: ""; position: absolute; left: -36px; top: 6px; width: 17px; height: 17px; border-radius: 50%;
  background: radial-gradient(circle at 35% 30%, #B58BE0, #7B4DB5 55%, #4A2580); box-shadow: 0 0 0 3px var(--grape-bg, #FBF9FE); }
.podo-step .date { font-size: .82rem; font-weight: 700; color: var(--grape); }
.podo-step .meta { font-size: .8rem; color: #7A6A93; }
.podo-step.final::before { background: radial-gradient(circle at 35% 30%, #A8D98A, #5E9A3E 60%, #3F6E27); }
.podo-owner { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: .72rem; font-weight: 700; margin-left: 6px; }
.podo-owner.본인, .podo-owner.팀원 { background: var(--leaf-soft); color: #2F5A1C; }
.podo-owner.수준 { background: var(--amber-soft); color: #7A4E10; }
.podo-owner.부족 { background: var(--rose-soft); color: #7E2140; }
.podo-list { margin: 4px 0 6px; padding-left: 0; list-style: none; }
.podo-list li { padding: 6px 0 6px 26px; position: relative; border-bottom: 1px dashed var(--grape-line); }
.podo-list li:last-child { border-bottom: none; }
.podo-list li::before { content: ""; position: absolute; left: 4px; top: 12px; width: 11px; height: 11px; border-radius: 50%;
  background: radial-gradient(circle at 35% 30%, #B58BE0, #7B4DB5 55%, #4A2580); }
.podo-list.todo li::before { border-radius: 4px; background: #fff; border: 2px solid var(--grape); width: 9px; height: 9px; }
.podo-list.gap li::before { background: radial-gradient(circle at 35% 30%, #F2A5BA, #B83B5E 60%, #7E2140); }
</style>
"""

STATUS_CLASS = {"참여 가능": "ok", "준비하면 가능": "prep", "확인 필요": "warn", "어려움": "fail"}
OWNER_LABEL = {"본인": ("본인", "본인"), "팀원": ("팀원", "팀원"), "수준 부족": ("실력 보강", "수준"), "부족": ("역량 부족", "부족")}
STATUS_MARK = {"ok": "✓", "warn": "!", "fail": "✕"}


def hero() -> str:
    return (
        f'<div class="podo-hero">{GRAPE_SVG}<div><h1>PODO</h1>'
        "<p><b>포</b>기 말고 <b>도</b>전 — 이 공모전, 나도 할 수 있을까?</p></div></div>"
    )


def chips(items: list[str]) -> str:
    if not items:
        return '<span style="color:#9A8DB0">공고에 명시 없음</span>'
    return "".join(f'<span class="podo-chip">{escape(item)}</span>' for item in items)


def verdict_card(verdict: str, summary: str, days_left: int | None) -> str:
    timing = "" if days_left is None else f" · 마감까지 {days_left}일"
    return (
        f'<div class="podo-verdict {STATUS_CLASS[verdict]}"><div class="label">PODO 판정{timing}</div>'
        f'<div class="title">{escape(verdict)}</div><div>{escape(summary)}</div></div>'
    )


def check_list(checks) -> str:
    rows = "".join(
        f'<div class="podo-check"><div class="dot {c.status}">{STATUS_MARK[c.status]}</div>'
        f'<div><span class="item">{escape(c.item)}</span> — {escape(c.detail)}</div></div>'
        for c in checks
    )
    return f"<div>{rows}</div>"


def bullet_list(items: list[str], kind: str = "") -> str:
    return f'<ul class="podo-list {kind}">' + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def _date_range(start: str | None, end: str | None) -> str:
    if not start:
        return "날짜 미정"
    short = lambda value: f"{int(value[5:7])}/{int(value[8:])}"
    return short(start) if start == end else f"{short(start)} ~ {short(end)}"


def plan_timeline(tasks) -> str:
    steps = []
    for i, task in enumerate(tasks):
        label = OWNER_LABEL.get(task.owner)
        badge = f'<span class="podo-owner {label[1]}">{label[0]}</span>' if label else ""
        skill = f" · {escape(task.skill)}" if task.skill else ""
        steps.append(
            f'<div class="podo-step{" final" if i == len(tasks) - 1 else ""}">'
            f'<div class="date">{_date_range(task.start, task.end)}</div>'
            f"<div><b>{escape(task.name)}</b>{badge}</div>"
            f'<div class="meta">약 {task.hours:g}시간{skill}</div></div>'
        )
    return f'<div class="podo-plan">{"".join(steps)}</div>'
