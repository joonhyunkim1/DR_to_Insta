"""슬롯 하나를 처리한다: 뉴스 선택 -> 게시물 생성 -> 이미지 합성/업로드 -> 정각 대기 -> 발행 -> 기록.

미리 만들어 대기열에 쌓지 않고 슬롯 시점에 만든다. DR이 늦게 돈 날에도 다음 슬롯부터 바로
반영되고, 대기열 상태를 관리할 필요가 없다.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

from . import repository as repo
from .config import AppConfig
from .content.llm_client import LLM
from .content.post_builder import build_caption, build_hashtags, normalize_post
from .content.selector import Selection, SelectionResult, choose_news
from .db import ROOT_DIR
from .images import composer, template
from .images.ai_background import ImageBackend
from .images.storage import S3LikeClient, upload_image
from .news import ORIGIN_WEB, NewsItem
from .notify.telegram import Notifier
from .publish.instagram_client import InstagramAPIError, InstagramClient
from .slots import Slot, seconds_until

logger = logging.getLogger(__name__)

PREVIEW_DIR = ROOT_DIR / "data" / "preview"

STATUS_PUBLISHED = "published"
STATUS_ALREADY_PUBLISHED = "already_published"
STATUS_DRY_RUN = "dry_run"
STATUS_NO_NEWS = "no_news"


@dataclass
class Deps:
    llm: LLM
    image_backend: ImageBackend
    fetch_dr: Callable[[date], list[NewsItem]]
    instagram: Optional[InstagramClient] = None  # dry-run이면 None
    storage: Optional[S3LikeClient] = None  # dry-run이면 None
    notifier: Optional[Notifier] = None
    now: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))
    sleep: Callable[[float], None] = time.sleep


@dataclass
class GeneratedPost:
    title: str
    slides: list[str]  # 렌더링된 순서 그대로: [제목(커버), 본문...]
    caption: str
    images: list[Image.Image]


@dataclass
class SlotOutcome:
    status: str
    slot: Slot
    title: Optional[str] = None
    media_id: Optional[str] = None
    via_fallback: bool = False
    preview_dir: Optional[Path] = None
    message: str = ""


def _redact(text: str) -> str:
    """토큰/DB 비밀번호가 텔레그램이나 (공개 저장소에 커밋되는) DB에 남지 않도록 가린다."""
    text = re.sub(r"(access_token=)[^&\s'\"]+", r"\1***", text)
    return re.sub(r"postgres(?:ql)?://\S+", "postgres://***", text)


def _notify(deps: Deps, text: str) -> None:
    if deps.notifier is not None:
        deps.notifier.send(text)


def load_dr_items(deps: Deps, slot: Slot, cfg: AppConfig) -> tuple[list[NewsItem], Optional[str]]:
    """DR 조회 실패는 예외로 끝내지 않고 (빈 목록, 사유)로 돌려서 폴백으로 넘어가게 한다."""
    since = slot.local_date - timedelta(days=cfg.source.dr_lookback_days - 1)
    try:
        return deps.fetch_dr(since), None
    except Exception as e:  # noqa: BLE001
        reason = _redact(f"{type(e).__name__}: {e}").splitlines()[0][:200]
        logger.warning("DR 조회 실패: %s", reason)
        return [], reason


def generate_post(item: NewsItem, deps: Deps, cfg: AppConfig) -> GeneratedPost:
    raw = deps.llm.write_post(item, cfg.posting.body_slides_min, cfg.posting.body_slides_max)
    post = normalize_post(raw, cfg.posting.body_slides_min, cfg.posting.body_slides_max)
    hashtags = build_hashtags(post.hashtags, cfg.content.fixed_hashtags, cfg.content.max_hashtags)
    caption = build_caption(post.title, post.caption, item.source_line(), hashtags)
    slides = [post.title, *post.body_slides]
    images = composer.compose_slides(
        post.title,
        slides,
        deps.image_backend,
        cfg.models.image_quality,
        template.load_brand_style(cfg),
    )
    return GeneratedPost(title=post.title, slides=slides, caption=caption, images=images)


def _origin_label(selection: Selection) -> str:
    if selection.via_fallback or selection.item.origin == ORIGIN_WEB:
        return "실시간 검색 폴백"
    return f"DR {selection.item.batch_date} 배치"


def _save_preview(
    root: Path, slot: Slot, post: GeneratedPost, result: SelectionResult
) -> Path:
    out = root / slot.file_key
    out.mkdir(parents=True, exist_ok=True)
    for i, image in enumerate(post.images):
        image.save(out / f"{i:02d}.jpg", format="JPEG", quality=90)
    (out / "caption.txt").write_text(post.caption, encoding="utf-8")
    selection = result.selection
    meta = {
        "slot": slot.key,
        "slides": post.slides,
        "news": asdict(selection.item) if selection else None,
        "impact": selection.impact if selection else None,
        "via_fallback": selection.via_fallback if selection else None,
        "fallback_reason": result.fallback_reason,
        "considered": [{"title": title, "impact": impact} for title, impact in result.considered],
        "skipped": [{"title": s.item.title, "reason": s.reason} for s in result.skipped],
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ---- 발행 실패 분류 (AInstagram queue_worker와 같은 기준) ----

# 토큰 만료(190)/세션(102)/권한(10, 200번대)/호출 한도(4, 17, 32, 613): 뉴스가 아니라 계정 문제
_ACCOUNT_ERROR_CODES = {4, 10, 17, 32, 102, 190, 613}
_ITEM_ERROR_SUBCODES = {2207010}  # 캡션 길이 초과


def _handle_publish_failure(
    conn: sqlite3.Connection, item: NewsItem, error: Exception, deps: Deps, cfg: AppConfig, slot: Slot
) -> None:
    message = _redact(str(error))
    max_attempts = cfg.source.max_attempts
    if isinstance(error, InstagramAPIError) and error.path.endswith("media_publish"):
        # 실제로는 올라갔을 수 있다 - 같은 뉴스로 다시 시도하면 중복 게시 위험
        repo.record_news_failure(conn, item, message, give_up_at=max_attempts)
        note = "발행 요청 단계 실패 - 실제로는 게시됐을 수 있어 이 뉴스는 다시 시도하지 않습니다. 피드를 확인해주세요."
    elif isinstance(error, InstagramAPIError) and (
        error.code in _ACCOUNT_ERROR_CODES or (isinstance(error.code, int) and 200 <= error.code < 300)
    ):
        note = "토큰/권한/호출 한도 문제로 보입니다 (뉴스는 다음 슬롯에 다시 후보가 됩니다)."
    elif isinstance(error, InstagramAPIError) and error.subcode in _ITEM_ERROR_SUBCODES:
        repo.record_news_failure(conn, item, message, give_up_at=max_attempts)
        note = "인스타그램이 게시물 내용을 거절해 이 뉴스는 포기합니다."
    else:
        attempts = repo.record_news_failure(conn, item, message)
        note = f"실패 {attempts}/{max_attempts}회" + (" - 이 뉴스는 포기합니다." if attempts >= max_attempts else "")
    _notify(deps, f"❌ {slot.label} 게시 실패: {item.title}\n\n{message[:1000]}\n\n→ {note}")


def run_slot(
    conn: sqlite3.Connection,
    slot: Slot,
    deps: Deps,
    cfg: AppConfig,
    *,
    dry_run: bool = False,
    wait: bool = True,
    force_fallback: bool = False,
    preview_root: Path = PREVIEW_DIR,
) -> SlotOutcome:
    """dry_run이면 R2/인스타/DB/텔레그램 어디에도 쓰지 않고 data/preview/에 결과만 남긴다."""
    if repo.post_exists(conn, slot.key):
        return SlotOutcome(STATUS_ALREADY_PUBLISHED, slot, message="이미 게시된 슬롯")

    dr_items, dr_error = ([], None) if force_fallback else load_dr_items(deps, slot, cfg)
    since = (deps.now() - timedelta(days=cfg.dedup.window_days)).isoformat()
    result = choose_news(
        dr_items=dr_items,
        excluded_keys=repo.excluded_news_keys(conn, cfg.source.max_attempts),
        recent_posts=repo.recent_posts(conn, since),
        llm=deps.llm,
        cfg=cfg,
        today=slot.local_date.isoformat(),
        dr_error=dr_error,
        skip_dr=force_fallback,
    )
    if not dry_run:
        for skip in result.skipped:
            repo.log_news(conn, skip.item, repo.STATUS_SKIPPED, skip.reason)

    selection = result.selection
    if selection is None:
        message = f"게시할 뉴스를 찾지 못해 이번 슬롯은 건너뜁니다 ({result.fallback_reason})"
        if not dry_run:
            _notify(deps, f"⚠️ {slot.label} {message}")
        return SlotOutcome(STATUS_NO_NEWS, slot, message=message)

    item = selection.item
    try:
        post = generate_post(item, deps, cfg)
    except Exception as e:
        if not dry_run:
            reason = _redact(f"{type(e).__name__}: {e}")
            attempts = repo.record_news_failure(conn, item, f"생성 실패: {reason}")
            _notify(
                deps,
                f"❌ {slot.label} 게시물 생성 실패: {item.title}\n\n{reason[:1000]}\n\n"
                f"→ 실패 {attempts}/{cfg.source.max_attempts}회",
            )
        raise

    if dry_run:
        out = _save_preview(preview_root, slot, post, result)
        return SlotOutcome(
            STATUS_DRY_RUN, slot, title=post.title, via_fallback=selection.via_fallback, preview_dir=out
        )

    assert deps.storage is not None and deps.instagram is not None
    prefix = cfg.image.storage_prefix.strip("/")
    image_urls = [
        upload_image(deps.storage, image, f"{prefix}/{slot.file_key}/{i}.jpg")
        for i, image in enumerate(post.images)
    ]

    if wait:
        remaining = seconds_until(slot, deps.now())
        if remaining > 0:
            deps.sleep(min(remaining, cfg.posting.max_wait_minutes * 60))

    try:
        media_id = deps.instagram.publish_carousel(image_urls, post.caption)
    except Exception as e:
        _handle_publish_failure(conn, item, e, deps, cfg, slot)
        raise

    repo.insert_post(
        conn,
        slot_key=slot.key,
        item=item,
        title=post.title,
        caption=post.caption,
        slides=post.slides,
        image_urls=image_urls,
        embedding=selection.embedding,
        instagram_media_id=media_id,
    )
    repo.log_news(conn, item, repo.STATUS_POSTED)

    lines = [f"✅ {slot.label} 게시 완료", post.title, f"뉴스: {_origin_label(selection)}"]
    if selection.impact is not None:
        lines[-1] += f" · 임팩트 {selection.impact}/10"
    if selection.via_fallback and result.fallback_reason:
        lines.append(f"폴백 사유: {result.fallback_reason}")
    _notify(deps, "\n".join(lines))

    return SlotOutcome(
        STATUS_PUBLISHED, slot, title=post.title, media_id=media_id, via_fallback=selection.via_fallback
    )
