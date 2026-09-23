"""config.yaml + .env를 읽어 전역 설정 객체를 만든다."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT_DIR / "config" / "config.yaml"

load_dotenv(ROOT_DIR / ".env")


@dataclass
class PostingConfig:
    timezone: str
    times: list[str]
    window_before_minutes: int
    window_after_minutes: int
    max_wait_minutes: int
    body_slides_min: int
    body_slides_max: int


@dataclass
class SourceConfig:
    dr_lookback_days: int
    min_impact: int
    fallback_candidates: int
    max_attempts: int


@dataclass
class DedupConfig:
    similarity_threshold: float
    window_days: int


@dataclass
class ContentConfig:
    fixed_hashtags: list[str]
    max_hashtags: int


@dataclass
class ModelConfig:
    writer: str
    ranker: str
    search: str
    structurer: str
    embedding: str
    image: str
    image_quality: str


@dataclass
class FontConfig:
    regular: str
    bold: str


@dataclass
class BrandConfig:
    primary_color: str
    overlay_opacity: int
    canvas_size: tuple[int, int]


@dataclass
class ImageConfig:
    font: FontConfig
    brand: BrandConfig
    storage_prefix: str


@dataclass
class AppConfig:
    account_name: str
    posting: PostingConfig
    source: SourceConfig
    dedup: DedupConfig
    content: ContentConfig
    models: ModelConfig
    image: ImageConfig
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    dr_database_url: str = field(default_factory=lambda: os.getenv("DR_DATABASE_URL", ""))


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    posting = raw["posting"]
    image = raw["image"]
    return AppConfig(
        account_name=raw["instagram"]["account_name"],
        posting=PostingConfig(
            timezone=posting["timezone"],
            times=posting["times"],
            window_before_minutes=posting["slot_window_minutes"]["before"],
            window_after_minutes=posting["slot_window_minutes"]["after"],
            max_wait_minutes=posting["max_wait_minutes"],
            body_slides_min=posting["body_slides"]["min"],
            body_slides_max=posting["body_slides"]["max"],
        ),
        source=SourceConfig(**raw["source"]),
        dedup=DedupConfig(**raw["dedup"]),
        content=ContentConfig(**raw["content"]),
        models=ModelConfig(**raw["models"]),
        image=ImageConfig(
            font=FontConfig(**image["font"]),
            brand=BrandConfig(
                primary_color=image["brand"]["primary_color"],
                overlay_opacity=image["brand"]["overlay_opacity"],
                canvas_size=tuple(image["brand"]["canvas_size"]),
            ),
            storage_prefix=image["storage_prefix"],
        ),
    )


_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config
