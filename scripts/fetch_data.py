#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SK하이닉스 본주(000660) / ADR(SKHYV) / 환율(KRW=X) 시세를 수집해 괴리율을 계산하고
data.json 에 누적 저장한다. GitHub Actions 에서 주기 실행되는 데이터 백본.

- 본주: 네이버 금융(KRX 정규 + NXT 시간외 통합) 우선, 실패 시 Yahoo(000660.KS) 폴백.
  · KRX 정규장(09:00~15:30)엔 KRX 실시간가, 그 외 시간(프리 08:00~09:00 / 애프터 15:30~20:00)엔
    NXT 시간외가(overMarketPriceInfo)를 '유효가(effective)'로 사용 → 08:00~20:00 연속 반영.
- ADR/환율: Yahoo Finance chart(v8), query1→query2 폴백.
- 표준 라이브러리만 사용.
"""
import json
import os
import sys
import time
import datetime
import urllib.request
import urllib.error

# ── 설정 ────────────────────────────────────────────────────────────────
COMMON_CODE = "000660"     # SK하이닉스 본주 (네이버 종목코드)
COMMON_YH   = "000660.KS"  # Yahoo 폴백용
ADR_SYM     = "SKHYV"       # SK하이닉스 ADR (Nasdaq, USD)
FX_SYM      = "KRW=X"       # USD/KRW
RATIO       = 0.1           # 1 ADR = 보통주 0.1주 (10 ADR = 1주)
MAX_HISTORY = 3000

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}
NAVER_POLL = "https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"

OUT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data.json")
)


def http_get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _num(s):
    """'2,180,000' / '-64,000' / '-' / None → float | None"""
    if s in (None, "", "-"):
        return None
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return None


def _iso_epoch(s):
    if not s:
        return None
    try:
        return int(datetime.datetime.fromisoformat(s).timestamp())
    except (ValueError, TypeError):
        return None


# ── Yahoo Finance chart(v8) : crumb/쿠키 불필요 ─────────────────────────────
YH_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")


def yh_chart(symbol):
    # query1 → query2 호스트 폴백 + 재시도 (GitHub 러너 IP의 429/403 대비)
    last_err = None
    for attempt in range(3):
        host = YH_HOSTS[attempt % len(YH_HOSTS)]
        url = f"https://{host}/v8/finance/chart/{symbol}?range=1d&interval=1m"
        try:
            data = json.loads(http_get(url))
            m = data["chart"]["result"][0]["meta"]
            period = (m.get("currentTradingPeriod") or {}).get("regular") or {}
            return _leg_from_meta(m, period)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5)
    raise last_err if last_err else RuntimeError("yahoo fetch failed")


def _leg_from_meta(m, period):
    return {
        "symbol":      m.get("symbol"),
        "price":       m.get("regularMarketPrice"),
        "currency":    m.get("currency"),
        "prevClose":   m.get("chartPreviousClose") or m.get("previousClose"),
        "marketTime":  m.get("regularMarketTime"),
        "marketState": m.get("marketState"),
        "exchange":    m.get("exchangeName"),
        "gmtoffset":   m.get("gmtoffset"),
        "tzName":      m.get("exchangeTimezoneName"),
        "sessStart":   period.get("start"),
        "sessEnd":     period.get("end"),
    }


# ── 본주: 네이버(KRX+NXT) ───────────────────────────────────────────────────
def fetch_common_naver(code):
    url = NAVER_POLL.format(code=code)
    d = json.loads(http_get(url, headers={"Referer": "https://finance.naver.com/"}))["datas"][0]

    krx_price = _num(d.get("closePrice"))
    krx_chg = _num(d.get("compareToPreviousClosePrice")) or 0.0
    krx = {
        "price":     krx_price,
        "prevClose": (krx_price - krx_chg) if krx_price is not None else None,
        "ts":        _iso_epoch(d.get("localTradedAt")),
        "status":    d.get("marketStatus"),            # OPEN / PREOPEN / CLOSE ...
        "fluctRatio": _num(d.get("fluctuationsRatio")),
    }

    om = d.get("overMarketPriceInfo") or {}
    nxt = None
    if om.get("overPrice") not in (None, "", "-"):
        n_price = _num(om.get("overPrice"))
        n_chg = _num(om.get("compareToPreviousClosePrice")) or 0.0
        nxt = {
            "price":     n_price,
            "prevClose": (n_price - n_chg) if n_price is not None else None,
            "ts":        _iso_epoch(om.get("localTradedAt")),
            "session":   om.get("tradingSessionType"),  # PRE_MARKET / REGULAR_MARKET / AFTER_MARKET
            "status":    om.get("overMarketStatus"),     # OPEN / CLOSED
            "fluctRatio": _num(om.get("fluctuationsRatio")),
        }
    return {"source": "naver", "krx": krx, "nxt": nxt}


def fetch_common_yahoo():
    m = yh_chart(COMMON_YH)
    return {
        "source": "yahoo",
        "krx": {
            "price": m["price"], "prevClose": m["prevClose"], "ts": m["marketTime"],
            "status": ("OPEN" if m.get("marketState") == "REGULAR" else (m.get("marketState") or "CLOSE")),
            "fluctRatio": None, "sessStart": m.get("sessStart"), "sessEnd": m.get("sessEnd"),
        },
        "nxt": None,
    }


def build_common(prev, errors):
    """네이버 우선 → Yahoo 폴백 → 직전값 재사용. 유효가(effective) 결정."""
    c = None
    try:
        c = fetch_common_naver(COMMON_CODE)
    except Exception as e:  # noqa: BLE001
        errors.append(f"common-naver: {type(e).__name__}: {e}")
        try:
            c = fetch_common_yahoo()
        except Exception as e2:  # noqa: BLE001
            errors.append(f"common-yahoo: {type(e2).__name__}: {e2}")
            if prev:
                c = dict(prev)
                c["stale"] = True
                return c
            return {"source": None, "krx": {"price": None}, "nxt": None, "price": None, "stale": True}

    krx = c.get("krx") or {}
    nxt = c.get("nxt")

    # 유효가 선택: KRX 정규장 열림이면 KRX, 아니면(프리/애프터) NXT 시간외가 우선.
    krx_open = (krx.get("status") == "OPEN")
    nxt_ext_open = bool(
        nxt and nxt.get("price") and nxt.get("status") == "OPEN"
        and nxt.get("session") in ("PRE_MARKET", "AFTER_MARKET")
    )
    if krx_open and krx.get("price"):
        venue, leg = "KRX", krx
    elif nxt_ext_open:
        venue, leg = "NXT", nxt
    elif krx.get("price"):
        venue, leg = "KRX", krx      # 양쪽 다 마감 → KRX 종가
    else:
        venue, leg = ("NXT", nxt) if (nxt and nxt.get("price")) else ("KRX", krx)

    c["effective"] = {
        "venue": venue,
        "price": leg.get("price"),
        "prevClose": leg.get("prevClose"),
        "ts": leg.get("ts"),
    }
    # 레거시 호환 필드(대시보드가 참조) = 유효가
    c["price"] = leg.get("price")
    c["prevClose"] = leg.get("prevClose")
    c["marketTime"] = leg.get("ts")
    c["currency"] = "KRW"
    c["symbol"] = COMMON_CODE
    c["exchange"] = venue
    c["stale"] = False
    return c


def fetch_leg_yh(symbol, prev_leg, errors, name):
    try:
        leg = yh_chart(symbol)
        if leg.get("price") is None:
            raise ValueError("price is None")
        leg["stale"] = False
        return leg
    except Exception as e:  # noqa: BLE001
        errors.append(f"{name}({symbol}): {type(e).__name__}: {e}")
        if prev_leg:
            fb = dict(prev_leg)
            fb["stale"] = True
            return fb
        return {"symbol": symbol, "price": None, "stale": True}


def load_existing():
    try:
        with open(OUT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def main():
    existing = load_existing()
    errors = []

    common = build_common(existing.get("common"), errors)
    adr    = fetch_leg_yh(ADR_SYM, existing.get("adr"), errors, "adr")
    fx     = fetch_leg_yh(FX_SYM,  existing.get("fx"),  errors, "fx")

    pc = common.get("price")
    pa = adr.get("price")
    frate = fx.get("price")

    record = {
        "updated": now_iso(),
        "ratio": RATIO,
        "symbols": {"common": COMMON_CODE, "adr": ADR_SYM, "fx": FX_SYM},
        "common": common,
        "adr": adr,
        "fx": fx,
        "errors": errors,
    }

    if pc and pa and frate:
        adr_fair = pc * RATIO / frate
        implied_common = pa * frate / RATIO
        disparity = (pa - adr_fair) / adr_fair * 100.0
        record["adr_fair_usd"] = round(adr_fair, 4)
        record["implied_common_krw"] = round(implied_common, 2)
        record["disparity_pct"] = round(disparity, 4)
    else:
        record["disparity_pct"] = None
        errors.append("disparity 계산 불가(누락된 시세 존재)")

    history = existing.get("history", [])
    if record.get("disparity_pct") is not None and not common.get("stale"):
        history.append({
            "t": record["updated"],
            "common": pc,
            "venue": (common.get("effective") or {}).get("venue"),
            "adr": pa,
            "fx": round(frate, 4),
            "fair": record["adr_fair_usd"],
            "disparity": record["disparity_pct"],
        })
        history = history[-MAX_HISTORY:]
    record["history"] = history

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    eff = common.get("effective") or {}
    krx = common.get("krx") or {}
    nxt = common.get("nxt") or {}
    print(f"[{record['updated']}] -> {OUT_PATH}  (common src={common.get('source')})")
    print(f"  common eff: {eff.get('price')} KRW @{eff.get('venue')}  "
          f"| KRX {krx.get('price')}({krx.get('status')})  NXT {nxt.get('price')}({nxt.get('session')}/{nxt.get('status')})")
    print(f"  adr {ADR_SYM}: {pa} USD (stale={adr.get('stale')}) | fx {frate} (stale={fx.get('stale')})")
    print(f"  disparity: {record.get('disparity_pct')}%  history={len(history)}")
    if errors:
        print("  errors:", "; ".join(errors))
        if pc is None and pa is None and frate is None and not existing:
            sys.exit(1)


if __name__ == "__main__":
    main()
