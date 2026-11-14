#!/usr/bin/env python3
"""새로 수집된 딜을 텔레그램으로 요약 발송한다.

원칙(전자책 부록 팩트체크 반영):
  - 텔레그램 알림에는 쿠팡 파트너스 제휴(직)링크를 넣지 않는다.
  - 상품명·할인율·가격 요약과 자체 사이트 링크만 보낸다.

환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (없으면 조용히 건너뛰고 종료 코드 0)
사용 예:  python3 scripts/notify_telegram.py [--dry-run] [--limit 8]
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "docs" / "data"
DEALS_PATH = DATA_DIR / "deals.json"
SENT_PATH = DATA_DIR / "sent_ids.json"

DEFAULT_SITE_URL = "https://dgnslabs.github.io/hotdeal-bot/"
NOTICE = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
FORBIDDEN_HOSTS = ("link.coupang.com", "coupang.com", "coupa.ng", "partners.coupang.com")
HISTORY_KEEP_DAYS = 60


def log(msg: str) -> None:
    print(msg, flush=True)


def iso(when: datetime) -> str:
    return when.astimezone(KST).isoformat(timespec="seconds")


def load_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError) as exc:
        log(f"[warn] {path.name} 을(를) 읽지 못했습니다 ({exc}).")
        return default


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tmp.replace(path)


def won(price: int | None) -> str:
    if not price:
        return "가격 미확인"
    return f"{price:,}원"


def deal_line(deal: dict) -> str:
    name = html.escape(str(deal.get("name") or "상품명 없음"))
    if len(name) > 60:
        name = name[:58] + "…"
    parts = []
    rate = deal.get("discount_rate")
    if rate:
        parts.append(f"{round(float(rate))}% 할인")
    parts.append(won(deal.get("price")))
    tags = [t for t in ("골드박스", "로켓배송") if t in (deal.get("tags") or [])]
    tail = f" ({', '.join(tags)})" if tags else ""
    return f"{name} — {' · '.join(parts)}{tail}"


def build_message(deals: list[dict], site_url: str, when: datetime) -> str:
    head = f"🛒 <b>오늘의 핫딜</b> · {when.month}/{when.day} {when:%H:%M}"
    lines = [head, ""]
    for idx, deal in enumerate(deals, start=1):
        lines.append(f"{idx}. {deal_line(deal)}")
    lines += [
        "",
        f"👉 전체 목록 보기: {site_url}",
        "",
        f"<i>{NOTICE}</i>",
    ]
    return "\n".join(lines)


def assert_no_affiliate_links(message: str, site_url: str) -> None:
    """알림 본문에 쿠팡 제휴 링크가 섞이면 발송을 중단한다."""
    lowered = message.lower()
    for host in FORBIDDEN_HOSTS:
        if host in lowered:
            raise SystemExit(f"[abort] 알림 본문에 제휴 링크({host})가 포함되어 발송하지 않습니다.")
    urls = re.findall(r"https?://[^\s<>\"]+", message)
    allowed = site_url.rstrip("/")
    for url in urls:
        if not url.startswith(allowed):
            raise SystemExit(f"[abort] 허용되지 않은 링크가 본문에 있습니다: {url}")


def send_telegram(token: str, chat_id: str, text: str) -> dict:
    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description") or "telegram error")
    return payload


def prune_notified(notified: dict, when: datetime) -> dict:
    cutoff = when - timedelta(days=HISTORY_KEEP_DAYS)
    kept = {}
    for pid, stamp in notified.items():
        try:
            if datetime.fromisoformat(stamp) >= cutoff:
                kept[pid] = stamp
        except (TypeError, ValueError):
            continue
    return kept


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="텔레그램 핫딜 요약 발송")
    parser.add_argument("--dry-run", action="store_true", help="발송하지 않고 본문만 출력")
    parser.add_argument("--limit", type=int, default=int(os.getenv("NOTIFY_MAX", "8")))
    parser.add_argument("--max-age-hours", type=int, default=int(os.getenv("NOTIFY_MAX_AGE_HOURS", "36")))
    args = parser.parse_args(argv)

    when = datetime.now(KST)
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    site_url = (os.getenv("SITE_URL") or DEFAULT_SITE_URL).strip()

    snapshot = load_json(DEALS_PATH, {})
    deals = snapshot.get("deals") or []
    sent = load_json(SENT_PATH, {"notified": {}})
    notified = sent.get("notified") if isinstance(sent.get("notified"), dict) else {}

    fresh = []
    for deal in deals:
        pid = str(deal.get("id") or "")
        if not pid or pid in notified:
            continue
        first_seen = deal.get("first_seen") or snapshot.get("generated_at")
        try:
            seen_at = datetime.fromisoformat(first_seen)
        except (TypeError, ValueError):
            seen_at = when
        if when - seen_at > timedelta(hours=args.max_age_hours):
            continue
        fresh.append(deal)

    fresh.sort(key=lambda d: (d.get("discount_rate") or 0, -(d.get("price") or 0)), reverse=True)
    fresh = fresh[: max(args.limit, 1)]

    if not fresh:
        log("[skip] 새로 알릴 딜이 없습니다 (정상 종료).")
        return 0

    message = build_message(fresh, site_url, when)
    assert_no_affiliate_links(message, site_url)

    if args.dry_run:
        log("[dry-run] 아래 본문을 보냈을 것입니다:")
        log(message)
        return 0

    if not token or not chat_id:
        log("[skip] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 없어 알림을 건너뜁니다 (정상 종료).")
        log("       Secrets 등록 후 다음 실행부터 발송됩니다.")
        return 0

    try:
        send_telegram(token, chat_id, message)
    except (urllib.error.URLError, RuntimeError, json.JSONDecodeError, TimeoutError) as exc:
        log(f"[warn] 텔레그램 발송 실패: {exc} (다음 실행에서 다시 시도합니다)")
        return 0

    log(f"[sent] {len(fresh)}건 발송 완료")
    stamp = iso(when)
    for deal in fresh:
        notified[str(deal["id"])] = stamp
    write_json(SENT_PATH, {
        "updated_at": stamp,
        "last_notified_at": stamp,
        "notified": prune_notified(notified, when),
    })
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
