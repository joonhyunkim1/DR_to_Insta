"""DR(PersonalDailyReport) DB에서 최근 AI 뉴스를 읽어온다 (읽기 전용).

DR은 매일 08시대(KST)에 AI 뉴스를 생성해 BriefingSection(sectionType=AI_NEWS).contentJson에
{"items": [{title, summary, whyItMatters, researcherView, engineerView, keyFacts?, sourceName?,
sourceUrl?, publishedDate?}]} 형태로 저장한다. engineerView는 DR 사용자 개인을 향한 조언이라
공개 채널에는 쓰지 않는다.

DR 쪽 JSON 구조가 바뀌면 여기서 걸러진다 - 필수 필드(title/summary)가 없는 항목은 버리고,
하나도 안 남으면 파이프라인이 실시간 검색 폴백으로 넘어간다.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Iterable

from ..news import ORIGIN_DR, NewsItem, make_key

logger = logging.getLogger(__name__)

# 같은 날짜에 AI_NEWS 섹션이 여러 개일 수 있다 (DR이 PARTIAL 실행 후 재실행되면 섹션을 새로 쌓음).
# 날짜별로 가장 마지막에 만들어진 성공 섹션만 쓴다. FALLBACK(placeholder) 섹션은 제외.
QUERY = """
SELECT DISTINCT ON (r."runDate") r."runDate" AS run_date, s."contentJson" AS content
FROM "BriefingSection" s
JOIN "BriefingRun" r ON r.id = s."runId"
WHERE s."sectionType" = 'AI_NEWS' AND s.status = 'SUCCESS' AND r."runDate" >= %(since)s
ORDER BY r."runDate" DESC, s."createdAt" DESC
"""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def parse_rows(rows: Iterable[tuple[date, Any]]) -> list[NewsItem]:
    """(run_date, contentJson) 행들을 NewsItem 목록으로 바꾼다. 최신 날짜가 먼저 오도록 유지."""
    items: list[NewsItem] = []
    for run_date, content in rows:
        batch_date = run_date.isoformat()
        raw_items = content.get("items") if isinstance(content, dict) else None
        if not isinstance(raw_items, list):
            logger.warning("DR AI_NEWS(%s)에 items 목록이 없어 건너뜀", batch_date)
            continue
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            title, summary = _text(raw.get("title")), _text(raw.get("summary"))
            if not title or not summary:
                continue
            key_facts = raw.get("keyFacts")
            items.append(
                NewsItem(
                    key=make_key(ORIGIN_DR, batch_date, title),
                    origin=ORIGIN_DR,
                    title=title,
                    summary=summary,
                    why_it_matters=_text(raw.get("whyItMatters")),
                    analysis=_text(raw.get("researcherView")),
                    key_facts=[_text(f) for f in key_facts if _text(f)] if isinstance(key_facts, list) else [],
                    source_name=_optional_text(raw.get("sourceName")),
                    source_url=_optional_text(raw.get("sourceUrl")),
                    published_date=_optional_text(raw.get("publishedDate")),
                    batch_date=batch_date,
                )
            )
    return items


def fetch_dr_news(database_url: str, since: date, connect_timeout: int = 15) -> list[NewsItem]:
    """since(KST 날짜) 이후 DR 배치의 AI 뉴스. 연결/쿼리 실패는 호출부에서 폴백으로 처리한다."""
    import psycopg

    if not database_url:
        raise RuntimeError("DR_DATABASE_URL이 설정되지 않음")
    with psycopg.connect(database_url, connect_timeout=connect_timeout) as conn:
        conn.read_only = True
        rows = conn.execute(QUERY, {"since": since}).fetchall()
    return parse_rows(rows)
