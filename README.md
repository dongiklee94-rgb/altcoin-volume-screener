# altcoin-volume-screener

알트코인 스크리너. 매시간 자동으로 돌면서 조건에 맞는 종목을 텔레그램으로 알려준다.

**⚠️ 이 알림은 차트를 볼 후보 목록이지 매수 신호가 아니다.**

---

## 무엇을 하는가

세 종류의 결과를 하나의 메시지로 보낸다.

| 구분 | 무엇을 찾나 | 데이터 소스 |
|---|---|---|
| **A** | 평소 대비 거래량이 튄 코인 (방향 무관) | CoinGecko + CoinMarketCap |
| **B** | 이미 상승을 시작한 저가 알트 (진입 후보) | Binance 현물 미러 |
| **C** | A와 B 양쪽에 동시에 걸린 종목 | 위 둘의 교집합 |

C가 가장 강한 신호일 가능성이 높아 메시지 맨 위에 표시된다.

---

## 조건

### A · 거래량 급등 (세 조건 모두 충족)

| 조건 | 기본값 | 설정 변수 |
|---|---|---|
| 최소 유동성 (현물+선물 24h) | $4,000만 이상 | `A_MIN_VOLUME_FLOOR_USD` |
| RVOL (최근 7일 평균 대비) | 2배 이상 | `A_RVOL_MULTIPLIER_THRESHOLD` |
| 회전율 (현물 거래량 ÷ 시총) | 30% 이상 | `A_TURNOVER_RATIO_THRESHOLD` |

BTC, ETH, 스테이블코인은 제외한다.

회전율에 현물 거래량만 쓰는 이유: 선물은 레버리지 때문에 명목 거래량이 부풀려져서
"실제 코인이 얼마나 손바뀜 했는지"를 왜곡한다.

**A는 히스토리 축적이 필요하다.** CoinGecko가 "최근 N시간 평균"을 주지 않기 때문에
실행할 때마다 스냅샷을 직접 쌓는다. 스냅샷 12개(약 12시간)가 모여야 판단을 시작한다.
GitHub Actions 캐시로 유지되며, 캐시가 만료되면 처음부터 다시 쌓는다.

### B · 진입 후보 (6개 조건 중 4개 이상)

갓물주 스크리너 명세서 v1.0의 조건을 그대로 구현했다.

| # | 조건 | 기준 |
|---|---|---|
| ① | 1시간 거래대금 급증 (V) | 직전 24봉 평균의 1.5배 이상 |
| ② | 직전 24시간 수익률 | +3% 이상 |
| ③ | 직전 4시간 수익률 | +2% 이상 |
| ④ | MA20 이격도 | +3% 이상 |
| ⑤ | 연속 상승봉 | 2개 이상 |
| ⑥ | 30봉 저점 대비 | +10% 이상 |

유니버스: USDT 마켓 / 가격 $1 미만 / 24h 거래대금 $200만 이상 (약 90~100개 종목)

V가 3배 이상이면 🔥 태그가 붙는다. 명세서에서 집중배율이 가장 높았던(19.6배)
단일 조건이라 점수가 낮아도 눈여겨볼 만하다.

---

## 알림 형식

```
📊 알트 스크리너 (08-21 14:08 KST)

🔴 C · 양쪽 충족 1
6점🔥 SPK · V3.3 · 24H+14% · MA+16% · RVOL3.1

🎯 B · 진입 후보 6
6점 YB · V1.8 · 24H+18% · MA+19%
5점🔥 SKY · V7.2 · 24H+3% · MA+7%

🚨 A · 거래량 급등 2
SOL · RVOL3.1 · 회전38% · $2.10B
ARB · RVOL2.4 · 회전45% · $85.3M ⚠️
```

- `V` = 1시간 거래대금 배율 (B)
- `RVOL` = 최근 7일 평균 대비 거래량 배율 (A)
- `MA` = MA20 이격도
- `⚠️` = CoinGecko와 CoinMarketCap 수치가 30% 넘게 차이남 (자전거래 의심)

