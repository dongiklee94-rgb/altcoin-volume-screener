#!/usr/bin/env python3
"""
고래와개미팀 - 알트코인 '거래량 급등' 실시간 스크리너 (RVOL + 회전율 기반)

목적: "그냥 거래량이 큰 코인"이 아니라 "평소 대비 거래량이 실제로 튄 코인"을 찾아서
차트 확인 대상으로 텔레그램에 알려주는 스크립트. GitHub Actions에서 1시간마다 실행되며,
새로 조건을 충족한 코인이 나타났을 때만 알림을 보낸다.

--- 왜 절대 거래량 기준만으로는 안 되는가 ---
절대값(예: $1억 이상)만 기준으로 잡으면 SOL/XRP/DOGE 같은 시총 상위 알트코인은
평소에도 항상 그 이상 거래되기 때문에 매일 똑같은 코인들만 반복해서 걸린다.
반대로 평소 $1000만 수준이던 중소형 알트코인이 갑자기 5배(=$5000만)로 튀어도
절대 $1억 기준에는 못 미쳐서 걸러지지 않는다. 두 경우 다 "급등 신호"로서는 실패.

--- 이 스크립트가 쓰는 3중 필터 (모두 AND로 적용) ---
1. 최소 유동성 바닥 (절대 거래량) - 너무 작은 코인(스캠/슬리피지 위험) 제외
2. RVOL(Relative Volume) - 오늘 거래량이 최근 N일 평균 대비 몇 배인가
3. 거래량/시가총액 비율(회전율, Turnover Ratio) - 시총 대비 오늘 하루 얼마나 손바뀜 했는가
   -> 이 조건이 사실상 SOL/XRP 같은 초대형 시총 코인을 자연스럽게 걸러주는 역할을 한다.
      (시총이 워낙 크기 때문에 어지간한 이벤트가 아니면 하루 거래량이 시총의 30%까지 못 감)

--- RVOL 계산을 위한 히스토리 저장 ---
코인게코 API는 "최근 N시간 평균 거래량"을 바로 주지 않기 때문에, 실행할 때마다 그 시점의
거래량 스냅샷을 JSON 파일(volume_history.json)에 저장해서 히스토리를 직접 쌓는다.
스냅샷이 MIN_HISTORY_SNAPSHOTS_REQUIRED개 이상 쌓이기 전까지는 RVOL 계산이 안 되므로,
그 기간 동안 해당 코인은 급등 판단에서 자동 제외된다.
1시간 주기 기준으로 약 12시간이면 판단이 시작되고, 7일이면 완전한 기준선이 만들어진다.

GitHub Actions는 실행이 끝나면 서버가 사라지기 때문에, 이 히스토리 파일은 Actions 캐시로
저장/복원된다(워크플로우 파일 참고). 캐시가 만료되거나 지워지면 히스토리도 처음부터 다시 쌓인다.

--- 중복 알림 방지 ---
1시간마다 돌기 때문에 같은 코인이 계속 조건을 만족하면 매시간 알림이 올 수 있다.
이를 막기 위해 한 번 알린 코인은 ALERT_COOLDOWN_HOURS(기본 24시간) 동안 다시 알리지 않는다.
기록은 alert_log.json에 저장되며 역시 Actions 캐시로 유지된다.

한 가지 알아둘 점: 급등한 코인의 거래량도 그대로 이후의 "평균"에 포함되기 때문에,
연속으로 급등이 이어지면 평균 자체가 같이 올라가면서 RVOL 배율이 점점 둔감해질 수 있다.
지금은 단순 평균이고, 필요하면 나중에 "직전 급등 구간 제외" 로직을 추가할 수 있다.

--- 파생상품(선물) 거래량과 회전율 계산에 대해 ---
회전율(거래량/시총)은 현물(spot) 거래량만으로 계산한다. 선물은 레버리지 때문에
실제 코인 이동량보다 명목 거래량이 부풀려질 수 있어서, "진짜 코인 손바뀜 강도"를
보려면 현물 기준이 이론적으로 더 정확하다. (RVOL과 절대 유동성 바닥은 여전히
현물+선물 합산 기준을 그대로 쓴다 - 선물 트레이딩 신호로서의 의미도 있기 때문)

[사전 준비물]
1. CoinGecko Demo API 키 (무료) - https://www.coingecko.com/en/developers/dashboard
2. CoinMarketCap Basic API 키 (무료, 교차검증용) - https://coinmarketcap.com/api/
3. 텔레그램 봇 토큰 (@BotFather에서 /newbot)
4. 알림 받을 채팅방의 CHAT_ID

[환경변수 설정]
API 키는 코드에 직접 쓰지 않는다. 아래 4개를 환경변수로 넣어준다.
  COINGECKO_API_KEY
  COINMARKETCAP_API_KEY
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID

- GitHub Actions: 저장소 Settings > Secrets and variables > Actions > New repository secret
- 로컬 테스트: 스크립트와 같은 폴더에 .env 파일을 만들고 아래처럼 작성
    COINGECKO_API_KEY=CG-xxxxx
    COINMARKETCAP_API_KEY=xxxxx
    TELEGRAM_BOT_TOKEN=123456:xxxxx
    TELEGRAM_CHAT_ID=-1001234567890
  .env 파일은 절대 git에 커밋하지 말 것 (.gitignore에 포함되어 있어야 한다)

[로컬 실행]
  pip install requests
  python altcoin_volume_screener.py

[주의]
- 이 스크립트는 샌드박스 환경에서 실제 API 도메인에 접근할 수 없어, 핵심 로직(필터링,
  RVOL/회전율 계산, 쿨다운, 메시지 포맷팅)만 목데이터로 단위 테스트했다. 실제 API 연동은
  로컬이나 GitHub Actions에서 먼저 수동 실행해서 확인할 것.
- CoinGecko/CMC API 응답 필드는 바뀔 수 있다. 에러가 나면 최신 문서와 대조할 것.
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import requests

# ==================== 설정 ====================
# API 키는 코드에 직접 쓰지 않고 환경변수에서 읽어온다.
# - GitHub Actions에서는 저장소 Settings > Secrets and variables > Actions 에 등록한 값이 주입된다.
# - 로컬에서 테스트할 때는 실행 전에 환경변수를 직접 설정하거나,
#   같은 폴더에 .env 파일을 만들어두면 아래 load_dotenv_if_present()가 읽어온다.
#   (.env 파일은 절대 git에 커밋하지 말 것! .gitignore에 반드시 포함)


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
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError as e:
        print(f"[경고] .env 파일 로드 실패: {e}")


load_dotenv_if_present()

COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "")
COINMARKETCAP_API_KEY = os.environ.get("COINMARKETCAP_API_KEY", "")  # 교차검증(더블체크)용
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def check_required_env():
    """필수 환경변수가 비어있으면 어떤 값이 없는지 알려주고 종료한다."""
    missing = [
        name for name, value in [
            ("COINGECKO_API_KEY", COINGECKO_API_KEY),
            ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
            ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
        ] if not value
    ]
    if missing:
        raise SystemExit(
            "[오류] 다음 환경변수가 설정되지 않았습니다: " + ", ".join(missing) +
            "\nGitHub Actions라면 저장소 Settings > Secrets에 등록했는지 확인하세요."
            "\n로컬이라면 .env 파일을 만들거나 환경변수를 직접 설정하세요."
        )
    if not COINMARKETCAP_API_KEY:
        print("[안내] COINMARKETCAP_API_KEY가 없습니다. 교차검증 없이 진행합니다.")

# 1. 최소 유동성 바닥 (절대 24h 거래량, spot+선물 합계 기준) - 스캠/슬리피지 방지
MIN_VOLUME_FLOOR_USD = 40_000_000  # 3000만~5000만 사이 중간값. 취향껏 조정하세요.

# 2. RVOL(상대거래량) 기준
# 1시간마다 실행되므로 스냅샷은 시간 단위로 쌓인다. 최근 N시간 평균 대비 몇 배인지로 판단.
RVOL_LOOKBACK_HOURS = 168  # 7일치 = 168시간
RVOL_MULTIPLIER_THRESHOLD = 2.0  # 2배 이상

# RVOL을 계산하려면 최소 이만큼의 과거 스냅샷이 쌓여있어야 함 (그전까지는 판단 보류)
MIN_HISTORY_SNAPSHOTS_REQUIRED = 12  # 1시간 주기 기준 약 12시간치

# 3. 회전율(거래량/시가총액) 기준
TURNOVER_RATIO_THRESHOLD = 0.30  # 30%

# 코인게코 vs 코인마켓캡 거래량 차이가 이 비율 이상이면 "확인 필요" 표시
CROSS_CHECK_DISCREPANCY_THRESHOLD = 0.30  # 30%

TOP_N_DISPLAY = 30

# 같은 코인을 이 시간 안에 다시 알리지 않는다 (중복 알림 방지)
ALERT_COOLDOWN_HOURS = 24

# 조건에 맞는 코인이 없을 때도 "이상 없음" 메시지를 보낼지 여부
# False면 급등 종목이 있을 때만 알림이 온다 (조용한 대신 봇 생존 확인은 어려움)
SEND_MESSAGE_WHEN_EMPTY = False

# 알트코인 판별에서 제외할 코인 (BTC, ETH는 "알트코인"이 아니므로)
EXCLUDE_IDS = {"bitcoin", "ethereum"}

# 제외할 주요 스테이블코인 (심볼 기준, 대문자). 필요하면 여기에 추가하세요.
STABLECOIN_SYMBOLS = {
    "USDT", "USDC", "DAI", "TUSD", "FDUSD", "USDE", "USDP",
    "BUSD", "GUSD", "USDD", "PYUSD", "FRAX", "USTC",
}

# 상태 파일들이 저장될 폴더.
# GitHub Actions에서는 캐시로 복원/저장되는 경로를 STATE_DIR 환경변수로 지정한다.
STATE_DIR = os.environ.get(
    "SCREENER_STATE_DIR",
    os.path.dirname(os.path.abspath(__file__)),
)
os.makedirs(STATE_DIR, exist_ok=True)

# 거래량 히스토리 (RVOL 계산용). 이 파일이 사라지면 처음부터 다시 쌓아야 함.
HISTORY_FILE_PATH = os.path.join(STATE_DIR, "volume_history.json")
HISTORY_RETENTION_HOURS = 24 * 10  # 10일치까지만 보관

# 최근 알림 발송 기록 (중복 알림 방지용)
ALERT_LOG_PATH = os.path.join(STATE_DIR, "alert_log.json")

CG_BASE_URL = "https://api.coingecko.com/api/v3"
CG_HEADERS = {"x-cg-demo-api-key": COINGECKO_API_KEY}

CMC_BASE_URL = "https://pro-api.coinmarketcap.com/v1"
CMC_HEADERS = {"X-CMC_PRO_API_KEY": COINMARKETCAP_API_KEY, "Accept": "application/json"}


def get_spot_markets():
    """거래량 내림차순으로 정렬해서 현물 24h 거래량 + 시가총액을 코인별로 가져온다."""
    coins = {}
    for page in (1, 2, 3):  # 최대 750개까지 (급등 후보는 저시총일 수도 있어서 범위를 넓게 잡음)
        params = {
            "vs_currency": "usd",
            "order": "volume_desc",
            "per_page": 250,
            "page": page,
            "sparkline": "false",
        }
        resp = requests.get(
            f"{CG_BASE_URL}/coins/markets", headers=CG_HEADERS, params=params, timeout=20
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break

        for coin in data:
            symbol = coin["symbol"].upper()
            coins[symbol] = {
                "id": coin["id"],
                "name": coin["name"],
                "symbol": symbol,
                "spot_volume": coin.get("total_volume") or 0,
                "market_cap": coin.get("market_cap") or 0,
            }

        # 이번 페이지 마지막 코인 거래량이 이미 최소 바닥의 1/5 아래면 다음 페이지는 볼 필요 없음
        if data[-1].get("total_volume", 0) < MIN_VOLUME_FLOOR_USD / 5:
            break
        time.sleep(1.5)  # 레이트리밋 여유

    return coins


def get_derivatives_volumes():
    """파생상품(선물/무기한) 전체 티커를 가져와 코인(index_id)별로 거래량을 합산한다."""
    resp = requests.get(f"{CG_BASE_URL}/derivatives", headers=CG_HEADERS, timeout=30)
    resp.raise_for_status()
    tickers = resp.json()

    deriv_volumes = {}
    for t in tickers:
        base = (t.get("index_id") or "").upper()
        if not base:
            continue
        try:
            vol = float(t.get("volume_24h") or 0)
        except (TypeError, ValueError):
            continue
        deriv_volumes[base] = deriv_volumes.get(base, 0) + vol

    return deriv_volumes


def get_cmc_volumes():
    """코인마켓캡 거래량 상위 코인 리스트 (교차검증/더블체크 용도, 필터링에는 관여 안 함)."""
    params = {"start": "1", "limit": "300", "sort": "volume_24h", "sort_dir": "desc", "convert": "USD"}
    try:
        resp = requests.get(
            f"{CMC_BASE_URL}/cryptocurrency/listings/latest",
            headers=CMC_HEADERS, params=params, timeout=20,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except requests.RequestException as e:
        print(f"[경고] CMC 조회 실패, 교차검증 없이 진행합니다: {e}")
        return {}

    cmc_volumes = {}
    for coin in data:
        symbol = coin["symbol"].upper()
        vol = coin.get("quote", {}).get("USD", {}).get("volume_24h") or 0
        if symbol not in cmc_volumes or vol > cmc_volumes[symbol]:
            cmc_volumes[symbol] = vol
    return cmc_volumes


def cross_check_label(cg_total_vol, cmc_vol):
    """코인게코 vs CMC 거래량을 비교해서 신뢰도 표시 라벨을 반환한다."""
    if cmc_vol is None or cg_total_vol == 0:
        return "🔶 CMC 미확인"
    diff_ratio = abs(cg_total_vol - cmc_vol) / cg_total_vol
    if diff_ratio <= CROSS_CHECK_DISCREPANCY_THRESHOLD:
        return "✅ 검증됨"
    return f"⚠️ 확인필요 (CMC {fmt_usd(cmc_vol)})"


# ==================== 히스토리 (RVOL 계산용) ====================

def load_history():
    if not os.path.exists(HISTORY_FILE_PATH):
        return {}
    try:
        with open(HISTORY_FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        print("[경고] 히스토리 파일 로드 실패, 빈 히스토리로 시작합니다.")
        return {}


def save_history(history):
    """오래된 스냅샷을 정리하고 저장한다. 키는 'YYYY-MM-DD HH' 형식(UTC 기준 시각)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=HISTORY_RETENTION_HOURS)).strftime("%Y-%m-%d %H")
    trimmed = {ts: vols for ts, vols in history.items() if ts >= cutoff}
    try:
        with open(HISTORY_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(trimmed, f)
    except OSError as e:
        print(f"[경고] 히스토리 파일 저장 실패: {e}")


def get_trailing_average(history, symbol, lookback_hours):
    """과거 스냅샷에서 최근 lookback_hours치 평균 거래량과, 실제 확보된 스냅샷 수를 반환한다."""
    timestamps_sorted = sorted(history.keys(), reverse=True)  # 최신순
    vols = []
    for ts in timestamps_sorted:
        if symbol in history[ts]:
            vols.append(history[ts][symbol])
        if len(vols) >= lookback_hours:
            break
    if not vols:
        return None, 0
    return sum(vols) / len(vols), len(vols)


# ==================== 알림 쿨다운 (중복 알림 방지) ====================

def load_alert_log():
    if not os.path.exists(ALERT_LOG_PATH):
        return {}
    try:
        with open(ALERT_LOG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_alert_log(alert_log):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ALERT_COOLDOWN_HOURS * 2)).isoformat()
    trimmed = {sym: ts for sym, ts in alert_log.items() if ts >= cutoff}
    try:
        with open(ALERT_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(trimmed, f)
    except OSError as e:
        print(f"[경고] 알림 기록 저장 실패: {e}")


def is_in_cooldown(alert_log, symbol):
    """최근 ALERT_COOLDOWN_HOURS 안에 이미 알린 코인인지 확인한다."""
    last_ts = alert_log.get(symbol)
    if not last_ts:
        return False
    try:
        last_dt = datetime.fromisoformat(last_ts)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - last_dt) < timedelta(hours=ALERT_COOLDOWN_HOURS)


