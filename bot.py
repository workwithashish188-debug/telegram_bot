#!/usr/bin/env python
# coding: utf-8

# # 📊 Crypto Signal Bot — ETHUSDT
# 
# Automated trading signal bot using **CCI (60)**, **EMA 7**, and **RSI (14)** on 30-minute candles from Delta Exchange.  
# Sends Telegram alerts on signal changes and logs them to `signals.csv`.
# 
# ---
# **Strategy Logic:**
# - 🟢 **Long Entry** → CCI > CCI_EMA, |Diff_CCI| > 4, Close > EMA7
# - 🔴 **Short Entry** → CCI < CCI_EMA, |Diff_CCI| > 4, Close < EMA7
# - ⚪ **No Trade** → Conditions not met
# 
# Scheduler fires at **HH:00:05** and **HH:30:05** IST every day.

# ## 1. Install Dependencies

# In[1]:


# Run once to install required packages
#get_ipython().system('pip install apscheduler pytz requests pandas numpy --quiet')


# ## 2. Configuration & Environment Setup

# In[2]:


import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import pytz

# ── Set your credentials here (or use environment variables) ────
# os.environ["BOT_TOKEN"]  = "your_telegram_bot_token"
# os.environ["CHAT_IDS"]   = "chat_id_1,chat_id_2"
# os.environ["SYMBOL"]     = "ETHUSDT"  # optional, default is ETHUSDT

BOT_TOKEN = "8749089704:AAFq_Xh6_oYk61V4mv8eNVdcX3Yh27AJuuY"

CHAT_IDS = [
    "1070509960",
    "1937479700",
    "5034473353",
    "2037873693"
]


SYMBOL     ="ETHUSDT"
IST        = pytz.timezone("Asia/Kolkata")

last_signal = None  # in-memory; resets on restart

print(f"[CONFIG] Symbol: {SYMBOL}")
print(f"[CONFIG] Chat IDs loaded: {len(CHAT_IDS)}")


# ## 3. Telegram Messenger

# In[3]:


