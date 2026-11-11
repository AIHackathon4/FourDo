"""PODO 데모: 공고를 가져와 구조화하고, 내 프로필로 참여 가능성과 준비 일정을 본다.

실행: uv run streamlit run demo/app.py
"""

import json
import os
from html import escape
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

import style
from aihack4.collector import intake
from aihack4.collector.fetcher import Fetcher
from aihack4.feasibility import EXPERIENCES, GOALS, LEVELS, TEAM_MODES, Period, Profile, judge

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data/contests/contests.db"
load_dotenv(ROOT / ".env")

st.set_page_config(page_title="PODO · 포기 말고 도전", page_icon="🍇", layout="centered")
st.markdown(style.CSS, unsafe_allow_html=True)

FIELD_LABELS = {"deadline": "마감", "eligibility": "참가 자격", "team": "팀 조건", "submissions": "제출물"}
UNDERGRAD = {"yes": "가능", "no": "불가", "unknown": "명시 없음"}
SKILL_OPTIONS = [
    "기획서 작성", "발표·PPT", "디자인(Figma)", "영상 편집", "사진 촬영", "글쓰기", "Python", "데이터 분석",
    "웹 개발", "앱 개발", "AI·머신러닝", "마케팅", "외국어",
]


@st.cache_resource
def llm() -> tuple[OpenAI, str]:
    return OpenAI(), os.environ["LLM_MODEL"]


# ---------- 프로필 ----------

REASONS = ["시험", "여행", "알바", "행사", "기타"]


def segmented(label: str, options, default: str, key: str, collapsed: bool = False) -> str:
    visibility = "collapsed" if collapsed else "visible"
    return st.segmented_control(label, options, default=default, key=key, label_visibility=visibility) or default


def skill_picker(key: str) -> list[str]:
    """자주 쓰는 역량은 칩으로 고르고, 목록에 없는 역량은 입력칸에 써서 엔터로 추가한다."""
    custom = st.session_state.setdefault(f"{key}-custom", [])

    def add() -> None:
        name = st.session_state[f"{key}-new"].strip()
        if name and name not in SKILL_OPTIONS and name not in custom:
            custom.append(name)
        if name:
            st.session_state[key] = list(dict.fromkeys([*st.session_state.get(key, []), name]))
        st.session_state[f"{key}-new"] = ""

    chosen = st.pills("역량", [*SKILL_OPTIONS, *custom], selection_mode="multi", key=key, label_visibility="collapsed")
    st.text_input("직접 추가", key=f"{key}-new", on_change=add, placeholder="+ 목록에 없으면 입력 후 엔터",
                  label_visibility="collapsed")
    return chosen or []


def period_text(period: Period) -> str:
    short = lambda day: f"{day.month}/{day.day}"
    span = short(period.start) if period.start == period.end else f"{short(period.start)} ~ {short(period.end)}"
    return f"{span} {period.reason}".strip()


def blocked_editor() -> list[Period]:
    periods: list[Period] = st.session_state.setdefault("blocked", [])
    form = st.session_state.setdefault("period-form", 0)  # 추가할 때마다 입력칸을 새로 만든다

    def add() -> None:
        picked = st.session_state.get(f"period-{form}") or ()
        st.session_state["period-missing"] = not picked
        if picked:
            periods.append(Period(picked[0], picked[-1], st.session_state.get(f"reason-{form}") or ""))
            periods.sort(key=lambda period: period.start)
            st.session_state["period-form"] += 1

    def remove(index: int) -> None:
        periods.pop(index)

    st.markdown("**못 쓰는 날**")
    if not periods:
        st.caption("시험·여행처럼 작업할 수 없는 날은 빼고 계산해요.")
    for index, period in enumerate(periods):
        text, delete = st.columns([6, 1], vertical_alignment="center")
        text.markdown(f'<span class="podo-chip">📅 {escape(period_text(period))}</span>', unsafe_allow_html=True)
        delete.button("✕", key=f"remove-{index}-{period.start}", type="tertiary", on_click=remove, args=(index,))

    with st.popover("＋ 못 쓰는 날 추가", width="stretch"):
        st.date_input("날짜 (시작일 → 끝나는 날 순서로 누르기, 하루면 한 번)", value=(), min_value=date.today(),
                      format="YYYY.MM.DD", key=f"period-{form}")
        st.pills("사유", REASONS, default="시험", key=f"reason-{form}")
        st.button("추가하기", type="primary", on_click=add, width="stretch", key=f"add-{form}")
        if st.session_state.get("period-missing"):
            st.caption("날짜를 먼저 골라 주세요.")
    return list(periods)


