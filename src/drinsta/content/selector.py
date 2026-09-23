"""이번 슬롯에 올릴 뉴스 한 건을 고른다.

1. DR 뉴스 중 아직 처리하지 않은 것만, 최신 배치(runDate)부터 본다.
2. 배치 안에서 LLM이 AI 관련성/최근 게시와 중복 여부/임팩트를 평가 -> 부적합한 건 건너뜀 기록,
   나머지는 임팩트 순으로 정렬 (좋은 뉴스가 먼저 소진된다).
3. 임베딩 유사도로 최근 게시물과 한 번 더 중복 확인 (LLM 판단의 안전장치).
4. 고를 게 없으면 (DR 실패/전부 사용/전부 중복) 실시간 웹 검색으로 가장 임팩트 있는 AI 뉴스를 찾는다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..config import AppConfig
from ..news import NewsItem
from ..repository import PostRecord
from . import dedup
from .llm_client import LLM

logger = logging.getLogger(__name__)

MAX_EXCLUDE_TITLES = 40


@dataclass
class Skip:
    item: NewsItem
    reason: str


@dataclass
class Selection:
    item: NewsItem
    embedding: list[float]
    impact: Optional[int]
    via_fallback: bool


@dataclass
class SelectionResult:
    selection: Optional[Selection]
    skipped: list[Skip] = field(default_factory=list)
    fallback_reason: Optional[str] = None
    considered: list[tuple[str, Optional[int]]] = field(default_factory=list)  # (제목, 임팩트) 평가 순서


def group_batches(items: list[NewsItem]) -> list[list[NewsItem]]:
    """batch_date 최신순으로 묶는다. 배치 안의 순서(DR이 준 순서)는 유지."""
    batches: dict[str, list[NewsItem]] = {}
    for item in items:
        batches.setdefault(item.batch_date or "", []).append(item)
    return [batches[d] for d in sorted(batches, reverse=True)]


def rank_candidates(
    llm: LLM, items: list[NewsItem], recent_titles: list[str], min_impact: int
) -> tuple[list[tuple[NewsItem, Optional[int]]], list[Skip]]:
    """(게시 가능한 후보를 임팩트 순으로, 건너뛸 후보) 를 반환한다.

    랭킹 호출 자체가 실패하면 게시를 멈추지 않도록 원래 순서 그대로 전부 후보로 둔다.
    """
    try:
        entries = llm.rank_news(items, recent_titles)
    except Exception as e:  # noqa: BLE001 - 랭킹은 보조 수단이라 실패해도 진행
        logger.warning("후보 랭킹 실패, 원래 순서로 진행: %s", e)
        return [(item, None) for item in items], []

    by_index = {}
    for entry in entries:
        if 0 <= entry.index < len(items):
            by_index.setdefault(entry.index, entry)

    ranked: list[tuple[NewsItem, Optional[int]]] = []
    unranked: list[tuple[NewsItem, Optional[int]]] = []
    skipped: list[Skip] = []
    for index, item in enumerate(items):
        entry = by_index.get(index)
        if entry is None:
            unranked.append((item, None))  # 모델이 빠뜨린 후보는 판단 보류 - 맨 뒤에서 시도
        elif not entry.ai_relevant:
            skipped.append(Skip(item, f"AI 관련성 낮음: {entry.reason}"))
        elif entry.duplicate_of_recent:
            skipped.append(Skip(item, f"최근 게시와 같은 소식: {entry.reason}"))
        elif entry.impact < min_impact:
            skipped.append(Skip(item, f"임팩트 {entry.impact}점: {entry.reason}"))
        else:
            ranked.append((item, entry.impact))
    ranked.sort(key=lambda pair: -(pair[1] or 0))
    return ranked + unranked, skipped


def _recent_titles(recent_posts: list[PostRecord]) -> list[str]:
    titles: list[str] = []
    for post in recent_posts:
        for title in (post.news_title, post.title):
            if title and title not in titles:
                titles.append(title)
    return titles


def choose_news(
    *,
    dr_items: list[NewsItem],
    excluded_keys: set[str],
    recent_posts: list[PostRecord],
    llm: LLM,
    cfg: AppConfig,
    today: str,
    dr_error: Optional[str] = None,
    skip_dr: bool = False,
) -> SelectionResult:
    recent_titles = _recent_titles(recent_posts)
    history_embeddings = [p.embedding for p in recent_posts if p.embedding]
    skipped: list[Skip] = []
    considered: list[tuple[str, Optional[int]]] = []

    def first_unique(ranked: list[tuple[NewsItem, Optional[int]]]):
        for item, impact in ranked:
            embedding = llm.embed(item.dedup_text())
            if dedup.is_duplicate(embedding, history_embeddings, cfg.dedup.similarity_threshold):
                skipped.append(Skip(item, "최근 게시와 같은 소식 (임베딩 유사도)"))
                continue
            return item, embedding, impact
        return None

    fresh = [] if skip_dr else [i for i in dr_items if i.key not in excluded_keys]
    for batch in group_batches(fresh):
        ranked, batch_skipped = rank_candidates(llm, batch, recent_titles, cfg.source.min_impact)
        skipped.extend(batch_skipped)
        considered.extend((item.title, impact) for item, impact in ranked)
        picked = first_unique(ranked)
        if picked:
            item, embedding, impact = picked
            return SelectionResult(
                Selection(item, embedding, impact, via_fallback=False), skipped, considered=considered
            )

    if skip_dr:
        fallback_reason = "DR 건너뛰기(수동 폴백 테스트)"
    elif dr_error:
        fallback_reason = f"DR 조회 실패: {dr_error}"
    elif not dr_items:
        fallback_reason = "최근 DR 뉴스 없음 (DR 생성 실패 또는 미실행)"
    else:
        fallback_reason = "DR 뉴스를 모두 사용했거나 중복/부적합"
    logger.info("실시간 검색 폴백: %s", fallback_reason)

    exclude_titles = (recent_titles + [i.title for i in dr_items])[:MAX_EXCLUDE_TITLES]
    found = llm.research_news(today, exclude_titles, cfg.source.fallback_candidates)
    found = [i for i in found if i.key not in excluded_keys]
    if found:
        # 폴백은 이미 '가장 임팩트 있는 뉴스'를 찾아온 것이라 임팩트 하한은 두지 않고,
        # AI 관련성/중복만 거른다 (슬롯을 비우는 것보다 올리는 게 낫다).
        ranked, fallback_skipped = rank_candidates(llm, found, recent_titles, min_impact=1)
        skipped.extend(fallback_skipped)
        considered.extend((item.title, impact) for item, impact in ranked)
        picked = first_unique(ranked)
        if picked:
            item, embedding, impact = picked
            return SelectionResult(
                Selection(item, embedding, impact, via_fallback=True), skipped, fallback_reason, considered
            )
    return SelectionResult(None, skipped, fallback_reason, considered)
