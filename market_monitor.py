#!/usr/bin/env python3
"""
========================================
  Market Anomaly Monitor + Telegram Bot
  Wersja: Railway Cloud
========================================
Konfiguracja przez zmienne środowiskowe Railway:
  TELEGRAM_TOKEN   — token bota Telegram
  TELEGRAM_CHAT_ID — twoje chat ID
"""

import yfinance as yf
import requests
import pandas as pd
import schedule
import time
import os
from datetime import datetime, time as dtime
import pytz


# ============================================================
#  KONFIGURACJA — przez zmienne środowiskowe (Railway)
# ============================================================

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

VOLUME_SPIKE_THRESHOLD = float(os.environ.get("VOLUME_SPIKE_THRESHOLD", "3.0"))
PRICE_MOVE_THRESHOLD   = float(os.environ.get("PRICE_MOVE_THRESHOLD", "2.0"))
CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "15"))

# ============================================================
#  GODZINY SESJI GIEŁDOWEJ (NYSE)
#  Czas polski (CET/CEST):
#    zimą:  15:30 – 22:00
#    latem: 15:30 – 22:00 (NYSE nie zmienia godzin)
#  Pre-market (opcjonalnie): 10:00 – 15:30
# ============================================================

TIMEZONE_PL     = pytz.timezone("Europe/Warsaw")
TIMEZONE_NYSE   = pytz.timezone("America/New_York")

# Godziny sesji NYSE w czasie nowojorskim
MARKET_OPEN     = dtime(9, 25)   # 5 min przed otwarciem
MARKET_CLOSE    = dtime(16, 5)   # 5 min po zamknięciu

# Dni tygodnia: 0=poniedziałek, 4=piątek
MARKET_DAYS     = {0, 1, 2, 3, 4}


def is_market_open() -> bool:
    """Zwraca True jeśli trwa sesja NYSE (pon–pt, 9:25–16:05 ET)."""
    now_ny  = datetime.now(TIMEZONE_NYSE)
    weekday = now_ny.weekday()
    current = now_ny.time()
    return weekday in MARKET_DAYS and MARKET_OPEN <= current <= MARKET_CLOSE


def seconds_until_market_open() -> int:
    """Zwraca ile sekund do otwarcia następnej sesji."""
    now_ny  = datetime.now(TIMEZONE_NYSE)
    weekday = now_ny.weekday()

    # Jeśli dziś dzień roboczy i przed otwarciem
    if weekday in MARKET_DAYS and now_ny.time() < MARKET_OPEN:
        target = now_ny.replace(
            hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute,
            second=0, microsecond=0
        )
        return int((target - now_ny).total_seconds())

    # Znajdź następny poniedziałek–piątek
    days_ahead = 1
    while True:
        next_day = (weekday + days_ahead) % 7
        if next_day in MARKET_DAYS:
            break
        days_ahead += 1

    from datetime import timedelta
    target = (now_ny + timedelta(days=days_ahead)).replace(
        hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute,
        second=0, microsecond=0
    )
    return int((target - now_ny).total_seconds())


def wait_for_market():
    """Usypia skrypt do otwarcia sesji i wysyła info na Telegram."""
    secs  = seconds_until_market_open()
    hours = secs // 3600
    mins  = (secs % 3600) // 60

    now_pl = datetime.now(TIMEZONE_PL).strftime("%H:%M")
    print(f"[{now_pl}] Sesja zamknięta — czekam {hours}h {mins}min do otwarcia NYSE.")

    send_telegram(
        f"😴 <b>Sesja zamknięta</b>\n"
        f"Monitor uśpiony na {hours}h {mins}min.\n"
        f"Wznowię o otwarciu NYSE (ok. 15:30 PL)."
    )
    time.sleep(secs)


# ============================================================
#  OBSERWOWANE INSTRUMENTY
# ============================================================

STOCKS_TO_WATCH = [
    "SPY", "QQQ", "GLD", "SLV", "USO",
    "XLE", "XLF", "AAPL", "NVDA", "BRK-B",
]

COMMODITIES = {
    "GC=F": "Złoto",
    "SI=F": "Srebro",
    "CL=F": "Ropa WTI",
}

