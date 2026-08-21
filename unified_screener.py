#!/usr/bin/env python3
"""
고래와개미팀 - 통합 알트코인 스크리너

두 개의 서로 다른 스크리닝을 한 번에 돌려서 한 방으로 알림을 보낸다.

  A (기존)   : 코인게코 기준 "거래량 급등" 감지
               - 평소 대비 거래량이 튀었는가 (RVOL) + 회전율
               - 방향성 없음. 오르든 내리든 "뭔가 일어나는 중"인 코인을 잡는다.

  B (갓물주) : 바이낸스 현물 기준 "진입 후보" 스코어링
               - 갓물주 스크리너 명세서 v1.0의 6개 조건을 그대로 구현
               - 6개 조건 충족 개수를 점수로 매긴다. 이미 상승을 시작한 저가 알트를 찾는다.

  C          : A와 B 양쪽에 동시에 걸린 종목
               - 거래량도 터졌고 진입 자리도 맞는 상태. 알림에서 맨 위에 표시한다.

⚠️ B 로직에 대한 중요한 한계 (명세서 6장에서 스스로 밝힌 내용)
   - 원 분석 데이터에 손실 사례가 한 건도 없다. 즉 "이 자리를 고르는가"까지만 검증됐고
     "그 자리가 수익이 나는가"는 검증되지 않았다.
   - 점수와 수익률의 상관계수는 -0.50이었다. 점수가 높다고 더 오르지 않는다.
   - 따라서 점수를 포지션 크기나 목표가 산출에 쓰면 안 된다.
   - 이 알림은 "차트를 볼 후보 목록"일 뿐이며 매수 신호가 아니다.

--- 데이터 소스 ---
  A: CoinGecko API (API 키 필요) - 24시간 누적 스냅샷
  B: Binance 현물 공개 미러 data-api.binance.vision (API 키 불필요) - 1시간봉/4시간봉 캔들

⚠️ B의 데이터 소스가 명세서와 다르다
   명세서는 바이낸스 USDT 무기한선물(fapi.binance.com)을 지정했으나, GitHub Actions
   러너에서 해당 도메인이 451(지역 차단)을 반환한다. 현물 API(api.binance.com)도 동일하게
   차단되어, 인증·지역 제한이 없는 공개 데이터 미러를 사용한다.

   그 결과:
   - 종목 구성이 다르다 (선물에만 상장된 코인이 빠지고, 현물에만 있는 코인이 들어온다)
   - 거래대금 규모가 선물보다 작다 (임계값 조정이 필요할 수 있다)
   - 가격은 거의 동일하다 (실측 차이 0.1% 내외). 조건 ②③④⑥은 사실상 같은 값이 나온다
   - 조건 ①(1H 거래량 배율)과 유동성 필터는 선물 기준과 달라진다

   명세서에서 집중배율이 가장 높았던 것이 하필 거래량 조건(19.6배)이므로,
   명세서의 통계 수치가 그대로 재현된다고 기대해서는 안 된다.

--- 필요한 환경변수 ---
  TELEGRAM_BOT_TOKEN      (필수)
  TELEGRAM_CHAT_ID        (필수)
  COINGECKO_API_KEY       (A를 돌리려면 필요. 없으면 A를 건너뛰고 B만 돌린다)
  COINMARKETCAP_API_KEY   (선택. A의 교차검증용. 없으면 검증 없이 진행)
  SCREENER_STATE_DIR      (선택. 상태 파일 저장 폴더. GitHub Actions에서 캐시 경로로 지정)

--- 실행 ---
  pip install requests
  python unified_screener.py

  자체 테스트(네트워크 없이 계산 로직만 검증):
  python unified_screener.py --self-test

--- 주의 ---
이 스크립트는 샌드박스 환경에서 바이낸스/코인게코 API에 실제로 접근할 수 없어,
계산 로직만 목데이터로 검증했습니다. 실제 API 연동은 GitHub Actions에서 수동 실행으로
먼저 확인하세요. 특히 B 로직은 명세서 8장의 검증값(ACTUSDT)을 실데이터로 대조해보는 것이
가장 확실합니다.
"""

import argparse
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests


# ==================== 환경변수 ====================

