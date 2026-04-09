#!/usr/bin/env python3
"""
========================================
  Market Anomaly Monitor + Telegram Bot
  Wersja: Railway Cloud v2
========================================
Konfiguracja przez zmienne środowiskowe Railway:
  TELEGRAM_TOKEN   — token bota Telegram
  TELEGRAM_CHAT_ID — twoje chat ID
"""

import yfinance as yf
import requests
import time
import os
from datetime import datetime, time as dtime
import pytz


# ============================================================
#  KONFIGURACJA
# ============================================================

TELEGRAM_TOKEN        = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID      = os.environ.get("TELEGRAM_CHAT_ID", "")
ALPHA_VANTAGE_KEY     = os.environ.get("ALPHA_VANTAGE_KEY", "")

PRICE_MOVE_THRESHOLD   = float(os.environ.get("PRICE_MOVE_THRESHOLD", "2.0"))
CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "15"))

TIMEZONE_PL   = pytz.timezone("Europe/Warsaw")
TIMEZONE_NYSE = pytz.timezone("America/New_York")
MARKET_OPEN   = dtime(9, 25)
MARKET_CLOSE  = dtime(16, 5)
MARKET_DAYS   = {0, 1, 2, 3, 4}

# Symbole Alpha Vantage dla surowców
COMMODITIES_AV = {
    "GOLD":        "Złoto",
    "SILVER":      "Srebro",
    "WTI":         "Ropa WTI",
}

# Minimum instrumentów giełdowych (yfinance — tylko sesja)
STOCKS_TO_WATCH = ["SPY", "GLD", "SLV"]
OPTIONS_PROXY   = ["UVXY"]


# ============================================================
#  HELPERS
# ============================================================

def is_market_open() -> bool:
    now_ny = datetime.now(TIMEZONE_NYSE)
    return now_ny.weekday() in MARKET_DAYS and MARKET_OPEN <= now_ny.time() <= MARKET_CLOSE


def is_weekday() -> bool:
    return datetime.now(TIMEZONE_NYSE).weekday() in MARKET_DAYS


def fetch_av_commodity(symbol: str) -> dict | None:
    """Pobiera dane surowca z Alpha Vantage (dzienny interwał)."""
    url = (
        f"https://www.alphavantage.co/query"
        f"?function=COMMODITY_EXCHANGE_RATE"
        f"&from_currency={symbol}"
        f"&to_currency=USD"
        f"&apikey={ALPHA_VANTAGE_KEY}"
    )
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        if "Realtime Commodity Exchange Rate" in data:
            rate = data["Realtime Commodity Exchange Rate"]
            return {
                "price": float(rate["5. Exchange Rate"]),
                "time":  rate["6. Last Refreshed"],
            }
    except Exception as e:
        print(f"Błąd AV {symbol}: {e}")
    return None


def fetch_yf(symbol: str, period: str, interval: str):
    """Pobiera dane z yfinance z pauzą (tylko dla ETF podczas sesji)."""
    time.sleep(3)
    try:
        hist = yf.Ticker(symbol).history(period=period, interval=interval)
        return hist if not hist.empty else None
    except Exception as e:
        print(f"Błąd yf {symbol}: {e}")
        return None


# ============================================================
#  TELEGRAM
# ============================================================

def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TELEGRAM brak konfiguracji]\n{message}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        if r.status_code != 200:
            print(f"Błąd Telegram: {r.text}")
    except Exception as e:
        print(f"Błąd Telegram: {e}")


def fmt(emoji: str, title: str, lines: list) -> str:
    now = datetime.now(TIMEZONE_PL).strftime("%d.%m.%Y %H:%M")
    parts = [f"{emoji} <b>{title}</b>", f"🕐 {now}", ""]
    parts.extend(lines)
    parts += ["", "⚠️ <i>Nie jest to rekomendacja inwestycyjna.</i>"]
    return "\n".join(parts)


# ============================================================
#  MODUŁ 1: PODSUMOWANIE GODZINNE SUROWCÓW (Alpha Vantage)
# ============================================================

# Przechowuje ostatnie ceny do obliczania zmian
_last_prices = {}

def hourly_commodity_summary():
    global _last_prices
    lines = []

    for symbol, name in COMMODITIES_AV.items():
        data = fetch_av_commodity(symbol)
        time.sleep(12)  # Alpha Vantage limit: 5 req/min na darmowym planie

        if data is None:
            continue

        current = data["price"]
        prev    = _last_prices.get(symbol)
        _last_prices[symbol] = current

        if prev:
            chg = ((current - prev) / prev) * 100
            icon = "🟢" if chg > 0 else "🔴"
            lines.append(f"{icon} <b>{name}</b>: ${current:.2f} ({chg:+.2f}% / 1h)")
        else:
            lines.append(f"⚪ <b>{name}</b>: ${current:.2f}")

    if lines:
        send_telegram(fmt("⏰", "PODSUMOWANIE GODZINNE", lines))
        print(f"[{datetime.now():%H:%M:%S}] Wysłano podsumowanie godzinne")
    else:
        print(f"[{datetime.now():%H:%M:%S}] Podsumowanie — brak danych AV")


