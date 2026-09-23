from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from drinsta import repository as repo
from drinsta.config import get_config
from drinsta.db import get_connection
from drinsta.pipeline import (
    STATUS_ALREADY_PUBLISHED,
    STATUS_DRY_RUN,
    STATUS_NO_NEWS,
    STATUS_PUBLISHED,
    Deps,
    run_slot,
)
from drinsta.publish.instagram_client import InstagramAPIError
from drinsta.slots import Slot
from fakes import FakeInstagram, FakeLLM, FakeNotifier, FakeStorage, SolidBackend, dr_item, web_item

KST = ZoneInfo("Asia/Seoul")
SLOT = Slot(datetime(2026, 9, 24, 12, 0, tzinfo=KST))
NEXT_SLOT = Slot(datetime(2026, 9, 24, 18, 0, tzinfo=KST))


@pytest.fixture(autouse=True)
def r2_env(monkeypatch):
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://cdn.example.com")


@pytest.fixture
def conn(tmp_path):
    return get_connection(tmp_path / "test.db")


def make_deps(llm=None, dr_items=None, dr_error=None, instagram=None, now=None):
    sleeps = []
    fetch_calls = []

    def fetch_dr(since):
        fetch_calls.append(since)
        if dr_error:
            raise dr_error
        return list(dr_items or [])

    deps = Deps(
        llm=llm or FakeLLM(),
        image_backend=SolidBackend(),
        fetch_dr=fetch_dr,
        instagram=instagram or FakeInstagram(),
        storage=FakeStorage(),
        notifier=FakeNotifier(),
        now=lambda: now or (SLOT.at - timedelta(minutes=15)).astimezone(timezone.utc),
        sleep=sleeps.append,
    )
    return deps, sleeps, fetch_calls


def test_publishes_best_dr_item_and_records_it(conn):
    item = dr_item("엔비디아 새 칩", source_name="NVIDIA Blog", published_date="2026-09-23")
    deps, sleeps, fetch_calls = make_deps(dr_items=[item])

    outcome = run_slot(conn, SLOT, deps, get_config())

    assert outcome.status == STATUS_PUBLISHED
    assert outcome.media_id == "media-1"
    assert fetch_calls[0].isoformat() == "2026-09-23"  # 오늘+어제 배치
    assert sleeps == [15 * 60]  # 생성 후 정각까지 대기

    [(urls, caption)] = deps.instagram.published
    assert urls == [f"https://cdn.example.com/dr-insta/20260924-1200/{i}.jpg" for i in range(4)]
    assert caption.startswith("엔비디아, 새 AI 칩 공개\n\n")  # 제목 끝 마침표 제거
    assert "출처: NVIDIA Blog (2026-09-23)" in caption
    assert caption.rstrip().split("\n")[-1].startswith("#엔비디아 #GPU #AI칩 #AI뉴스")

    assert repo.post_exists(conn, SLOT.key)
    assert item.key in repo.excluded_news_keys(conn, max_attempts=2)
    assert "✅" in deps.notifier.messages[0] and "DR 2026-09-24 배치" in deps.notifier.messages[0]


def test_same_slot_is_never_published_twice(conn):
    deps, _, _ = make_deps(dr_items=[dr_item("a"), dr_item("b")])
    run_slot(conn, SLOT, deps, get_config())
    again = run_slot(conn, SLOT, deps, get_config())
    assert again.status == STATUS_ALREADY_PUBLISHED
    assert len(deps.instagram.published) == 1


def test_next_slot_uses_the_next_news_item(conn):
    llm = FakeLLM()
    deps, _, _ = make_deps(llm=llm, dr_items=[dr_item("first"), dr_item("second")])
    run_slot(conn, SLOT, deps, get_config())
    run_slot(conn, NEXT_SLOT, deps, get_config(), wait=False)
    assert llm.called("write") == ["first", "second"]


def test_dry_run_writes_preview_but_touches_nothing_else(conn, tmp_path):
    item = dr_item("a")
    llm = FakeLLM(rank=lambda c, r: [])  # 모델이 전부 빠뜨려도 후보로는 남는다
    deps, sleeps, _ = make_deps(llm=llm, dr_items=[item])

    outcome = run_slot(conn, SLOT, deps, get_config(), dry_run=True, preview_root=tmp_path / "preview")

    assert outcome.status == STATUS_DRY_RUN
    files = sorted(p.name for p in outcome.preview_dir.iterdir())
    assert files == ["00.jpg", "01.jpg", "02.jpg", "03.jpg", "caption.txt", "meta.json"]
    assert deps.instagram.published == [] and deps.storage.keys == [] and deps.notifier.messages == []
    assert sleeps == []
    assert not repo.post_exists(conn, SLOT.key)
    assert repo.excluded_news_keys(conn, max_attempts=2) == set()


def test_dr_failure_falls_back_to_web_search_and_says_why(conn):
    llm = FakeLLM(research=[web_item("실시간 뉴스", source_name="Reuters")])
    deps, _, _ = make_deps(
        llm=llm, dr_error=RuntimeError("connection to postgres://user:pw@host/db failed")
    )

    outcome = run_slot(conn, SLOT, deps, get_config())

    assert outcome.status == STATUS_PUBLISHED and outcome.via_fallback
    message = deps.notifier.messages[0]
    assert "실시간 검색 폴백" in message and "DR 조회 실패" in message
    assert "pw@host" not in message


def test_no_news_anywhere_skips_slot_and_warns(conn):
    deps, _, _ = make_deps(llm=FakeLLM(research=[]), dr_items=[])
    outcome = run_slot(conn, SLOT, deps, get_config())
    assert outcome.status == STATUS_NO_NEWS
    assert deps.instagram.published == []
    assert deps.notifier.messages[0].startswith("⚠️")


def test_uncertain_publish_failure_gives_up_on_that_news(conn):
    item = dr_item("a")
    error = InstagramAPIError("boom", status=403, code=None, path="123/media_publish")
    deps, _, _ = make_deps(dr_items=[item], instagram=FakeInstagram(error=error))

    with pytest.raises(InstagramAPIError):
        run_slot(conn, SLOT, deps, get_config())

    assert item.key in repo.excluded_news_keys(conn, max_attempts=2)
    assert not repo.post_exists(conn, SLOT.key)
    assert "피드를 확인" in deps.notifier.messages[0]


def test_account_level_publish_failure_does_not_penalize_the_news(conn):
    item = dr_item("a")
    error = InstagramAPIError("token expired", status=400, code=190, path="123/media")
    deps, _, _ = make_deps(dr_items=[item], instagram=FakeInstagram(error=error))

    with pytest.raises(InstagramAPIError):
        run_slot(conn, SLOT, deps, get_config())

    row = conn.execute("SELECT * FROM news_log WHERE news_key = ?", (item.key,)).fetchone()
    assert row is None
    assert "토큰/권한" in deps.notifier.messages[0]


def test_generation_failure_counts_attempts_until_the_news_is_dropped(conn):
    item = dr_item("a")
    llm = FakeLLM()
    llm.post = llm.post.model_copy(update={"body_slides": ["하나뿐"]})  # 최소 슬라이드 미달
    deps, _, _ = make_deps(llm=llm, dr_items=[item])

    for slot in (SLOT, NEXT_SLOT):
        with pytest.raises(ValueError):
            run_slot(conn, slot, deps, get_config(), wait=False)

    assert item.key in repo.excluded_news_keys(conn, max_attempts=2)
    assert "실패 2/2회" in deps.notifier.messages[-1]