def load_dotenv_if_present():
    """같은 폴더에 .env 파일이 있으면 읽어서 환경변수로 로드한다(로컬 테스트 편의용)."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError as e:
        print(f"[경고] .env 파일 로드 실패: {e}")


load_dotenv_if_present()

COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "")
COINMARKETCAP_API_KEY = os.environ.get("COINMARKETCAP_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


# ==================== A: 코인게코 거래량 급등 설정 ====================

A_MIN_VOLUME_FLOOR_USD = 40_000_000   # 최소 유동성 바닥 (현물+선물 합계)
A_RVOL_LOOKBACK_HOURS = 168           # 7일치 = 168시간
A_RVOL_MULTIPLIER_THRESHOLD = 2.0     # 평소 대비 2배 이상
A_MIN_HISTORY_SNAPSHOTS = 12          # 이만큼 스냅샷이 쌓여야 RVOL 판단 시작
A_TURNOVER_RATIO_THRESHOLD = 0.30     # 회전율(현물 거래량/시총) 30% 이상
A_TOP_N = 15

CROSS_CHECK_DISCREPANCY_THRESHOLD = 0.30  # 코인게코 vs CMC 오차 허용치

EXCLUDE_IDS = {"bitcoin", "ethereum"}
STABLECOIN_SYMBOLS = {
    "USDT", "USDC", "DAI", "TUSD", "FDUSD", "USDE", "USDP",
    "BUSD", "GUSD", "USDD", "PYUSD", "FRAX", "USTC",
}


# ==================== B: 갓물주 명세서 설정 ====================
# 조건 임계값은 명세서 3장 그대로다.
#
# ⚠️ 데이터 소스가 명세서와 다르다.
#    명세서는 바이낸스 USDT 무기한선물 기준인데, GitHub Actions에서 선물 API가
#    451 지역 차단을 받아 현물 공개 미러를 쓴다. 종목 구성과 거래대금 규모가 다르므로
#    명세서의 통계 수치(집중배율 8.8배 등)가 그대로 재현된다고 볼 수 없다.
#    조건 자체는 상식적이라 후보 선별에는 여전히 쓸 만하지만, 검증된 성능이 아니라
#    참고 기준으로 봐야 한다.

B_MAX_PRICE_USDT = 1.0                # 명세서 1장: 현재가 1달러 미만
# 현물 1달러 미만 종목 분포 (진단 결과): 전체 496개 / $1M↑ 165개 / $2M↑ 100개 / $5M↑ 52개
B_MIN_24H_QUOTE_VOLUME = 2_000_000    # 명세서 1장 값. 유니버스가 너무 좁으면 낮출 것
B_SCORE_CUTOFF = 4                    # 명세서 4장 권장 기본값
B_STRONG_SIGNAL_VOLX1 = 3.0           # 명세서 4장: 1H 거래량 3배 이상이면 "강신호" 태그
B_TOP_N = 15

# 6개 조건 임계값 (명세서 3장)
B_TH_VOLX1 = 1.5        # ① 1H 거래대금 급증 배율
B_TH_R24 = 3.0          # ② 직전 24시간 수익률 %
B_TH_R4 = 2.0           # ③ 직전 4시간 수익률 %
B_TH_DEV20 = 3.0        # ④ MA20 이격도 %
B_TH_UPBARS = 2         # ⑤ 연속 상승봉 개수
B_TH_FROMLO30 = 10.0    # ⑥ 30봉 저점 대비 상승폭 %

# 명세서 2-2: 필요한 마감봉 개수
B_NEED_1H_CLOSED = 25   # H0 + H1~H24
B_NEED_4H_CLOSED = 30   # B0~B29 (조건 ⑥이 30개를 요구)

# 레이트리밋 관리 (명세서 9장: 바이낸스 선물 2400 weight/분, 종목당 2회 호출)
B_MAX_WORKERS = 8
B_RETRY_COUNT = 3
B_RETRY_BACKOFF_SEC = 2.0


# ==================== 공통 설정 ====================

ALERT_COOLDOWN_HOURS = 12   # 명세서 7장 권장값. 같은 심볼 재알림 금지 시간
SEND_MESSAGE_WHEN_EMPTY = False  # 아무것도 안 걸리면 조용히 넘어간다

STATE_DIR = os.environ.get(
    "SCREENER_STATE_DIR",
    os.path.dirname(os.path.abspath(__file__)),
)
os.makedirs(STATE_DIR, exist_ok=True)

HISTORY_FILE_PATH = os.path.join(STATE_DIR, "volume_history.json")
HISTORY_RETENTION_HOURS = 24 * 10
ALERT_LOG_PATH = os.path.join(STATE_DIR, "alert_log.json")

CG_BASE_URL = "https://api.coingecko.com/api/v3"
CMC_BASE_URL = "https://pro-api.coinmarketcap.com/v1"
BINANCE_API = "https://data-api.binance.vision/api/v3"

HTTP_TIMEOUT = 20


# ==================== 유틸 ====================

def fmt_usd(n):
    if n is None:
        return "-"
    if n <= 0:
        return "$0"
    if n >= 1_000_000_000:
        return f"${n/1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"${n/1_000_000:.1f}M"
    return f"${n/1_000:.0f}K"


def fmt_price(p):
    """저가 코인이 많으므로 자릿수를 가격 크기에 맞춰 조절한다."""
    if p is None:
        return "-"
    if p >= 1:
        return f"${p:,.4f}"
    if p >= 0.01:
        return f"${p:.5f}"
    return f"${p:.8f}".rstrip("0")


def base_symbol(binance_symbol):
    """'ACTUSDT' -> 'ACT'. A(코인게코 심볼)와 B(바이낸스 심볼)를 대조하기 위한 변환."""
    return binance_symbol[:-4] if binance_symbol.endswith("USDT") else binance_symbol


# ==================== 상태 파일 ====================

def load_json_file(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"[경고] {os.path.basename(path)} 로드 실패, 초기값으로 시작합니다.")
        return default


def save_json_file(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError as e:
        print(f"[경고] {os.path.basename(path)} 저장 실패: {e}")


def save_history(history):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=HISTORY_RETENTION_HOURS)).strftime("%Y-%m-%d %H")
    save_json_file(HISTORY_FILE_PATH, {ts: v for ts, v in history.items() if ts >= cutoff})


def save_alert_log(alert_log):
    """
    오래된 알림 기록을 정리하고 저장한다.

    기록 형식은 {"ts": ISO시각, "score": 점수 or None} 인데, 구버전에서는 시각 문자열만
    저장했기 때문에 두 형식이 섞여 있을 수 있다. 양쪽 다 처리한다.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ALERT_COOLDOWN_HOURS * 2)).isoformat()

    def entry_ts(entry):
        if isinstance(entry, dict):
            return entry.get("ts", "")
        if isinstance(entry, str):
            return entry
        return ""

    trimmed = {s: e for s, e in alert_log.items() if entry_ts(e) >= cutoff}
    save_json_file(ALERT_LOG_PATH, trimmed)