# ==================== 리포트 생성 ====================

def build_report():
    spot = get_spot_markets()
    deriv = get_derivatives_volumes()
    history = load_history()      # 이번 실행 직전까지 쌓인 스냅샷들
    alert_log = load_alert_log()  # 최근에 이미 알린 코인 기록

    all_symbols = set(spot.keys()) | set(deriv.keys())
    current_snapshot = {}
    candidates = []
    skipped_cooldown = 0

    for symbol in all_symbols:
        spot_info = spot.get(symbol)

        if spot_info and spot_info["id"] in EXCLUDE_IDS:
            continue
        if symbol in STABLECOIN_SYMBOLS:
            continue

        spot_vol = spot_info["spot_volume"] if spot_info else 0
        deriv_vol = deriv.get(symbol, 0)
        total_vol = spot_vol + deriv_vol
        market_cap = spot_info["market_cap"] if spot_info else 0

        # 이번 스냅샷은 통과 여부와 무관하게 전부 기록 (다음 실행부터 RVOL 계산 재료가 됨)
        if total_vol > 0:
            current_snapshot[symbol] = total_vol

        # --- 조건 1: 최소 유동성 바닥 ---
        if total_vol < MIN_VOLUME_FLOOR_USD:
            continue

        # --- 조건 2: RVOL (최근 N시간 평균 대비 배율) ---
        avg_vol, snapshots_available = get_trailing_average(history, symbol, RVOL_LOOKBACK_HOURS)
        if snapshots_available < MIN_HISTORY_SNAPSHOTS_REQUIRED:
            continue  # 히스토리 부족 -> 판단 보류
        if not avg_vol or avg_vol <= 0:
            continue
        rvol = total_vol / avg_vol
        if rvol < RVOL_MULTIPLIER_THRESHOLD:
            continue

        # --- 조건 3: 회전율 (거래량/시가총액) ---
        # 현물 거래량만 사용 (선물은 레버리지 때문에 명목 거래량이 부풀려져서
        # "진짜 코인 손바뀜 강도"를 볼 때는 현물 기준이 더 정확함)
        turnover_vol = spot_vol
        if market_cap <= 0:
            continue
        turnover_ratio = turnover_vol / market_cap
        if turnover_ratio < TURNOVER_RATIO_THRESHOLD:
            continue

        # --- 중복 알림 방지: 최근에 이미 알린 코인은 건너뛴다 ---
        if is_in_cooldown(alert_log, symbol):
            skipped_cooldown += 1
            continue

        candidates.append({
            "symbol": symbol,
            "name": spot_info["name"] if spot_info else symbol,
            "spot_vol": spot_vol,
            "deriv_vol": deriv_vol,
            "total_vol": total_vol,
            "market_cap": market_cap,
            "rvol": rvol,
            "turnover_ratio": turnover_ratio,
            "snapshots_used": snapshots_available,
        })

    # 이번 스냅샷을 히스토리에 반영해서 저장 (다음 실행분의 재료가 됨)
    now_key = datetime.now(timezone.utc).strftime("%Y-%m-%d %H")
    history[now_key] = current_snapshot
    save_history(history)
    print(f"[정보] 누적 스냅샷 수: {len(history)}개 "
          f"(RVOL 계산에는 코인별로 최소 {MIN_HISTORY_SNAPSHOTS_REQUIRED}개 필요)")
    if skipped_cooldown:
        print(f"[정보] 최근 {ALERT_COOLDOWN_HOURS}시간 내 이미 알린 코인 {skipped_cooldown}개는 생략했습니다.")

    candidates.sort(key=lambda x: x["rvol"], reverse=True)
    candidates = candidates[:TOP_N_DISPLAY]

    # 이번에 알릴 코인들을 알림 기록에 남긴다
    now_iso = datetime.now(timezone.utc).isoformat()
    for r in candidates:
        alert_log[r["symbol"]] = now_iso
    save_alert_log(alert_log)

    # CMC 교차검증은 최종 후보에 한해서만 (커버리지 확장이 아니라 더블체크 목적)
    cmc_volumes = get_cmc_volumes()
    for r in candidates:
        cmc_vol = cmc_volumes.get(r["symbol"])
        r["cmc_vol"] = cmc_vol
        r["check_label"] = cross_check_label(r["total_vol"], cmc_vol)

    return candidates


