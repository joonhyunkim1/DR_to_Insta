"""게시 재료가 되는 뉴스 한 건. DR 조회 결과와 실시간 검색 폴백 결과가 같은 형태로 모인다."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

ORIGIN_DR = "dr"
ORIGIN_WEB = "web"


def make_key(origin: str, *parts: str) -> str:
    """같은 뉴스면 항상 같은 키가 나오도록 내용 기반으로 만든다 (DR 섹션이 재실행돼도 유지)."""
    digest = hashlib.sha1("\n".join(p.strip() for p in parts).encode("utf-8")).hexdigest()[:12]
    return f"{origin}:{digest}"


@dataclass
class NewsItem:
    key: str
    origin: str
    title: str
    summary: str
    why_it_matters: str = ""
    analysis: str = ""
    key_facts: list[str] = field(default_factory=list)
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    published_date: Optional[str] = None
    batch_date: Optional[str] = None  # DR runDate (YYYY-MM-DD). 폴백 뉴스는 None

    def dedup_text(self) -> str:
        return f"{self.title}\n{self.summary}"

    def brief(self) -> str:
        """선별(랭킹) 프롬프트용 짧은 요약."""
        lines = [f"제목: {self.title}", f"요약: {self.summary}"]
        if self.why_it_matters:
            lines.append(f"왜 중요한가: {self.why_it_matters}")
        return "\n".join(lines)

    def as_reference(self) -> str:
        """게시물 작성 프롬프트에 넘기는 전체 자료. 여기 없는 사실은 게시물에 쓰면 안 된다."""
        lines = [f"제목: {self.title}", f"요약: {self.summary}"]
        if self.key_facts:
            lines.append("핵심 사실:\n" + "\n".join(f"- {fact}" for fact in self.key_facts))
        if self.why_it_matters:
            lines.append(f"왜 중요한가: {self.why_it_matters}")
        if self.analysis:
            lines.append(f"분석: {self.analysis}")
        if self.source_name or self.published_date:
            lines.append(f"출처: {self.source_name or '미상'} ({self.published_date or '날짜 미상'})")
        return "\n".join(lines)

    def source_line(self) -> str:
        """캡션 끝에 붙는 출처 표기. 출처를 모르면 빈 문자열."""
        if not self.source_name:
            return ""
        return f"출처: {self.source_name}" + (f" ({self.published_date})" if self.published_date else "")
