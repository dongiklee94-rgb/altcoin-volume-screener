#!/usr/bin/env python3
"""
바이낸스 현물(미러) vs OKX 선물 데이터 비교 진단.

목적:
  1) 두 소스의 응답 구조를 실제로 확인한다 (필드명, 정렬 순서, 단위)
  2) 같은 코인의 거래량이 두 거래소에서 얼마나 차이 나는지 본다
  3) 명세서 6개 조건을 각각 계산해서 결과가 얼마나 다른지 대조한다
  4) 유니버스 크기(조건에 맞는 종목 수)를 비교해서 임계값 조정 근거를 만든다

이 결과를 보고 어느 소스를 메인으로 쓸지, 임계값을 얼마로 잡을지 결정한다.
한 번 돌려보고 나면 저장소에서 지워도 된다.

⚠️ 이 스크립트는 샌드박스에서 두 API에 접근할 수 없어 구조 파악 없이 작성했다.
   필드명이 실제와 다르면 에러가 날 수 있는데, 그 에러 메시지 자체가 구조 파악에
   도움이 되므로 실패해도 로그를 확인할 것.
"""

import json
import statistics
import requests

TIMEOUT = 20
BINANCE_MIRROR = "https://data-api.binance.vision/api/v3"
OKX_BASE = "https://www.okx.com/api/v5"

# 비교할 코인들 (양쪽에 다 상장돼 있을 만한 저가 알트 위주)
COMPARE_COINS = ["DOGE", "XRP", "ADA", "TRX", "SHIB", "PEPE", "SAND", "GALA"]


# ==================== 구조 확인 ====================

def inspect_structures():
    """두 API의 응답 구조를 실제로 찍어본다. 코드 작성의 근거가 된다."""
    print("=" * 70)
    print("1. 응답 구조 확인")
    print("=" * 70)

    # --- 바이낸스 미러: 24h 티커 ---
    print("\n[바이낸스 미러] /ticker/24hr (단일 종목)")
    try:
        r = requests.get(f"{BINANCE_MIRROR}/ticker/24hr",
                         params={"symbol": "DOGEUSDT"}, timeout=TIMEOUT)
        r.raise_for_status()
        d = r.json()
        print(f"   타입: {type(d).__name__}")
        print(f"   주요 필드: lastPrice={d.get('lastPrice')}, "
              f"quoteVolume={d.get('quoteVolume')}, volume={d.get('volume')}")
        print(f"   전체 키: {sorted(d.keys())}")
    except Exception as e:
        print(f"   ❌ 실패: {e}")

    # --- 바이낸스 미러: 캔들 ---
    print("\n[바이낸스 미러] /klines (1시간봉 3개)")
    try:
        r = requests.get(f"{BINANCE_MIRROR}/klines",
                         params={"symbol": "DOGEUSDT", "interval": "1h", "limit": 3},
                         timeout=TIMEOUT)
        r.raise_for_status()
        ks = r.json()
        print(f"   원소 {len(ks)}개, 각 원소 길이 {len(ks[0])}")
        print(f"   첫 캔들: openTime={ks[0][0]}, close={ks[0][4]}, "
              f"low={ks[0][3]}, quoteVol(idx7)={ks[0][7]}")
        print(f"   시간 순서: {'오래된->최신' if ks[0][0] < ks[-1][0] else '최신->오래된'}")
    except Exception as e:
        print(f"   ❌ 실패: {e}")

    # --- OKX: 티커 ---
    print("\n[OKX] /market/ticker (단일 종목)")
    try:
        r = requests.get(f"{OKX_BASE}/market/ticker",
                         params={"instId": "DOGE-USDT-SWAP"}, timeout=TIMEOUT)
        r.raise_for_status()
        d = r.json()
        print(f"   code={d.get('code')}, msg={d.get('msg')}")
        if d.get("data"):
            t = d["data"][0]
            print(f"   주요 필드: last={t.get('last')}, volCcy24h={t.get('volCcy24h')}, "
                  f"vol24h={t.get('vol24h')}, volCcyQuote24h={t.get('volCcyQuote24h')}")
            print(f"   전체 키: {sorted(t.keys())}")
    except Exception as e:
        print(f"   ❌ 실패: {e}")

    # --- OKX: 캔들 ---
    print("\n[OKX] /market/candles (1시간봉 3개)")
    try:
        r = requests.get(f"{OKX_BASE}/market/candles",
                         params={"instId": "DOGE-USDT-SWAP", "bar": "1H", "limit": 3},
                         timeout=TIMEOUT)
        r.raise_for_status()
        d = r.json()
        print(f"   code={d.get('code')}")
        ks = d.get("data", [])
        if ks:
            print(f"   원소 {len(ks)}개, 각 원소 길이 {len(ks[0])}")
            print(f"   첫 캔들 전체: {ks[0]}")
            print(f"   시간 순서: {'오래된->최신' if int(ks[0][0]) < int(ks[-1][0]) else '최신->오래된'}")
            print("   (OKX 캔들 형식: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm])")
            if len(ks[0]) >= 9:
                print(f"   confirm 플래그: {ks[0][8]} (1=마감된 봉, 0=진행중)")
    except Exception as e:
        print(f"   ❌ 실패: {e}")

    # --- OKX: 종목 목록 ---
    print("\n[OKX] /public/instruments (SWAP)")
    try:
        r = requests.get(f"{OKX_BASE}/public/instruments",
                         params={"instType": "SWAP"}, timeout=TIMEOUT)
        r.raise_for_status()
        d = r.json()
        insts = d.get("data", [])
        usdt_swaps = [i for i in insts if i.get("settleCcy") == "USDT"
                      and i.get("state") == "live"]
        print(f"   전체 SWAP {len(insts)}개, USDT 정산 live {len(usdt_swaps)}개")
        if usdt_swaps:
            print(f"   샘플 키: {sorted(usdt_swaps[0].keys())}")
            print(f"   샘플: instId={usdt_swaps[0].get('instId')}, "
                  f"ctVal={usdt_swaps[0].get('ctVal')}, "
                  f"ctValCcy={usdt_swaps[0].get('ctValCcy')}")
            print("   ⚠️ OKX SWAP은 '계약(contract)' 단위라 vol은 계약수다.")
            print("      USDT 거래대금은 volCcyQuote를 쓰거나 vol*ctVal*price로 환산해야 한다.")
    except Exception as e:
        print(f"   ❌ 실패: {e}")