OPTIONS_PROXY = ["UVXY", "SQQQ", "SPXU", "TQQQ", "NUGT"]

INSTITUTIONAL_ETFS = {
    "BRK-B": "Berkshire Hathaway (Buffett)",
    "ARKK":  "ARK Innovation (C. Wood)",
    "SPY":   "SPDR S&P 500",
    "IVV":   "iShares S&P 500 (Dalio proxy)",
    "GLD":   "SPDR Gold",
}


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


def format_alert(emoji: str, title: str, details: list) -> str:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [f"{emoji} <b>{title}</b>", f"🕐 {now}", ""]
    lines.extend(details)
    lines += ["", "⚠️ <i>Nie jest to rekomendacja inwestycyjna.</i>"]
    return "\n".join(lines)


# ============================================================
#  MODUŁ 1: ANOMALIE WOLUMENU
# ============================================================

def check_volume_anomalies():
    alerts = []
    for ticker in STOCKS_TO_WATCH:
        try:
            hist = yf.Ticker(ticker).history(period="20d", interval="1d")
            if hist.empty or len(hist) < 5:
                continue
            avg_vol    = hist["Volume"][:-1].mean()
            today_vol  = hist["Volume"].iloc[-1]
            today_px   = hist["Close"].iloc[-1]
            prev_px    = hist["Close"].iloc[-2]
            chg        = ((today_px - prev_px) / prev_px) * 100
            spike      = today_vol / avg_vol if avg_vol > 0 else 0
            if spike >= VOLUME_SPIKE_THRESHOLD:
                alerts.append(
                    f"📊 <b>{ticker}</b>\n"
                    f"   Wolumen: {today_vol:,.0f} ({spike:.1f}x śr.)\n"
                    f"   Cena: ${today_px:.2f} ({chg:+.2f}%)"
                )
        except Exception as e:
            print(f"Błąd {ticker}: {e}")

    if alerts:
        send_telegram(format_alert("🚨", "ANOMALIA WOLUMENU",
            ["Wykryto nietypowy wolumen:"] + alerts))
    print(f"[{datetime.now():%H:%M:%S}] Wolumen: {len(alerts)} alertów")


# ============================================================
#  MODUŁ 2: RUCHY NA SUROWCACH
# ============================================================

def check_commodities():
    alerts = []
    for symbol, name in COMMODITIES.items():
        try:
            # Dane 5-minutowe z ostatnich 2 dni — najdokładniejsze do ruchów śróddziennych
            hist = yf.Ticker(symbol).history(period="2d", interval="5m")

            if hist.empty or len(hist) < 12:
                continue

            current_px  = hist["Close"].iloc[-1]

            # Cena sprzed ~1 godziny (12 świeczek po 5 minut)
            hour_ago_px = hist["Close"].iloc[-12]

            # Cena sprzed ~4 godzin (48 świeczek) jako proxy "otwarcia dnia"
            open_px     = hist["Close"].iloc[-48] if len(hist) >= 48 else hist["Close"].iloc[0]

            chg_1h    = ((current_px - hour_ago_px) / hour_ago_px) * 100
            chg_today = ((current_px - open_px) / open_px) * 100

            # Alert jeśli ruch w ciągu godziny > 0.8% LUB ruch 4h > 1.5%
            if abs(chg_1h) >= 0.8 or abs(chg_today) >= 1.5:
                icon = "📈" if chg_1h > 0 else "📉"
                alerts.append(
                    f"{icon} <b>{name}</b>\n"
                    f"   Ostatnia godzina: {chg_1h:+.2f}%\n"
                    f"   Ostatnie 4h: {chg_today:+.2f}%\n"
                    f"   Cena: ${current_px:.2f}"
                )
        except Exception as e:
            print(f"Błąd {symbol}: {e}")
        time.sleep(3)  # pauza między zapytaniami — unika rate limit

    if alerts:
        send_telegram(format_alert("🏅", "RUCH NA SUROWCACH",
            ["Znaczący ruch cen:"] + alerts))
    else:
        print(f"[{datetime.now():%H:%M:%S}] Surowce: brak alertów (złoto: sprawdzono OK)")
    print(f"[{datetime.now():%H:%M:%S}] Surowce: {len(alerts)} alertów")


