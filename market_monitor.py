#!/usr/bin/env python3
"""
========================================
  Market Anomaly Monitor + Telegram Bot
  Wersja: Railway Cloud v3
========================================
Zmienne środowiskowe Railway:
  TELEGRAM_TOKEN     — token bota Telegram
  TELEGRAM_CHAT_ID   — twoje chat ID
  ALPHA_VANTAGE_KEY  — klucz API Alpha Vantage
  PRICE_MOVE_THRESHOLD — próg alertu % (domyślnie 2.0)
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

# Surowce przez Alpha Vantage (3 zapytania/h = 72/dobę)
# Darmowy plan AV: 25 req/dzień — używamy tylko podsumowania godzinnego
# i dziennego, NIE alertów co 15 minut
COMMODITIES_AV = {
    "GOLD":   "Złoto",
    "SILVER": "Srebro",
    "WTI":    "Ropa WTI",
}

# ETF sesyjne przez yfinance (tylko podczas sesji NYSE)
STOCKS_TO_WATCH = ["SPY"]
OPTIONS_PROXY   = ["UVXY"]


# ============================================================
#  HELPERS
# ============================================================

def is_market_open() -> bool:
    now_ny = datetime.now(TIMEZONE_NYSE)
    return (now_ny.weekday() in MARKET_DAYS
            and MARKET_OPEN <= now_ny.time() <= MARKET_CLOSE)


def is_weekday() -> bool:
    return datetime.now(TIMEZONE_NYSE).weekday() in MARKET_DAYS


def fetch_av(symbol: str) -> float | None:
    """Pobiera aktualną cenę surowca z Alpha Vantage. Zwraca float lub None."""
    if not ALPHA_VANTAGE_KEY:
        print("BRAK ALPHA_VANTAGE_KEY!")
        return None
    url = (
        "https://www.alphavantage.co/query"
        f"?function=COMMODITY_EXCHANGE_RATE"
        f"&from_currency={symbol}"
        f"&to_currency=USD"
        f"&apikey={ALPHA_VANTAGE_KEY}"
    )
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        if "Realtime Commodity Exchange Rate" in data:
            return float(data["Realtime Commodity Exchange Rate"]["5. Exchange Rate"])
        print(f"AV {symbol} nieoczekiwana odpowiedź: {data}")
    except Exception as e:
        print(f"Błąd AV {symbol}: {e}")
    return None


def fetch_yf(symbol: str, period: str, interval: str):
    """Pobiera dane z yfinance z pauzą. Zwraca DataFrame lub None."""
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
#  STAN GLOBALNY — ceny do obliczania zmian
# ============================================================

_prices_hourly = {}   # ceny z ostatniego podsumowania godzinnego
_prices_daily  = {}   # ceny z początku dnia (reset o północy)


def _fetch_all_commodities() -> dict:
    """Pobiera ceny wszystkich surowców z AV. Pauza 13s między zapytaniami
    (limit darmowego planu: 5 req/min). Zwraca {symbol: cena}."""
    prices = {}
    for symbol in COMMODITIES_AV:
        price = fetch_av(symbol)
        if price is not None:
            prices[symbol] = price
        time.sleep(13)
    return prices


# ============================================================
#  MODUŁ 1: PODSUMOWANIE GODZINNE SUROWCÓW
# ============================================================

def hourly_commodity_summary():
    global _prices_hourly
    print(f"[{datetime.now():%H:%M:%S}] Pobieranie cen surowców (AV)...")
    prices = _fetch_all_commodities()

    if not prices:
        print(f"[{datetime.now():%H:%M:%S}] Podsumowanie godzinne — brak danych AV")
        return

    lines = []
    for symbol, name in COMMODITIES_AV.items():
        current = prices.get(symbol)
        if current is None:
            continue
        prev = _prices_hourly.get(symbol)
        if prev and prev > 0:
            chg = ((current - prev) / prev) * 100
            icon = "🟢" if chg > 0 else "🔴"
            lines.append(f"{icon} <b>{name}</b>: ${current:.2f} ({chg:+.2f}% / 1h)")
        else:
            lines.append(f"⚪ <b>{name}</b>: ${current:.2f} (pierwsza cena)")

    _prices_hourly.update(prices)

    if lines:
        send_telegram(fmt("⏰", "PODSUMOWANIE GODZINNE", lines))
        print(f"[{datetime.now():%H:%M:%S}] Wysłano podsumowanie godzinne")


# ============================================================
#  MODUŁ 2: PODSUMOWANIE DZIENNE (17:00 PL)
# ============================================================

def daily_commodity_summary():
    global _prices_daily
    print(f"[{datetime.now():%H:%M:%S}] Pobieranie cen na podsumowanie dzienne...")
    prices = _fetch_all_commodities()

    if not prices:
        print(f"[{datetime.now():%H:%M:%S}] Podsumowanie dzienne — brak danych AV")
        return

    lines = []
    for symbol, name in COMMODITIES_AV.items():
        current  = prices.get(symbol)
        open_px  = _prices_daily.get(symbol)
        if current is None:
            continue
        if open_px and open_px > 0:
            chg = ((current - open_px) / open_px) * 100
            icon = "🟢" if chg > 0 else "🔴"
            lines.append(f"{icon} <b>{name}</b>: ${current:.2f} ({chg:+.2f}% / 24h)")
        else:
            lines.append(f"⚪ <b>{name}</b>: ${current:.2f}")

    if lines:
        send_telegram(fmt("📊", "PODSUMOWANIE DNIA (24h)", lines))
        print(f"[{datetime.now():%H:%M:%S}] Wysłano podsumowanie dzienne")

    # Resetuj ceny otwarcia na następny dzień
    _prices_daily.update(prices)


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
        "Surowce (AV): złoto, srebro, ropa\n"
        "Podsumowanie poranne: 9:00 PL\n"
        "Podsumowanie dzienne: 17:00 PL\n"
        "Sesja NYSE: wolumen SPY + VIX co 15 min"
    )

    # Pobierz ceny otwarcia dnia — tylko raz przy starcie
    print("Pobieranie cen otwarcia...")
    opening_prices = _fetch_all_commodities()
    _prices_daily.update(opening_prices)
    _prices_hourly.update(opening_prices)
    print(f"Ceny otwarcia: {opening_prices}")

    last_session      = time.time()
    daily_sent_today  = False
    morning_sent_today = False
    last_day          = datetime.now(TIMEZONE_PL).day

    SESSION_SECS = CHECK_INTERVAL_MINUTES * 60

    while True:
        now    = time.time()
        now_pl = datetime.now(TIMEZONE_PL)

        # Reset flag o północy (nowy dzień)
        if now_pl.day != last_day:
            daily_sent_today   = False
            morning_sent_today = False
            last_day = now_pl.day
            print(f"[{now_pl:%H:%M:%S}] Nowy dzień — reset flag")

        # Podsumowanie poranne o 9:00 PL
        if now_pl.hour == 9 and now_pl.minute < 2 and not morning_sent_today:
            hourly_commodity_summary()
            morning_sent_today = True

        # Podsumowanie dzienne o 17:00 PL
        if now_pl.hour == 17 and now_pl.minute < 2 and not daily_sent_today:
            daily_commodity_summary()
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