def is_in_cooldown(alert_log, symbol, current_score=None):
    """
    최근에 이미 알린 종목인지 확인한다.
    명세서 7장: 이전 알림보다 점수가 올라간 경우는 쿨다운을 무시하고 다시 알린다.
    """
    entry = alert_log.get(symbol)
    if not entry:
        return False
    if isinstance(entry, str):  # 구버전 형식 호환
        entry = {"ts": entry, "score": None}
    try:
        last_dt = datetime.fromisoformat(entry["ts"])
    except (ValueError, KeyError, TypeError):
        return False
    if (datetime.now(timezone.utc) - last_dt) >= timedelta(hours=ALERT_COOLDOWN_HOURS):
        return False
    prev_score = entry.get("score")
    if current_score is not None and prev_score is not None and current_score > prev_score:
        return False  # 점수가 올라갔으므로 재알림 허용
    return True


def get_trailing_average(history, symbol, lookback):
    """과거 스냅샷에서 최근 lookback개 평균 거래량과 실제 확보된 스냅샷 수를 반환한다."""
    vols = []
    for ts in sorted(history.keys(), reverse=True):
        if symbol in history[ts]:
            vols.append(history[ts][symbol])
        if len(vols) >= lookback:
            break
    if not vols:
        return None, 0
    return sum(vols) / len(vols), len(vols)


# ==================== A: 코인게코 ====================

def cg_get_spot_markets():
    """거래량 상위 코인들의 현물 24h 거래량 + 시가총액을 가져온다."""
    headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}
    coins = {}
    for page in (1, 2, 3):
        params = {"vs_currency": "usd", "order": "volume_desc",
                  "per_page": 250, "page": page, "sparkline": "false"}
        resp = requests.get(f"{CG_BASE_URL}/coins/markets", headers=headers,
                            params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        for coin in data:
            sym = coin["symbol"].upper()
            coins[sym] = {
                "id": coin["id"],
                "name": coin["name"],
                "spot_volume": coin.get("total_volume") or 0,
                "market_cap": coin.get("market_cap") or 0,
            }
        if data[-1].get("total_volume", 0) < A_MIN_VOLUME_FLOOR_USD / 5:
            break
        time.sleep(1.5)
    return coins


def cg_get_derivatives_volumes():
    """파생상품 티커를 코인별로 합산한다."""
    headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}
    resp = requests.get(f"{CG_BASE_URL}/derivatives", headers=headers, timeout=30)
    resp.raise_for_status()
    out = {}
    for t in resp.json():
        base = (t.get("index_id") or "").upper()
        if not base:
            continue
        try:
            out[base] = out.get(base, 0) + float(t.get("volume_24h") or 0)
        except (TypeError, ValueError):
            continue
    return out


