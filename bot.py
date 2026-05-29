import os
import asyncio
import aiohttp
from datetime import datetime

# ── Configurare ───────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8670161331:AAGKofK4yxbbuhfhyqgW7CMg7PEUISBo1BA")
CHAT_ID   = os.environ.get("CHAT_ID",   "8639469548")
BINANCE   = "https://api.binance.com/api/v3"
TOP_N     = 50       # câte monede scanează
INTERVAL  = 3600     # la câte secunde scanează (3600 = 1 oră)
MIN_SCORE = 70       # scor minim pentru alertă

# ── Indicatori tehnici ────────────────────────────────────────────────────────
def ema(arr, p):
    k = 2 / (p + 1)
    e = arr[0]
    for x in arr[1:]:
        e = x * k + e * (1 - k)
    return e

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains = losses = 0
    for i in range(len(closes) - period, len(closes)):
        d = closes[i] - closes[i-1]
        if d > 0: gains += d
        else: losses += abs(d)
    ag, al = gains/period, losses/period
    if al == 0: return 100
    return 100 - 100 / (1 + ag/al)

def calc_macd(closes):
    if len(closes) < 26: return 0
    return ema(closes[-26:], 12) - ema(closes[-26:], 26)

def calc_wyckoff(candles):
    s = candles[-30:]
    avg_vol = sum(c["vol"] for c in s) / len(s)
    up_big = down_sm = down_big = 0
    for c in s:
        is_up = c["close"] > c["open"]
        big   = c["vol"] > avg_vol * 1.2
        sm    = c["vol"] < avg_vol * 0.8
        if is_up and big:  up_big  += 1
        if not is_up and sm:  down_sm += 1
        if not is_up and big: down_big += 1
    bull  = up_big * 3 + down_sm * 2
    bear  = down_big * 3
    total = bull + bear or 1
    score = round((bull / total) * 100)
    if score >= 72 and down_sm >= 4 and up_big >= 3:
        phase = "Acumulare Wyckoff ✓"
    elif score >= 58:
        phase = "Posibilă Acumulare"
    elif score <= 30:
        phase = "Distribuție"
    else:
        phase = "Neutru"
    return {"score": score, "phase": phase}

def calc_vwap(candles):
    s = candles[-20:]
    sum_pv = sum_v = 0
    for c in s:
        tp = (c["high"] + c["low"] + c["close"]) / 3
        sum_pv += tp * c["vol"]
        sum_v  += c["vol"]
    vwap = sum_pv / sum_v if sum_v else 0
    last = candles[-1]["close"]
    pct  = ((last - vwap) / vwap * 100) if vwap else 0
    return {"below": last < vwap, "pct": round(pct, 2)}

def calc_cvd(candles):
    s = candles[-40:]
    deltas, cum = [], 0
    for c in s:
        r = c["high"] - c["low"] or 0.0001
        cum += ((c["close"]-c["low"])/r - (c["high"]-c["close"])/r) * c["vol"]
        deltas.append({"cum": cum, "close": c["close"]})
    r10 = deltas[-10:]
    cvd_slope   = r10[-1]["cum"]   - r10[0]["cum"]
    price_slope = r10[-1]["close"] - r10[0]["close"]
    return {
        "bull_div": cvd_slope > 0 and price_slope < 0,
        "bear_div": cvd_slope < 0 and price_slope > 0,
        "positive": cvd_slope > 0,
    }

def calc_cmf(candles, period=20):
    s = candles[-period:]
    mfv = vol = 0
    for c in s:
        r = c["high"] - c["low"] or 0.0001
        mfv += ((c["close"]-c["low"]) - (c["high"]-c["close"])) / r * c["vol"]
        vol += c["vol"]
    return mfv / vol if vol else 0

def calc_trend(closes):
    if len(closes) < 50: return {"bull": False, "slope": 0}
    e20 = ema(closes[-20:], 20)
    e50 = ema(closes[-50:], 50)
    ep  = ema(closes[-21:-1], 20)
    return {"bull": e20 > e50, "slope": round((e20-ep)/ep*100, 3)}

def calc_golden_cross(closes):
    if len(closes) < 55: return False
    ne20 = ema(closes[-20:], 20); ne50 = ema(closes[-50:], 50)
    pe20 = ema(closes[-25:-5], 20); pe50 = ema(closes[-55:-5], 50)
    return pe20 < pe50 and ne20 > ne50

def calc_breakout(candles):
    if len(candles) < 22: return False
    max_h = max(c["high"] for c in candles[-22:-2])
    return candles[-1]["close"] > max_h