# ============================================================
#  MODUŁ 2: ALERTY SUROWCÓW (Alpha Vantage)
# ============================================================

def check_commodities():
    alerts = []

    for symbol, name in COMMODITIES_AV.items():
        data = fetch_av_commodity(symbol)
        time.sleep(12)

        if data is None:
            continue

        current = data["price"]
        prev    = _last_prices.get(symbol)

        if prev and prev > 0:
            chg = ((current - prev) / prev) * 100
            if abs(chg) >= 0.8:
                icon = "📈" if chg > 0 else "📉"
                alerts.append(
                    f"{icon} <b>{name}</b>\n"
                    f"   Zmiana: {chg:+.2f}%\n"
                    f"   Cena: ${current:.2f}"
                )

        _last_prices[symbol] = current

    if alerts:
        send_telegram(fmt("🏅", "RUCH NA SUROWCACH", ["Znaczący ruch:"] + alerts))
    print(f"[{datetime.now():%H:%M:%S}] Surowce AV: {len(alerts)} alertów")


# ============================================================
#  MODUŁ 3: ANOMALIE WOLUMENU (tylko sesja)
# ============================================================

def check_volume_anomalies():
    alerts = []
    for ticker in STOCKS_TO_WATCH:
        hist = fetch(ticker, "20d", "1d")
        if hist is None or len(hist) < 5:
            continue
        avg_vol   = hist["Volume"][:-1].mean()
        today_vol = hist["Volume"].iloc[-1]
        today_px  = hist["Close"].iloc[-1]
        prev_px   = hist["Close"].iloc[-2]
        chg       = ((today_px - prev_px) / prev_px) * 100
        spike     = today_vol / avg_vol if avg_vol > 0 else 0

        if spike >= 3.0:
            alerts.append(
                f"📊 <b>{ticker}</b>\n"
                f"   Wolumen: {spike:.1f}x śr.\n"
                f"   Cena: ${today_px:.2f} ({chg:+.2f}%)"
            )

    if alerts:
        send_telegram(fmt("🚨", "ANOMALIA WOLUMENU", ["Nietypowy wolumen:"] + alerts))
    print(f"[{datetime.now():%H:%M:%S}] Wolumen: {len(alerts)} alertów")


# ============================================================
#  MODUŁ 4: VIX / STRACH (tylko sesja)
# ============================================================

def check_fear():
    hist = fetch("UVXY", "10d", "1d")
    if hist is None or len(hist) < 3:
        return
    avg_vol   = hist["Volume"][:-1].mean()
    today_vol = hist["Volume"].iloc[-1]
    today_px  = hist["Close"].iloc[-1]
    prev_px   = hist["Close"].iloc[-2]
    chg       = ((today_px - prev_px) / prev_px) * 100
    spike     = today_vol / avg_vol if avg_vol > 0 else 0

    if spike >= 2.5 and abs(chg) >= 3.0:
        send_telegram(fmt("😱", "WZROST STRACHU (VIX)", [
            f"UVXY: {chg:+.2f}% | Wolumen: {spike:.1f}x śr.",
            f"Cena: ${today_px:.2f}"
        ]))
    print(f"[{datetime.now():%H:%M:%S}] VIX: sprawdzono")


# ============================================================
#  GŁÓWNA PĘTLA
# ============================================================

def main():
    print("="*40)
    print("  Market Monitor v2 — Railway Cloud")
    print("="*40)
    print(f"TOKEN:   {'OK' if TELEGRAM_TOKEN else 'BRAK!'}")
    print(f"CHAT_ID: {'OK' if TELEGRAM_CHAT_ID else 'BRAK!'}\n")

    send_telegram(
        "🚀 <b>Market Monitor v2 uruchomiony!</b>\n"
        "Surowce: złoto, srebro, ropa (24h)\n"
        "Podsumowanie: co godzinę\n"
        "Alerty: przy ruchu > 0.8%/1h lub > 2%/4h"
    )

    # Pierwsze podsumowanie od razu
    hourly_commodity_summary()

    last_commodity  = time.time()
    last_hourly     = time.time()
    last_session    = time.time()

    COMMODITY_SECS  = 15 * 60
    HOURLY_SECS     = 60 * 60
    SESSION_SECS    = CHECK_INTERVAL_MINUTES * 60

    while True:
        now = time.time()
        now_pl = datetime.now(TIMEZONE_PL).strftime("%H:%M")

        # Podsumowanie godzinne — zawsze
        if (now - last_hourly) >= HOURLY_SECS:
            hourly_commodity_summary()
            last_hourly = time.time()

        # Alerty surowców — co 15 minut w dni robocze
        if is_weekday() and (now - last_commodity) >= COMMODITY_SECS:
            check_commodities()
            last_commodity = time.time()

        # Sesja giełdowa — wolumen i VIX
        if is_market_open() and (now - last_session) >= SESSION_SECS:
            print(f"[{now_pl}] Sesja otwarta...")
            check_volume_anomalies()
            check_fear()
            last_session = time.time()

        time.sleep(60)  # sprawdzaj co minutę


if __name__ == "__main__":
    main()