def cmc_get_volumes():
    """코인마켓캡 거래량 (A의 교차검증용. 실패해도 진행)."""
    if not COINMARKETCAP_API_KEY:
        return {}
    headers = {"X-CMC_PRO_API_KEY": COINMARKETCAP_API_KEY, "Accept": "application/json"}
    params = {"start": "1", "limit": "300", "sort": "volume_24h",
              "sort_dir": "desc", "convert": "USD"}
    try:
        resp = requests.get(f"{CMC_BASE_URL}/cryptocurrency/listings/latest",
                            headers=headers, params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except requests.RequestException as e:
        print(f"[경고] CMC 조회 실패, 교차검증 없이 진행합니다: {e}")
        return {}
    out = {}
    for c in data:
        sym = c["symbol"].upper()
        v = c.get("quote", {}).get("USD", {}).get("volume_24h") or 0
        if sym not in out or v > out[sym]:
            out[sym] = v
    return out


def cross_check_label(cg_vol, cmc_vol):
    if cmc_vol is None or not cg_vol:
        return "🔶 CMC 미확인"
    if abs(cg_vol - cmc_vol) / cg_vol <= CROSS_CHECK_DISCREPANCY_THRESHOLD:
        return "✅ 검증됨"
    return f"⚠️ 확인필요 (CMC {fmt_usd(cmc_vol)})"


def run_screener_a(history):
    """
    A: 코인게코 기준 거래량 급등 감지.
    history를 갱신해서 반환한다(호출자가 저장). 조건 통과 목록도 함께 반환.
    """
    spot = cg_get_spot_markets()
    deriv = cg_get_derivatives_volumes()

    snapshot = {}
    passed = []

    for symbol in set(spot) | set(deriv):
        info = spot.get(symbol)
        if info and info["id"] in EXCLUDE_IDS:
            continue
        if symbol in STABLECOIN_SYMBOLS:
            continue

        spot_vol = info["spot_volume"] if info else 0
        deriv_vol = deriv.get(symbol, 0)
        total_vol = spot_vol + deriv_vol
        mcap = info["market_cap"] if info else 0

        if total_vol > 0:
            snapshot[symbol] = total_vol

        if total_vol < A_MIN_VOLUME_FLOOR_USD:
            continue

        avg_vol, n = get_trailing_average(history, symbol, A_RVOL_LOOKBACK_HOURS)
        if n < A_MIN_HISTORY_SNAPSHOTS or not avg_vol:
            continue
        rvol = total_vol / avg_vol
        if rvol < A_RVOL_MULTIPLIER_THRESHOLD:
            continue

        # 회전율은 현물 거래량만 사용 (선물은 레버리지로 명목 거래량이 부풀려짐)
        if mcap <= 0:
            continue
        turnover = spot_vol / mcap
        if turnover < A_TURNOVER_RATIO_THRESHOLD:
            continue

        passed.append({
            "symbol": symbol,
            "name": info["name"] if info else symbol,
            "spot_vol": spot_vol,
            "deriv_vol": deriv_vol,
            "total_vol": total_vol,
            "rvol": rvol,
            "turnover": turnover,
        })

    # 이번 스냅샷 기록 (다음 실행의 RVOL 재료)
    history[datetime.now(timezone.utc).strftime("%Y-%m-%d %H")] = snapshot

    passed.sort(key=lambda x: x["rvol"], reverse=True)
    passed = passed[:A_TOP_N]

    cmc = cmc_get_volumes()
    for r in passed:
        r["check_label"] = cross_check_label(r["total_vol"], cmc.get(r["symbol"]))

    return passed, len(history)


# ==================== B: 바이낸스 (갓물주 명세서) ====================

def binance_get(path, params=None):
    """바이낸스 공개 API 호출. 레이트리밋(429/418)에 백오프로 대응한다."""
    last_err = None
    for attempt in range(B_RETRY_COUNT):
        try:
            resp = requests.get(f"{BINANCE_API}{path}", params=params, timeout=HTTP_TIMEOUT)
            if resp.status_code in (429, 418):
                wait = B_RETRY_BACKOFF_SEC * (2 ** attempt)
                print(f"[경고] 레이트리밋({resp.status_code}), {wait:.0f}초 대기 후 재시도")
                time.sleep(wait)
                last_err = RuntimeError(f"rate limited {resp.status_code}")
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_err = e
            if attempt < B_RETRY_COUNT - 1:
                time.sleep(B_RETRY_BACKOFF_SEC * (2 ** attempt))
    raise last_err


def binance_get_universe():
    """
    유니버스를 구성한다.

    명세서 1장은 'USDT 무기한선물'을 대상으로 했으나, GitHub Actions에서 바이낸스 선물
    API(fapi)가 451 지역 차단을 반환하기 때문에 현물 공개 미러(data-api.binance.vision)를
    사용한다. 따라서 종목 구성과 거래대금 규모가 명세서 원본과 다르다는 점을 감안해야 한다.
    (가격 자체는 선물과 거의 동일하므로 조건 ②③④⑥은 큰 차이가 없다)

    조건: USDT 마켓 / TRADING 상태 / 가격 1달러 미만 / 24h 거래대금 기준치 이상
    """
    info = binance_get("/exchangeInfo")
    eligible = set()
    for s in info.get("symbols", []):
        sym = s.get("symbol", "")
        base = s.get("baseAsset", "")
        if s.get("status") != "TRADING":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        # 현물에는 스테이블코인 페어와 레버리지 토큰이 섞여 있으므로 걸러낸다.
        if base in STABLECOIN_SYMBOLS or base in ("BTC", "ETH"):
            continue
        # 레버리지 토큰: BTCUP/BTCDOWN, ETHBULL/ETHBEAR 등
        if any(base.endswith(suf) for suf in ("UP", "DOWN", "BULL", "BEAR")):
            continue
        eligible.add(sym)

    tickers = binance_get("/ticker/24hr")
    universe = []
    for t in tickers:
        sym = t.get("symbol")
        if sym not in eligible:
            continue
        try:
            price = float(t.get("lastPrice") or 0)
            qv24 = float(t.get("quoteVolume") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0 or price >= B_MAX_PRICE_USDT:
            continue
        if qv24 < B_MIN_24H_QUOTE_VOLUME:
            continue
        universe.append({"symbol": sym, "price": price, "qv24h": qv24})

    universe.sort(key=lambda x: x["qv24h"], reverse=True)
    return universe


def fetch_closed_klines(symbol, interval, need_closed):
    """
    마감된 캔들만 반환한다.

    명세서 2-1의 핵심 규칙: REST로 받은 캔들의 마지막 원소는 '진행 중인 봉'이므로
    반드시 버려야 한다. 이걸 어기면 미래참조가 되어 성적이 부풀려진다.
    """
    raw = binance_get("/klines", {"symbol": symbol, "interval": interval,
                                  "limit": need_closed + 1})
    if not raw:
        return []
    closed = raw[:-1]  # ← 진행 중인 마지막 봉 제거
    return closed


def parse_kline(k):
    """바이낸스 kline 배열을 딕셔너리로. quoteAssetVolume은 인덱스 7(명세서 2-3)."""
    return {
        "close": float(k[4]),
        "low": float(k[3]),
        "qv": float(k[7]),
    }


def compute_score(h1_closed, h4_closed):
    """
    명세서 3장의 6개 조건을 계산한다.
    h1_closed / h4_closed 는 오래된 것 -> 최신 순으로 정렬된 마감봉 리스트.
    반환: (score, detail dict) 또는 데이터 부족 시 (None, None)
    """
    if len(h1_closed) < B_NEED_1H_CLOSED or len(h4_closed) < B_NEED_4H_CLOSED:
        return None, None

    h1 = [parse_kline(k) for k in h1_closed]
    h4 = [parse_kline(k) for k in h4_closed]

    # H0 = 마지막 마감봉, H1~H24 = 그 이전. 리스트가 오름차순이므로 뒤집어서 인덱싱.
    H = list(reversed(h1))   # H[0] = H0, H[1] = H1, ...
    B = list(reversed(h4))   # B[0] = B0, B[1] = B1, ...

    # ① 1시간 거래대금 급증 — 직전 봉(H0)을 평균에서 제외하는 것이 핵심
    prev24 = [H[i]["qv"] for i in range(1, 25)]
    avg_prev24 = sum(prev24) / len(prev24)
    volX1 = H[0]["qv"] / avg_prev24 if avg_prev24 > 0 else 0.0

    # ② 직전 24시간 수익률 (4시간봉 6개 전 대비)
    r24 = (B[0]["close"] / B[6]["close"] - 1) * 100 if B[6]["close"] > 0 else 0.0

    # ③ 직전 4시간 수익률
    r4 = (B[0]["close"] / B[1]["close"] - 1) * 100 if B[1]["close"] > 0 else 0.0

    # ④ MA20 이격도 (B0 포함 20개)
    ma20 = sum(B[i]["close"] for i in range(20)) / 20
    dev20 = (B[0]["close"] / ma20 - 1) * 100 if ma20 > 0 else 0.0

    # ⑤ 연속 상승봉
    upBars = 0
    for i in range(len(B) - 1):
        if B[i]["close"] > B[i + 1]["close"]:
            upBars += 1
        else:
            break

    # ⑥ 30봉 저점 대비 상승폭
    low30 = min(B[i]["low"] for i in range(30))
    fromLo30 = (B[0]["close"] / low30 - 1) * 100 if low30 > 0 else 0.0

    conditions = {
        "volX1":    (volX1,    B_TH_VOLX1),
        "r24":      (r24,      B_TH_R24),
        "r4":       (r4,       B_TH_R4),
        "dev20":    (dev20,    B_TH_DEV20),
        "upBars":   (upBars,   B_TH_UPBARS),
        "fromLo30": (fromLo30, B_TH_FROMLO30),
    }
    detail = {k: {"value": v, "threshold": t, "pass": v >= t}
              for k, (v, t) in conditions.items()}
    score = sum(1 for d in detail.values() if d["pass"])
    detail["qv1h"] = H[0]["qv"]
    return score, detail


def score_one_symbol(item):
    """한 종목에 대해 캔들 2회 호출 후 점수 계산."""
    sym = item["symbol"]
    try:
        h1 = fetch_closed_klines(sym, "1h", B_NEED_1H_CLOSED)
        h4 = fetch_closed_klines(sym, "4h", B_NEED_4H_CLOSED)
    except Exception as e:
        return {"symbol": sym, "error": str(e)}

    score, detail = compute_score(h1, h4)
    if score is None:
        return {"symbol": sym, "error": "캔들 부족"}

    return {
        "symbol": sym,
        "price": item["price"],
        "qv24h": item["qv24h"],
        "score": score,
        "detail": detail,
        "tag": "강신호" if detail["volX1"]["value"] >= B_STRONG_SIGNAL_VOLX1 else None,
    }


def run_screener_b():
    """B: 바이낸스 선물 대상으로 명세서 스코어링을 돌린다."""
    universe = binance_get_universe()
    print(f"[정보] B 대상 유니버스: {len(universe)}개 종목")

    results, errors = [], 0
    with ThreadPoolExecutor(max_workers=B_MAX_WORKERS) as pool:
        futures = {pool.submit(score_one_symbol, it): it for it in universe}
        for fut in as_completed(futures):
            r = fut.result()
            if r.get("error"):
                errors += 1
                continue
            results.append(r)

    if errors:
        print(f"[정보] B 스코어링 중 {errors}개 종목 실패(데이터 부족 또는 요청 오류)")

    passed = [r for r in results if r["score"] >= B_SCORE_CUTOFF]
    passed.sort(key=lambda x: (x["score"], x["detail"]["volX1"]["value"]), reverse=True)
    return passed[:B_TOP_N], len(universe)


# ==================== 결과 병합 (A / B / C) ====================

def merge_results(a_list, b_list, alert_log):
    """
    A와 B 결과를 합쳐 C(교집합)를 판정하고, 쿨다운을 적용한다.
    A는 코인게코 심볼('ACT'), B는 바이낸스 심볼('ACTUSDT')이므로 base 기준으로 대조한다.
    """
    a_by_base = {r["symbol"]: r for r in a_list}
    b_by_base = {base_symbol(r["symbol"]): r for r in b_list}
    both = set(a_by_base) & set(b_by_base)

    group_c, group_b, group_a = [], [], []

    for base in both:
        b = b_by_base[base]
        if is_in_cooldown(alert_log, base, b["score"]):
            continue
        group_c.append({"base": base, "a": a_by_base[base], "b": b})

    for base, b in b_by_base.items():
        if base in both:
            continue
        if is_in_cooldown(alert_log, base, b["score"]):
            continue
        group_b.append({"base": base, "b": b})

    for base, a in a_by_base.items():
        if base in both:
            continue
        if is_in_cooldown(alert_log, base):
            continue
        group_a.append({"base": base, "a": a})

    group_c.sort(key=lambda x: x["b"]["score"], reverse=True)
    group_b.sort(key=lambda x: x["b"]["score"], reverse=True)
    group_a.sort(key=lambda x: x["a"]["rvol"], reverse=True)

    return group_c, group_b, group_a


def record_alerts(alert_log, group_c, group_b, group_a):
    now = datetime.now(timezone.utc).isoformat()
    for g in group_c + group_b:
        alert_log[g["base"]] = {"ts": now, "score": g["b"]["score"]}
    for g in group_a:
        alert_log[g["base"]] = {"ts": now, "score": None}


# ==================== 메시지 ====================

def fmt_b_line(b):
    d = b["detail"]
    tag = f" [{b['tag']}]" if b.get("tag") else ""
    return (
        f"<b>{b['symbol']}</b>  {b['score']}/6점{tag}\n"
        f"   {fmt_price(b['price'])}\n"
        f"   1H거래량 {d['volX1']['value']:.2f}배 · "
        f"24H {d['r24']['value']:+.1f}% · 4H {d['r4']['value']:+.1f}%\n"
        f"   MA20 {d['dev20']['value']:+.1f}% · "
        f"연속상승 {int(d['upBars']['value'])}봉 · "
        f"저점대비 {d['fromLo30']['value']:+.1f}%"
    )


def fmt_a_line(a):
    return (
        f"<b>{a['symbol']}</b> ({a['name']})\n"
        f"   거래량 {fmt_usd(a['total_vol'])} "
        f"(현물 {fmt_usd(a['spot_vol'])} · 선물 {fmt_usd(a['deriv_vol'])})\n"
        f"   RVOL {a['rvol']:.1f}배 · 회전율 {a['turnover']*100:.1f}%\n"
        f"   {a['check_label']}"
    )


def build_message(group_c, group_b, group_a, a_enabled, a_ready):
    now_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%m-%d %H:%M")
    total = len(group_c) + len(group_b) + len(group_a)

    lines = [f"📊 <b>알트 스크리너</b> ({now_kst} KST)\n"]

    if group_c:
        lines.append(f"🔴 <b>C · 양쪽 충족</b>  {len(group_c)}개")
        for i, g in enumerate(group_c, 1):
            a, b = g["a"], g["b"]
            lines.append(
                f"{i}. " + fmt_b_line(b) + "\n"
                f"   └ RVOL {a['rvol']:.1f}배 · 회전율 {a['turnover']*100:.1f}%"
            )
        lines.append("")

    if group_b:
        lines.append(f"🎯 <b>B · 진입 후보</b>  {len(group_b)}개")
        for i, g in enumerate(group_b, 1):
            lines.append(f"{i}. " + fmt_b_line(g["b"]))
        lines.append("")

    if group_a:
        lines.append(f"🚨 <b>A · 거래량 급등</b>  {len(group_a)}개")
        for i, g in enumerate(group_a, 1):
            lines.append(f"{i}. " + fmt_a_line(g["a"]))
        lines.append("")

    if total == 0:
        lines.append("조건에 맞는 신규 종목이 없습니다.")

    if a_enabled and not a_ready:
        lines.append(f"<i>ℹ️ A는 데이터 축적 중입니다 (스냅샷 {a_ready}개 확보, "
                     f"{A_MIN_HISTORY_SNAPSHOTS}개 필요).</i>")

    lines.append(
        "\n<i>A = 코인게코 거래량 급등(RVOL·회전율) · "
        "B = 바이낸스 진입 후보 6점 만점 · C = 양쪽 동시 충족\n"
        f"같은 종목은 {ALERT_COOLDOWN_HOURS}시간 내 재알림하지 않습니다(점수 상승 시 예외).\n"
        "⚠️ 후보 목록일 뿐 매수 신호가 아닙니다. 차트를 직접 확인하세요.</i>"
    )
    return "\n".join(lines)


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = [message[i:i + 3800] for i in range(0, len(message), 3800)] or [message]
    for chunk in chunks:
        resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk,
                                        "parse_mode": "HTML"}, timeout=15)
        resp.raise_for_status()
        time.sleep(0.5)