def calc_squeeze(closes):
    if len(closes) < 20: return False
    sl = closes[-20:]
    m  = sum(sl) / 20
    sd = (sum((x-m)**2 for x in sl)/20)**0.5
    return (sd*4/m*100) < 4

def calc_score(rsi, macd, cmf, trend, gc, bo, sq, wyckoff, vwap, cvd, change24h):
    s = 45
    if rsi < 30:   s += 16
    elif rsi < 44: s += 8
    elif rsi > 72: s -= 14
    s += 7 if macd > 0 else -4
    if cmf > 0.15:   s += 12
    elif cmf > 0.05: s += 6
    elif cmf < -0.15: s -= 14
    elif cmf < -0.05: s -= 7
    if trend["bull"] and trend["slope"] > 0.3: s += 14
    elif trend["bull"]: s += 6
    elif not trend["bull"] and trend["slope"] < -0.3: s -= 11
    if gc: s += 18
    if bo: s += 14
    if sq: s += 7
    if wyckoff["score"] >= 72: s += 22
    elif wyckoff["score"] >= 58: s += 11
    elif wyckoff["score"] <= 30: s -= 18
    if vwap["below"] and cmf > 0.05: s += 14
    elif vwap["below"]: s += 5
    if cvd["bull_div"]:  s += 20
    elif cvd["bear_div"]: s -= 18
    elif cvd["positive"]: s += 8
    if change24h > 6:   s += 6
    elif change24h > 2: s += 3
    elif change24h < -7: s -= 9
    elif change24h < -3: s -= 4
    return max(0, min(100, round(s)))

# ── Binance API ───────────────────────────────────────────────────────────────
async def get_top_tickers(session):
    async with session.get(f"{BINANCE}/ticker/24hr") as r:
        data = await r.json()
    exclude = ["DOWN","UP","BULL","BEAR"]
    filtered = [t for t in data
                if t["symbol"].endswith("USDT")
                and not any(x in t["symbol"] for x in exclude)]
    filtered.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
    return filtered[:TOP_N]

async def get_klines(session, symbol):
    url = f"{BINANCE}/klines?symbol={symbol}&interval=1h&limit=80"
    async with session.get(url) as r:
        data = await r.json()
    return [{"open": float(k[1]), "high": float(k[2]),
             "low":  float(k[3]), "close": float(k[4]),
             "vol":  float(k[5])} for k in data]

# ── Scanare completă ──────────────────────────────────────────────────────────
async def scan_market():
    results = []
    async with aiohttp.ClientSession() as session:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Scaneaz top {TOP_N} monede...")
        tickers = await get_top_tickers(session)

        for i, t in enumerate(tickers):
            symbol = t["symbol"]
            try:
                candles = await get_klines(session, symbol)
                if len(candles) < 30:
                    continue
                closes    = [c["close"] for c in candles]
                change24h = float(t["priceChangePercent"])

                rsi     = calc_rsi(closes)
                macd    = calc_macd(closes)
                cmf     = calc_cmf(candles)
                trend   = calc_trend(closes)
                gc      = calc_golden_cross(closes)
                bo      = calc_breakout(candles)
                sq      = calc_squeeze(closes)
                wyckoff = calc_wyckoff(candles)
                vwap    = calc_vwap(candles)
                cvd     = calc_cvd(candles)
                score   = calc_score(rsi, macd, cmf, trend, gc, bo, sq,
                                     wyckoff, vwap, cvd, change24h)

                results.append({
                    "symbol":   symbol.replace("USDT",""),
                    "price":    float(t["lastPrice"]),
                    "change":   change24h,
                    "score":    score,
                    "rsi":      round(rsi, 1),
                    "cmf":      round(cmf, 3),
                    "wyckoff":  wyckoff,
                    "vwap":     vwap,
                    "cvd":      cvd,
                    "gc":       gc,
                    "bo":       bo,
                    "sq":       sq,
                    "trend":    trend,
                })
                print(f"  [{i+1}/{len(tickers)}] {symbol}: scor {score}")
                await asyncio.sleep(0.1)  # rate limit protecție
            except Exception as e:
                print(f"  ⚠️  {symbol} eroare: {e}")

    return sorted(results, key=lambda x: x["score"], reverse=True)

