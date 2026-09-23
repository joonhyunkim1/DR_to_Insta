from datetime import date

from drinsta.news import ORIGIN_DR
from drinsta.sources.dr_source import parse_rows

FULL_ITEM = {
    "title": "Qualcomm Snapdragon Summit 2026",
    "summary": "퀄컴이 모바일을 agentic AI 실행 기반으로 재정의했다.",
    "whyItMatters": "온디바이스 AI가 상시 동작 런타임으로 이동한다.",
    "researcherView": "시스템 문제로 봐야 한다.",
    "engineerView": "Jetson 경험이 있다면 포트폴리오에 유리하다.",
    "keyFacts": ["스마트폰·PC·차량을 묶는 플랫폼", " ", "연 1회 행사"],
    "sourceName": "Qualcomm Newsroom",
    "sourceUrl": "https://www.qualcomm.com/news/x",
    "publishedDate": "2026-09-22",
}


def test_maps_dr_fields_and_drops_engineer_view():
    [item] = parse_rows([(date(2026, 9, 24), {"items": [FULL_ITEM]})])
    assert item.origin == ORIGIN_DR
    assert item.batch_date == "2026-09-24"
    assert item.title == FULL_ITEM["title"]
    assert item.why_it_matters == FULL_ITEM["whyItMatters"]
    assert item.analysis == FULL_ITEM["researcherView"]
    assert item.key_facts == ["스마트폰·PC·차량을 묶는 플랫폼", "연 1회 행사"]
    assert item.source_name == "Qualcomm Newsroom"
    assert item.source_url == "https://www.qualcomm.com/news/x"
    assert "Jetson" not in item.as_reference()


def test_rows_stored_before_new_fields_still_parse():
    old = {k: FULL_ITEM[k] for k in ("title", "summary", "whyItMatters", "researcherView", "engineerView")}
    [item] = parse_rows([(date(2026, 9, 20), {"items": [old]})])
    assert item.key_facts == []
    assert item.source_name is None
    assert item.source_line() == ""


def test_items_missing_title_or_summary_and_malformed_content_are_skipped():
    rows = [
        (date(2026, 9, 24), {"items": [{"title": "", "summary": "x"}, {"title": "t"}, "junk", FULL_ITEM]}),
        (date(2026, 9, 23), {"unexpected": True}),
        (date(2026, 9, 22), None),
    ]
    items = parse_rows(rows)
    assert [i.title for i in items] == [FULL_ITEM["title"]]


def test_key_is_stable_per_batch_and_title():
    a = parse_rows([(date(2026, 9, 24), {"items": [FULL_ITEM]})])[0]
    b = parse_rows([(date(2026, 9, 24), {"items": [dict(FULL_ITEM, summary="다시 생성된 요약")]})])[0]
    c = parse_rows([(date(2026, 9, 25), {"items": [FULL_ITEM]})])[0]
    assert a.key == b.key
    assert a.key != c.key