# ==================== 자체 테스트 ====================

def make_fake_klines(closes, lows, qvs):
    """테스트용 kline 배열 생성. 인덱스 3=low, 4=close, 7=quoteAssetVolume."""
    return [[0, 0, 0, lows[i], closes[i], 0, 0, qvs[i], 0, 0, 0, 0]
            for i in range(len(closes))]


def self_test():
    """네트워크 없이 계산 로직을 검증한다."""
    print("=== 자체 테스트 시작 ===\n")
    failures = []

    # --- 테스트 1: 마감봉 제거 규칙 ---
    # fetch_closed_klines가 마지막 원소를 버리는지 (명세서 2-1)
    raw = [[i] * 12 for i in range(5)]
    assert raw[:-1] == [[i] * 12 for i in range(4)], "슬라이싱 로직 오류"
    print("✅ 테스트1: 마감봉만 사용(klines[:-1]) 규칙 확인")

    # --- 테스트 2: 6개 조건 전부 충족하는 케이스 → score 6 ---
    # 4시간봉 30개: 저점 100에서 시작해 꾸준히 상승, 마지막 5봉 연속 상승
    closes_4h = [100.0] * 10 + [101 + i * 1.5 for i in range(15)] + \
                [124.0, 127.0, 131.0, 136.0, 142.0]
    lows_4h = [c * 0.98 for c in closes_4h]
    lows_4h[0] = 100.0  # 30봉 저점
    qv_4h = [1000.0] * 30

    # 1시간봉 25개: 마지막(H0) 거래대금이 앞 24개 평균의 3배 이상
    qv_1h = [100.0] * 24 + [400.0]
    closes_1h = [142.0] * 25
    lows_1h = [140.0] * 25

    h4 = make_fake_klines(closes_4h, lows_4h, qv_4h)
    h1 = make_fake_klines(closes_1h, lows_1h, qv_1h)

    score, detail = compute_score(h1, h4)
    print(f"\n테스트2: 이상적 상승 케이스 → score={score}/6")
    for k in ("volX1", "r24", "r4", "dev20", "upBars", "fromLo30"):
        d = detail[k]
        mark = "✓" if d["pass"] else "✗"
        print(f"   {mark} {k:9s} = {d['value']:8.2f}  (기준 {d['threshold']})")
    if score != 6:
        failures.append(f"테스트2: score 6 기대, {score} 나옴")
    else:
        print("✅ 테스트2 통과")

    # --- 테스트 3: 조건 ①의 평균에 H0를 포함하면 안 된다 ---
    prev24_avg = sum(qv_1h[:24]) / 24
    expected_volx1 = qv_1h[24] / prev24_avg
    actual_volx1 = detail["volX1"]["value"]
    print(f"\n테스트3: volX1 계산 (H0 제외 평균)")
    print(f"   기대 {expected_volx1:.4f} / 실제 {actual_volx1:.4f}")
    if abs(expected_volx1 - actual_volx1) > 1e-9:
        failures.append("테스트3: volX1이 H0를 평균에 포함하고 있음")
    else:
        print("✅ 테스트3 통과 (직전 봉이 평균에서 제외됨)")

    # --- 테스트 4: 아무 조건도 충족 못 하는 하락 케이스 → score 0 ---
    closes_down = [200 - i * 2 for i in range(30)]  # 계속 하락
    lows_down = [c * 0.99 for c in closes_down]
    qv_flat = [1000.0] * 30
    h4_down = make_fake_klines(closes_down, lows_down, qv_flat)
    h1_flat = make_fake_klines([142.0] * 25, [140.0] * 25, [100.0] * 25)
    score_d, detail_d = compute_score(h1_flat, h4_down)
    print(f"\n테스트4: 하락 케이스 → score={score_d}/6 (0 기대)")
    if score_d != 0:
        failed = [k for k in ("volX1","r24","r4","dev20","upBars","fromLo30")
                  if detail_d[k]["pass"]]
        failures.append(f"테스트4: score 0 기대, {score_d} 나옴 (통과조건: {failed})")
    else:
        print("✅ 테스트4 통과")

    # --- 테스트 5: 캔들 부족 시 None 반환 ---
    short_score, _ = compute_score(h1[:10], h4)
    print(f"\n테스트5: 캔들 부족 시 → {short_score} (None 기대)")
    if short_score is not None:
        failures.append("테스트5: 캔들 부족인데 점수를 반환함")
    else:
        print("✅ 테스트5 통과")

    # --- 테스트 6: 쿨다운 로직 (점수 상승 시 예외) ---
    now = datetime.now(timezone.utc).isoformat()
    log = {"AAA": {"ts": now, "score": 4}}
    print("\n테스트6: 쿨다운")
    c1 = is_in_cooldown(log, "AAA", 4)
    c2 = is_in_cooldown(log, "AAA", 5)
    c3 = is_in_cooldown(log, "BBB", 4)
    print(f"   같은 점수(4) 재알림 차단: {c1} (True 기대)")
    print(f"   점수 상승(5) 재알림 허용: {not c2} (True 기대)")
    print(f"   기록 없는 종목 통과: {not c3} (True 기대)")
    if not (c1 and not c2 and not c3):
        failures.append("테스트6: 쿨다운 로직 오류")
    else:
        print("✅ 테스트6 통과")

    # --- 테스트 6b: 알림 기록 저장 (dict/str 혼재 처리) ---
    # 실제로 여기서 TypeError가 났었다. 구버전 문자열 형식과 신버전 dict 형식이 섞이면
    # 정리 과정에서 비교가 깨진다.
    print("\n테스트6b: 알림 기록 저장 (형식 혼재)")
    import tempfile
    global ALERT_LOG_PATH
    orig_path = ALERT_LOG_PATH
    try:
        with tempfile.TemporaryDirectory() as td:
            ALERT_LOG_PATH = os.path.join(td, "alert_log.json")
            old_ts = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
            mixed = {
                "NEWFMT": {"ts": now, "score": 5},      # 신버전, 최신
                "OLDFMT": now,                           # 구버전 문자열, 최신
                "EXPIRED": {"ts": old_ts, "score": 3},   # 신버전, 만료
                "BROKEN": None,                          # 깨진 값
            }
            save_alert_log(mixed)
            saved = load_json_file(ALERT_LOG_PATH, {})
            print(f"   저장된 키: {sorted(saved.keys())}")
            ok = ("NEWFMT" in saved and "OLDFMT" in saved
                  and "EXPIRED" not in saved and "BROKEN" not in saved)
            if ok:
                print("   ✅ 테스트6b 통과 (혼재 형식 처리, 만료 정리, 깨진 값 제거)")
            else:
                failures.append("테스트6b: 알림 기록 저장 처리 오류")
    except Exception as e:
        failures.append(f"테스트6b: 예외 발생 {type(e).__name__}: {e}")
        print(f"   ❌ 예외: {e}")
    finally:
        ALERT_LOG_PATH = orig_path

    # --- 테스트 7: A/B/C 병합 ---
    print("\n테스트7: A/B/C 병합")
    a_list = [
        {"symbol": "ACT", "name": "Act", "spot_vol": 5e7, "deriv_vol": 2e7,
         "total_vol": 7e7, "rvol": 3.1, "turnover": 0.45, "check_label": "✅ 검증됨"},
        {"symbol": "ONLYA", "name": "OnlyA", "spot_vol": 5e7, "deriv_vol": 0,
         "total_vol": 5e7, "rvol": 2.5, "turnover": 0.35, "check_label": "✅ 검증됨"},
    ]
    b_list = [
        {"symbol": "ACTUSDT", "price": 0.0118, "qv24h": 1.8e7, "score": 6,
         "tag": "강신호", "detail": detail},
        {"symbol": "ONLYBUSDT", "price": 0.05, "qv24h": 5e6, "score": 4,
         "tag": None, "detail": detail},
    ]
    gc, gb, ga = merge_results(a_list, b_list, {})
    print(f"   C(교집합): {[g['base'] for g in gc]} (['ACT'] 기대)")
    print(f"   B단독: {[g['base'] for g in gb]} (['ONLYB'] 기대)")
    print(f"   A단독: {[g['base'] for g in ga]} (['ONLYA'] 기대)")
    ok7 = ([g["base"] for g in gc] == ["ACT"]
           and [g["base"] for g in gb] == ["ONLYB"]
           and [g["base"] for g in ga] == ["ONLYA"])
    if not ok7:
        failures.append("테스트7: 병합 결과 불일치")
    else:
        print("✅ 테스트7 통과")

    # --- 테스트 8: 메시지 생성 ---
    print("\n테스트8: 메시지 생성")
    msg = build_message(gc, gb, ga, True, 20)
    assert "C · 양쪽 충족" in msg and "B · 진입 후보" in msg and "A · 거래량 급등" in msg
    print("✅ 테스트8 통과\n")
    print("--- 메시지 미리보기 ---")
    print(msg)
    print("--- 미리보기 끝 ---\n")

    print("=" * 50)
    if failures:
        print("❌ 실패한 테스트가 있습니다:")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("✅ 모든 자체 테스트 통과")
    print("\n⚠️ 참고: 이 테스트는 계산 로직만 검증합니다.")
    print("   명세서 8장의 실데이터 검증값(ACTUSDT / 2026-08-10T04:20Z → score 6)은")
    print("   과거 캔들이 필요하므로, 실제 API가 연결된 환경에서 별도로 대조하세요.")
    return 0


