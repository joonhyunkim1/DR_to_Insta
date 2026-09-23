"""LLM이 만든 게시물 문구를 정리하고 최종 캡션(제목 + 본문 + 출처 + 해시태그)을 조립한다."""
from __future__ import annotations

import re

from .caption import INSTAGRAM_CAPTION_LIMIT, PARAGRAPH_BREAK, _trim_body, _truncate_words, caption_length
from .schemas import PostContent

# 인스타그램 해시태그는 문자/숫자/밑줄만 인식한다 (\w는 한글 포함). "GPT-5" -> "GPT5"
_NON_TAG_CHARS = re.compile(r"[^\w]")


_SENTENCE_END = re.compile(r"[.!?…,:;·]$")


def _one_line(text: str) -> str:
    """이미지 템플릿은 공백 기준으로 줄바꿈을 직접 계산하므로 모델이 넣은 개행은 펴야 한다.
    모델이 마침표 없이 개행으로 문장을 나누는 경우가 있어서, 그런 줄 끝에는 마침표를 붙여 잇는다
    ("통합했다\\nLinux를 지원한다" -> "통합했다. Linux를 지원한다")."""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line]
    joined = [
        line if i == len(lines) - 1 or _SENTENCE_END.search(line) else f"{line}."
        for i, line in enumerate(lines)
    ]
    return " ".join(joined)


def normalize_post(post: PostContent, min_slides: int, max_slides: int) -> PostContent:
    """빈 슬라이드를 빼고 개수를 설정 범위로 맞춘다. 너무 적으면 생성 실패로 본다."""
    slides = [_one_line(s) for s in post.body_slides if s and s.strip()]
    if len(slides) < min_slides:
        raise ValueError(f"본문 슬라이드가 {len(slides)}장뿐입니다 (최소 {min_slides}장)")
    if len(slides) > max_slides:
        # 마지막 장은 요약(takeaway)이라 유지하고 가운데를 줄인다
        slides = slides[: max_slides - 1] + [slides[-1]]
    title = " ".join(post.title.split()).rstrip(".")
    if not title:
        raise ValueError("게시물 제목이 비어 있습니다")
    return PostContent(
        title=title, body_slides=slides, caption=post.caption.strip(), hashtags=post.hashtags
    )


def build_hashtags(dynamic: list[str], fixed: list[str], max_count: int) -> list[str]:
    """뉴스별 태그를 앞에, 고정 태그를 뒤에. 중복(대소문자 무시)은 한 번만, 총 max_count개까지."""
    seen: set[str] = set()
    tags: list[str] = []
    for raw in [*dynamic, *fixed]:
        if len(tags) >= max_count:
            break
        tag = _NON_TAG_CHARS.sub("", raw.lstrip("#"))
        if not tag or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        tags.append(tag)
    return tags


def build_caption(
    title: str,
    body: str,
    source_line: str,
    hashtags: list[str],
    limit: int = INSTAGRAM_CAPTION_LIMIT,
) -> str:
    """제목 / 본문 / 출처 / 해시태그를 빈 줄로 잇는다. 2,200자를 넘으면 본문만 문장 단위로 줄인다
    (제목·출처·해시태그는 짧고 빠지면 안 되는 요소라 유지)."""
    tag_line = " ".join(f"#{tag}" for tag in hashtags)
    fixed_parts = [p for p in (title, source_line, tag_line) if p]
    fixed_length = sum(caption_length(p) for p in fixed_parts)
    separators = caption_length(PARAGRAPH_BREAK) * len(fixed_parts)  # 본문 앞뒤 구분자 포함
    body_budget = limit - fixed_length - separators
    if body_budget <= 0:
        return _truncate_words(PARAGRAPH_BREAK.join(fixed_parts), limit)
    body = _trim_body(body.strip(), body_budget)
    return PARAGRAPH_BREAK.join(p for p in (title, body, source_line, tag_line) if p)
