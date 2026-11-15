# PODO · 포기 말고 도전

**👉 데모 바로 써 보기: https://wgf85gecrgriwwejbvuqzr.streamlit.app/** (설치 없이 접속, 비밀번호는 팀에 문의)

공모전에 관심은 있지만 시간·역량·준비 부족으로 망설이는 대학생을 위해, 공모전 모집요강과 **내 일정·역량·준비 상태**를 비교해 현실적인 참여 가능성과 마감까지의 준비 계획을 알려주는 서비스입니다. (인공지능학과 대학 연합 해커톤)

- **LLM은 읽고 이해하는 일만** 합니다: 공고 구조화, 참가 자격 문구를 조건으로 옮기기(공고당 한 번), 제출물을 작업으로 나누고 소요 시간 추정, 계산된 결과를 조언 문장으로 풀어 쓰기
- **판단과 계산은 코드가** 합니다: 자격 조건과 프로필 비교, 마감까지 쓸 수 있는 시간, 판정(참여 가능 / 준비하면 가능 / 확인 필요 / 어려움), 일정 배치
- 프로필로 확인할 수 없는 자격 조건은 예/아니오 질문으로 묻고, 답하면 다시 판정합니다.

## 데모 실행

Python 3.10+와 [uv](https://docs.astral.sh/uv/)가 필요합니다.

```bash
uv sync
cp .env.example .env   # OPENAI_API_KEY, OPENAI_BASE_URL(OpenRouter 등), LLM_MODEL 입력
uv run streamlit run demo/app.py
```

1. 왼쪽에 프로필(쓸 수 있는 시간, 못 쓰는 기간, 역량, 팀)을 입력합니다.
2. **공고 가져오기** 탭에 공모전 링크·파일(PDF, HWP, HWPX)·본문을 넣습니다.
3. **내 프로필로 판정하기**를 누르면 판정, 부족한 점, 이번 주 할 일, 실행 계획이 나옵니다.

> 자동 수집한 공고 원문과 DB(`data/contests/`)는 수집처 약관 때문에 저장소에 올리지 않습니다. 그래서 **수집한 공고** 탭은 비어 있고, 공고는 링크로 직접 넣습니다.

## FourDo 웹 (프론트 + 백엔드)

`backend/static/`의 화면을 Flask 서버(`backend/app.py`)가 띄웁니다. 설계는 [docs/백엔드_설계_v3.md](docs/백엔드_설계_v3.md).

```bash
uv sync                     # 시간표를 Gemini로 읽으려면 uv sync --extra timetable
cp .env.example .env        # OPENAI_API_KEY, OPENAI_BASE_URL, LLM_MODEL, CARD_MODEL (+ GEMINI_API_KEY, 선택)
uv run python backend/app.py   # http://localhost:5000
```

**LLM은 선택지를 만들고, 코드가 고른다.**

| 단계 | 누가 | 하는 일 |
|---|---|---|
| 공고 카드 | LLM 1회(`CARD_MODEL`) | 기본 정보·자격 조건·평가 기준 + 제출물마다 만드는 방법 4~7개(완주형/완성형/수상 도전형, 강점별로 다른 길, 필요 역량·수준, 단계별 작업). 원문·모델·프롬프트가 같으면 저장한 카드를 다시 씀 |
| 맞춤 전략 | 코드(`strategy.py`) | 목표(완주/완성/수상 도전)와 본인·팀원 역량으로 제출물마다 방법 하나를 추천. 역량이 모자라면 할 수 있는 방법으로 대신하고, 시간이 모자라면 한 단계씩 낮춤. 학생이 고른 방법은 그대로 둠. 판정 |
| 할 일 배치 | LLM(`PLAN_MODEL`) + 코드 검사(`schedule.py`) | LLM이 날짜별로 배치(단계는 쪼개지 않고, 큰 작업은 시간 많은 날에). 코드가 못 쓰는 날·하루 시간·순서·누락·잘게 쪼개기를 검사해 어기면 한 번 다시 받고, 그래도 어기면 코드 배치(`roadmap.py`). 최종 점검·제출은 여유 기간에 코드가 넣음 |
| 화면 변환 | 코드(`backend/adapter.py`) | 전략 카드, 마감까지 하루별 할 일(시간표가 있으면 수업을 피해서), 주차별 전체 일정, 개인화 OFF/ON 비교, 계산에 쓴 가정 |
| 시간표 | LLM(선택) | 에브리타임 시간표 이미지에서 수업만 추출. `GEMINI_API_KEY`가 있으면 Gemini, 없으면 카드 모델의 이미지 입력. 못 읽으면 화면에 알리고 시간표 없이 진행 |

재판정(자격 질문에 답하기)과 기록 다시 열기는 카드와 저장해 둔 배치를 다시 써서 즉시 끝납니다.
방법 바꾸기와 다시 짜기(끝낸 일 체크)는 할 일이 바뀌므로 배치만 LLM을 다시 불러 몇 초 걸립니다.
분석 기록은 `backend/.sessions/`에 파일로 남아 서버를 다시 켜도 이어집니다(입력한 프로필이 들어 있어 커밋하지 않습니다).
가정한 값: 주당 시간은 평일 하루 : 주말 하루 = 1 : 2, 기획·글쓰기·발표 '입문'은 누구나, 팀원 역량은 '가능',
한 단계 모자란 역량은 시간 1.5배, 첫 참가는 1.2배, 가용의 85%를 넘게 쓰면 빠듯함, 여유 기간은 작업일의 10%.

## 웹 배포 (Streamlit Community Cloud)

설치 없이 주소로 접속할 수 있게 배포합니다. 무료입니다.

1. [share.streamlit.io](https://share.streamlit.io)에 GitHub 계정으로 로그인합니다. 처음이면 `AIHackathon4` 조직 저장소에 접근하도록 권한을 허용합니다.
2. **Create app**을 누르고 다음과 같이 설정합니다.
   - 저장소: `AIHackathon4/demo`
   - 브랜치: `main`
   - Main file: `demo/app.py`
3. **Advanced settings**에서 다음을 설정합니다.
   - Python 3.10 선택
   - **Secrets**에 [`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example)의 내용을 실제 값으로 채워 붙여 넣기
4. **Deploy**를 누르면 몇 분 뒤 `https://<이름>.streamlit.app` 주소가 생깁니다.

`APP_PASSWORD`를 넣으면 비밀번호를 아는 사람만 들어올 수 있습니다. 주소가 퍼져도 우리 API 키로 비용이 나가지 않게 막아 줍니다. 의존성은 `requirements.txt`에 있고, `uv.lock`에서 `uv export`로 만들었습니다.

## 검증

정답 데이터(실제로 참여할 수 있었는지) 없이 판정을 검증하고, 개인화 유무와 역할 분담의 효과를 비교합니다. LLM 응답은 `data/contests/feasibility_cache.jsonl`에 캐시됩니다.

```bash
uv run pytest                                          # 단위 테스트
uv run python scripts/eval_feasibility.py              # 관계 검사·반복 일관성·자격 통제 실험
uv run python scripts/compare_personalization.py       # A 개인화 없음 / B LLM 단독 / C PODO 비교
```

결과는 `data/results/`에 저장됩니다. 실제 공고는 로컬 수집 DB에서 읽으므로, DB가 없으면 합성 공고(`data/test/feasibility/notices_synthetic.json`)로만 실행됩니다.

## 구조

| 경로 | 내용 |
|---|---|
| `demo/` | Streamlit 데모 |
| `backend/` | FourDo 웹: Flask 서버(`app.py`), 엔진 결과 ↔ 화면 JSON 변환(`adapter.py`), 화면(`static/`) |
| `src/aihack4/card.py`, `strategy.py`, `roadmap.py` | 설계 v3 엔진: 공고 카드(LLM), 맞춤 전략·로드맵(코드) |
| `src/aihack4/feasibility.py` | 판정 엔진 (자격 조건 추출·비교, 작업 분해, 일정 계산) |
| `src/aihack4/collector/` | 공고 가져오기(링크·파일·본문 → 구조화)와 자동 수집 에이전트 |
| `src/aihack4/feasibility_eval.py`, `baselines.py`, `llm_runner.py` | 검증 로직, 비교 조건, 캐시·병렬 LLM 호출 |
| `scripts/` | 검증·비교 실행, 공고 수집 |
| `data/test/feasibility/` | 합성 페르소나 6명, 합성 공고 4건 |
| `docs/` | 기획 v2(교수님 피드백 반영), 작업 정리 PDF, 면담 질문지 |
