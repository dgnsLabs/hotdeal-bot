# 🛒 hotdeal-bot

쿠팡 파트너스 **반품·리퍼·고할인 특가**를 모아 **GitHub Pages** 사이트로 보여 주고,
**텔레그램으로 하루 세 번 요약 알림**을 보내는 자동화 파이프라인입니다.

- 사이트: <https://dgnslabs.github.io/hotdeal-bot/>
- 저장소: <https://github.com/dgnsLabs/hotdeal-bot>
- 실행 주기: 매일 **09:00 / 14:00 / 20:00 (KST)** 자동 수집 + 알림

---

## 1. 꼭 지키는 2가지 원칙

1. **공정위 대가성 문구는 사이트 최상단 고정**
   `이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.`
   → `docs/index.html` 최상단 고지 배너(`.disclosure`, 스크롤해도 상단 고정)와 푸터에 항상 표시됩니다.
2. **텔레그램 알림에는 쿠팡 제휴 링크를 넣지 않습니다.**
   → 알림에는 상품명·할인율·가격 요약과 자체 사이트 주소만 들어갑니다. 제휴 링크는 사이트 카드의
   `쿠팡에서 보기` 버튼에만 있습니다.
   → `scripts/notify_telegram.py`는 본문에 `link.coupang.com` 등 제휴 링크가 섞이면 **발송을 중단**합니다.

---

## 2. 등록이 필요한 GitHub Secrets 4개

> 저장소 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret 이름 | 어디서 얻나 | 필수 |
| --- | --- | --- |
| `COUPANG_ACCESS_KEY` | 쿠팡 파트너스 로그인 → 도움말/Open API 메뉴에서 발급한 Access Key | ✅ |
| `COUPANG_SECRET_KEY` | 같은 화면의 Secret Key (발급 직후 한 번만 보이는 값) | ✅ |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 `@BotFather` → `/newbot` → 받은 토큰 (`123456:ABC…`) | ✅ |
| `TELEGRAM_CHAT_ID` | 알림 받을 채팅방/대화의 ID (아래 3번 참고) | ✅ |

선택 값(저장소 변수 또는 워크플로우 입력으로 조정 가능):

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `COUPANG_SUB_ID` | `hotdeal-bot` | 쿠팡 파트너스 채널 구분용 subId (정산 추적용) |
| `MIN_DISCOUNT_RATE` | `10` | 최소 할인율(%). 상품명에 할인율 표기가 있는 상품에 적용 |
| `MIN_LIMIT` / `MAX_ITEMS` | `60` | 사이트에 노출할 최대 상품 수 |
| `SEARCH_KEYWORDS` | `리퍼노트북,공식리퍼,전시상품` | 키워드 검색으로 긁을 말 |
| `CATEGORY_IDS` | `1016,1012,1013,1007` | 베스트 카테고리 ID (가전디지털·식품·주방용품·1007) |
| `QUOTA_IT` / `QUOTA_REFURB` | `20` / `12` | 60칸 중 가전·IT / 반품·리퍼에 먼저 배정할 칸 수 |
| `IT_BEST_LIMIT` | `50` | 가전디지털(1016) 베스트에서 받아 올 후보 수 |
| `SITE_URL` | `https://dgnslabs.github.io/hotdeal-bot/` | 알림에 넣을 사이트 주소 |

> 4개 비밀값을 **아직 등록하지 않아도** 사이트는 정상 배포됩니다. 수집 스크립트는 키가 없으면
> 안내 로그만 남기고 종료 코드 0으로 끝나며, 사이트는 "준비 중" 안내 카드를 보여 줍니다.

---

## 3. 텔레그램 chat_id 구하는 법

1. `@BotFather` 에서 `/newbot` 으로 봇을 만들고 **토큰**을 복사합니다.
2. 만든 봇과의 대화방에서 아무 메시지나 한 번 보냅니다(또는 봇을 알림 받을 채널/그룹에 초대).
3. 브라우저에서 아래 주소를 열어 `"chat":{"id": …}` 값을 확인합니다.
   ```
   https://api.telegram.org/bot<봇토큰>/getUpdates
   ```
   - 개인 대화: 양수 ID (예: `123456789`)
   - 채널/그룹: 음수 ID (예: `-1001234567890`) → `-100…` 전체를 그대로 넣습니다.

---

## 4. 동작 구조

```
[매일 09·14·20시 KST / 수동 실행]
        │
        ▼
scripts/fetch_deals.py ── 쿠팡 파트너스 Open API (HMAC-SHA256 서명)
   ├ 골드박스(오늘의 특가)
   ├ 베스트 카테고리 4종 (가전디지털 1016 + 식품·주방·1007)
   └ 키워드 검색 (리퍼노트북·공식리퍼·전시상품)
        │  할인율 계산 · 카테고리 분류 · 중복 제거 · 카테고리 쿼터 선별
        │  (반품·리퍼 12칸, 가전·IT 20칸을 먼저 확보하고 나머지만 균형 배분)
        ▼
docs/data/deals.json      ← 사이트가 읽는 데이터
docs/data/price_history.json  ← 관측 최고가/최저가(할인율 계산용)
        │
        ▼
scripts/notify_telegram.py ── 신규 딜만 텔레그램 요약 발송
        │                        (사이트 주소만, 제휴 링크 없음)
        ▼
docs/data/sent_ids.json   ← 이미 알린 상품 기록(중복 알림 방지)
        │
        ▼
GitHub Actions: 변경분 커밋 → GitHub Pages 재배포
```

