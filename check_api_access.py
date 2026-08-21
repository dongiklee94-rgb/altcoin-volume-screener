#!/usr/bin/env python3
"""
GitHub Actions 러너에서 어떤 거래소 API가 접근 가능한지 확인하는 진단 스크립트.

바이낸스 선물(fapi.binance.com)이 451(지역 차단)을 반환했기 때문에,
대체 가능한 데이터 소스를 찾기 위해 만들었다. 한 번 돌려서 결과를 보고
어느 소스로 갈지 결정한 뒤에는 저장소에서 지워도 된다.

451 = 법적 사유로 접근 거부 (지역 차단). 코드 문제가 아니다.
"""

import json
import requests

TIMEOUT = 15

# (이름, URL, 성공 시 확인할 내용 설명)
TARGETS = [
    # --- 바이낸스 계열 ---
    ("바이낸스 선물 (fapi)",
     "https://fapi.binance.com/fapi/v1/exchangeInfo",
     "USDT 무기한선물. 명세서 원본 소스"),

    ("바이낸스 현물 (api)",
     "https://api.binance.com/api/v3/exchangeInfo",
     "현물. 선물이 막히면 차선책"),

    ("바이낸스 공개데이터 미러 (data-api.binance.vision)",
     "https://data-api.binance.vision/api/v3/exchangeInfo",
     "인증 불필요한 read-only 미러. 지역 제한이 없을 수 있음"),

    ("바이낸스 미러 - 캔들 조회",
     "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=3",
     "미러에서 실제 캔들이 나오는지 확인"),

    # --- 대체 거래소 (USDT 무기한선물 캔들 제공) ---
    ("Bybit 선물",
     "https://api.bybit.com/v5/market/instruments-info?category=linear&limit=5",
     "USDT 무기한선물 종목 목록"),

    ("Bybit 캔들",
     "https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60&limit=3",
     "1시간봉 캔들"),

    ("OKX 선물",
     "https://www.okx.com/api/v5/public/instruments?instType=SWAP",
     "무기한선물 종목 목록"),

    ("OKX 캔들",
     "https://www.okx.com/api/v5/market/candles?instId=BTC-USDT-SWAP&bar=1H&limit=3",
     "1시간봉 캔들"),

    ("Gate.io 선물",
     "https://api.gateio.ws/api/v4/futures/usdt/contracts?limit=5",
     "USDT 무기한선물 종목 목록"),
]


def check(name, url, note):
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        status = resp.status_code
        if status == 200:
            try:
                data = resp.json()
                if isinstance(data, dict):
                    size = len(json.dumps(data))
                    preview = f"dict, 키 {len(data)}개, {size:,}바이트"
                elif isinstance(data, list):
                    preview = f"list, 원소 {len(data)}개"
                else:
                    preview = type(data).__name__
            except ValueError:
                preview = "JSON 아님"
            print(f"✅ {name}")
            print(f"   200 OK · {preview}")
        elif status == 451:
            print(f"❌ {name}")
            print(f"   451 지역 차단 (Unavailable For Legal Reasons)")
        elif status == 403:
            print(f"❌ {name}")
            print(f"   403 접근 거부 (차단 또는 인증 필요)")
        else:
            print(f"⚠️  {name}")
            print(f"   HTTP {status}")
        print(f"   └ {note}\n")
        return status == 200
    except requests.RequestException as e:
        print(f"❌ {name}")
        print(f"   요청 실패: {type(e).__name__}")
        print(f"   └ {note}\n")
        return False


def main():
    print("=" * 60)
    print("거래소 API 접근성 진단")
    print("=" * 60)
    print()

    ok = []
    for name, url, note in TARGETS:
        if check(name, url, note):
            ok.append(name)

    print("=" * 60)
    print(f"접근 가능: {len(ok)}/{len(TARGETS)}")
    for n in ok:
        print(f"   ✅ {n}")
    if not ok:
        print("   (없음 — 모든 소스가 차단되었습니다)")
    print("=" * 60)


if __name__ == "__main__":
    main()
