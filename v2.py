# ============================================================
# ETHUSD BOT V3.1 SAFE — INDIA (Delta Exchange)
# Ultra Safe Mode: 2–3 trades/day, Conservative TP, Smart SL
# ============================================================

import os
import time
import json
import hmac
import hashlib
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# ---------------- API CONFIG ----------------
API_KEY = os.getenv("DELTA_API_KEY")
API_SECRET = os.getenv("DELTA_API_SECRET")

BASE_URL = "https://api.india.delta.exchange"
SYMBOL = "ETHUSD"

# ---------------- STRATEGY PARAMETERS ----------------
ENTRY_PREMIUM = 0.00050        # 0.05% → very safe entry
EXIT_PREMIUM  = 0.00010        # mean reversion exit
EMA_PERIOD = 20
VOL_MAX = 0.0008               # 0.08% volatility filter

# Conservative TP (SAFEST)
TP_MIN = 0.0015                # 0.15%
TP_MAX = 0.0022                # 0.22%

# Smart Stop-Loss
SL_LOW_VOL = 0.0020            # 0.20%
SL_NORMAL = 0.0025             # 0.25%
SL_HIGH_VOL = 0.0018           # 0.18%

POLL = 1.2
MIN_CONTRACTS = 1
MAX_TRADES_PER_DAY = 3

position = None
prices = []
today_trades = 0
last_day = datetime.now(timezone.utc).day



# ---------------- SIGNATURE ----------------

def sign(method, path, query="", body=""):
    ts = str(int(time.time()))
    payload = method + ts + path + query + body
    sig = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {
        "api-key": API_KEY,
        "timestamp": ts,
        "signature": sig,
        "User-Agent": "ethusd-safe-v31",
        "Content-Type": "application/json"
    }


# ---------------- TICKER ----------------

def get_ticker():
    r = requests.get(BASE_URL + "/v2/tickers", timeout=10).json()
    for t in r["result"]:
        if t["symbol"] == SYMBOL:
            mark = float(t["mark_price"])
            spot = float(t["spot_price"])
            premium = (mark - spot) / spot
            return mark, spot, premium
    raise Exception("ETHUSD ticker missing")


# ---------------- EMA ----------------

def ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


# ---------------- VOLATILITY FILTER ----------------

def calc_volatility(values):
    if len(values) < 4:
        return 0.0
    v1 = abs(values[-1] - values[-2]) / values[-2]
    v2 = abs(values[-2] - values[-3]) / values[-3]
    v3 = abs(values[-3] - values[-4]) / values[-4]
    return max(v1, v2, v3)


# ---------------- MARKET ORDER ----------------

def market_order(side, size, reduce_only=False):
    path = "/v2/orders"
    body_data = {
        "product_id": 3136,
        "size": size,
        "side": side,
        "order_type": "market_order",
        "reduce_only": reduce_only
    }
    body = json.dumps(body_data)
    headers = sign("POST", path, "", body)

    print(f"[EXEC] MARKET {side.upper()} size={size}")
    r = requests.post(BASE_URL + path, headers=headers, data=body)
    print("[ORDER RESPONSE]", r.text)
    return r.json()


# ---------------- MAIN LOOP ----------------

def main():
    global position, today_trades, last_day, prices

    print("\n--- ETHUSD BOT V3.1 ULTRA SAFE (INDIA REAL) ---\n")

    while True:
        try:
            # Reset daily trade limit
            now = datetime.now(timezone.utc)
            if now.day != last_day:
                today_trades = 0
                last_day = now.day
                print("\n[RESET] New day → trade counter reset\n")

            # STOP trading after limit
            if today_trades >= MAX_TRADES_PER_DAY:
                print("[SLEEP] Trade limit reached → waiting for tomorrow...")
                time.sleep(30)
                continue

            # ---------------- GET MARKET DATA ----------------
            mark, spot, premium = get_ticker()
            prices.append(mark)

            if len(prices) > EMA_PERIOD * 3:
                prices.pop(0)

            trend = ema(prices, EMA_PERIOD)
            trend_prev = ema(prices[:-1], EMA_PERIOD)

            vol = calc_volatility(prices)

            trend_display = f"{trend:.2f}" if trend else "warming…"
            print(f"[TICK] mark={mark:.2f} trend={trend_display} premium={premium*100:.4f}% vol={vol*100:.3f}%")

            # Wait for EMA warmup
            if not trend or not trend_prev:
                time.sleep(POLL)
                continue

            # High volatility → avoid trading
            if vol > VOL_MAX:
                print("[FILTER] volatility high → skipping")
                time.sleep(POLL)
                continue

            # ---------------- MANAGE ACTIVE POSITION ----------------
            if position:
                side = position["side"]
                entry = position["entry"]

                # Smart SL selection
                if vol < 0.0004:
                    sl = SL_LOW_VOL
                elif vol < 0.0007:
                    sl = SL_NORMAL
                else:
                    sl = SL_HIGH_VOL

                # Conservative TP selection
                tp = TP_MIN if abs(premium) < EXIT_PREMIUM else TP_MAX

                # STOP LOSS
                if side == "buy" and mark <= entry * (1 - sl):
                    print(f"[SL] LONG stopped at {mark}")
                    market_order("sell", MIN_CONTRACTS, True)
                    position = None
                    continue

                if side == "sell" and mark >= entry * (1 + sl):
                    print(f"[SL] SHORT stopped at {mark}")
                    market_order("buy", MIN_CONTRACTS, True)
                    position = None
                    continue

                # TAKE PROFIT
                if side == "buy" and mark >= entry * (1 + tp):
                    print(f"[TP] LONG profit at {mark}")
                    market_order("sell", MIN_CONTRACTS, True)
                    position = None
                    continue

                if side == "sell" and mark <= entry * (1 - tp):
                    print(f"[TP] SHORT profit at {mark}")
                    market_order("buy", MIN_CONTRACTS, True)
                    position = None
                    continue

                # Mean reversion exit
                if side == "buy" and premium >= -EXIT_PREMIUM:
                    print("[EXIT] LONG mean revert")
                    market_order("sell", MIN_CONTRACTS, True)
                    position = None
                    continue

                if side == "sell" and premium <= EXIT_PREMIUM:
                    print("[EXIT] SHORT mean revert")
                    market_order("buy", MIN_CONTRACTS, True)
                    position = None
                    continue

                time.sleep(POLL)
                continue

            # ---------------- ENTRY CONDITIONS ----------------

            # LONG entry
            if premium <= -ENTRY_PREMIUM and mark > trend and trend > trend_prev:
                print(f"[ENTRY] LONG @ {mark}")
                market_order("buy", MIN_CONTRACTS)
                position = {"side": "buy", "entry": mark}
                today_trades += 1
                continue

            # SHORT entry
            if premium >= ENTRY_PREMIUM and mark < trend and trend < trend_prev:
                print(f"[ENTRY] SHORT @ {mark}")
                market_order("sell", MIN_CONTRACTS)
                position = {"side": "sell", "entry": mark}
                today_trades += 1
                continue

            time.sleep(POLL)

        except KeyboardInterrupt:
            print("Bot stopped manually.")
            break
        except Exception as e:
            print("MAIN LOOP ERROR:", e)
            time.sleep(2)


# ---------------- START ----------------

if __name__ == "__main__":
    if not API_KEY or not API_SECRET:
        print("❌ ERROR: API keys missing (.env is empty)")
        exit()
    main()