def profile_sidebar() -> Profile:
    with st.sidebar:
        st.markdown("## 🍇 내 프로필")
        st.caption("판정에만 쓰고 저장하지 않아요.")

        with st.container(border=True):
            st.markdown("**⏰ 쓸 수 있는 시간**")
            weekday_hours = st.slider("평일 하루", 0.0, 8.0, 2.0, 0.5, format="%g시간")
            weekend_hours = st.slider("주말 하루", 0.0, 12.0, 4.0, 0.5, format="%g시간")
            st.caption(f"일주일에 약 {weekday_hours * 5 + weekend_hours * 2:g}시간")
            blocked = blocked_editor()

        with st.container(border=True):
            st.markdown("**🛠 할 수 있는 것**")
            names = skill_picker("skills")
            skills = {}
            if names:
                st.caption("입문: 해 봄 · 가능: 혼자 완성 · 능숙: 가르칠 수 있음")
                for name in names:
                    skills[name] = segmented(name, LEVELS, "가능", f"level-{name}")

        with st.container(border=True):
            st.markdown("**👥 팀**")
            team = segmented("팀 상황", TEAM_MODES, "혼자", "team", collapsed=True)
            team_size, teammate_skills = 1, []
            if team == "팀 있음":
                team_size = st.number_input("팀 인원(나 포함)", 2, 10, 3)
                st.caption("팀원이 할 수 있는 것")
                teammate_skills = st.pills("팀원 역량", SKILL_OPTIONS, selection_mode="multi", key="teammate-skills",
                                           label_visibility="collapsed") or []

        with st.expander("🎯 경험 · 목표"):
            experience = segmented("공모전 경험", EXPERIENCES, "처음", "experience")
            goal = segmented("이번 목표", GOALS, "경험 쌓기", "goal")
            portfolio = st.text_input("활용할 작업물", placeholder="예: 수업 프로젝트 앱, 사진 포트폴리오")

        with st.expander("🎓 기본 정보 (참가 자격 확인용)"):
            status = st.selectbox("신분", ["재학생", "휴학생", "졸업예정자", "대학원생", "졸업생"])
            grade = st.selectbox("학년", [1, 2, 3, 4, 5], index=2, format_func=lambda g: f"{g}학년" if g < 5 else "5학년 이상")
            school = st.text_input("학교", placeholder="예: 한국대학교")
            major = st.text_input("전공", placeholder="예: 컴퓨터공학과")
            region = st.text_input("거주 지역", placeholder="예: 서울")
            age = st.number_input("만 나이 (나이 제한 공고용, 0이면 비움)", 0, 99, 0)
            note = st.text_area("기타", placeholder="관심 분야, 하고 싶은 말", height=70)

    return Profile(status, grade, school.strip(), major.strip(), region.strip(), skills, experience, portfolio.strip(),
                   goal, team, int(team_size), teammate_skills, weekday_hours, weekend_hours, blocked, note.strip(),
                   int(age) or None)


# ---------- 공고 표시 ----------

def d_day(deadline: str | None) -> str:
    if not deadline:
        return "미확인"
    days = (date.fromisoformat(deadline) - date.today()).days
    if days < 0:
        return "마감됨"
    return "오늘 마감" if days == 0 else f"D-{days}"


def team_text(notice: dict) -> str:
    low, high = notice["team_min"], notice["team_max"]
    if low is None and high is None:
        return "명시 없음"
    if low == high == 1:
        return "개인"
    return f"{low or 1}~{high}명" if high else f"{low}명 이상"


def section(title: str, body_html: str) -> None:
    st.markdown(f'<div class="podo-section">{title}</div>{body_html}', unsafe_allow_html=True)


def show_notice(notice: dict, warnings: list[str], source: str | None) -> None:
    with st.container(border=True):
        st.subheader(notice["title"] or "(제목 없음)")
        caption = " · ".join(filter(None, [notice["organizer"], f"[원문 보기]({source})" if source else None]))
        if caption:
            st.caption(caption)
        deadline = notice["deadline"]
        columns = st.columns(3)
        columns[0].metric("마감", d_day(deadline), f"{deadline} {notice['deadline_time'] or ''}".strip() if deadline else None, delta_color="off", delta_arrow="off")
        columns[1].metric("대학생 참가", UNDERGRAD[notice["open_to_undergraduates"]])
        columns[2].metric("팀 구성", team_text(notice))
        for warning in warnings:
            st.warning(warning, icon="⚠️")

        left, right = st.columns(2)
        with left:
            section("참가 자격", style.chips(notice["eligibility"]))
            section("제출물", style.chips(notice["submissions"]))
        with right:
            section("공모 주제", style.chips(notice["topics"]))
            section("필요 역량", style.chips(notice["required_skills"]))
        section("시상", style.chips([notice["prize"]] if notice["prize"] else []))

        with st.expander(f"근거 문장 {len(notice['evidence'])}개 보기"):
            for item in notice["evidence"]:
                mark = "✅ 원문 확인" if item.get("found_in_source") else "⚠️ 원문과 불일치"
                st.markdown(f"**{FIELD_LABELS.get(item['field'], item['field'])}** · {mark}")
                st.markdown(f"> {item['quote']}")