def send_message(text):
    """Send a message to all configured Telegram chat IDs."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chat_id in CHAT_IDS:
        try:
            r = requests.post(
                url,
                data={"chat_id": chat_id.strip(), "text": text},
                timeout=10
            )
            r.raise_for_status()
            print(f"[TELEGRAM] Message sent to {chat_id.strip()}")
        except Exception as e:
            print(f"[ERROR] Telegram failed for {chat_id}: {e}")


# ## 4. Technical Indicators

# In[4]:


def calculate_rsi(series, length=14):
    """
    Wilder's RSI using EWM (alpha = 1/length).
    Returns a Series of RSI values (0–100).
    """
    delta    = series.diff()
    gain     = pd.Series(np.where(delta > 0, delta, 0), index=series.index)
    loss     = pd.Series(np.where(delta < 0, -delta, 0), index=series.index)
    avg_gain = gain.ewm(alpha=1/length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/length, adjust=False).mean()
    rs       = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ## 5. Fetch Candle Data from Delta Exchange

# In[5]:


def fetch_candles(symbol=SYMBOL, resolution="30m", lookback_candles=200):
    """Fetch OHLCV candles from Delta Exchange API."""
    end   = int(time.time())
    start = end - lookback_candles * 1800

    resp = requests.get(
        "https://api.delta.exchange/v2/history/candles",
        params={"symbol": symbol, "resolution": resolution, "start": start, "end": end},
        timeout=15
    )
    resp.raise_for_status()
    data = resp.json()

    if "result" not in data or not data["result"]:
        raise ValueError("No candle data returned from API")

    df = pd.DataFrame(data["result"])
    df.rename(columns={
        "time": "Open_time", "open": "Open", "high": "High",
        "low": "Low", "close": "Close", "volume": "Volume"
    }, inplace=True)

    df["Open_time"] = (
        pd.to_datetime(df["Open_time"], unit='s')
        .dt.tz_localize("UTC")
        .dt.tz_convert("Asia/Kolkata")
        .dt.tz_localize(None)
    )
    df = df.sort_values("Open_time").reset_index(drop=True)
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = df[col].astype(float)

    print(f"[FETCH] {len(df)} candles loaded. Latest: {df['Open_time'].iloc[-1]}")
    return df

# Test fetch
# df = fetch_candles()
# df.tail(3)


# ## 6. Compute Indicators & Generate Signal

# In[6]:


def compute_signals(df):
    """Compute CCI(60), EMA7, RSI(14) and derive trading signals."""
    # CCI (60)
    df["hlc3"]     = (df["High"] + df["Low"] + df["Close"]) / 3
    df["ma"]       = df["hlc3"].rolling(60).mean()
    df["mean_dev"] = df["hlc3"].rolling(60).apply(
        lambda x: np.mean(np.abs(x - np.mean(x))), raw=True
    )
    df["CCI_60"]   = (df["hlc3"] - df["ma"]) / (0.015 * df["mean_dev"])
    df["CCI_EMA"]  = df["CCI_60"].ewm(span=7, adjust=False).mean()
    df["Diff_CCI"] = df["CCI_60"] - df["CCI_EMA"]

    # EMA 7 and RSI 14
    df["EMA7"] = df["Close"].ewm(span=7, adjust=False).mean()
    df["RSI"]  = calculate_rsi(df["Close"])

    # Signal conditions
    long_cond  = (df["CCI_60"] > df["CCI_EMA"]) & (abs(df["Diff_CCI"]) > 4) & (df["Close"] > df["EMA7"])
    short_cond = (df["CCI_60"] < df["CCI_EMA"]) & (abs(df["Diff_CCI"]) > 4) & (df["Close"] < df["EMA7"])
    df["Signal"] = np.where(long_cond, "Long Entry", np.where(short_cond, "Short Entry", "No Trade"))

    return df

# Quick test (offline — uses random data)
# df = compute_signals(df)
# df[["Open_time","Close","CCI_60","EMA7","RSI","Signal"]].tail(5)


# ## 7. Main Signal-Check Job

# In[7]:


def run_signal_check():
    """Fetch data, compute signals, and send Telegram alert on signal change."""
    global last_signal
    print(f"[INFO] Job triggered at: {datetime.now(IST)}")

    try:
        df = fetch_candles()
    except Exception as e:
        print(f"[ERROR] API fetch failed: {e}")
        return

    df    = compute_signals(df)
    row   = df.iloc[-1]

    open_time = row["Open_time"].strftime("%Y-%m-%d %H:%M")
    close     = row["Close"]
    signal    = row["Signal"]
    rsi       = round(row["RSI"], 2)

    print(f"[INFO] Signal: {signal} | Close: {close} | RSI: {rsi}")

    if signal != last_signal:
        emoji = "🟢" if signal == "Long Entry" else "🔴" if signal == "Short Entry" else "⚪"
        msg = (
            f"{emoji} *{SYMBOL} Signal Alert*\n"
            f"🕐 Time  : {open_time} IST\n"
            f"💰 Close : {close}\n"
            f"📊 Signal: {signal}\n"
            f"📈 RSI   : {rsi}"
        )
        send_message(msg)

        log = pd.DataFrame([{"Open_time": open_time, "Close": close, "Signal": signal, "RSI": rsi}])
        log.to_csv("signals.csv", mode='a', header=not os.path.exists("signals.csv"), index=False)
        print(f"[LOG] Signal saved to signals.csv")

        last_signal = signal
    else:
        print(f"[INFO] Signal unchanged ({signal}), no alert sent.")


# ## 8. Run Once (Manual Test)
# > Use this cell to test the bot without the scheduler.

# In[8]:


# Single test run — fetches live data, computes signal, sends Telegram message
run_signal_check()


# ## 9. Start Scheduler
# > ⚠️ This cell **blocks** the notebook kernel. Run it last, or deploy to a server/Railway instead.

# In[ ]:


from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = BlockingScheduler(timezone=IST)

scheduler.add_job(
    run_signal_check,
    trigger=CronTrigger(minute="0,30", second="5", timezone=IST),
    misfire_grace_time=60,
    max_instances=1
)

print(f"[INFO] Scheduler started for {SYMBOL}. Fires at :00:05 and :30:05 IST")
send_message(f"✅ Bot started for {SYMBOL} — running every 30 mins")

try:
    scheduler.start()
except (KeyboardInterrupt, SystemExit):
    print("[INFO] Scheduler stopped.")


# ## 10. View Signal Log

# In[ ]:


import os
if os.path.exists("signals.csv"):
    log_df = pd.read_csv("signals.csv")
    print(f"Total signals logged: {len(log_df)}")
    display(log_df.tail(10))
else:
    print("No signals logged yet. Run the bot to generate signals.")

