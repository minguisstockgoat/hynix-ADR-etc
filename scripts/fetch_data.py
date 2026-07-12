#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SK하이닉스 본주(000660.KS) / ADR(SKHYV) / 환율(KRW=X) 시세를 Yahoo Finance에서
서버사이드로 수집하여 괴리율을 계산하고 data.json 에 누적 저장한다.

- GitHub Actions 에서 주기 실행되며, 브라우저 CORS 제약을 우회하기 위한 데이터 백본이다.
- 표준 라이브러리만 사용한다(설치 의존성 없음).
"""
import json
import os
import sys
import time
import datetime
import urllib.request
import urllib.error

# ── 설정 ────────────────────────────────────────────────────────────────
COMMON_SYM = "000660.KS"   # SK하이닉스 본주 (KOSPI, KRW)
ADR_SYM    = "SKHYV"       # SK하이닉스 ADR (Nasdaq, USD)  ※ OTC 티커 HXSCL 은 Yahoo 미지원
FX_SYM     = "KRW=X"       # USD/KRW
RATIO      = 0.1           # 1 ADR 이 대표하는 보통주 수 (10 ADR = 본주 1주)
MAX_HISTORY = 3000         # data.json 에 보관할 최대 히스토리 포인트 수

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}

OUT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data.json")
)


# ── Yahoo Finance chart(v8) : crumb/쿠키 불필요 ─────────────────────────────
YH_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")


def yh_chart(symbol):
    # query1 → query2 호스트 폴백 + 재시도 (GitHub 러너 IP의 429/403 대비)
    last_err = None
    for attempt in range(3):
        host = YH_HOSTS[attempt % len(YH_HOSTS)]
        url = (
            f"https://{host}/v8/finance/chart/{symbol}"
            "?range=1d&interval=1m"
        )
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            m = data["chart"]["result"][0]["meta"]
            period = (m.get("currentTradingPeriod") or {}).get("regular") or {}
            return _leg_from_meta(m, period)
        except Exception as e:  # noqa: BLE001 - 어떤 실패든 다음 호스트/재시도로
            last_err = e
            time.sleep(1.5)
    raise last_err if last_err else RuntimeError("yahoo fetch failed")


def _leg_from_meta(m, period):
    return {
        "symbol":      m.get("symbol"),
        "price":       m.get("regularMarketPrice"),
        "currency":    m.get("currency"),
        "prevClose":   m.get("chartPreviousClose") or m.get("previousClose"),
        "marketTime":  m.get("regularMarketTime"),          # epoch sec (UTC)
        "marketState": m.get("marketState"),                # PRE/REGULAR/POST/CLOSED (nullable)
        "exchange":    m.get("exchangeName"),
        "gmtoffset":   m.get("gmtoffset"),
        "tzName":      m.get("exchangeTimezoneName"),
        "sessStart":   period.get("start"),                 # 정규장 시작 epoch
        "sessEnd":     period.get("end"),                   # 정규장 종료 epoch
    }


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def load_existing():
    try:
        with open(OUT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def fetch_leg(symbol, prev_leg, errors, name):
    """한 종목 시세를 받아오고, 실패 시 직전 스냅샷 값을 재사용(stale 표시)."""
    try:
        leg = yh_chart(symbol)
        if leg.get("price") is None:
            raise ValueError("price is None")
        leg["stale"] = False
        return leg
    except Exception as e:  # noqa: BLE001 - 어떤 실패든 직전 값으로 폴백
        errors.append(f"{name}({symbol}): {type(e).__name__}: {e}")
        if prev_leg:
            fallback = dict(prev_leg)
            fallback["stale"] = True
            return fallback
        return {"symbol": symbol, "price": None, "stale": True}


def main():
    existing = load_existing()
    prev = {
        "common": existing.get("common"),
        "adr": existing.get("adr"),
        "fx": existing.get("fx"),
    }
    errors = []

    common = fetch_leg(COMMON_SYM, prev["common"], errors, "common")
    adr    = fetch_leg(ADR_SYM,    prev["adr"],    errors, "adr")
    fx     = fetch_leg(FX_SYM,     prev["fx"],     errors, "fx")

    pc = common.get("price")
    pa = adr.get("price")
    frate = fx.get("price")

    record = {
        "updated": now_iso(),
        "ratio": RATIO,
        "symbols": {"common": COMMON_SYM, "adr": ADR_SYM, "fx": FX_SYM},
        "common": common,
        "adr": adr,
        "fx": fx,
        "errors": errors,
    }

    if pc and pa and frate:
        adr_fair = pc * RATIO / frate            # ADR 이론가 (USD)
        implied_common = pa * frate / RATIO      # ADR 기준 본주 환산가 (KRW)
        disparity = (pa - adr_fair) / adr_fair * 100.0
        record["adr_fair_usd"] = round(adr_fair, 4)
        record["implied_common_krw"] = round(implied_common, 2)
        record["disparity_pct"] = round(disparity, 4)
    else:
        record["disparity_pct"] = None
        errors.append("disparity 계산 불가(누락된 시세 존재)")

    # ── 히스토리 누적 ─────────────────────────────────────────────────────
    history = existing.get("history", [])
    if record.get("disparity_pct") is not None and not (
        common.get("stale") and adr.get("stale") and fx.get("stale")
    ):
        history.append({
            "t": record["updated"],
            "common": pc,
            "adr": pa,
            "fx": round(frate, 4),
            "fair": record["adr_fair_usd"],
            "disparity": record["disparity_pct"],
        })
        history = history[-MAX_HISTORY:]
    record["history"] = history

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    # ── 로그 요약 ─────────────────────────────────────────────────────────
    print(f"[{record['updated']}] -> {OUT_PATH}")
    print(f"  common {COMMON_SYM}: {pc} KRW (stale={common.get('stale')})")
    print(f"  adr    {ADR_SYM}: {pa} USD (stale={adr.get('stale')})")
    print(f"  fx     {FX_SYM}: {frate} (stale={fx.get('stale')})")
    print(f"  disparity: {record.get('disparity_pct')}%  history={len(history)}")
    if errors:
        print("  errors:", "; ".join(errors))
        # 시세를 하나도 못 받았고 직전 데이터도 없으면 실패로 종료
        if pc is None and pa is None and frate is None and not existing:
            sys.exit(1)


if __name__ == "__main__":
    main()