def show_feasibility(notice: dict, profile: Profile, key: str) -> None:
    st.markdown("#### 🍇 나도 할 수 있을까?")
    results = st.session_state.setdefault("feasibility", {})
    answers = st.session_state.setdefault("answers", {}).setdefault(key, {})
    # 자격 조건은 공고마다 한 번만 추출한다(프로필이 바뀌어도 다시 부르지 않는다).
    conditions = st.session_state.setdefault("conditions", {})
    working = {**notice, "eligibility_conditions": conditions[key]} if key in conditions else dict(notice)
    cache_key = (key, repr(profile), repr(sorted(answers.items())))

    def run_judge() -> bool:
        client, model = llm()
        try:
            with st.spinner("공고 조건과 내 상황을 비교하는 중이에요."):
                results[cache_key] = judge(client, model, working, profile, answers=answers or None)
                conditions[key] = working["eligibility_conditions"]
            return True
        except Exception as error:  # 데모 화면이 멈추지 않도록 원인만 보여 준다
            st.error(f"판정 중 오류가 났어요: {type(error).__name__}")
            return False

    if st.button("내 프로필로 판정하기", type="primary", key=f"judge-{key}") and not run_judge():
        return
    result = results.get(cache_key)
    if result is None:
        if any(k[0] == key for k in results):
            st.caption("프로필이 바뀌었어요. 다시 판정해 주세요.")
        else:
            st.caption("왼쪽에 프로필을 채우고 판정을 눌러 보세요.")
        return

    st.markdown(style.verdict_card(result.verdict, result.summary, result.days_left), unsafe_allow_html=True)
    if result.tasks:
        columns = st.columns(3)
        columns[0].metric("필요한 시간", f"{result.need_min:.0f}~{result.target_hours:.0f}시간")
        columns[1].metric("쓸 수 있는 시간", f"{result.available_hours:.0f}시간", f"작업일 {result.work_days}일",
                          delta_color="off", delta_arrow="off")
        columns[2].metric("여유", f"{result.available_hours - result.target_hours:+.0f}시간")

    left, right = st.columns([1.1, 1])
    with left:
        with st.container(border=True):
            section("조건 확인", style.check_list(result.checks))
        if result.questions:
            with st.container(border=True):
                st.markdown("**참가 자격 확인**")
                st.caption("공고 조건 중 프로필만으로 확인할 수 없는 것이에요. 답하면 다시 판정해요.")
                picked = {}
                for index, question in enumerate(result.questions):
                    choice = st.radio(question, ["예", "아니오", "모르겠어요"], index=2, horizontal=True,
                                      key=f"q-{key}-{index}")
                    if choice != "모르겠어요":
                        picked[question] = choice == "예"
                if st.button("답변 반영해서 다시 판정", key=f"answer-{key}", disabled=not picked):
                    answers.update(picked)
                    cache_key = (key, repr(profile), repr(sorted(answers.items())))
                    if run_judge():
                        st.rerun()
        if result.gaps:
            with st.container(border=True):
                section("더 필요한 것", style.bullet_list(result.gaps, "gap"))
    with right:
        if result.this_week:
            with st.container(border=True):
                section("이번 주에 할 일", style.bullet_list(result.this_week, "todo"))
        if result.tips:
            with st.container(border=True):
                section("이렇게 하면 더 좋아요", style.bullet_list(result.tips))

    if result.tasks:
        with st.container(border=True):
            section("남은 기간 실행 계획", style.plan_timeline(result.tasks))
            st.caption("작업 시간은 AI 추정치예요. 부족한 역량은 1.5~2배, 첫 공모전은 1.2배로 여유를 두고, "
                       "못 쓰는 기간은 건너뛰어 배치했어요.")


# ---------- 탭: 공고 가져오기 ----------

