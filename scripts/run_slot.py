"""게시 슬롯 하나를 처리한다 (GitHub Actions가 슬롯 20분 전에 호출).

    python scripts/run_slot.py                  # 지금 시각이 속한 슬롯 (워크플로우용)
    python scripts/run_slot.py --now            # 슬롯 창과 무관하게 지금 바로 게시 (수동 실행)
    python scripts/run_slot.py --dry-run --now  # 생성만 하고 data/preview/에 저장 (인스타/R2/DB 반영 없음)

    --plain-images    AI 배경 대신 단색 배경 (이미지 비용 없이 레이아웃/문구만 확인)
    --force-fallback  DR을 건너뛰고 실시간 검색 폴백 경로로 생성
    --no-wait         정각까지 기다리지 않고 바로 발행
"""
import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from drinsta.config import get_config
from drinsta.content.llm_client import OpenAILLM
from drinsta.db import get_connection
from drinsta.images.ai_background import OpenAIImageBackend, PlainBackground
from drinsta.images.storage import get_r2_client
from drinsta.notify.telegram import TelegramNotifier
from drinsta.pipeline import STATUS_NO_NEWS, Deps, run_slot
from drinsta.publish.instagram_client import InstagramClient
from drinsta.slots import immediate_slot, resolve_slot
from drinsta.sources.dr_source import fetch_dr_news


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", action="store_true", help="슬롯 창과 무관하게 지금 바로 처리")
    parser.add_argument("--dry-run", action="store_true", help="인스타/R2/DB/텔레그램 반영 없이 생성만")
    parser.add_argument("--plain-images", action="store_true", help="AI 배경 대신 단색 배경")
    parser.add_argument("--force-fallback", action="store_true", help="DR 없이 실시간 검색 폴백으로")
    parser.add_argument("--no-wait", action="store_true", help="정각까지 기다리지 않음")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = get_config()
    now = datetime.now(timezone.utc)

    if args.now:
        slot = immediate_slot(now, cfg.posting.timezone)
    else:
        slot = resolve_slot(
            now,
            cfg.posting.times,
            cfg.posting.timezone,
            cfg.posting.window_before_minutes,
            cfg.posting.window_after_minutes,
        )
        if slot is None:
            print("지금은 어느 게시 슬롯 창에도 속하지 않아 아무것도 하지 않음 (--now로 강제 실행 가능)")
            return 0

    if not cfg.dr_database_url and not args.force_fallback:
        print("DR_DATABASE_URL이 없어 DR 조회는 실패 처리되고 실시간 검색 폴백으로 진행합니다.")

    deps = Deps(
        llm=OpenAILLM.from_config(cfg),
        image_backend=PlainBackground() if args.plain_images else OpenAIImageBackend.from_config(),
        fetch_dr=lambda since: fetch_dr_news(cfg.dr_database_url, since),
        instagram=None if args.dry_run else InstagramClient.from_env(),
        storage=None if args.dry_run else get_r2_client(),
        notifier=None if args.dry_run else TelegramNotifier.from_env(prefix=f"[{cfg.account_name}]"),
    )

    outcome = run_slot(
        get_connection(),
        slot,
        deps,
        cfg,
        dry_run=args.dry_run,
        wait=not args.no_wait,
        force_fallback=args.force_fallback,
    )

    print(f"[{slot.key}] {outcome.status}: {outcome.title or outcome.message}")
    if outcome.preview_dir:
        print(f"미리보기: {outcome.preview_dir}")
    if outcome.media_id:
        print(f"인스타그램 미디어 ID: {outcome.media_id}")
    # 게시할 뉴스를 못 찾은 경우 워크플로우를 실패로 표시해서 GitHub 알림 메일이 가게 한다
    return 1 if outcome.status == STATUS_NO_NEWS else 0


if __name__ == "__main__":
    sys.exit(main())
