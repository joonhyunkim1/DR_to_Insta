"""테스트용 가짜 의존성 (OpenAI/인스타/R2/텔레그램을 실제로 호출하지 않는다)."""
from __future__ import annotations

from typing import Callable, Optional

from PIL import Image

from drinsta.content.schemas import PostContent, RankEntry
from drinsta.news import ORIGIN_DR, ORIGIN_WEB, NewsItem, make_key

EMBEDDING_DIM = 256


def dr_item(title: str, batch_date: str = "2026-09-24", **kwargs) -> NewsItem:
    return NewsItem(
        key=make_key(ORIGIN_DR, batch_date, title),
        origin=ORIGIN_DR,
        title=title,
        summary=kwargs.pop("summary", f"{title} 요약"),
        batch_date=batch_date,
        **kwargs,
    )


def web_item(title: str, **kwargs) -> NewsItem:
    return NewsItem(
        key=make_key(ORIGIN_WEB, title),
        origin=ORIGIN_WEB,
        title=title,
        summary=kwargs.pop("summary", f"{title} 요약"),
        **kwargs,
    )


def rank_all(impact: int = 7):
    def rank(candidates, recent_titles):
        return [
            RankEntry(index=i, ai_relevant=True, duplicate_of_recent=False, impact=impact, reason="ok")
            for i in range(len(candidates))
        ]

    return rank


class FakeLLM:
    def __init__(
        self,
        rank: Optional[Callable] = None,
        post: Optional[PostContent] = None,
        research: Optional[list[NewsItem]] = None,
        embeddings: Optional[dict[str, list[float]]] = None,
    ):
        self.rank = rank or rank_all()
        self.post = post or PostContent(
            title="엔비디아, 새 AI 칩 공개.",
            body_slides=["엔비디아가 새 칩을 공개했다.", "성능이 2배 빨라졌다.", "AI 인프라 경쟁이 더 치열해진다."],
            caption="엔비디아가 새 AI 칩을 공개했습니다. 성능은 이전 세대보다 2배 빠릅니다.",
            hashtags=["엔비디아", "GPU", "AI칩"],
        )
        self.research = research or []
        self.embeddings = embeddings or {}
        self._auto_vectors: dict[str, list[float]] = {}
        self.calls: list[tuple[str, object]] = []

    def rank_news(self, candidates, recent_titles):
        self.calls.append(("rank", [c.title for c in candidates]))
        return self.rank(candidates, recent_titles)

    def write_post(self, item, min_slides, max_slides):
        self.calls.append(("write", item.title))
        return self.post

    def research_news(self, today, exclude_titles, count):
        self.calls.append(("research", list(exclude_titles)))
        return list(self.research)

    def embed(self, text):
        if text in self.embeddings:
            return self.embeddings[text]
        # 명시하지 않은 텍스트는 서로 겹치지 않는 one-hot 벡터 (유사도 0)
        if text not in self._auto_vectors:
            vec = [0.0] * EMBEDDING_DIM
            vec[len(self._auto_vectors) % EMBEDDING_DIM] = 1.0
            self._auto_vectors[text] = vec
        return self._auto_vectors[text]

    def called(self, name: str) -> list:
        return [arg for call, arg in self.calls if call == name]


class SolidBackend:
    def __init__(self):
        self.prompts: list[str] = []

    def generate_background(self, prompt, quality, size="1024x1024"):
        self.prompts.append(prompt)
        return Image.new("RGB", (64, 64), (20, 20, 20))


class FakeStorage:
    def __init__(self):
        self.keys: list[str] = []

    def put_object(self, **kwargs):
        self.keys.append(kwargs["Key"])


class FakeInstagram:
    def __init__(self, error: Optional[Exception] = None):
        self.error = error
        self.published: list[tuple[list[str], str]] = []

    def publish_carousel(self, image_urls, caption):
        if self.error:
            raise self.error
        self.published.append((image_urls, caption))
        return f"media-{len(self.published)}"


class FakeNotifier:
    def __init__(self):
        self.messages: list[str] = []

    def send(self, text):
        self.messages.append(text)
