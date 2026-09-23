"""LLM 구조화 출력 스키마 (OpenAI Responses API의 text_format으로 그대로 넘긴다).

길이/개수/점수 범위는 스키마 제약 대신 프롬프트로 지시하고 코드에서 한 번 더 정리한다 -
strict JSON schema가 지원하는 제약이 모델/버전마다 달라서 거절되는 일을 피하기 위함.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class RankEntry(BaseModel):
    index: int
    ai_relevant: bool
    duplicate_of_recent: bool
    impact: int
    reason: str


class RankResult(BaseModel):
    entries: list[RankEntry]


class PostContent(BaseModel):
    title: str
    body_slides: list[str]
    caption: str
    hashtags: list[str]


class FoundNews(BaseModel):
    title: str
    summary: str
    why_it_matters: str
    analysis: str
    key_facts: list[str]
    source_name: Optional[str]
    source_url: Optional[str]
    published_date: Optional[str]


class FoundNewsList(BaseModel):
    items: list[FoundNews]