같은 종목은 12시간 내 중복 알림하지 않는다. 단, 점수가 올라간 경우는 예외로 다시 알린다.
조건에 맞는 종목이 없으면 아무것도 보내지 않는다.

---

## 설정

`unified_screener.py` 상단에서 조정한다.

```python
B_SCORE_CUTOFF = 4          # 알림이 너무 많으면 5로
B_MAX_PRICE_USDT = 1.0      # 가격 상한
B_MIN_24H_QUOTE_VOLUME = 2_000_000
ALERT_COOLDOWN_HOURS = 12
SEND_MESSAGE_WHEN_EMPTY = False
```

### 필요한 Secrets

저장소 Settings → Secrets and variables → Actions

| 이름 | 필수 | 용도 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | 알림 전송 |
| `TELEGRAM_CHAT_ID` | ✅ | 받을 채팅방 |
| `COINGECKO_API_KEY` | A에 필요 | 없으면 B만 돌아감 |
| `COINMARKETCAP_API_KEY` | 선택 | A의 교차검증용 |

**텔레그램 토큰은 `crypto-tg-bot` 저장소와 공유한다.** 토큰을 재발급하면 양쪽 다 업데이트해야 한다.

---

## 실행

```bash
pip install requests
python unified_screener.py                # 실행
python unified_screener.py --self-test    # 네트워크 없이 로직 검증
python unified_screener.py --dry-run      # 전송 없이 출력만
python unified_screener.py --skip-a       # B만
python unified_screener.py --skip-b       # A만
```

로컬 실행 시 `.env` 파일에 키를 넣으면 자동으로 읽는다. (`.gitignore`에 포함되어 있음)

GitHub Actions는 매시 5분에 자동 실행된다. 크론은 부하에 따라 5~20분 밀릴 수 있다.

---

## 알아둘 점

**바이낸스 선물 API를 쓰지 못한다.** `fapi.binance.com`과 `api.binance.com` 모두
GitHub Actions 러너에서 451(지역 차단)을 반환한다. Bybit도 403으로 막혀 있다.
그래서 인증·지역 제한이 없는 공개 미러 `data-api.binance.vision`(현물)을 사용한다.

원 명세서는 바이낸스 USDT 무기한선물 기준이므로:
- 종목 구성이 다르다 (선물 전용 상장 코인이 빠진다)
- 거래대금 규모가 선물보다 작다
- 가격은 거의 같다 (실측 0.1% 내외) → 조건 ②③④⑥은 사실상 동일
- 조건 ①과 유동성 필터는 달라진다

**명세서의 통계 수치를 그대로 믿으면 안 된다.** 원 분석은 추천 51건 vs 무작위 138건
비교였는데 **손실 사례가 한 건도 없는 데이터**였다. "어떤 자리를 고르는가"까지만
검증됐고 "그 자리가 수익이 나는가"는 검증되지 않았다. 점수와 수익률의 상관계수는
-0.50으로, 점수가 높다고 더 오르지도 않았다.

따라서 점수를 포지션 크기나 목표가 산출에 쓰지 말 것. 후보 목록과 순위 매기기 용도로만.

접근 가능한 대체 소스(참고): OKX 선물, Gate.io 선물. OKX는 캔들이 최신→오래된 순이고
`confirm` 플래그로 마감봉을 확실히 구분할 수 있다는 장점이 있다.

---

## 파일 구조

```
unified_screener.py                     메인 스크립트
.github/workflows/unified-screener.yml  매시 5분 자동 실행
altcoin_volume_screener.py              구버전(A 전용). 통합본에 흡수됨
.github/workflows/volume-screener.yml   구버전 워크플로우
```

상태 파일 두 개가 Actions 캐시로 유지된다.
- `volume_history.json` — A의 RVOL 계산용 스냅샷
- `alert_log.json` — 중복 알림 방지 기록
