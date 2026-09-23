"""게시 슬롯(07/12/18/23시 KST) 계산.

워크플로우는 슬롯보다 일찍 떠서 생성을 끝내고 정각까지 기다린 뒤 발행한다. GitHub cron은
지연되는 일이 잦아서, 실행 시각이 슬롯 기준 [-before, +after] 분 안에 있으면 그 슬롯으로 본다.
슬롯 간격이 5시간 이상이라 창이 겹치지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Slot:
    at: datetime  # 슬롯 시각 (tz-aware)

    @property
    def key(self) -> str:
        """중복 게시 방지용 식별자 - 같은 슬롯은 한 번만 게시된다."""
        return self.at.strftime("%Y-%m-%dT%H:%M")

    @property
    def file_key(self) -> str:
        return self.at.strftime("%Y%m%d-%H%M")

    @property
    def local_date(self) -> date:
        return self.at.date()

    @property
    def label(self) -> str:
        return self.at.strftime("%m/%d %H:%M")


def _parse_time(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def resolve_slot(
    now: datetime,
    times: list[str],
    timezone: str,
    before_minutes: int,
    after_minutes: int,
) -> Optional[Slot]:
    """now가 속한 슬롯을 찾는다. 어느 슬롯 창에도 없으면 None."""
    tz = ZoneInfo(timezone)
    local_now = now.astimezone(tz)
    for day_offset in (-1, 0, 1):
        day = local_now.date() + timedelta(days=day_offset)
        for value in times:
            at = datetime.combine(day, _parse_time(value), tzinfo=tz)
            if at - timedelta(minutes=before_minutes) <= local_now <= at + timedelta(minutes=after_minutes):
                return Slot(at)
    return None


def immediate_slot(now: datetime, timezone: str) -> Slot:
    """수동 실행용: 지금 이 시각(분 단위)을 슬롯으로 쓴다."""
    local_now = now.astimezone(ZoneInfo(timezone)).replace(second=0, microsecond=0)
    return Slot(local_now)


def seconds_until(slot: Slot, now: datetime) -> float:
    return (slot.at - now).total_seconds()
