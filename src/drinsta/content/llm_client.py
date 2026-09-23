"""OpenAI 호출 래퍼 (Responses API + 구조화 출력).

파이프라인은 LLM Protocol에만 의존하므로 테스트에서는 가짜 객체로 바꿔 끼운다.
"""
from __future__ import annotations

from typing import Protocol, TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from openai import OpenAI
from pydantic import BaseModel

from ..config import AppConfig, ModelConfig, get_config
from ..news import ORIGIN_WEB, NewsItem, make_key
from . import prompts
from .schemas import FoundNewsList, PostContent, RankEntry, RankResult

T = TypeVar("T", bound=BaseModel)


class LLM(Protocol):
    def rank_news(self, candidates: list[NewsItem], recent_titles: list[str]) -> list[RankEntry]: ...
    def write_post(self, item: NewsItem, min_slides: int, max_slides: int) -> PostContent: ...
    def research_news(self, today: str, exclude_titles: list[str], count: int) -> list[NewsItem]: ...
    def embed(self, text: str) -> list[float]: ...


def strip_tracking(url: str) -> str:
    """web_search가 붙이는 utm_* 추적 파라미터를 뗀다."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.startswith("utm_")]
    return urlunsplit(parts._replace(query=urlencode(query)))


def extract_citations(response) -> dict[str, str]:
    """web_search 응답의 url_citation 주석 -> {url: title}. 실제로 검색된 URL은 이것뿐이다."""
    citations: dict[str, str] = {}
    for item in response.output:
        if getattr(item, "type", None) != "message":
            continue
        for part in item.content:
            if getattr(part, "type", None) != "output_text":
                continue
            for annotation in part.annotations or []:
                if getattr(annotation, "type", None) == "url_citation":
                    citations.setdefault(annotation.url, annotation.title)
    return citations


class OpenAILLM:
    def __init__(self, api_key: str, models: ModelConfig):
        self._client = OpenAI(api_key=api_key)
        self.models = models

    def _parse(self, model: str, instructions: str, prompt: str, schema: type[T]) -> T:
        response = self._client.responses.parse(
            model=model, instructions=instructions, input=prompt, text_format=schema
        )
        if response.output_parsed is None:
            raise RuntimeError(f"구조화 출력 파싱 실패 ({schema.__name__})")
        return response.output_parsed

    def rank_news(self, candidates: list[NewsItem], recent_titles: list[str]) -> list[RankEntry]:
        result = self._parse(
            self.models.ranker,
            prompts.RANKER_SYSTEM,
            prompts.ranker_prompt(candidates, recent_titles),
            RankResult,
        )
        return result.entries

    def write_post(self, item: NewsItem, min_slides: int, max_slides: int) -> PostContent:
        return self._parse(
            self.models.writer,
            prompts.WRITER_SYSTEM,
            prompts.writer_prompt(item, min_slides, max_slides),
            PostContent,
        )

    def research_news(self, today: str, exclude_titles: list[str], count: int) -> list[NewsItem]:
        """2단계: web_search로 자유 텍스트 리서치 -> 저렴한 모델로 구조화 (DR과 같은 패턴)."""
        research = self._client.responses.create(
            model=self.models.search,
            instructions=prompts.SEARCH_SYSTEM,
            input=prompts.search_prompt(today, exclude_titles, count),
            tools=[{"type": "web_search", "search_context_size": "low"}],
        )
        citations = extract_citations(research)
        structure_input = research.output_text
        if citations:
            structure_input += "\n\n[인용된 출처]\n" + "\n".join(
                f"- {title}: {url}" for url, title in citations.items()
            )
        found = self._parse(
            self.models.structurer, prompts.STRUCTURER_SYSTEM, structure_input, FoundNewsList
        )

        items = []
        for news in found.items:
            if not news.title.strip() or not news.summary.strip():
                continue
            # 구조화 단계가 목록에 없는 URL을 만들어냈으면 버린다
            source_url = strip_tracking(news.source_url) if news.source_url in citations else None
            items.append(
                NewsItem(
                    key=make_key(ORIGIN_WEB, source_url or news.title),
                    origin=ORIGIN_WEB,
                    title=news.title.strip(),
                    summary=news.summary.strip(),
                    why_it_matters=news.why_it_matters.strip(),
                    analysis=news.analysis.strip(),
                    key_facts=[f.strip() for f in news.key_facts if f.strip()],
                    source_name=(news.source_name or "").strip() or None,
                    source_url=source_url,
                    published_date=(news.published_date or "").strip() or None,
                )
            )
        return items

    def embed(self, text: str) -> list[float]:
        response = self._client.embeddings.create(model=self.models.embedding, input=text)
        return response.data[0].embedding

    @classmethod
    def from_config(cls, cfg: AppConfig | None = None) -> "OpenAILLM":
        cfg = cfg or get_config()
        return cls(api_key=cfg.openai_api_key, models=cfg.models)
