# PODO · 포기 말고 도전

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
| `src/aihack4/feasibility.py` | 판정 엔진 (자격 조건 추출·비교, 작업 분해, 일정 계산) |
| `src/aihack4/collector/` | 공고 가져오기(링크·파일·본문 → 구조화)와 자동 수집 에이전트 |
| `src/aihack4/feasibility_eval.py`, `baselines.py`, `llm_runner.py` | 검증 로직, 비교 조건, 캐시·병렬 LLM 호출 |
| `scripts/` | 검증·비교 실행, 공고 수집 |
| `data/test/feasibility/` | 합성 페르소나 6명, 합성 공고 4건 |
| `docs/` | 기획 v2(교수님 피드백 반영), 작업 정리 PDF, 면담 질문지 |