# ============================================================
#  MODUŁ 3: PROXY AKTYWNOŚCI OPCYJNEJ
# ============================================================

def check_options_activity():
    alerts = []
    for ticker in OPTIONS_PROXY:
        try:
            hist = yf.Ticker(ticker).history(period="10d", interval="1d")
            if hist.empty or len(hist) < 3:
                continue
            avg_vol   = hist["Volume"][:-1].mean()
            today_vol = hist["Volume"].iloc[-1]
            today_px  = hist["Close"].iloc[-1]
            prev_px   = hist["Close"].iloc[-2]
            chg       = ((today_px - prev_px) / prev_px) * 100
            spike     = today_vol / avg_vol if avg_vol > 0 else 0
            if spike >= 2.5 and abs(chg) >= 3.0:
                alerts.append(
                    f"⚡ <b>{ticker}</b>\n"
                    f"   Wolumen: {spike:.1f}x śr.\n"
                    f"   Ruch: {chg:+.2f}%"
                )
        except Exception as e:
            print(f"Błąd {ticker}: {e}")

    if alerts:
        send_telegram(format_alert("🔍", "AKTYWNOŚĆ OPCYJNA (PROXY)",
            ["Wysokie wolumeny ETF lewarowanych:"] + alerts))
    print(f"[{datetime.now():%H:%M:%S}] Opcje: {len(alerts)} alertów")


# ============================================================
#  MODUŁ 4: PRZEPŁYWY INSTYTUCJONALNE
# ============================================================

def check_institutional():
    alerts = []
    for symbol, name in INSTITUTIONAL_ETFS.items():
        try:
            hist = yf.Ticker(symbol).history(period="5d", interval="1d")
            if hist.empty or len(hist) < 2:
                continue
            avg_vol   = hist["Volume"][:-1].mean()
            today_vol = hist["Volume"].iloc[-1]
            today_px  = hist["Close"].iloc[-1]
            prev_px   = hist["Close"].iloc[-2]
            chg       = ((today_px - prev_px) / prev_px) * 100
            spike     = today_vol / avg_vol if avg_vol > 0 else 0
            if spike >= 2.0 and abs(chg) >= 1.5:
                alerts.append(
                    f"🏦 <b>{name}</b>\n"
                    f"   Wolumen: {spike:.1f}x śr.\n"
                    f"   Ruch ceny: {chg:+.2f}%"
                )
        except Exception as e:
            print(f"Błąd {symbol}: {e}")

    if alerts:
        send_telegram(format_alert("🏛️", "RUCHY INSTYTUCJONALNE",
            ["Podejrzane przepływy:"] + alerts))
    print(f"[{datetime.now():%H:%M:%S}] Instytucje: {len(alerts)} alertów")


# ============================================================
#  PODSUMOWANIE DZIENNE
# ============================================================

def daily_summary():
    lines = []
    for ticker, label in [("SPY","S&P 500"), ("GLD","Złoto"),
                           ("CL=F","Ropa"), ("UVXY","VIX (strach)")]:
        try:
            hist = yf.Ticker(ticker).history(period="2d", interval="1d")
            if len(hist) >= 2:
                px  = hist["Close"].iloc[-1]
                chg = ((hist["Close"].iloc[-1] - hist["Close"].iloc[-2])
                       / hist["Close"].iloc[-2]) * 100
                icon = "🟢" if chg > 0 else "🔴"
                lines.append(f"{icon} {label}: ${px:.2f} ({chg:+.2f}%)")
        except:
            pass
    if lines:
        send_telegram(format_alert("📋", "PODSUMOWANIE DNIA", lines))


# ============================================================
#  GŁÓWNA PĘTLA
# ============================================================

