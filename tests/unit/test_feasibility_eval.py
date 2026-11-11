from datetime import date

from test_collector import notice
from test_feasibility import profile, raw, task

from aihack4 import feasibility_eval as fe
from aihack4.feasibility import evaluate

TODAY = date(2026, 11, 10)


def test_base_date_is_lead_days_before_deadline():
    assert fe.base_date({"deadline": "2026-12-01"}) == date(2026, 11, 10)


def test_make_profile_turns_offsets_into_dates():
    persona = {"profile": {"skills": {"글쓰기": "입문"}}, "blocked_offsets": [[2, 4, "시험"]]}
    p = fe.make_profile(persona, TODAY)
    assert (p.blocked[0].start, p.blocked[0].end, p.blocked[0].reason) == (date(2026, 11, 12), date(2026, 11, 14), "시험")


def test_all_code_relations_hold_on_tight_case():
    # 마감 21일 남은 빠듯한 경우: 판정이 시간에 민감해서 관계가 깨지면 드러난다.
    n = notice(deadline="2026-12-01", team_min=None, team_max=3)
    p = profile(experience="처음", weekday_hours=1, weekend_hours=1)
    r = raw(task(low=20, high=30), task("영상 제작", "영상 편집", "가능", None, 10, 14))
    checked = [fe.check_relation(rel, n, p, TODAY, r) for rel in fe.RELATIONS]
    assert all(c is not None for c in checked)  # 이 경우엔 모든 관계가 적용된다
    assert [c["relation"] for c in checked if not c["ok"]] == []


def test_relation_not_applied_when_precondition_fails():
    n = notice(deadline="2026-12-01")
    experienced = next(rel for rel in fe.RELATIONS if rel.name == "experienced")
    assert fe.check_relation(experienced, n, profile(experience="참가 경험"), TODAY, raw(task())) is None


def test_check_relation_reports_violation():
    broken = fe.Relation("broken", "판정이 좋아져야 하는데 시간을 줄인다", lambda n, p, t: True,
                         lambda p, t: (fe.replace(p, weekday_hours=0, weekend_hours=0), t), +1, "available_hours", +1)
    result = fe.check_relation(broken, notice(deadline="2026-12-01"), profile(), TODAY, raw(task()))
    assert not result["ok"] and "metric" in result["failed"]


def test_consistency_measures_agreement_and_spread():
    p = profile(weekday_hours=2, weekend_hours=2)
    n = notice(deadline="2026-12-01")
    small, large = evaluate(n, p, raw(task(low=4, high=4)), TODAY), evaluate(n, p, raw(task(low=60, high=60)), TODAY)
    item = fe.consistency([small, small, large])
    assert item["verdict_agreement"] == 2 / 3
    assert item["hours_spread"] > 1
    summary = fe.summarize_consistency([item, {**item, "verdict_agreement": 1.0}])
    assert summary["fully_consistent_rate"] == 0.5


def test_irrelevant_change_keeps_skills_and_adds_note():
    p = profile(skills={"A": "가능", "B": "입문"})
    changed = fe.irrelevant_change(p)
    assert list(changed.skills) == ["B", "A"] and changed.skills == p.skills
    assert changed.note == fe.IRRELEVANT_NOTE


def test_fill_gaps_adds_missing_and_low_level_skills():
    p = profile(skills={"기획서 작성": "입문"})
    r = raw(task(level="가능"), task("영상 제작", "영상 편집", "가능", None, 2, 3))
    result = evaluate(notice(deadline="2026-12-01"), p, r, TODAY)
    filled = fe.fill_gaps(p, result)
    assert filled.skills == {"기획서 작성": "능숙", "영상 편집": "능숙"}
    after = evaluate(notice(deadline="2026-12-01"), filled,
                     raw(task(level="가능"), task("영상 제작", "영상 편집", "가능", "영상 편집", 2, 3)), TODAY)
    check = fe.skill_fill_ok(result, after)
    assert check["ok"] and check["resolved_all"]


def test_fill_gaps_none_when_no_gap():
    result = evaluate(notice(deadline="2026-12-01"), profile(), raw(task()), TODAY)
    assert fe.fill_gaps(profile(), result) is None


def test_eligibility_notice_hides_undergrad_flag_and_evidence():
    changed = fe.eligibility_notice(notice(), ["한빛대학교 재학생"])
    assert changed["eligibility"] == ["한빛대학교 재학생"]
    assert changed["open_to_undergraduates"] == "unknown" and changed["evidence"] == []


def test_summarize_eligibility_counts_dangerous_errors():
    items = [{"case": "a", "expected": "fail", "got": "ok"}, {"case": "a", "expected": "fail", "got": "fail"},
             {"case": "b", "expected": "warn", "got": "warn"}]
    summary = fe.summarize_eligibility(items)
    assert summary["accuracy"] == 2 / 3
    assert summary["fail_judged_ok"] == 1
    assert summary["by_case"] == {"a": 0.5, "b": 1.0}


def test_conditions_agreement_ignores_wording():
    a = {"type": "region", "allowed": ["부산"], "min": None, "max": None, "quote": "부산 거주", "question": "부산에 사나요?"}
    b = {**a, "quote": "부산광역시 거주자", "question": "주소가 부산인가요?"}
    c = {**a, "type": "other"}
    assert fe.conditions_agreement([[a], [b], [c]]) == 2 / 3
    assert fe.conditions_signature([a, c]) == fe.conditions_signature([c, a])


def test_other_condition_wording_is_ignored():
    a = {"type": "other", "allowed": ["예비창업자"], "min": None, "max": None, "quote": "", "question": ""}
    assert fe.conditions_agreement([[a], [{**a, "allowed": ["예비 창업자", "초기 창업자"]}]]) == 1
