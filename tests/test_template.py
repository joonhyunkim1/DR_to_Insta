import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image

from drinsta.config import get_config
from drinsta.images import template


def test_render_thumbnail_matches_canvas_size():
    style = template.load_brand_style(get_config())
    bg = Image.new("RGB", (100, 100), color=(50, 50, 50))
    img = template.render_thumbnail(bg, "GPT-5 출시로 달라지는 것들 총정리", style)
    assert img.size == style.canvas_size
    assert img.mode == "RGB"


def test_render_content_slide_matches_canvas_size():
    style = template.load_brand_style(get_config())
    bg = Image.new("RGB", (100, 100), color=(10, 10, 10))
    img = template.render_content_slide(bg, "본문 슬라이드 테스트 문장입니다.", 1, 6, style)
    assert img.size == style.canvas_size


def test_render_thumbnail_wraps_long_topic_without_error():
    style = template.load_brand_style(get_config())
    bg = Image.new("RGB", (100, 100), color=(80, 80, 80))
    long_topic = "이것은 아주 길게 작성된 인스타그램 카드뉴스 썸네일용 주제 문구 테스트입니다 " * 3
    img = template.render_thumbnail(bg, long_topic, style)
    assert img.size == style.canvas_size


def test_sanitize_text_replaces_glyphs_missing_from_font():
    assert template._sanitize_text("ten‑blue‑links") == "ten-blue-links"
    assert template._sanitize_text("soft­hyphen") == "softhyphen"
    assert template._sanitize_text("non breaking") == "non breaking"
    assert template._sanitize_text("normal text") == "normal text"


def _cover_wrap(text):
    from PIL import ImageDraw

    style = template.load_brand_style(get_config())
    w, h = style.canvas_size
    font = template._load_font(style.font_bold_path, int(h * 0.068))
    draw = ImageDraw.Draw(Image.new("RGB", style.canvas_size))
    return template._wrap_text(draw, text, font, int(w * (1 - template.SIDE_MARGIN_RATIO * 2)))


def test_version_number_is_not_split_from_model_name():
    # dry-run에서 "WeatherNext / 3 구글 전면 통합"으로 갈라졌던 커버 제목
    lines = _cover_wrap("WeatherNext 3 구글 전면 통합")
    assert not any(line.startswith("3") for line in lines)
    assert any("WeatherNext 3" in line for line in lines)


def test_keep_versions_attached_only_after_latin_names():
    merge = template._keep_versions_attached
    assert merge("Claude Opus 5.5 공개".split(" ")) == ["Claude", "Opus 5.5", "공개"]
    assert merge("Gemini 3.8 Flash 출시".split(" ")) == ["Gemini 3.8", "Flash", "출시"]
    assert merge("최대 5km 해상도".split(" ")) == ["최대", "5km", "해상도"]
    assert merge("정확도 50% 향상".split(" ")) == ["정확도", "50%", "향상"]