# ── Formatare mesaj Telegram ──────────────────────────────────────────────────
def format_alert(coin):
    c = coin
    price = c["price"]
    if price < 0.001:  price_str = f"${price:.8f}"
    elif price < 1:    price_str = f"${price:.4f}"
    else:              price_str = f"${price:.2f}"

    change_emoji = "🟢" if c["change"] >= 0 else "🔴"
    score_emoji  = "🚀" if c["score"] >= 75 else "📈"

    signals = []
    if c["wyckoff"]["score"] >= 72:
        signals.append(f"🐋 Wyckoff: {c['wyckoff']['phase']} ({c['wyckoff']['score']}/100)")
    elif c["wyckoff"]["score"] >= 58:
        signals.append(f"🐋 Wyckoff: {c['wyckoff']['phase']}")
    if c["cvd"]["bull_div"]:
        signals.append("💎 CVD↑ Preț↓ = Acumulare Ascunsă!")
    if c["vwap"]["below"] and c["cmf"] > 0.05:
        signals.append(f"📍 Sub VWAP ({c['vwap']['pct']}%) + Acumulare")
    elif c["vwap"]["below"]:
        signals.append(f"📍 Sub VWAP ({c['vwap']['pct']}%)")
    if c["gc"]:
        signals.append("⭐ Golden Cross!")
    if c["bo"]:
        signals.append("🚀 Breakout!")
    if c["sq"]:
        signals.append("⚡ Squeeze — mișcare iminentă")
    if c["rsi"] < 30:
        signals.append(f"📊 RSI Supravândut ({c['rsi']})")
    if c["trend"]["bull"] and c["trend"]["slope"] > 0.3:
        signals.append("📈 Trend Bullish puternic")

    signals_text = "\n".join(f"  {s}" for s in signals) if signals else "  Indicatori generali pozitivi"

    return (
        f"{score_emoji} *{c['symbol']}/USDT* — Scor: *{c['score']}/100*\n"
        f"💰 Preț: `{price_str}` {change_emoji} {c['change']:+.2f}%\n"
        f"📋 RSI: `{c['rsi']}` | CMF: `{c['cmf']}`\n"
        f"\n*Semnale detectate:*\n{signals_text}\n"
        f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )

# ── Trimite mesaj Telegram ────────────────────────────────────────────────────
async def send_telegram(text):
    url  = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=data) as r:
            return await r.json()

# ── Loop principal ────────────────────────────────────────────────────────────
async def main():
    print("╔══════════════════════════════════╗")
    print("║   CRYPTO SCANNER BOT PORNIT 🚀   ║")
    print("╚══════════════════════════════════╝")

    # Mesaj de start
    await send_telegram(
        "🤖 *Crypto Scanner Bot pornit!*\n"
        f"Scanez top {TOP_N} monede de pe Binance\n"
        f"Alertă când scor ≥ {MIN_SCORE}/100\n"
        f"Interval: la fiecare oră ⏰\n\n"
        "_Wyckoff · VWAP · CVD · RSI · MACD · Golden Cross_"
    )

    while True:
        try:
            coins = await scan_market()
            hot   = [c for c in coins if c["score"] >= MIN_SCORE]

            now = datetime.now().strftime("%H:%M")
            print(f"\n✅ Scanare completă. {len(hot)} monede cu scor ≥ {MIN_SCORE}")

            if hot:
                # Trimite sumar
                header = (
                    f"🔔 *CRYPTO SCANNER — {now}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 {len(coins)} monede scanate\n"
                    f"🎯 {len(hot)} oportunități găsite (scor ≥ {MIN_SCORE})\n"
                    f"━━━━━━━━━━━━━━━━━━━━"
                )
                await send_telegram(header)
                await asyncio.sleep(1)

                # Trimite top 5 alerte individuale
                for coin in hot[:5]:
                    await send_telegram(format_alert(coin))
                    await asyncio.sleep(1)

                # Dacă sunt mai multe, trimite lista scurtă
                if len(hot) > 5:
                    extra = "\n".join(
                        f"  • *{c['symbol']}* — {c['score']}/100"
                        for c in hot[5:10]
                    )
                    await send_telegram(f"📋 *Alte oportunități:*\n{extra}")
            else:
                await send_telegram(
                    f"🔍 *Scanare {now}*\n"
                    f"Nicio monedă cu scor ≥ {MIN_SCORE}.\n"
                    f"Piața e neutră momentan. ⚖️"
                )

        except Exception as e:
            print(f"❌ Eroare scanare: {e}")
            await send_telegram(f"⚠️ Eroare la scanare: {e}")

        print(f"💤 Aștept {INTERVAL//60} minute până la următoarea scanare...\n")
        await asyncio.sleep(INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
