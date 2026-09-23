from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from drinsta.slots import immediate_slot, resolve_slot, seconds_until

KST = ZoneInfo("Asia/Seoul")
TIMES = ["07:00", "12:00", "18:00", "23:00"]


def resolve(now):
    return resolve_slot(now, TIMES, "Asia/Seoul", before_minutes=60, after_minutes=120)


def test_cron_fires_twenty_minutes_early_resolves_upcoming_slot():
    # 워크플로우 cron: UTC 21:40 = KST 다음날 06:40
    slot = resolve(datetime(2026, 9, 23, 21, 40, tzinfo=timezone.utc))
    assert slot.at == datetime(2026, 9, 24, 7, 0, tzinfo=KST)
    assert slot.key == "2026-09-24T07:00"
    assert slot.file_key == "20260924-0700"


def test_delayed_cron_still_maps_to_the_slot_it_was_meant_for():
    slot = resolve(datetime(2026, 9, 24, 12, 45, tzinfo=KST))
    assert slot.at == datetime(2026, 9, 24, 12, 0, tzinfo=KST)


def test_late_night_slot_resolves_across_midnight():
    slot = resolve(datetime(2026, 9, 25, 0, 30, tzinfo=KST))
    assert slot.at == datetime(2026, 9, 24, 23, 0, tzinfo=KST)
    assert slot.local_date.isoformat() == "2026-09-24"


def test_outside_every_window_returns_none():
    assert resolve(datetime(2026, 9, 24, 3, 0, tzinfo=KST)) is None
    assert resolve(datetime(2026, 9, 24, 15, 0, tzinfo=KST)) is None


def test_seconds_until_slot():
    slot = resolve(datetime(2026, 9, 24, 17, 40, tzinfo=KST))
    assert seconds_until(slot, datetime(2026, 9, 24, 17, 45, tzinfo=KST)) == 15 * 60


def test_immediate_slot_truncates_to_minute_in_kst():
    slot = immediate_slot(datetime(2026, 9, 23, 17, 31, 42, tzinfo=timezone.utc), "Asia/Seoul")
    assert slot.key == "2026-09-24T02:31"