def hourly_commodity_summary():
    """Wysyła godzinne podsumowanie cen złota, srebra i ropy."""
    lines = []
    for symbol, name in COMMODITIES.items():
        try:
            hist = yf.Ticker(symbol).history(period="2d", interval="5m")
            if hist.empty or len(hist) < 12:
                continue
            current_px  = hist["Close"].iloc[-1]
            hour_ago_px = hist["Close"].iloc[-12]
            chg_1h      = ((current_px - hour_ago_px) / hour_ago_px) * 100
            icon = "🟢" if chg_1h > 0 else "🔴"
            lines.append(f"{icon} <b>{name}</b>: ${current_px:.2f} ({chg_1h:+.2f}% / 1h)")
            time.sleep(2)
        except Exception as e:
            print(f"Błąd {symbol}: {e}")
    if lines:
        send_telegram(format_alert("⏰", "PODSUMOWANIE GODZINNE", lines))
    """Sprawdzenia tylko podczas sesji giełdowej (akcje, ETF, opcje, instytucje)."""
    print(f"\n{'='*40}")
    print(f"Sprawdzanie sesji: {datetime.now():%d.%m.%Y %H:%M:%S}")
    print('='*40)
    check_volume_anomalies()
    check_options_activity()
    check_institutional()


def run_commodity_checks():
    """Surowce — działa 24h na dobę, pon–pt."""
    print(f"\n[{datetime.now():%H:%M:%S}] Sprawdzanie surowców (24h)...")
    check_commodities()


def main():
    print("="*40)
    print("  Market Monitor — Railway Cloud")
    print("="*40)
    print(f"TOKEN:   {'OK' if TELEGRAM_TOKEN else 'BRAK!'}")
    print(f"CHAT_ID: {'OK' if TELEGRAM_CHAT_ID else 'BRAK!'}")
    print(f"Interwał sesja: co {CHECK_INTERVAL_MINUTES} min")
    print(f"Interwał surowce: co 15 min (24h)\n")

    send_telegram(
        "🚀 <b>Market Monitor uruchomiony w chmurze!</b>\n"
        f"Surowce (złoto, srebro, ropa): monitoruję <b>24h</b>\n"
        f"Akcje/ETF/opcje: tylko podczas sesji NYSE\n"
        f"Interwał: co {CHECK_INTERVAL_MINUTES} min"
    )

    # Pierwsze sprawdzenie surowców od razu
    run_commodity_checks()

    last_commodity_check = time.time()
    last_hourly_summary  = time.time()
    COMMODITY_INTERVAL   = 15 * 60   # co 15 minut
    HOURLY_INTERVAL      = 60 * 60   # co godzinę

    # Pierwsze podsumowanie od razu
    hourly_commodity_summary()

    while True:
        now = time.time()

        # Podsumowanie godzinne — zawsze co godzinę
        if (now - last_hourly_summary) >= HOURLY_INTERVAL:
            hourly_commodity_summary()
            last_hourly_summary = time.time()

        # Surowce — alerty co 15 minut
        now_ny = datetime.now(TIMEZONE_NYSE)
        is_weekday = now_ny.weekday() in MARKET_DAYS
        if is_weekday and (now - last_commodity_check) >= COMMODITY_INTERVAL:
            run_commodity_checks()
            last_commodity_check = time.time()

        # Akcje/ETF — tylko podczas sesji NYSE
        if is_market_open():
            now_pl = datetime.now(TIMEZONE_PL).strftime("%H:%M")
            print(f"[{now_pl}] Sesja otwarta — sprawdzam akcje/ETF...")
            run_market_checks()
            # Czekaj interwał sprawdzając surowce po drodze
            for _ in range(CHECK_INTERVAL_MINUTES * 2):
                time.sleep(30)
                now = time.time()
                if is_weekday and (now - last_commodity_check) >= COMMODITY_INTERVAL:
                    run_commodity_checks()
                    last_commodity_check = time.time()
                if not is_market_open():
                    break
        else:
            # Sesja zamknięta — czekaj ale monitoruj surowce co 15 minut
            now_pl = datetime.now(TIMEZONE_PL).strftime("%H:%M")
            secs = seconds_until_market_open()
            hours = secs // 3600
            mins  = (secs % 3600) // 60
            print(f"[{now_pl}] Sesja zamknięta. Do otwarcia: {hours}h {mins}min")
            now = time.time()
            if (now - last_commodity_check) >= COMMODITY_INTERVAL:
                run_commodity_checks()
                last_commodity_check = time.time()
            time.sleep(60)


if __name__ == "__main__":
    main()
