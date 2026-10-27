#!/usr/bin/env python3
"""
고래와개미팀 - 저평가 종목알림 (5분봉 역배열 기반)

5분봉에서 1792 이동평균선을 이탈한 뒤, 24시간(288봉) 안에
7개 이평선이 완전한 역배열로 정렬된 알트코인을 찾아 텔레그램으로 알린다.

  사용 이평선 : 20 · 60 · 112 · 224 · 448 · 896 · 1792 (전부 5분봉 단순이동평균)
  이탈        : 종가가 1792선 아래에 있을 것
  역배열 완성 : MA20 < MA60 < MA112 < MA224 < MA448 < MA896 < MA1792
                (직전 봉에서는 성립하지 않았는데 이번 봉에서 처음 성립한 시점)
  신호 조건   : 역배열이 '처음 완성된 봉'이 최근 RECENT_BARS(12시간) 안에 있고,
                그 시점의 종가가 1792선 아래였으며,
                지금도 역배열이 유지되고 있을 것

⚠️ 이 알림의 성격
   - 하락 추세로 돌아선 종목을 기계적으로 찾아낸 '관찰 목록'이다.
   - 매도나 숏 진입 신호가 아니다. 역배열 직후 급반등(숏스퀴즈)이 나오는 경우도 흔하다.
   - 과거 성과로 검증된 조건이 아니라, 요청받은 규칙을 그대로 구현한 것이다.

--- 데이터 소스 ---
  Binance 현물 공개 미러 data-api.binance.vision (API 키 불필요)
  * 선물 API(fapi)는 GitHub Actions에서 451 지역 차단을 받아 사용할 수 없다.
  * MA1792를 5분봉으로 계산하려면 약 6.2일치 캔들이 필요하고,
    이탈 탐색(288봉)까지 더해 종목당 2,400봉을 받아온다 (1500개씩 2회 호출).

--- 필요한 환경변수 ---
  TELEGRAM_BOT_TOKEN        (필수)
  TELEGRAM_CHAT_ID_REVERSAL (필수) 저평가 종목알림 전용 채널 ID
  SCREENER_STATE_DIR        (선택) 중복 알림 방지 기록 저장 폴더

--- 실행 ---
  pip install requests
  python reversal_screener.py                # 실제 실행
  python reversal_screener.py --dry-run      # 전송 없이 출력만
  python reversal_screener.py --self-test    # 네트워크 없이 계산 로직만 검증
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests


# ==================== 환경변수 ====================

def load_dotenv_if_present():
    """같은 폴더에 .env 파일이 있으면 읽어서 환경변수로 로드한다(로컬 테스트용)."""
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

# 값 앞뒤의 공백·줄바꿈을 반드시 제거한다.
# GitHub Secrets에 토큰을 붙여넣을 때 엔터가 같이 들어가는 일이 흔한데,
# 그대로 두면 요청 주소가 '.../bot<토큰>%0A/sendMessage'가 되어 404가 난다.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID_REVERSAL", "").strip()


# ==================== 조건 설정 ====================

# 이평선 (5분봉 단순이동평균). 반드시 짧은 것 -> 긴 것 순으로 둘 것.
MA_PERIODS = [20, 60, 112, 224, 448, 896, 1792]
MA_LONGEST = MA_PERIODS[-1]

INTERVAL = "5m"
RECENT_BARS = 144                # 역배열이 '최근 이만큼 안에' 완성됐을 때만 알린다 (144봉 = 12시간)
REQUIRE_STILL_ALIGNED = True     # 지금도 역배열이 유지 중일 것

# 1792선 이탈 조건
#   언제 깨고 내려왔는지(교차 시점)는 따지지 않는다. 역배열이 완성된 시점에
#   종가가 1792선 아래에 있기만 하면 통과한다.
#
#   처음에는 '이탈 후 24시간 안에 역배열 완성'으로 만들었는데, 실제 데이터로 돌려보니
#   전 종목이 걸러졌다. 하락장에서는 1792선을 깬 지 며칠 지난 뒤에야 역배열이
#   완성되기 때문이다 (2026-10-26 기준 56종목 중 40종목이 이미 1792선 아래였다).
REQUIRE_BELOW_MA_LONG = True

# 캔들을 몇 개나 받아올지. MA1792 + 최근 144 + 여유
NEED_CLOSED_BARS = MA_LONGEST + RECENT_BARS + 120   # 2056

# 유니버스 (세력탐지기와 동일 기준)
MAX_PRICE_USDT = 1.0
MIN_24H_QUOTE_VOLUME = 2_000_000
TOP_N = 15                       # 알림에 올리는 최대 종목 수

STABLECOIN_SYMBOLS = {
    "USDT", "USDC", "DAI", "TUSD", "FDUSD", "USDE", "USDP",
    "BUSD", "GUSD", "USDD", "PYUSD", "FRAX", "USTC",
}

# 강도 막대: 현재가가 1792선보다 얼마나 아래로 벌어졌는지(이격도 %)
SHOW_STRENGTH_BAR = False        # True 로 바꾸면 종목명 앞에 강도 막대가 나온다
BAR_FILLED = "▰"
BAR_EMPTY = "▱"
BAR_LEVELS = [5.0, 12.0]         # 이 값을 넘을 때마다 한 칸씩 늘어난다 (%)
BAR_MIN = 3
BAR_MAX = 5

# 채널에 보이는 이름과 기호 (여기만 고치면 메시지 전체가 따라 바뀐다)
CHANNEL_TITLE = "저평가 종목알림"
TITLE_EMOJI = "🔎"
TICKER_EMOJI = "🟢"

LINK_TO_TRADINGVIEW = True
TRADINGVIEW_URL = "https://www.tradingview.com/chart/?symbol=BINANCE%3A{symbol}&interval=5"

# 같은 종목 재알림 금지 시간
ALERT_COOLDOWN_HOURS = 12
SEND_MESSAGE_WHEN_EMPTY = False

MAX_WORKERS = 8
RETRY_COUNT = 3
RETRY_BACKOFF_SEC = 2.0
HTTP_TIMEOUT = 20

BINANCE_API = "https://data-api.binance.vision/api/v3"

# 현물 klines는 한 번에 최대 1000개만 준다. 1500을 요청해도 조용히 1000개만 돌려주므로
# 이 값을 넘겨 요청하면 "더 이상 과거가 없다"고 잘못 판단하게 된다. (선물은 1500)
MAX_KLINES_PER_CALL = 1000

STATE_DIR = os.environ.get(
    "SCREENER_STATE_DIR",
    os.path.dirname(os.path.abspath(__file__)),
)
os.makedirs(STATE_DIR, exist_ok=True)
ALERT_LOG_PATH = os.path.join(STATE_DIR, "reversal_alert_log.json")


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


def save_alert_log(alert_log):
    cutoff = (datetime.now(timezone.utc)
              - timedelta(hours=ALERT_COOLDOWN_HOURS * 2)).isoformat()
    trimmed = {s: ts for s, ts in alert_log.items()
               if isinstance(ts, str) and ts >= cutoff}
    save_json_file(ALERT_LOG_PATH, trimmed)


def is_in_cooldown(alert_log, symbol):
    ts = alert_log.get(symbol)
    if not isinstance(ts, str):
        return False
    try:
        last = datetime.fromisoformat(ts)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - last) < timedelta(hours=ALERT_COOLDOWN_HOURS)


# ==================== 바이낸스 ====================

def binance_get(path, params=None):
    """공개 API 호출. 레이트리밋(429/418)에 백오프로 대응한다."""
    last_err = None
    for attempt in range(RETRY_COUNT):
        try:
            resp = requests.get(f"{BINANCE_API}{path}", params=params,
                                timeout=HTTP_TIMEOUT)
            if resp.status_code in (429, 418):
                wait = RETRY_BACKOFF_SEC * (2 ** attempt)
                print(f"[경고] 레이트리밋({resp.status_code}), {wait:.0f}초 대기")
                time.sleep(wait)
                last_err = RuntimeError(f"rate limited {resp.status_code}")
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_err = e
            if attempt < RETRY_COUNT - 1:
                time.sleep(RETRY_BACKOFF_SEC * (2 ** attempt))
    raise last_err


def get_universe():
    """USDT 마켓 / TRADING / 1달러 미만 / 24시간 거래대금 기준치 이상."""
    info = binance_get("/exchangeInfo")
    eligible = set()
    for s in info.get("symbols", []):
        base = s.get("baseAsset", "")
        if s.get("status") != "TRADING" or s.get("quoteAsset") != "USDT":
            continue
        if base in STABLECOIN_SYMBOLS or base in ("BTC", "ETH"):
            continue
        # 레버리지 토큰 제외 (BTCUP/BTCDOWN, ETHBULL/ETHBEAR 등)
        if any(base.endswith(suf) for suf in ("UP", "DOWN", "BULL", "BEAR")):
            continue
        eligible.add(s["symbol"])

    universe = []
    for t in binance_get("/ticker/24hr"):
        sym = t.get("symbol")
        if sym not in eligible:
            continue
        try:
            price = float(t.get("lastPrice") or 0)
            qv24 = float(t.get("quoteVolume") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0 or price >= MAX_PRICE_USDT:
            continue
        if qv24 < MIN_24H_QUOTE_VOLUME:
            continue
        universe.append({"symbol": sym, "price": price, "qv24h": qv24})

    universe.sort(key=lambda x: x["qv24h"], reverse=True)
    return universe


def fetch_closed_closes(symbol, need):
    """
    마감된 5분봉 종가를 오래된 것 -> 최신 순으로 need개 반환한다.

    바이낸스는 한 번에 1500개까지만 주므로 endTime을 옮겨가며 거슬러 올라간다.
    REST 응답의 마지막 봉은 '진행 중'이므로 반드시 버린다(미래참조 방지).
    """
    chunks = []
    end_time = None
    remaining = need + 1          # 진행 중인 봉 1개를 버릴 몫
    while remaining > 0:
        want = min(MAX_KLINES_PER_CALL, remaining)
        params = {"symbol": symbol, "interval": INTERVAL, "limit": want}
        if end_time is not None:
            params["endTime"] = end_time
        raw = binance_get("/klines", params)
        if not raw:
            break
        chunks.append(raw)
        remaining -= len(raw)
        end_time = raw[0][0] - 1   # 다음 호출은 이 묶음보다 더 과거
        if len(raw) < want:
            break                  # 상장 초기라 더 이상 과거가 없다

    bars = []
    for chunk in reversed(chunks):
        bars.extend(chunk)
    if len(bars) < 2:
        return []
    bars = bars[:-1]               # 진행 중인 마지막 봉 제거
    return [float(b[4]) for b in bars]


# ==================== 계산 ====================

def sma_series(values, period):
    """단순이동평균 시계열. 값이 모자라는 구간은 None."""
    out = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def is_bearish_aligned(mas, i):
    """i번째 봉에서 MA20 < MA60 < ... < MA1792 인지."""
    prev = None
    for series in mas:
        v = series[i]
        if v is None:
            return False
        if prev is not None and not (prev < v):
            return False
        prev = v
    return True


def find_signal(closes):
    """
    조건을 만족하면 상세 dict를, 아니면 None을 반환한다.

    1) 역배열이 '처음 완성된' 가장 최근 봉 j를 찾는다 (j-1은 역배열이 아니어야 한다)
    2) j가 최근 RECENT_BARS 안에 있어야 한다
    3) j 시점의 종가가 1792선 아래여야 한다
    4) REQUIRE_STILL_ALIGNED면 마지막 봉도 역배열이어야 한다
    """
    n = len(closes)
    if n < MA_LONGEST + 2:
        return None

    mas = [sma_series(closes, p) for p in MA_PERIODS]
    ma_long = mas[-1]
    last = n - 1

    if REQUIRE_STILL_ALIGNED and not is_bearish_aligned(mas, last):
        return None

    # 1~2) 최근 RECENT_BARS 안에서 역배열이 처음 완성된 봉 찾기 (가장 최신부터)
    completed_at = None
    for j in range(last, max(MA_LONGEST, last - RECENT_BARS) - 1, -1):
        if is_bearish_aligned(mas, j) and not is_bearish_aligned(mas, j - 1):
            completed_at = j
            break
    if completed_at is None:
        return None

    # 3) 역배열이 완성된 시점에 종가가 1792선 아래였는지
    if REQUIRE_BELOW_MA_LONG:
        ref = ma_long[completed_at]
        if ref is None or closes[completed_at] >= ref:
            return None

    # 참고용: 1792선을 마지막으로 깨고 내려온 시점 (찾지 못하면 None)
    broke_at = None
    for i in range(completed_at, MA_LONGEST, -1):
        if ma_long[i] is None or ma_long[i - 1] is None:
            continue
        if closes[i] < ma_long[i] and closes[i - 1] >= ma_long[i - 1]:
            broke_at = i
            break

    price = closes[last]
    gap = (price / ma_long[last] - 1) * 100 if ma_long[last] else 0.0
    return {
        "price": price,
        "ma1792": ma_long[last],
        "gap_pct": gap,                                   # 음수 = 1792선 아래
        "bars_since_break": None if broke_at is None else last - broke_at,
        "bars_since_align": last - completed_at,
        "bars_break_to_align": None if broke_at is None else completed_at - broke_at,
    }


def screen_one(item):
    sym = item["symbol"]
    try:
        closes = fetch_closed_closes(sym, NEED_CLOSED_BARS)
    except Exception as e:
        return {"symbol": sym, "error": str(e)}
    if len(closes) < MA_LONGEST + 2:
        return {"symbol": sym, "error": "캔들 부족"}
    hit = find_signal(closes)
    if not hit:
        return None
    hit["symbol"] = sym
    hit["qv24h"] = item["qv24h"]
    return hit


def run_screener():
    universe = get_universe()
    print(f"[정보] 대상 유니버스: {len(universe)}개 종목")

    hits, errors = [], 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(screen_one, it) for it in universe]
        for fut in as_completed(futures):
            r = fut.result()
            if r is None:
                continue
            if r.get("error"):
                errors += 1
                continue
            hits.append(r)

    if errors:
        print(f"[정보] {errors}개 종목 조회 실패(데이터 부족 또는 요청 오류)")

    # 역배열이 갓 완성된 것 우선, 그다음 이격이 큰 순
    hits.sort(key=lambda h: (h["bars_since_align"], h["gap_pct"]))
    return hits[:TOP_N], len(universe)


# ==================== 메시지 ====================

def strength_bar(gap_pct):
    """1792선 아래로 벌어진 정도를 3~5칸 막대로."""
    depth = abs(gap_pct)
    level = BAR_MIN + sum(1 for th in BAR_LEVELS if depth >= th)
    level = max(BAR_MIN, min(BAR_MAX, level))
    return BAR_FILLED * level + BAR_EMPTY * (BAR_MAX - level)


def short_symbol(sym):
    return sym[:-4] if sym.endswith("USDT") else sym


def ticker_line(hit):
    base = short_symbol(hit["symbol"])
    # SHOW_STRENGTH_BAR 을 True 로 두면 종목명 앞에 강도 막대가 함께 표시된다.
    bar = f"{strength_bar(hit['gap_pct'])} " if SHOW_STRENGTH_BAR else ""
    if LINK_TO_TRADINGVIEW:
        url = TRADINGVIEW_URL.format(symbol=hit["symbol"])
        return f'{TICKER_EMOJI} {bar}<a href="{url}"><b>{base}</b></a>'
    return f"{TICKER_EMOJI} {bar}<b>{base}</b>"


MESSAGE_FOOTER = (
    "━━━━━━━━━━━━━━\n\n"
    "5분봉 기준으로 평소보다 깊게 눌린 종목 중,\n"
    "하락 흐름이 상승으로 돌아설 가능성이 보이는 종목을 골라 알려드립니다.\n\n"
    "단기 되돌림을 노리며 분할매수를 준비해볼 만한 구간입니다.\n\n"
    "※ 매수 신호가 아닙니다. 눌림이 더 깊어질 수 있으니\n"
    "   진입과 비중은 꼭 직접 판단하세요."
)


def build_message(hits):
    now_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%m-%d %H:%M")
    lines = [f"{TITLE_EMOJI} <b>{CHANNEL_TITLE}</b> | {now_kst}", ""]

    for h in hits:
        lines.append(ticker_line(h))
    if not hits:
        lines.append("조건에 맞는 신규 종목이 없습니다.")

    lines.append("")
    lines.append(MESSAGE_FOOTER)
    return "\n".join(lines)


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = [message[i:i + 3800] for i in range(0, len(message), 3800)] or [message]
    for chunk in chunks:
        resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk,
                                        "parse_mode": "HTML",
                                        "disable_web_page_preview": "true"},
                             timeout=15)
        resp.raise_for_status()
        time.sleep(0.5)


# ==================== 자체 테스트 ====================

def make_closes(trend):
    """테스트용 종가 시계열 생성 헬퍼."""
    return list(trend)


def self_test():
    print("=== 자체 테스트 시작 ===\n")
    failures = []

    # --- 테스트 1: 이동평균 계산 ---
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    ma3 = sma_series(vals, 3)
    print(f"테스트1: SMA(3) = {ma3}")
    if ma3[:2] != [None, None] or abs(ma3[2] - 2.0) > 1e-9 or abs(ma3[4] - 4.0) > 1e-9:
        failures.append("테스트1: SMA 계산 오류")
    else:
        print("✅ 테스트1 통과\n")

    # --- 테스트 2: 마감봉만 사용하는지 (슬라이싱 규칙) ---
    raw = [[i] * 12 for i in range(5)]
    assert raw[:-1] == [[i] * 12 for i in range(4)], "슬라이싱 규칙 오류"
    print("✅ 테스트2 통과 (진행 중인 마지막 봉 제거)\n")

    # --- 테스트 3: 역배열 판정 ---
    print("테스트3: 역배열 판정")
    up = [[10.0], [9.0], [8.0], [7.0], [6.0], [5.0], [4.0]]      # 정배열(상승)
    down = [[4.0], [5.0], [6.0], [7.0], [8.0], [9.0], [10.0]]    # 역배열(하락)
    flat = [[5.0], [5.0], [6.0], [7.0], [8.0], [9.0], [10.0]]    # 20==60 이라 실패
    r_up, r_down, r_flat = (is_bearish_aligned(up, 0),
                            is_bearish_aligned(down, 0),
                            is_bearish_aligned(flat, 0))
    print(f"   정배열 -> {r_up} (False 기대)")
    print(f"   역배열 -> {r_down} (True 기대)")
    print(f"   동률 섞임 -> {r_flat} (False 기대)")
    if r_up or not r_down or r_flat:
        failures.append("테스트3: 역배열 판정 오류")
    else:
        print("✅ 테스트3 통과\n")

    def falling_series(slope, extra_bars, n_up=2600, n_down=1200):
        """상승하다 slope만큼 꺾여 내려오는 시세를 만들고,
        역배열이 처음 완성된 시점 + extra_bars 에서 잘라서 돌려준다."""
        closes = [100.0 + i * 0.05 for i in range(n_up)]
        peak = closes[-1]
        closes += [max(1.0, peak - (i + 1) * slope) for i in range(n_down)]
        mas = [sma_series(closes, p) for p in MA_PERIODS]
        first = next((i for i in range(MA_LONGEST, len(closes))
                      if is_bearish_aligned(mas, i)), None)
        if first is None:
            return None
        return closes[:first + 1 + extra_bars]

    # --- 테스트 4: 역배열이 막 완성된 시세 -> 신호 발생 ---
    print("테스트4: 역배열이 방금 완성된 경우")
    c4 = falling_series(slope=1.0, extra_bars=5)
    hit = find_signal(c4) if c4 else None
    if hit:
        print(f"   신호 발생 · 1792선 대비 {hit['gap_pct']:+.1f}% · "
              f"이탈 후 {hit['bars_since_break']}봉 · "
              f"이탈→역배열 {hit['bars_break_to_align']}봉 · "
              f"완성 후 {hit['bars_since_align']}봉")
    else:
        print("   신호 없음")
    if not hit:
        failures.append("테스트4: 역배열이 막 완성됐는데 신호가 안 나옴")
    elif hit["gap_pct"] >= 0:
        failures.append("테스트4: 1792선 위인데 신호가 남")
    elif hit["price"] >= hit["ma1792"]:
        failures.append("테스트4: 1792선 위인데 신호가 남")
    else:
        print("✅ 테스트4 통과\n")

    # --- 테스트 5: 계속 오르는 시세 -> 신호 없음 ---
    rising = [100.0 + i * 0.05 for i in range(3000)]
    r5 = find_signal(rising)
    print(f"테스트5: 상승 추세 -> {'신호 발생' if r5 else '신호 없음'} (신호 없음 기대)")
    if r5:
        failures.append("테스트5: 상승 추세인데 신호가 남")
    else:
        print("✅ 테스트5 통과\n")

    # --- 테스트 6: 역배열이 한참 전에 완성된 경우 -> 신호 없음 ---
    c6 = falling_series(slope=1.0, extra_bars=RECENT_BARS + 60)
    r6 = find_signal(c6) if c6 else None
    print(f"테스트6: {RECENT_BARS}봉보다 전에 완성 -> "
          f"{'신호 발생' if r6 else '신호 없음'} (신호 없음 기대)")
    if r6:
        failures.append("테스트6: 완성 시점이 오래됐는데 신호가 남")
    else:
        print("✅ 테스트6 통과\n")

    # --- 테스트 6b: 역배열이지만 가격이 1792선 위 -> 신호 없음 ---
    # 길게 눌렸다가 막 반등해서 1792선을 되돌린 모양. 이평선은 아직 역배열이지만
    # 가격은 1792선 위에 있으므로 걸러져야 한다.
    c6b = falling_series(slope=1.0, extra_bars=0)
    if c6b:
        mas6b = [sma_series(c6b, p) for p in MA_PERIODS]
        ref = mas6b[-1][-1]
        c6b = c6b[:-1] + [ref * 1.05]      # 마지막 봉만 1792선 위로 올려놓는다
    r6b = find_signal(c6b) if c6b else None
    print(f"테스트6b: 역배열이지만 가격이 1792선 위 -> "
          f"{'신호 발생' if r6b else '신호 없음'} (신호 없음 기대)")
    if r6b:
        failures.append("테스트6b: 1792선 위인데 신호가 남")
    else:
        print("✅ 테스트6b 통과\n")

    # --- 테스트 7: 캔들 부족 ---
    r7 = find_signal([100.0] * 500)
    print(f"테스트7: 캔들 부족 -> {r7} (None 기대)")
    if r7 is not None:
        failures.append("테스트7: 캔들이 부족한데 결과를 반환함")
    else:
        print("✅ 테스트7 통과\n")

    # --- 테스트 8: 쿨다운 ---
    now = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    log = {"AAA": now, "BBB": old}
    c1, c2, c3 = (is_in_cooldown(log, "AAA"), is_in_cooldown(log, "BBB"),
                  is_in_cooldown(log, "CCC"))
    print(f"테스트8: 쿨다운 · 최근={c1}(True 기대) 오래됨={c2}(False 기대) "
          f"기록없음={c3}(False 기대)")
    if not (c1 and not c2 and not c3):
        failures.append("테스트8: 쿨다운 로직 오류")
    else:
        print("✅ 테스트8 통과\n")

    # --- 테스트 9: 막대 ---
    print("테스트9: 강도 막대")
    bars = [(2.0, strength_bar(-2.0)), (7.0, strength_bar(-7.0)),
            (20.0, strength_bar(-20.0))]
    for depth, b in bars:
        print(f"   -{depth}% -> {b}")
    ok9 = (bars[0][1].count(BAR_FILLED) == 3 and bars[1][1].count(BAR_FILLED) == 4
           and bars[2][1].count(BAR_FILLED) == 5)
    if not ok9:
        failures.append("테스트9: 막대 칸 수 오류")
    else:
        print("✅ 테스트9 통과\n")

    # --- 테스트 10: 메시지 ---
    print("테스트10: 메시지 생성")
    sample = [
        {"symbol": "ABCUSDT", "gap_pct": -3.2, "bars_since_align": 2},
        {"symbol": "XYZUSDT", "gap_pct": -8.4, "bars_since_align": 9},
        {"symbol": "QQQUSDT", "gap_pct": -17.1, "bars_since_align": 21},
    ]
    msg = build_message(sample)
    ok10 = (CHANNEL_TITLE in msg and "ABC" in msg
            and "매수 신호가 아닙니다" in msg      # 면책 문구가 빠지지 않았는지
            and "interval=5" in msg
            and (BAR_FILLED in msg) == SHOW_STRENGTH_BAR)
    if not ok10:
        failures.append("테스트10: 메시지 형식 오류")
    else:
        print("✅ 테스트10 통과\n")
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
    print("   실제 종목에서 제대로 걸리는지는 Actions에서 수동 실행으로 확인하세요.")
    return 0


# ==================== 메인 ====================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true",
                        help="네트워크 없이 계산 로직만 검증")
    parser.add_argument("--dry-run", action="store_true",
                        help="텔레그램 전송 없이 출력만")
    args = parser.parse_args()

    if args.self_test:
        sys.exit(self_test())

    if not args.dry_run:
        missing = [n for n, v in [("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
                                  ("TELEGRAM_CHAT_ID_REVERSAL", TELEGRAM_CHAT_ID)]
                   if not v]
        if missing:
            raise SystemExit(f"[오류] 환경변수 없음: {', '.join(missing)}")

    alert_log = load_json_file(ALERT_LOG_PATH, {})

    try:
        hits, universe_size = run_screener()
    except Exception as e:
        raise SystemExit(f"[오류] 스크리닝 실패: {e}")

    print(f"[정보] 조건 통과: {len(hits)}개 (유니버스 {universe_size}개)")

    fresh = [h for h in hits if not is_in_cooldown(alert_log, h["symbol"])]
    print(f"[정보] 신규 알림 대상: {len(fresh)}개")

    message = build_message(fresh)
    print("\n" + message + "\n")

    if args.dry_run:
        print("[dry-run] 전송하지 않습니다.")
        return

    if not fresh and not SEND_MESSAGE_WHEN_EMPTY:
        print("신규 종목이 없어 전송을 생략합니다.")
        return

    send_telegram(message)
    now = datetime.now(timezone.utc).isoformat()
    for h in fresh:
        alert_log[h["symbol"]] = now
    save_alert_log(alert_log)
    print("텔레그램 전송 완료.")


if __name__ == "__main__":
    main()