# ==================== 메인 ====================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true",
                        help="네트워크 없이 계산 로직만 검증")
    parser.add_argument("--skip-a", action="store_true", help="A(코인게코)를 건너뛴다")
    parser.add_argument("--skip-b", action="store_true", help="B(바이낸스)를 건너뛴다")
    parser.add_argument("--dry-run", action="store_true", help="텔레그램 전송 없이 출력만")
    args = parser.parse_args()

    if args.self_test:
        sys.exit(self_test())

    if not args.dry_run:
        missing = [n for n, v in [("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
                                  ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)] if not v]
        if missing:
            raise SystemExit(f"[오류] 환경변수 없음: {', '.join(missing)}")

    history = load_json_file(HISTORY_FILE_PATH, {})
    alert_log = load_json_file(ALERT_LOG_PATH, {})

    # --- A 실행 ---
    a_list = []
    a_enabled = bool(COINGECKO_API_KEY) and not args.skip_a
    snapshots = 0
    if a_enabled:
        try:
            a_list, snapshots = run_screener_a(history)
            save_history(history)
            print(f"[정보] A 완료: {len(a_list)}개 통과 (누적 스냅샷 {snapshots}개, "
                  f"판단에 {A_MIN_HISTORY_SNAPSHOTS}개 필요)")
        except Exception as e:
            print(f"[경고] A 실행 실패, B만 진행합니다: {e}")
    else:
        print("[안내] A는 건너뜁니다 (COINGECKO_API_KEY 없음 또는 --skip-a)")

    # --- B 실행 ---
    b_list = []
    if not args.skip_b:
        try:
            b_list, universe_size = run_screener_b()
            print(f"[정보] B 완료: {len(b_list)}개 통과 "
                  f"({B_SCORE_CUTOFF}점 이상, 유니버스 {universe_size}개)")
        except Exception as e:
            print(f"[경고] B 실행 실패: {e}")
    else:
        print("[안내] B는 건너뜁니다 (--skip-b)")

    # --- 병합 및 전송 ---
    group_c, group_b, group_a = merge_results(a_list, b_list, alert_log)
    total = len(group_c) + len(group_b) + len(group_a)
    print(f"[정보] 신규 알림 대상: C={len(group_c)} B={len(group_b)} A={len(group_a)}")

    message = build_message(group_c, group_b, group_a, a_enabled, snapshots)
    print("\n" + message + "\n")

    if args.dry_run:
        print("[dry-run] 전송하지 않습니다.")
        return

    if total == 0 and not SEND_MESSAGE_WHEN_EMPTY:
        print("신규 종목이 없어 전송을 생략합니다.")
        return

    send_telegram(message)
    record_alerts(alert_log, group_c, group_b, group_a)
    save_alert_log(alert_log)
    print("텔레그램 전송 완료.")


if __name__ == "__main__":
    main()
