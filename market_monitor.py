#!/usr/bin/env python3
"""
========================================
  Market Anomaly Monitor + Telegram Bot
  Wersja: Railway Cloud v3
========================================
Zmienne środowiskowe Railway:
  TELEGRAM_TOKEN        — token bota Telegram
  TELEGRAM_CHAT_ID      — twoje chat ID
  ALPHA_VANTAGE_KEY     — klucz API Alpha Vantage
  PRICE_MOVE_THRESHOLD  — próg alertu % (domyślnie 2.0)
  CHECK_INTERVAL_MINUTES — interwał sesji (domyślnie 15)
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

TELEGRAM_TOKEN         = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID       = os.environ.get("TELEGRAM_CHAT_ID", "")
ALPHA_VANTAGE_KEY      = os.environ.get("ALPHA_VANTAGE_KEY", "")
PRICE_MOVE_THRESHOLD   = float(os.environ.get("PRICE_MOVE_THRESHOLD", "2.0"))
CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "15"))

TIMEZONE_PL   = pytz.timezone("Europe/Warsaw")
TIMEZONE_NYSE = pytz.timezone("America/New_York")
MARKET_OPEN   = dtime(9, 25)
MARKET_CLOSE  = dtime(16, 5)
MARKET_DAYS   = {0, 1, 2, 3, 4}

# ETF sesyjne przez yfinance (tylko podczas sesji NYSE)
STOCKS_TO_WATCH = ["SPY"]


# ============================================================
#  HELPERS
# ============================================================

def is_market_open() -> bool:
    now_ny = datetime.now(TIMEZONE_NYSE)
    return (now_ny.weekday() in MARKET_DAYS
            and MARKET_OPEN <= now_ny.time() <= MARKET_CLOSE)


def fetch_yf(symbol: str, period: str, interval: str):
    """Pobiera dane z yfinance z pauzą. Zwraca DataFrame lub None."""
    time.sleep(3)
    try:
        hist = yf.Ticker(symbol).history(period=period, interval=interval)
        return hist if not hist.empty else None
    except Exception as e:
        print(f"Błąd yf {symbol}: {e}")
        return None


def fetch_all_commodities() -> dict:
    """
    Pobiera ceny złota, srebra i ropy z Alpha Vantage.
    Używa 2 zapytań: GOLD_SILVER_SPOT (złoto + srebro naraz) + WTI (ropa).
    Pauza 13s między zapytaniami (limit: 5 req/min na darmowym planie).
    Zwraca: {"Złoto": 4812.0, "Srebro": 31.2, "Ropa WTI": 89.4}
    """
    if not ALPHA_VANTAGE_KEY:
        print("BRAK ALPHA_VANTAGE_KEY!")
        return {}

    prices = {}
    base = "https://www.alphavantage.co/query"

    # Zapytanie 1: złoto i srebro naraz
    try:
        r = requests.get(
            f"{base}?function=GOLD_SILVER_SPOT&apikey={ALPHA_VANTAGE_KEY}",
            timeout=10
        )
        data = r.json()
        print(f"AV GOLD_SILVER_SPOT odpowiedź: {list(data.keys())}")  # loguj klucze
        if "Realtime Gold and Silver" in data:
            metals = data["Realtime Gold and Silver"]
            if "Gold" in metals:
                prices["Złoto"] = float(metals["Gold"]["price"])
            if "Silver" in metals:
                prices["Srebro"] = float(metals["Silver"]["price"])
        else:
            print(f"AV GOLD_SILVER_SPOT nieoczekiwana odpowiedź: {data}")
    except Exception as e:
        print(f"Błąd AV GOLD_SILVER_SPOT: {e}")

    time.sleep(13)

    # Zapytanie 2: ropa WTI (zwraca serię dzienną — bierzemy ostatni punkt)
    try:
        r = requests.get(
            f"{base}?function=WTI&interval=daily&apikey={ALPHA_VANTAGE_KEY}",
            timeout=10
        )
        data = r.json()
        if "data" in data and len(data["data"]) > 0:
            prices["Ropa WTI"] = float(data["data"][0]["value"])
        else:
            print(f"AV WTI nieoczekiwana odpowiedź: {data}")
    except Exception as e:
        print(f"Błąd AV WTI: {e}")

    time.sleep(13)

    return prices


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
            print(f"Błąd Telegram HTTP {r.status_code}: {r.text}")
    except Exception as e:
        print(f"Błąd Telegram: {e}")


def fmt(emoji: str, title: str, lines: list) -> str:
    now = datetime.now(TIMEZONE_PL).strftime("%d.%m.%Y %H:%M")
    parts = [f"{emoji} <b>{title}</b>", f"🕐 {now}", ""]
    parts.extend(lines)
    parts += ["", "⚠️ <i>Nie jest to rekomendacja inwestycyjna.</i>"]
    return "\n".join(parts)


# ============================================================
#  STAN GLOBALNY
# ============================================================

_prices_open  = {}   # ceny z początku dnia (do obliczenia zmiany 24h)


# ============================================================
#  MODUŁ 1: PODSUMOWANIE PORANNE (9:00 PL)
# ============================================================

def morning_summary():
    print(f"[{datetime.now():%H:%M:%S}] Podsumowanie poranne — pobieram ceny...")
    prices = fetch_all_commodities()

    if not prices:
        print(f"[{datetime.now():%H:%M:%S}] Brak danych AV")
        return

    lines = []
    for name, current in prices.items():
        lines.append(f"⚪ <b>{name}</b>: ${current:.2f}")

    send_telegram(fmt("🌅", "PODSUMOWANIE PORANNE", lines))
    print(f"[{datetime.now():%H:%M:%S}] Wysłano podsumowanie poranne")


# ============================================================
#  MODUŁ 2: PODSUMOWANIE DZIENNE (17:00 PL)
# ============================================================

def daily_summary():
    global _prices_open
    print(f"[{datetime.now():%H:%M:%S}] Podsumowanie dzienne — pobieram ceny...")
    prices = fetch_all_commodities()

    if not prices:
        print(f"[{datetime.now():%H:%M:%S}] Brak danych AV")
        return

    lines = []
    for name, current in prices.items():
        open_px = _prices_open.get(name)
        if open_px and open_px > 0:
            chg = ((current - open_px) / open_px) * 100
            icon = "🟢" if chg > 0 else "🔴"
            lines.append(f"{icon} <b>{name}</b>: ${current:.2f} ({chg:+.2f}% / 24h)")
        else:
            lines.append(f"⚪ <b>{name}</b>: ${current:.2f}")

    send_telegram(fmt("📊", "PODSUMOWANIE DNIA (24h)", lines))
    print(f"[{datetime.now():%H:%M:%S}] Wysłano podsumowanie dzienne")

    # Resetuj ceny otwarcia na następny dzień
    _prices_open.update(prices)


# ============================================================
#  MODUŁ 3: ANOMALIE WOLUMENU (tylko sesja NYSE)
# ============================================================

def check_volume_anomalies():
    alerts = []
    for ticker in STOCKS_TO_WATCH:
        hist = fetch_yf(ticker, "20d", "1d")
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
                f"   Wolumen: {spike:.1f}x średniej\n"
                f"   Cena: ${today_px:.2f} ({chg:+.2f}%)"
            )

    if alerts:
        send_telegram(fmt("🚨", "ANOMALIA WOLUMENU", ["Nietypowy wolumen:"] + alerts))
    print(f"[{datetime.now():%H:%M:%S}] Wolumen: {len(alerts)} alertów")


# ============================================================
#  MODUŁ 4: VIX / STRACH (tylko sesja NYSE)
# ============================================================

def check_fear():
    hist = fetch_yf("UVXY", "10d", "1d")
    if hist is None or len(hist) < 3:
        print(f"[{datetime.now():%H:%M:%S}] VIX: brak danych")
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
    print(f"[{datetime.now():%H:%M:%S}] VIX: sprawdzono ({chg:+.2f}%)")


# ============================================================
#  GŁÓWNA PĘTLA
# ============================================================

def main():
    print("=" * 40)
    print("  Market Monitor v3 — Railway Cloud")
    print("=" * 40)
    print(f"TOKEN:    {'OK' if TELEGRAM_TOKEN else 'BRAK!'}")
    print(f"CHAT_ID:  {'OK' if TELEGRAM_CHAT_ID else 'BRAK!'}")
    print(f"AV KEY:   {'OK' if ALPHA_VANTAGE_KEY else 'BRAK!'}\n")

    send_telegram(
        "🚀 <b>Market Monitor v3 uruchomiony!</b>\n"
        "🌅 Podsumowanie poranne: 9:00 PL\n"
        "📊 Podsumowanie dzienne: 17:00 PL\n"
        "🚨 Sesja NYSE: wolumen SPY + VIX co 15 min\n"
        "Łącznie: ~6 zapytań AV dziennie"
    )

    # Pobierz ceny otwarcia dnia — 2 zapytania AV
    print("Pobieranie cen otwarcia dnia...")
    opening = fetch_all_commodities()
    _prices_open.update(opening)
    print(f"Ceny otwarcia: {opening}")

    last_session       = time.time()
    morning_sent_today = False
    daily_sent_today   = False
    last_day           = datetime.now(TIMEZONE_PL).day

    SESSION_SECS = CHECK_INTERVAL_MINUTES * 60

    while True:
        now    = time.time()
        now_pl = datetime.now(TIMEZONE_PL)

        # Reset flag o północy
        if now_pl.day != last_day:
            morning_sent_today = False
            daily_sent_today   = False
            last_day = now_pl.day
            print(f"[{now_pl:%H:%M:%S}] Nowy dzień — reset flag")

        # Podsumowanie poranne o 9:00 PL
        if now_pl.hour == 9 and now_pl.minute < 2 and not morning_sent_today:
            morning_summary()
            morning_sent_today = True

        # Podsumowanie dzienne o 17:00 PL
        if now_pl.hour == 17 and now_pl.minute < 2 and not daily_sent_today:
            daily_summary()
            daily_sent_today = True

        # Sesja NYSE — wolumen i VIX co CHECK_INTERVAL_MINUTES
        if is_market_open() and (now - last_session) >= SESSION_SECS:
            print(f"[{now_pl:%H:%M:%S}] Sesja otwarta — sprawdzam...")
            check_volume_anomalies()
            check_fear()
            last_session = time.time()

        time.sleep(60)


if __name__ == "__main__":
    main()
