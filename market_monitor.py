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
from datetime import datetime


# ============================================================
#  KONFIGURACJA — przez zmienne środowiskowe (Railway)
# ============================================================

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

VOLUME_SPIKE_THRESHOLD = float(os.environ.get("VOLUME_SPIKE_THRESHOLD", "3.0"))
PRICE_MOVE_THRESHOLD   = float(os.environ.get("PRICE_MOVE_THRESHOLD", "2.0"))
CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "15"))


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
    "NG=F": "Gaz ziemny",
    "HG=F": "Miedź",
    "PL=F": "Platyna",
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
            hist = yf.Ticker(symbol).history(period="5d", interval="1d")
            if hist.empty or len(hist) < 2:
                continue
            today     = hist["Close"].iloc[-1]
            yesterday = hist["Close"].iloc[-2]
            chg       = ((today - yesterday) / yesterday) * 100
            week_chg  = ((today - hist["Close"].iloc[0]) / hist["Close"].iloc[0]) * 100
            if abs(chg) >= PRICE_MOVE_THRESHOLD:
                icon = "📈" if chg > 0 else "📉"
                alerts.append(
                    f"{icon} <b>{name}</b>\n"
                    f"   Dziś: {chg:+.2f}% | Tydz.: {week_chg:+.2f}%\n"
                    f"   Cena: ${today:.2f}"
                )
        except Exception as e:
            print(f"Błąd {symbol}: {e}")

    if alerts:
        send_telegram(format_alert("🏅", "RUCH NA SUROWCACH",
            ["Znaczący ruch cen:"] + alerts))
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

def run_all_checks():
    print(f"\n{'='*40}")
    print(f"Sprawdzanie: {datetime.now():%d.%m.%Y %H:%M:%S}")
    print('='*40)
    check_volume_anomalies()
    check_commodities()
    check_options_activity()
    check_institutional()


def main():
    print("="*40)
    print("  Market Monitor — Railway Cloud")
    print("="*40)
    print(f"TOKEN: {'OK' if TELEGRAM_TOKEN else 'BRAK!'}")
    print(f"CHAT_ID: {'OK' if TELEGRAM_CHAT_ID else 'BRAK!'}")
    print(f"Interwał: co {CHECK_INTERVAL_MINUTES} min\n")

    send_telegram(
        "🚀 <b>Market Monitor uruchomiony w chmurze!</b>\n"
        f"Monitoruję: wolumen, surowce, opcje, instytucje\n"
        f"Interwał: co {CHECK_INTERVAL_MINUTES} min"
    )

    run_all_checks()

    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(run_all_checks)
    schedule.every().day.at("18:00").do(daily_summary)

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
