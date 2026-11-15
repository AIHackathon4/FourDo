from aihack4.card import check_card, quote_found

SOURCE = "제출물: Windows 11에서 실행 가능한 구동 파일\n소스코드 전체\n실행/빌드 매뉴얼\n접수 마감 2026. 10. 23.(금) 18:00"


def test_quote_found_tolerates_joined_list_items():
    assert quote_found("Windows 11에서 실행 가능한 구동 파일", SOURCE)
    assert quote_found("Windows 11에서 실행 가능한 구동 파일, 소스코드 전체, 실행/빌드 매뉴얼", SOURCE)
    assert not quote_found("Windows 11에서 실행 가능한 구동 파일, 발표 자료", SOURCE)
    assert not quote_found("", SOURCE)


def card(**overrides):
    base = {"title": "t", "organizer": "o", "application_start": None, "deadline": "2026-10-23", "deadline_time": "18:00",
            "deadline_quote": "접수 마감 2026. 10. 23.(금) 18:00", "open_to_undergraduates": "yes", "team_min": 1,
            "team_max": 5, "conditions": [], "criteria": [], "forms": [], "submissions": [{
                "name": "구동 파일", "requirement": "Windows 11", "quote": "Windows 11에서 실행 가능한 구동 파일",
                "options": [
                    {"name": "B", "tier": "표준", "skills": [{"skill": "개발", "level": "가능"}], "how": "", "steps": [{"name": "a", "hours": 10}]},
                    {"name": "A", "tier": "최소", "skills": [{"skill": "개발", "level": "입문"}], "how": "",
                     "steps": [{"name": "a", "hours": 3}, {"name": "b", "hours": 0}]}]}]}
    return {**base, **overrides}


def test_check_card_sorts_options_sums_hours_and_flags_skill_required():
    c = check_card(card(), SOURCE)
    sub = c["submissions"][0]
    assert [o["tier"] for o in sub["options"]] == ["최소", "표준"]
    assert sub["options"][0]["hours"] == 3 and len(sub["options"][0]["steps"]) == 1  # 0시간 단계는 뺀다
    assert sub["needs_skill"] and sub["quote_ok"] and c["issues"] == []


def test_check_card_reports_bad_date_and_missing_quote():
    c = check_card(card(deadline="10월 23일", deadline_quote="없는 문장"), SOURCE)
    assert c["deadline"] is None and any("형식" in i for i in c["issues"])


def test_check_card_splits_datetime_deadline():
    c = check_card(card(deadline="2026-10-23T18:00", deadline_time=None), SOURCE)
    assert (c["deadline"], c["deadline_time"]) == ("2026-10-23", "18:00")