| 경로 | 역할 |
| --- | --- |
| `docs/index.html` | 사이트 본문, 최상단 공정위 고지 배너 |
| `docs/style.css` | 다크/라이트 테마, 반응형 카드 UI |
| `docs/app.js` | `data/deals.json` 로드, 카테고리 필터·검색·정렬, 테마 전환 |
| `docs/data/deals.json` | 사이트가 읽는 특가 목록(수집 결과) |
| `docs/data/sent_ids.json` | 알림 발송 이력 |
| `docs/data/price_history.json` | 상품별 관측 최고/최저가 이력 |
| `scripts/fetch_deals.py` | 수집·선별·저장 (키 없으면 안전 종료) |
| `scripts/notify_telegram.py` | 신규 딜 텔레그램 요약 발송 (토큰 없으면 안전 종료) |
| `.github/workflows/pages.yml` | `main` 푸시 시 Pages 자동 배포 |
| `.github/workflows/hotdeal.yml` | 스케줄 수집 → 알림 → 커밋 → Pages 재배포 |

---

## 5. 로컬에서 돌려 보기

```bash
python3 scripts/fetch_deals.py --self-test   # 네트워크 없이 로직 검증
python3 scripts/fetch_deals.py --dry-run     # 수집 결과만 확인 (파일 미변경)

COUPANG_ACCESS_KEY=... COUPANG_SECRET_KEY=... python3 scripts/fetch_deals.py
python3 scripts/notify_telegram.py --dry-run  # 알림 본문 미리보기

cd docs && python3 -m http.server 8000        # http://localhost:8000
```

수동 실행(즉시 한 번 수집): 저장소 **Actions** → `Collect deals and notify` → **Run workflow**.

---

## 6. 알아 둘 점

- **할인율 표기**: 쿠팡 파트너스 API 응답에는 정가·할인율 필드가 없습니다. 그래서
  ① 상품명에 적힌 `35% 할인` 표기를 먼저 쓰고, ② 없으면 이 저장소가 지켜본 **관측 최고가 대비 하락률**
  (`price_history.json`)을 씁니다. 둘 다 없으면 할인율 뱃지를 표시하지 않습니다(가격만 표기).
- **API 호출 제한**: 쿠팡 파트너스는 시간당 30회 이상 호출 시 제한됩니다. 이 스크립트는 1회 실행에
  최대 12회(기본 8회: 골드박스 1 + 베스트 4 + 검색 3)만 호출하고 호출 사이 0.6초를 쉽니다.
- **카테고리 쿼터**: 쿠팡 API에 할인율 필드가 없어 할인율 없는 상품이 가격 오름차순으로 밀립니다.
  그대로 60개를 자르면 고가 가전·IT가 전부 잘려 나가므로, 반품·리퍼 → 가전·IT 몫을 먼저 채우고
  남은 칸만 생활·뷰티·기타로 번갈아 채웁니다(`QUOTA_IT`, `QUOTA_REFURB`로 조정).
- **가격·재고**: 사이트의 가격은 수집 시점 값입니다. 실제 결제 가격은 쿠팡 상품 페이지에서 확인해야 하며,
  사이트 상단·푸터에 이 안내를 항상 표시합니다.
- **인증 실패 시**: 키가 틀리거나 서버 시간이 어긋나면 401이 납니다. 스크립트는 로그만 남기고
  기존 데이터를 유지한 채 종료 코드 0으로 끝납니다(워크플로우가 빨갛게 죽지 않게).
- **데모 상태**: 아직 실상품이 없을 때 사이트는 가짜 가격 없이 "샘플/준비 중" 카드를 보여 줍니다.

---

## 7. 문제 해결

| 증상 | 확인할 것 |
| --- | --- |
| 사이트에 "첫 수집 대기 중" 이 계속 뜸 | Actions 로그에서 `[skip] 쿠팡 파트너스 API 키가 없어…` 확인 → Secrets 등록 |
| 알림이 오지 않음 | Actions 로그의 `[skip]`, `[warn]` 확인. 채널이면 봇을 관리자로 초대했는지 확인 |
| `rCode=…` 오류 | 키 오타, 서버 시간 오차(서명은 GMT 기준), 앱스토어 발급 키인지 확인 |
| Actions가 커밋을 못 함 | Settings → Actions → General → Workflow permissions 가 `Read and write` 인지 확인 |