def fmt_usd(n):
    if n >= 1_000_000_000:
        return f"${n/1_000_000_000:.2f}B"
    return f"${n/1_000_000:.1f}M"


def format_telegram_message(results):
    # 서버는 보통 UTC로 돌아가므로 한국 시간으로 변환해서 표시한다
    now_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%m-%d %H:%M")
    lines = [
        f"🚨 <b>알트코인 거래량 급등 감지</b> ({now_kst} KST)",
        f"조건: 거래량 ≥{fmt_usd(MIN_VOLUME_FLOOR_USD)} · "
        f"평균 대비 {RVOL_MULTIPLIER_THRESHOLD}배↑ · "
        f"회전율 {int(TURNOVER_RATIO_THRESHOLD*100)}%↑ (BTC/ETH/스테이블 제외)",
        f"신규 {len(results)}개 발견\n",
    ]

    if not results:
        lines.append("조건에 맞는 신규 급등 코인이 없습니다.")
    else:
        for i, r in enumerate(results, 1):
            lines.append(
                f"{i}. <b>{r['symbol']}</b> ({r['name']})\n"
                f"   거래량 <b>{fmt_usd(r['total_vol'])}</b> "
                f"(현물 {fmt_usd(r['spot_vol'])} · 선물 {fmt_usd(r['deriv_vol'])})\n"
                f"   RVOL <b>{r['rvol']:.1f}배</b> · "
                f"회전율 <b>{r['turnover_ratio']*100:.1f}%</b>\n"
                f"   {r.get('check_label', '')}"
            )

    lines.append(
        "\n<i>RVOL = 현재 거래량(현물+선물) ÷ 최근 평균 · 회전율 = 현물 거래량 ÷ 시가총액\n"
        f"같은 코인은 {ALERT_COOLDOWN_HOURS}시간 내 중복 알림하지 않습니다.\n"
        "✅ 검증됨 = CoinGecko·CMC 오차 " + f"{int(CROSS_CHECK_DISCREPANCY_THRESHOLD*100)}%" +
        " 이내 / ⚠️ 확인필요 = 오차 큼 / 🔶 = CMC 미확인(참고용)</i>"
    )

    return "\n".join(lines)


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = [message[i:i + 4000] for i in range(0, len(message), 4000)] or [message]
    for chunk in chunks:
        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"},
            timeout=15,
        )
        resp.raise_for_status()


def main():
    check_required_env()
    print("스크리닝 시작...")
    results = build_report()
    message = format_telegram_message(results)
    print(message)

    if not results and not SEND_MESSAGE_WHEN_EMPTY:
        print("급등 종목이 없어 텔레그램 전송을 생략합니다.")
        return

    send_telegram(message)
    print("텔레그램 전송 완료.")


if __name__ == "__main__":
    main()