# ==================== 유니버스 비교 ====================

def compare_universe():
    """두 소스에서 '1달러 미만' 종목이 몇 개나 되는지, 거래량 분포는 어떤지 본다."""
    print("\n" + "=" * 70)
    print("2. 유니버스 비교 (가격 1달러 미만)")
    print("=" * 70)

    # --- 바이낸스 현물 ---
    bn = []
    try:
        r = requests.get(f"{BINANCE_MIRROR}/ticker/24hr", timeout=30)
        r.raise_for_status()
        for t in r.json():
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            try:
                price = float(t.get("lastPrice") or 0)
                qv = float(t.get("quoteVolume") or 0)
            except (TypeError, ValueError):
                continue
            if 0 < price < 1.0:
                bn.append({"symbol": sym, "price": price, "qv24h": qv})
    except Exception as e:
        print(f"   바이낸스 실패: {e}")

    # --- OKX 선물 ---
    okx = []
    try:
        r = requests.get(f"{OKX_BASE}/market/tickers",
                         params={"instType": "SWAP"}, timeout=30)
        r.raise_for_status()
        for t in r.json().get("data", []):
            inst = t.get("instId", "")
            if not inst.endswith("-USDT-SWAP"):
                continue
            try:
                price = float(t.get("last") or 0)
                # volCcyQuote24h가 USDT 거래대금
                qv = float(t.get("volCcyQuote24h") or 0)
            except (TypeError, ValueError):
                continue
            if 0 < price < 1.0:
                okx.append({"symbol": inst, "price": price, "qv24h": qv})
    except Exception as e:
        print(f"   OKX 실패: {e}")

    for name, lst in [("바이낸스 현물", bn), ("OKX 선물", okx)]:
        if not lst:
            print(f"\n[{name}] 데이터 없음")
            continue
        vols = sorted([x["qv24h"] for x in lst], reverse=True)
        print(f"\n[{name}] 1달러 미만 {len(lst)}개")
        print(f"   거래대금 중앙값: ${statistics.median(vols):,.0f}")
        for th in (500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000):
            cnt = sum(1 for v in vols if v >= th)
            print(f"   ≥ ${th/1_000_000:>5.1f}M : {cnt:>4}개")


# ==================== 같은 코인 직접 비교 ====================

def compare_same_coins():
    """양쪽에 다 있는 코인의 가격/거래량을 나란히 비교한다."""
    print("\n" + "=" * 70)
    print("3. 동일 종목 비교")
    print("=" * 70)
    print(f"\n{'코인':<8} {'바낸가격':>12} {'OKX가격':>12} {'가격차':>8} "
          f"{'바낸거래대금':>15} {'OKX거래대금':>15} {'배율':>7}")
    print("-" * 82)

    for coin in COMPARE_COINS:
        bn_price = bn_qv = okx_price = okx_qv = None
        try:
            r = requests.get(f"{BINANCE_MIRROR}/ticker/24hr",
                             params={"symbol": f"{coin}USDT"}, timeout=TIMEOUT)
            if r.status_code == 200:
                d = r.json()
                bn_price = float(d.get("lastPrice") or 0)
                bn_qv = float(d.get("quoteVolume") or 0)
        except Exception:
            pass
        try:
            r = requests.get(f"{OKX_BASE}/market/ticker",
                             params={"instId": f"{coin}-USDT-SWAP"}, timeout=TIMEOUT)
            if r.status_code == 200:
                data = r.json().get("data", [])
                if data:
                    okx_price = float(data[0].get("last") or 0)
                    okx_qv = float(data[0].get("volCcyQuote24h") or 0)
        except Exception:
            pass

        if bn_price is None or okx_price is None:
            print(f"{coin:<8} {'(한쪽 없음)':>12}")
            continue

        pdiff = (okx_price / bn_price - 1) * 100 if bn_price else 0
        ratio = bn_qv / okx_qv if okx_qv else 0
        print(f"{coin:<8} {bn_price:>12.6f} {okx_price:>12.6f} {pdiff:>7.2f}% "
              f"{bn_qv:>14,.0f} {okx_qv:>14,.0f} {ratio:>6.2f}x")

    print("\n※ 배율 = 바이낸스 거래대금 ÷ OKX 거래대금")
    print("  1보다 크면 바이낸스가 더 활발, 작으면 OKX가 더 활발")
    print("  가격차가 1% 넘으면 뭔가 이상한 것 (차익거래로 보통 0.1% 이내)")


def main():
    inspect_structures()
    compare_universe()
    compare_same_coins()
    print("\n" + "=" * 70)
    print("진단 완료. 이 결과를 보고 데이터 소스와 임계값을 결정한다.")
    print("=" * 70)


if __name__ == "__main__":
    main()
