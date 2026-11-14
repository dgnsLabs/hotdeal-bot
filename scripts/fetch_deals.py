#!/usr/bin/env python3
"""쿠팡 파트너스 오픈 API에서 반품·특가 상품을 수집해 docs/data/deals.json 을 갱신한다.

- 인증: HMAC-SHA256 CEA 서명 (COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY)
- 수집: 골드박스(오늘의 특가) + 베스트 카테고리 + 키워드 검색
- 중복 제거: productId 기준, 이미 알림을 보낸 상품은 docs/data/sent_ids.json 에 기록
- API 키가 없거나 통신에 실패해도 종료 코드 0 으로 끝나고 기존 데이터를 덮어쓰지 않는다.

사용 예:
    python3 scripts/fetch_deals.py                # 수집 후 deals.json 갱신
    python3 scripts/fetch_deals.py --dry-run      # 결과만 출력
    python3 scripts/fetch_deals.py --self-test    # 네트워크 없이 내부 로직 검증
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
UTC = timezone.utc

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "docs" / "data"
DEALS_PATH = DATA_DIR / "deals.json"
SENT_PATH = DATA_DIR / "sent_ids.json"
HISTORY_PATH = DATA_DIR / "price_history.json"

API_HOST = "https://api-gateway.coupang.com"
API_PREFIX = "/v2/providers/affiliate_open_api/apis/openapi/v1"
GOLDBOX_PATH = f"{API_PREFIX}/products/goldbox"
BEST_PATH = f"{API_PREFIX}/products/bestcategories"
SEARCH_PATH = f"{API_PREFIX}/products/search"

DEFAULT_CATEGORY_IDS = "1007,1005,1013"          # 생활용품, 식품, 디지털/가전
DEFAULT_SEARCH_KEYWORDS = "리퍼,반품,공식리퍼"      # 반품·리퍼 특가 중심
DEFAULT_SUB_ID = "hotdeal-bot"

MAX_API_CALLS = int(os.getenv("MAX_API_CALLS", "12"))
MAX_ITEMS = int(os.getenv("MAX_ITEMS", "60"))
# API 별 limit 상한: 베스트 카테고리는 100, 검색은 10 (초과하면 rCode=400 limit is out of range)
BEST_LIMIT = 20
SEARCH_LIMIT = 10
MIN_DISCOUNT_RATE = int(os.getenv("MIN_DISCOUNT_RATE", "10"))
REQUEST_TIMEOUT = 20

CATEGORY_KEYS = {
    "refurb": "반품·리퍼",
    "it": "가전·IT",
    "living": "생활·식품",
    "beauty": "뷰티·패션",
    "etc": "기타",
}

REFURB_WORDS = ("리퍼", "반품", "중고", "전시", "개봉", "b급", "as-is", " refurb", "전시상품")
IT_WORDS = (
    "디지털", "가전", "컴퓨터", "노트북", "모니터", "태블릿", "휴대폰", "스마트폰", "이어폰",
    "헤드폰", "키보드", "마우스", "공기청정기", "선풍기", "가습기", "청소기", "tv", "셋톱",
    "충전기", "보조배터리", "카메라", "프린터", "냉장고", "세탁기", "에어컨", "전자레인지",
)
LIVING_WORDS = (
    "식품", "생활", "주방", "세제", "휴지", "생필품", "가공식품", "신선", "과일", "채소",
    "정육", "수산", "간식", "음료", "커피", "쌀", "계란", "우유", "라면", "김치", "욕실",
    "세탁", "청소용품", "수납", "침구", "홈인테리어", "반려", "펫",
)
BEAUTY_WORDS = (
    "뷰티", "화장품", "스킨", "로션", "크림", "세럼", "샴푸", "패션", "의류", "신발",
    "가방", "액세서리", "스포츠", "레저", "유아", "아동", "출산",
)

PERCENT_PATTERNS = (
    re.compile(r"(\d{1,2}(?:\.\d)?)\s*%\s*(?:할인|정가|DC|dc|off|OFF)"),
    re.compile(r"(?:할인|DC|dc)\s*(\d{1,2}(?:\.\d)?)\s*%"),
)

NOTICE = (
    "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
)
PRICE_NOTICE = (
    "표시 가격·재고는 수집 시점 기준이며 실시간으로 달라질 수 있습니다. "
    "최종 가격은 쿠팡 상품 페이지에서 확인해 주세요."
)


# --------------------------------------------------------------------------- utils


def now_kst() -> datetime:
    return datetime.now(KST)


def iso(dt: datetime) -> str:
    return dt.astimezone(KST).isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(msg, flush=True)


def load_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError) as exc:
        log(f"[warn] {path.name} 을(를) 읽지 못했습니다 ({exc}). 기본값으로 진행합니다.")
        return default


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tmp.replace(path)


# ------------------------------------------------------------------- API signing


def sign(method: str, path: str, query: str, access_key: str, secret_key: str,
         when: datetime | None = None) -> str:
    """쿠팡 파트너스 CEA 서명 헤더 값을 만든다. message = datetime + method + path + query."""
    moment = (when or datetime.now(UTC)).astimezone(UTC)
    signed_date = moment.strftime("%y%m%dT%H%M%SZ")
    message = f"{signed_date}{method.upper()}{path}{query}"
    signature = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"),
                         hashlib.sha256).hexdigest()
    return (f"CEA algorithm=HmacSHA256, access-key={access_key}, "
            f"signed-date={signed_date}, signature={signature}")


class ApiError(RuntimeError):
    pass


def api_get(path: str, params: dict, access_key: str, secret_key: str) -> dict:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{API_HOST}{path}" + (f"?{query}" if query else "")
    headers = {
        "Authorization": sign("GET", path, query, access_key, secret_key),
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "hotdeal-bot/1.0 (+https://github.com/dgnsLabs/hotdeal-bot)",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        snippet = exc.read().decode("utf-8", "replace")[:300]
        raise ApiError(f"HTTP {exc.code} {path}: {snippet}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ApiError(f"network error {path}: {exc}") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ApiError(f"non-JSON response {path}: {body[:300]}") from exc

    if str(payload.get("rCode", "0")) != "0":
        raise ApiError(f"rCode={payload.get('rCode')} {payload.get('rMessage')} ({path})")
    return payload


def extract_products(payload: dict) -> list[dict]:
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("productData", "products", "items"):
            if isinstance(data.get(key), list):
                return [p for p in data[key] if isinstance(p, dict)]
        return []
    if isinstance(data, list):
        return [p for p in data if isinstance(p, dict)]
    return []


# ------------------------------------------------------------------ classifying


def classify(category_name: str, product_name: str) -> str:
    text = f"{category_name or ''} {product_name or ''}".lower()
    if any(word in text for word in REFURB_WORDS):
        return "refurb"
    if any(word in text for word in IT_WORDS):
        return "it"
    if any(word in text for word in BEAUTY_WORDS):
        return "beauty"
    if any(word in text for word in LIVING_WORDS):
        return "living"
    return "etc"


def parse_discount(name: str) -> float | None:
    for pattern in PERCENT_PATTERNS:
        match = pattern.search(name or "")
        if match:
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            if 1 <= value <= 90:
                return value
    return None


def to_int(value) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def normalize(raw: dict, source: str, seen_at: str) -> dict | None:
    """API 상품 한 건을 사이트용 딜 레코드로 바꾼다. id/이름/가격/링크가 없으면 버린다."""
    product_id = str(raw.get("productId") or "").strip()
    name = (raw.get("productName") or "").strip()
    price = to_int(raw.get("productPrice"))
    url = (raw.get("productUrl") or "").strip()
    if not product_id or not name or price is None or not url.startswith("https://"):
        return None

    category_name = (raw.get("categoryName") or "").strip()
    key = classify(category_name, name)
    rate = parse_discount(name)
    tags: list[str] = []
    if source == "goldbox":
        tags.append("골드박스")
    if raw.get("isRocket"):
        tags.append("로켓배송")
    if raw.get("isFreeShipping"):
        tags.append("무료배송")
    if key == "refurb":
        tags.append("반품·리퍼")

    return {
        "id": product_id,
        "name": name,
        "price": price,
        "original_price": None,
        "discount_rate": rate,
        "discount_source": "name" if rate is not None else None,
        "category": CATEGORY_KEYS[key],
        "category_key": key,
        "category_name": category_name or None,
        "image": (raw.get("productImage") or "").replace("http://", "https://") or None,
        "url": url,
        "rocket": bool(raw.get("isRocket")),
        "free_shipping": bool(raw.get("isFreeShipping")),
        "tags": tags,
        "first_seen": seen_at,
        "last_seen": seen_at,
    }


# ------------------------------------------------------------------ price history


def apply_history(deal: dict, history: dict) -> None:
    """관측 최고가 대비 하락률을 할인율로 환산한다(상품명에 할인율이 없을 때만)."""
    pid = deal["id"]
    entry = history.get(pid) or {}
    prev_max = to_int(entry.get("max"))
    price = deal["price"]
    if deal["discount_rate"] is None and prev_max and price <= prev_max * 0.92:
        rate = round((1 - price / prev_max) * 100, 1)
        if 3 <= rate <= 90:
            deal["discount_rate"] = rate
            deal["discount_source"] = "history"
            deal["original_price"] = prev_max


def update_history(history: dict, deals: list[dict], when: datetime) -> dict:
    stamp = iso(when)
    for deal in deals:
        pid = deal["id"]
        price = deal["price"]
        entry = history.get(pid) or {}
        entry["max"] = max(to_int(entry.get("max")) or price, price)
        entry["min"] = min(to_int(entry.get("min")) or price, price)
        entry["last"] = price
        entry["first_seen"] = entry.get("first_seen") or stamp
        entry["last_seen"] = stamp
        entry["seen"] = int(entry.get("seen") or 0) + 1
        history[pid] = entry

    cutoff = when - timedelta(days=90)
    pruned = {}
    for pid, entry in history.items():
        last_seen = entry.get("last_seen")
        try:
            keep = datetime.fromisoformat(last_seen) >= cutoff
        except (TypeError, ValueError):
            keep = False
        if keep:
            pruned[pid] = entry
    return pruned


# ---------------------------------------------------------------------- fetching


def collect(access_key: str, secret_key: str, sub_id: str,
            category_ids: list[str], keywords: list[str]) -> tuple[list[dict], dict]:
    stamp = iso(now_kst())
    found: dict[str, dict] = {}
    stats = {"api_calls": 0, "errors": [], "sources": {}}

    def take(payload: dict, source: str, label: str) -> None:
        products = extract_products(payload)
        stats["sources"][label] = stats["sources"].get(label, 0) + len(products)
        for raw in products:
            deal = normalize(raw, source, stamp)
            if deal and deal["id"] not in found:
                found[deal["id"]] = deal

    calls_left = MAX_API_CALLS

    def call(path: str, params: dict, label: str) -> dict | None:
        nonlocal calls_left
        if calls_left <= 0:
            return None
        calls_left -= 1
        stats["api_calls"] += 1
        try:
            payload = api_get(path, params, access_key, secret_key)
        except ApiError as exc:
            log(f"[warn] {label} 조회 실패: {exc}")
            stats["errors"].append(f"{label}: {exc}")
            return None
        time.sleep(0.6)  # 파트너스 API 호출 제한(시간당 30회)을 배려한 간격
        return payload

    payload = call(GOLDBOX_PATH, {"subId": sub_id, "imageSize": "512x512"}, "goldbox")
    if payload:
        take(payload, "goldbox", "골드박스")

    for category_id in category_ids:
        payload = call(f"{BEST_PATH}/{category_id}", {"limit": BEST_LIMIT, "subId": sub_id},
                       f"best/{category_id}")
        if payload:
            take(payload, "best", f"베스트 {category_id}")

    for keyword in keywords:
        payload = call(SEARCH_PATH, {"keyword": keyword, "limit": SEARCH_LIMIT, "subId": sub_id},
                       f"search/{keyword}")
        if payload:
            take(payload, "search", f"검색 '{keyword}'")

    return list(found.values()), stats


def filter_deals(deals: list[dict], min_rate: int, history: dict) -> list[dict]:
    kept = []
    for deal in deals:
        apply_history(deal, history)
        rate = deal["discount_rate"]
        if rate is not None and rate < min_rate:
            continue
        kept.append(deal)

    kept.sort(key=lambda d: (d["discount_rate"] is not None,
                             d["discount_rate"] or 0,
                             -d["price"]), reverse=True)
    return kept[:MAX_ITEMS]


# ---------------------------------------------------------------------- snapshot


def build_snapshot(deals: list[dict], previous: dict, stats: dict, when: datetime) -> dict:
    previous_deals = {d.get("id"): d for d in (previous.get("deals") or []) if isinstance(d, dict)}
    new_count = 0
    for deal in deals:
        old = previous_deals.get(deal["id"])
        if old and old.get("first_seen"):
            deal["first_seen"] = old["first_seen"]
        else:
            new_count += 1
    return {
        "generated_at": iso(when),
        "source": "live",
        "site_url": "https://dgnslabs.github.io/hotdeal-bot/",
        "notice": NOTICE,
        "price_notice": PRICE_NOTICE,
        "filters": {"min_discount_rate": MIN_DISCOUNT_RATE},
        "stats": {
            "fetched": stats.get("fetched", 0),
            "kept": len(deals),
            "new": new_count,
            "api_calls": stats["api_calls"],
            "errors": stats["errors"][:5],
        },
        "deals": deals,
    }


def empty_snapshot() -> dict:
    return {
        "generated_at": None,
        "source": "demo",
        "site_url": "https://dgnslabs.github.io/hotdeal-bot/",
        "notice": NOTICE,
        "price_notice": PRICE_NOTICE,
        "filters": {"min_discount_rate": MIN_DISCOUNT_RATE},
        "stats": {"fetched": 0, "kept": 0, "new": 0, "api_calls": 0, "errors": []},
        "deals": [],
    }


# ------------------------------------------------------------------------ self test


def self_test() -> int:
    failures: list[str] = []

    def check(label: str, actual, expected) -> None:
        if actual != expected:
            failures.append(f"{label}: {actual!r} != {expected!r}")

    # 서명: 같은 입력이면 같은 결과, 형식이 맞는지
    when = datetime(2026, 11, 14, 11, 0, 0, tzinfo=UTC)
    header = sign("GET", GOLDBOX_PATH, "subId=hotdeal-bot", "AK", "SK", when=when)
    check("sign format", header.startswith(
        "CEA algorithm=HmacSHA256, access-key=AK, signed-date=261114T110000Z, signature="), True)
    expected_sig = hmac.new(b"SK", b"261114T110000ZGET" + GOLDBOX_PATH.encode()
                            + b"subId=hotdeal-bot", hashlib.sha256).hexdigest()
    check("sign digest", header.endswith(expected_sig), True)
    check("sign stable", sign("GET", GOLDBOX_PATH, "subId=hotdeal-bot", "AK", "SK", when=when),
          header)

    # 상품명 할인율 파싱
    check("pct 1", parse_discount("[특가] 무선청소기 35% 할인"), 35.0)
    check("pct 2", parse_discount("계란 30구 12%DC"), 12.0)
    check("pct none", parse_discount("탐사 3겹 화장지 30롤"), None)
    check("pct guard", parse_discount("100% 국내산 쌀"), None)

    # 카테고리 분류
    check("cls refurb", classify("생활용품", "삼성 무선청소기 리퍼상품"), "refurb")
    check("cls it", classify("디지털/가전", "로지텍 무선 마우스"), "it")
    check("cls living", classify("식품", "제주 감귤 3kg"), "living")
    check("cls beauty", classify("뷰티", "수분 크림 50ml"), "beauty")
    check("cls etc", classify("", "기타 잡화"), "etc")

    # 정규화 + 필터 + 이력
    raw = {
        "productId": 1001, "productName": "리퍼 노트북 20% 할인", "productPrice": 800000,
        "productImage": "http://img/1.jpg", "productUrl": "https://link.coupang.com/a/abc",
        "categoryName": "디지털/가전", "isRocket": True, "isFreeShipping": False,
    }
    deal = normalize(raw, "goldbox", "2026-11-14T20:00:00+09:00")
    check("normalize id", deal["id"], "1001")
    check("normalize image https", deal["image"], "https://img/1.jpg")
    check("normalize tags", deal["tags"][:2], ["골드박스", "로켓배송"])
    check("normalize bad url", normalize({**raw, "productUrl": "javascript:alert(1)"},
                                         "best", "x"), None)
    check("normalize no price", normalize({**raw, "productPrice": 0}, "best", "x"), None)

    history = {"2002": {"max": 10000, "last": 10000}}
    drop = {"id": "2002", "price": 7000, "discount_rate": None, "discount_source": None,
            "original_price": None}
    apply_history(drop, history)
    check("history rate", drop["discount_rate"], 30.0)
    check("history original", drop["original_price"], 10000)

    deals = [
        {"id": "1", "price": 1000, "discount_rate": 5},
        {"id": "2", "price": 2000, "discount_rate": 40},
        {"id": "3", "price": 3000, "discount_rate": None},
    ]
    kept = filter_deals(deals, 10, {})
    check("filter drops low rate", [d["id"] for d in kept], ["2", "3"])

    snapshot = build_snapshot([dict(deals[1], first_seen="x")],
                              {"deals": [{"id": "2", "first_seen": "2026-11-01T09:00:00+09:00"}]},
                              {"api_calls": 1, "errors": []}, when)
    check("first_seen preserved", snapshot["deals"][0]["first_seen"], "2026-11-01T09:00:00+09:00")
    check("new count", snapshot["stats"]["new"], 0)

    if failures:
        log("[self-test] 실패:")
        for item in failures:
            log(f"  - {item}")
        return 1
    log(f"[self-test] 통과 ({len(failures)} 실패)")
    return 0


# ---------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="쿠팡 파트너스 특가 수집")
    parser.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 결과만 출력")
    parser.add_argument("--self-test", action="store_true", help="네트워크 없이 내부 로직 검증")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    when = now_kst()
    access_key = (os.getenv("COUPANG_ACCESS_KEY") or "").strip()
    secret_key = (os.getenv("COUPANG_SECRET_KEY") or "").strip()
    sub_id = (os.getenv("COUPANG_SUB_ID") or DEFAULT_SUB_ID).strip()
    category_ids = [c.strip() for c in
                    (os.getenv("CATEGORY_IDS") or DEFAULT_CATEGORY_IDS).split(",") if c.strip()]
    keywords = [k.strip() for k in
                (os.getenv("SEARCH_KEYWORDS") or DEFAULT_SEARCH_KEYWORDS).split(",") if k.strip()]

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    previous = load_json(DEALS_PATH, empty_snapshot())
    history = load_json(HISTORY_PATH, {})

    if not access_key or not secret_key:
        log("[skip] 쿠팡 파트너스 API 키가 없어 수집을 건너뜁니다 (정상 종료).")
        log("       GitHub 저장소 → Settings → Secrets and variables → Actions 에")
        log("       COUPANG_ACCESS_KEY, COUPANG_SECRET_KEY 를 등록하면 다음 실행부터 수집합니다.")
        if not DEALS_PATH.exists():
            write_json(DEALS_PATH, empty_snapshot())
            log(f"       빈 상태 파일을 만들었습니다: {DEALS_PATH.relative_to(ROOT)}")
        return 0

    log(f"[start] 수집 시작 {iso(when)} (subId={sub_id})")
    deals, stats = collect(access_key, secret_key, sub_id, category_ids, keywords)
    stats["fetched"] = len(deals)

    if not deals:
        log("[warn] 수집된 상품이 없습니다. 기존 deals.json 을 그대로 둡니다.")
        return 0

    kept = filter_deals(deals, MIN_DISCOUNT_RATE, history)
    snapshot = build_snapshot(kept, previous, stats, when)
    updated_history = update_history(history, kept, when)

    log(f"[done] 수집 {stats['fetched']}건 / 노출 {len(kept)}건 / 신규 {snapshot['stats']['new']}건 "
        f"/ API {stats['api_calls']}회")
    for err in stats["errors"]:
        log(f"       - {err}")

    if args.dry_run:
        log(json.dumps(snapshot["deals"][:3], ensure_ascii=False, indent=2))
        return 0

    write_json(DEALS_PATH, snapshot)
    write_json(HISTORY_PATH, updated_history)
    if not SENT_PATH.exists():
        write_json(SENT_PATH, {"updated_at": None, "notified": {}})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