def run_intake(kind: str, value) -> None:
    client, model = llm()
    try:
        with st.spinner("공고를 읽는 중이에요. 첨부파일이 있으면 30초쯤 걸려요."):
            if kind == "링크":
                result = intake.intake_url(client, model, Fetcher(), value.strip())
            elif kind == "파일":
                result = intake.intake_file(client, model, value.getvalue(), value.name)
            else:
                result = intake.intake_text(client, model, value)
    except intake.IntakeError as error:
        st.session_state.pop("intake", None)
        st.error(str(error))
        return
    except Exception as error:  # 데모 화면이 멈추지 않도록 원인만 보여 준다
        st.session_state.pop("intake", None)
        st.error(f"처리 중 오류가 났어요: {type(error).__name__}")
        return
    st.session_state["intake"] = result


def intake_tab(profile: Profile) -> None:
    st.markdown("어디서 본 공고든 가져오세요. 조건과 준비물을 정리하고, 내가 할 수 있는지 알려 드려요.")
    kind = st.radio("가져오는 방법", ["링크", "파일", "텍스트"], horizontal=True, label_visibility="collapsed")
    if kind == "링크":
        value = st.text_input("공고 링크", placeholder="https://", label_visibility="collapsed")
    elif kind == "파일":
        value = st.file_uploader("공고문 파일 (PDF·HWP·HWPX·TXT)", type=["pdf", "hwp", "hwpx", "txt", "md"])
    else:
        value = st.text_area("공고 내용", height=200, placeholder="공고문을 복사해 붙여 넣어 주세요.", label_visibility="collapsed")

    if st.button("공고 분석하기", type="primary", disabled=not value):
        run_intake(kind, value)

    result = st.session_state.get("intake")
    if result:
        source = result.listing.url if result.listing.url.startswith("http") else None
        show_notice(result.agent.notice, result.warnings, source)
        documents = result.agent.documents
        if documents:
            attachments = sum(doc.kind == "attachment" for doc in documents)
            st.caption(f"읽은 자료 {len(documents)}개 (첨부 {attachments}개)")
        show_feasibility(result.agent.notice, profile, f"intake-{result.listing.source_id}")


# ---------- 탭: 수집된 공고 ----------

@st.cache_data(ttl=60)
def load_contests() -> tuple[pd.DataFrame, dict]:
    connection = sqlite3.connect(DB_PATH)
    frame = pd.read_sql("SELECT * FROM open_contests ORDER BY end_date", connection)
    stats = dict(connection.execute("SELECT status, COUNT(*) FROM contests GROUP BY status").fetchall())
    stats["searches"] = connection.execute("SELECT COUNT(*) FROM search_log").fetchone()[0]
    connection.close()
    return frame, stats


def collected_tab(profile: Profile) -> None:
    if not DB_PATH.exists():
        st.info("아직 수집된 공고가 없어요. scripts/collect_contests.py collect 를 먼저 실행해 주세요.")
        return
    frame, stats = load_contests()
    st.markdown("PODO 수집 에이전트가 공공기관·주최 측 공식 페이지에서 찾은, 지금 모집 중인 공고예요.")
    columns = st.columns(3)
    columns[0].metric("모집 중", f"{len(frame)}건")
    columns[1].metric("검토한 페이지", f"{sum(v for k, v in stats.items() if k != 'searches')}개")
    columns[2].metric("웹 검색", f"{stats['searches']}회")
    if frame.empty:
        return

    table = frame.assign(남은기간=frame["end_date"].map(d_day))[["title", "organizer", "end_date", "남은기간", "url"]]
    st.dataframe(
        table.rename(columns={"title": "공고", "organizer": "주최", "end_date": "마감일"}),
        hide_index=True,
        column_config={"url": st.column_config.LinkColumn("원문", display_text="열기")},
        width="stretch",
    )
    choice = st.selectbox("자세히 볼 공고", frame.index, format_func=lambda i: frame.at[i, "title"])
    row = frame.loc[choice]
    notice = json.loads(row["notice"])
    show_notice(notice, [], row["url"])
    show_feasibility(notice, profile, f"db-{row['source_id']}")
    st.caption("수집 공고는 공고명·마감일 같은 사실 정보만 저장하고, 원문은 링크로 연결해요.")


profile = profile_sidebar()
st.markdown(style.hero(), unsafe_allow_html=True)
tab_intake, tab_collected = st.tabs(["공고 가져오기", "수집된 공고"])
with tab_intake:
    intake_tab(profile)
with tab_collected:
    collected_tab(profile)
