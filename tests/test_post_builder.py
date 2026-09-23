import pytest

from drinsta.content.caption import INSTAGRAM_CAPTION_LIMIT, caption_length
from drinsta.content.post_builder import build_caption, build_hashtags, normalize_post
from drinsta.content.schemas import PostContent


def post(slides, title="제목"):
    return PostContent(title=title, body_slides=slides, caption=" 본문 ", hashtags=[])


def test_normalize_post_drops_empty_slides_and_trailing_period():
    result = normalize_post(post(["a", "  ", "b"], title=" 새 모델 공개. "), 2, 5)
    assert result.body_slides == ["a", "b"]
    assert result.title == "새 모델 공개"
    assert result.caption == "본문"


def test_normalize_post_flattens_line_breaks_for_the_image_template():
    slides = ["첫 문장이다.\n두 번째 문장이다.", "통합했다\n\nLinux를 지원한다", "b"]
    result = normalize_post(post(slides, title="두 줄\n제목"), 2, 5)
    assert result.body_slides[0] == "첫 문장이다. 두 번째 문장이다."
    assert result.body_slides[1] == "통합했다. Linux를 지원한다"
    assert result.title == "두 줄 제목"


def test_normalize_post_clamps_but_keeps_final_takeaway_slide():
    result = normalize_post(post(["1", "2", "3", "4", "5", "6", "요약"]), 2, 5)
    assert result.body_slides == ["1", "2", "3", "4", "요약"]


def test_normalize_post_rejects_too_few_slides():
    with pytest.raises(ValueError):
        normalize_post(post(["only one"]), 2, 5)


def test_hashtags_normalized_deduped_and_capped():
    tags = build_hashtags(["#GPT-5", "오픈 AI", "gpt5", "LLM"], ["AI뉴스", "llm", "인공지능"], max_count=4)
    assert tags == ["GPT5", "오픈AI", "LLM", "AI뉴스"]


def test_caption_orders_title_body_source_tags():
    caption = build_caption("제목", "본문입니다.", "출처: NVIDIA Blog (2026-09-22)", ["AI", "엔비디아"])
    assert caption == "제목\n\n본문입니다.\n\n출처: NVIDIA Blog (2026-09-22)\n\n#AI #엔비디아"


def test_caption_without_source_has_no_empty_paragraph():
    assert build_caption("제목", "본문.", "", ["AI"]) == "제목\n\n본문.\n\n#AI"


def test_long_caption_trims_body_only_and_keeps_source_and_tags():
    body = " ".join(f"{i}번째 문장은 새 모델의 세부 내용을 설명합니다." for i in range(200))
    source = "출처: The Verge (2026-09-23)"
    tags = [f"태그{i}" for i in range(12)]
    caption = build_caption("아주 중요한 AI 뉴스 제목", body, source, tags)
    assert caption_length(caption) <= INSTAGRAM_CAPTION_LIMIT
    assert caption.startswith("아주 중요한 AI 뉴스 제목\n\n0번째 문장은")
    assert f"\n\n{source}\n\n#태그0" in caption
    assert caption.endswith("#태그11")
