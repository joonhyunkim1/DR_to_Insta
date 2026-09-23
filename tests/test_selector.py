import pytest

from drinsta.config import get_config
from drinsta.content.schemas import RankEntry
from drinsta.content.selector import choose_news, group_batches
from drinsta.repository import PostRecord
from fakes import FakeLLM, dr_item, web_item


def choose(llm, dr_items, excluded=(), recent=(), **kwargs):
    return choose_news(
        dr_items=dr_items,
        excluded_keys=set(excluded),
        recent_posts=list(recent),
        llm=llm,
        cfg=get_config(),
        today="2026-09-24",
        **kwargs,
    )


def scored(scores: dict[str, tuple[bool, bool, int]]):
    """제목별 (ai_relevant, duplicate_of_recent, impact)로 랭킹하는 가짜 랭커."""

    def rank(candidates, recent_titles):
        return [
            RankEntry(index=i, ai_relevant=scores[c.title][0], duplicate_of_recent=scores[c.title][1],
                      impact=scores[c.title][2], reason=c.title)
            for i, c in enumerate(candidates)
        ]

    return rank


def test_group_batches_newest_first_keeping_order():
    items = [dr_item("a", "2026-09-23"), dr_item("b", "2026-09-24"), dr_item("c", "2026-09-24")]
    assert [[i.title for i in b] for b in group_batches(items)] == [["b", "c"], ["a"]]


def test_picks_highest_impact_in_newest_batch():
    llm = FakeLLM(rank=scored({"old": (True, False, 10), "mid": (True, False, 6), "top": (True, False, 9)}))
    items = [dr_item("old", "2026-09-23"), dr_item("mid"), dr_item("top")]
    result = choose(llm, items)
    assert result.selection.item.title == "top"
    assert result.selection.impact == 9
    assert result.selection.via_fallback is False
    assert llm.called("rank") == [["mid", "top"]]  # 최신 배치에서 골랐으면 이전 배치는 보지 않음


def test_irrelevant_duplicate_and_low_impact_items_are_skipped_with_reasons():
    llm = FakeLLM(rank=scored({
        "euv": (False, False, 8), "again": (True, True, 8), "meh": (True, False, 3), "good": (True, False, 6),
    }))
    result = choose(llm, [dr_item("euv"), dr_item("again"), dr_item("meh"), dr_item("good")])
    assert result.selection.item.title == "good"
    reasons = {s.item.title: s.reason for s in result.skipped}
    assert set(reasons) == {"euv", "again", "meh"}
    assert reasons["meh"].startswith("임팩트 3점")


def test_already_handled_items_are_not_candidates():
    first, second = dr_item("first"), dr_item("second")
    result = choose(FakeLLM(), [first, second], excluded={first.key})
    assert result.selection.item.title == "second"


def test_embedding_duplicate_of_recent_post_is_skipped():
    vec = [0.0] * 255 + [1.0]  # FakeLLM의 자동 벡터(앞쪽 축부터 할당)와 겹치지 않는 축
    dup = dr_item("dup")
    llm = FakeLLM(
        rank=scored({"dup": (True, False, 9), "fresh": (True, False, 5)}),
        embeddings={dup.dedup_text(): vec},
    )
    recent = [PostRecord("2026-09-23T23:00", "dup (영문 제목)", "중복 게시물", vec, "2026-09-23T14:00:00+00:00")]
    result = choose(llm, [dup, dr_item("fresh")], recent=recent)
    assert result.selection.item.title == "fresh"
    assert [s.item.title for s in result.skipped] == ["dup"]


def test_falls_back_to_older_batch_when_newest_is_exhausted():
    newest = dr_item("newest")
    result = choose(FakeLLM(), [newest, dr_item("leftover", "2026-09-23")], excluded={newest.key})
    assert result.selection.item.title == "leftover"


def test_rank_failure_keeps_original_order():
    def broken(candidates, recent_titles):
        raise RuntimeError("rate limited")

    result = choose(FakeLLM(rank=broken), [dr_item("a"), dr_item("b")])
    assert result.selection.item.title == "a"
    assert result.selection.impact is None


@pytest.mark.parametrize(
    "dr_items, dr_error, expected_reason",
    [
        ([], None, "최근 DR 뉴스 없음"),
        ([], "OperationalError: timeout", "DR 조회 실패: OperationalError: timeout"),
    ],
)
def test_web_fallback_when_dr_has_nothing(dr_items, dr_error, expected_reason):
    llm = FakeLLM(research=[web_item("breaking")])
    result = choose(llm, dr_items, dr_error=dr_error)
    assert result.selection.item.title == "breaking"
    assert result.selection.via_fallback is True
    assert expected_reason in result.fallback_reason


def test_web_fallback_when_all_dr_items_are_used_up_excludes_their_titles():
    used = dr_item("used")
    recent = [PostRecord("2026-09-23T23:00", "posted news", "게시 제목", None, "2026-09-23T14:00:00+00:00")]
    llm = FakeLLM(research=[web_item("breaking")])
    result = choose(llm, [used], excluded={used.key}, recent=recent)
    assert result.selection.item.title == "breaking"
    assert "모두 사용" in result.fallback_reason
    [exclude_titles] = llm.called("research")
    assert {"posted news", "게시 제목", "used"} <= set(exclude_titles)


def test_fallback_ignores_min_impact_but_still_drops_duplicates():
    llm = FakeLLM(
        rank=scored({"repeat": (True, True, 9), "small": (True, False, 2)}),
        research=[web_item("repeat"), web_item("small")],
    )
    result = choose(llm, [])
    assert result.selection.item.title == "small"


def test_no_selection_when_fallback_finds_nothing():
    result = choose(FakeLLM(research=[]), [])
    assert result.selection is None
    assert result.fallback_reason
