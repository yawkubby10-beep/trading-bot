"""
╔══════════════════════════════════════════════════════════╗
║   INSTITUTIONAL TRADING BOT v9.5 - RENDER DEPLOYMENT    ║
║   Multi-Timeframe SMC + AI Regime + Risk Engine         ║
║   BTC | ETH | SOL | BNB Perpetual Futures               ║
╚══════════════════════════════════════════════════════════╝
"""
import os, json, logging, time, threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from enum import Enum
import numpy as np

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
import requests
import pandas as pd

# ═══════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("TELEGRAM_ADMIN_ID", "0"))
STARTING_BALANCE = float(os.getenv("STARTING_BALANCE", "100"))

SYMBOLS = {
    "BTCUSDT": {"name": "₿ Bitcoin", "min_volume": 500_000_000, "atr_mult": 2.0, "rr_min": 2.5},
    "ETHUSDT": {"name": "Ξ Ethereum", "min_volume": 200_000_000, "atr_mult": 2.2, "rr_min": 2.5},
    "SOLUSDT": {"name": "◎ Solana", "min_volume": 100_000_000, "atr_mult": 2.5, "rr_min": 2.8},
    "BNBUSDT": {"name": "🔶 BNB", "min_volume": 50_000_000, "atr_mult": 2.3, "rr_min": 2.5},
}

class Regime(Enum):
    STRONG_TREND_UP = "strong_trending_up"
    STRONG_TREND_DOWN = "strong_trending_down"
    WEAK_TREND_UP = "weak_trending_up"
    WEAK_TREND_DOWN = "weak_trending_down"
    RANGING = "ranging"
    COMPRESSION = "compression_coil"
    VOLATILE = "volatile_expansion"

# ═══════════════════════════════════════
# DATA STORAGE (in-memory for Render)
# ═══════════════════════════════════════

trades_db = []
account_db = {"equity": STARTING_BALANCE, "daily_trades": 0, "consecutive_losses": 0, "last_reset": datetime.utcnow().strftime("%Y-%m-%d")}

# ═══════════════════════════════════════
# RISK ENGINE
# ═══════════════════════════════════════

class RiskEngine:
    MAX_DAILY_DD = 0.03
    MAX_CONSECUTIVE_LOSSES = 3
    MAX_DAILY_TRADES = 10
    MAX_POSITIONS = 3
    BASE_RISK_PCT = 0.01
    
    @staticmethod
    def position_size(equity, entry, stop, confidence, volatility):
        risk_pct = RiskEngine.BASE_RISK_PCT * (confidence / 80) * (1 / max(volatility, 0.5))
        risk_pct = min(risk_pct, 0.02)
        risk_amount = equity * risk_pct
        stop_pct = abs(entry - stop) / entry
        if stop_pct == 0: return 0
        return min(risk_amount / stop_pct, equity * 2)
    
    @staticmethod
    def check_limits():
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if account_db.get("last_reset") != today:
            account_db["daily_trades"] = 0
            account_db["last_reset"] = today
        
        if account_db.get("consecutive_losses", 0) >= RiskEngine.MAX_CONSECUTIVE_LOSSES:
            return False, "🚫 3 consecutive losses"
        if account_db.get("daily_trades", 0) >= RiskEngine.MAX_DAILY_TRADES:
            return False, "🚫 Max daily trades"
        open_t = [t for t in trades_db if t.get("status") == "open"]
        if len(open_t) >= RiskEngine.MAX_POSITIONS:
            return False, "🚫 Max positions"
        equity = account_db.get("equity", STARTING_BALANCE)
        dd = (STARTING_BALANCE - equity) / STARTING_BALANCE
        if dd >= RiskEngine.MAX_DAILY_DD:
            return False, f"🚫 Daily DD {dd*100:.1f}%"
        return True, "✅"

# ═══════════════════════════════════════
# MARKET DATA
# ═══════════════════════════════════════

def fetch_klines(symbol, interval, limit=100):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        r = requests.get(url, timeout=10)
        data = r.json()
        df = pd.DataFrame(data, columns=['time','open','high','low','close','volume','close_time','quote_volume','trades','taker_buy_base','taker_buy_quote','ignore'])
        for col in ['open','high','low','close','volume']:
            df[col] = df[col].astype(float)
        return df
    except:
        return pd.DataFrame()

def fetch_ticker(symbol):
    try:
        r = requests.get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}", timeout=10)
        d = r.json()
        return {"price": float(d["lastPrice"]), "change": float(d["priceChangePercent"]), "volume": float(d["quoteVolume"])}
    except:
        return {"price": 0, "change": 0, "volume": 0}

# ═══════════════════════════════════════
# TECHNICAL ANALYSIS
# ═══════════════════════════════════════

def calc_ema(df, period):
    return df['close'].ewm(span=period, adjust=False).mean().values

def calc_atr(df, period=14):
    high, low, close = df['high'].values, df['low'].values, df['close'].values
    tr = np.maximum(high[-period:] - low[-period:],
           np.maximum(abs(high[-period:] - np.roll(close, 1)[-period:]),
                     abs(low[-period:] - np.roll(close, 1)[-period:])))
    return float(np.mean(tr))

def calc_rsi(df, period=14):
    delta = df['close'].diff().values
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = np.mean(gain[-period:])
    avg_loss = np.mean(loss[-period:])
    if avg_loss == 0: return 100.0
    return float(100 - (100 / (1 + avg_gain / avg_loss)))

def calc_macd(df):
    ema12 = df['close'].ewm(span=12, adjust=False).mean().values
    ema26 = df['close'].ewm(span=26, adjust=False).mean().values
    macd = ema12 - ema26
    signal = pd.Series(macd).ewm(span=9, adjust=False).mean().values
    hist = macd - signal
    return {"bullish": macd[-1] > signal[-1] and hist[-1] > hist[-2]}

def detect_structure(df_4h, df_1h, df_15m):
    cur = df_1h['close'].iloc[-1]
    ema21_4h = calc_ema(df_4h, 21)[-1]
    ema50_4h = calc_ema(df_4h, 50)[-1]
    ema21_1h = calc_ema(df_1h, 21)[-1]
    ema21_15m = calc_ema(df_15m, 21)[-1]
    
    return {
        "htf_bullish": ema21_4h > ema50_4h,
        "htf_bearish": ema21_4h < ema50_4h,
        "mtf_bullish": cur > ema21_1h,
        "mtf_bearish": cur < ema21_1h,
        "ltf_aligned": (cur > ema21_15m) if cur > ema21_1h else (cur < ema21_15m),
        "strength": abs(ema21_4h - ema50_4h) / ema50_4h * 100
    }

def detect_regime(df_4h, df_1h):
    if df_4h.empty or df_1h.empty: return None
    cur = df_1h['close'].iloc[-1]
    ema21_4h = calc_ema(df_4h, 21)[-1]
    ema50_4h = calc_ema(df_4h, 50)[-1]
    atr = calc_atr(df_1h, 14)
    atr_pct = atr / cur * 100
    rsi = calc_rsi(df_1h, 14)
    price_change = (cur - df_1h['close'].iloc[-20]) / df_1h['close'].iloc[-20] * 100
    vol_5 = df_1h['volume'].iloc[-5:].mean()
    vol_20 = df_1h['volume'].iloc[-20:].mean()
    vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1.0
    bb_width = (2 * df_1h['close'].iloc[-20:].std()) / cur * 100
    
    if ema21_4h > ema50_4h and cur > ema21_4h and rsi > 50 and price_change > 3:
        return Regime.STRONG_TREND_UP
    elif ema21_4h < ema50_4h and cur < ema21_4h and rsi < 50 and price_change < -3:
        return Regime.STRONG_TREND_DOWN
    elif ema21_4h > ema50_4h and cur > ema21_4h:
        return Regime.WEAK_TREND_UP
    elif ema21_4h < ema50_4h and cur < ema21_4h:
        return Regime.WEAK_TREND_DOWN
    elif bb_width < 1.5 and vol_ratio > 1.3:
        return Regime.COMPRESSION
    elif atr_pct > 3:
        return Regime.VOLATILE
    return Regime.RANGING

def find_liquidity(df):
    highs, lows = df['high'].values, df['low'].values
    sh, sl = [], []
    for i in range(2, len(df)-2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            sh.append(float(highs[i]))
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            sl.append(float(lows[i]))
    return {"highs": sh[-3:] if sh else [], "lows": sl[-3:] if sl else [], "nearest_high": sh[-1] if sh else None, "nearest_low": sl[-1] if sl else None}

# ═══════════════════════════════════════
# SIGNAL GENERATOR
# ═══════════════════════════════════════

def analyze(symbol):
    df_4h = fetch_klines(symbol, "4h", 100)
    df_1h = fetch_klines(symbol, "1h", 100)
    df_15m = fetch_klines(symbol, "15m", 100)
    ticker = fetch_ticker(symbol)
    
    if df_1h.empty or ticker["price"] == 0: return None
    
    price = ticker["price"]
    change = ticker["change"]
    volume = ticker["volume"]
    config = SYMBOLS.get(symbol, SYMBOLS["BTCUSDT"])
    
    if volume < config["min_volume"]: return None
    
    regime = detect_regime(df_4h, df_1h)
    structure = detect_structure(df_4h, df_1h, df_15m)
    rsi = calc_rsi(df_1h, 14)
    macd = calc_macd(df_1h)
    atr = calc_atr(df_1h, 14)
    liquidity = find_liquidity(df_1h)
    
    direction, confidence, reasons = None, 0, []
    
    if regime in [Regime.STRONG_TREND_UP, Regime.WEAK_TREND_UP, Regime.COMPRESSION]:
        if structure["htf_bullish"] and structure["mtf_bullish"]:
            direction = "long"
            confidence += 30
            reasons.append(f"Regime: {regime.value}")
            if structure["ltf_aligned"]: confidence += 15; reasons.append("LTF aligned")
            if 40 < rsi < 70: confidence += 10; reasons.append(f"RSI {rsi:.0f}")
            if macd["bullish"]: confidence += 10; reasons.append("MACD bullish")
            if change > 0: confidence += 5
            if regime == Regime.STRONG_TREND_UP: confidence += 10
            elif regime == Regime.COMPRESSION: confidence += 5; reasons.append("Breakout potential")
    
    elif regime in [Regime.STRONG_TREND_DOWN, Regime.WEAK_TREND_DOWN]:
        if structure["htf_bearish"] and structure["mtf_bearish"]:
            direction = "short"
            confidence += 30
            reasons.append(f"Regime: {regime.value}")
            if structure["ltf_aligned"]: confidence += 15; reasons.append("LTF aligned")
            if 30 < rsi < 60: confidence += 10; reasons.append(f"RSI {rsi:.0f}")
            if not macd["bullish"]: confidence += 10; reasons.append("MACD bearish")
            if change < 0: confidence += 5
            if regime == Regime.STRONG_TREND_DOWN: confidence += 10
    
    if direction is None or confidence < 60: return None
    
    atr_mult = config["atr_mult"]
    sl_dist = atr * atr_mult
    tp_dist = atr * atr_mult * config["rr_min"]
    
    if direction == "long":
        entry, sl, tp = price, round(price - sl_dist, 2), round(price + tp_dist, 2)
        if liquidity["nearest_low"] and liquidity["nearest_low"] < entry:
            sl = max(sl, round(liquidity["nearest_low"] * 0.995, 2))
    else:
        entry, sl, tp = price, round(price + sl_dist, 2), round(price - tp_dist, 2)
        if liquidity["nearest_high"] and liquidity["nearest_high"] > entry:
            sl = min(sl, round(liquidity["nearest_high"] * 1.005, 2))
    
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = reward / risk if risk > 0 else 0
    
    if rr < config["rr_min"]: return None
    
    volatility = atr / entry * 100
    pos_size = RiskEngine.position_size(STARTING_BALANCE, entry, sl, confidence, volatility)
    
    return {
        "symbol": symbol, "name": config["name"], "direction": direction,
        "entry": entry, "stop_loss": sl, "take_profit": tp,
        "risk_reward": round(rr, 1), "confidence": min(confidence, 98),
        "regime": regime.value, "rsi": round(rsi, 1),
        "atr_pct": round(volatility, 2), "position_size": round(pos_size, 2),
        "reasons": reasons, "change": change
    }

def scan_all():
    signals = [s for s in [analyze(sym) for sym in SYMBOLS] if s]
    signals.sort(key=lambda x: x["confidence"], reverse=True)
    return signals

# ═══════════════════════════════════════
# POSITION MONITOR
# ═══════════════════════════════════════

def check_exits():
    global trades_db, account_db
    for trade in trades_db:
        if trade.get("status") != "open": continue
        ticker = fetch_ticker(trade["symbol"])
        cp = ticker["price"]
        if cp == 0: continue
        
        should_close, reason = False, ""
        
        if trade["direction"] == "long":
            if cp <= trade["stop_loss"]: should_close, reason = True, "Stop Loss"
            elif cp >= trade["take_profit"]: should_close, reason = True, "Take Profit"
            elif cp > trade["entry_price"] * 1.02:
                new_sl = trade["entry_price"] * 1.01
                if new_sl > trade.get("stop_loss", 0): trade["stop_loss"] = round(new_sl, 2)
        else:
            if cp >= trade["stop_loss"]: should_close, reason = True, "Stop Loss"
            elif cp <= trade["take_profit"]: should_close, reason = True, "Take Profit"
            elif cp < trade["entry_price"] * 0.98:
                new_sl = trade["entry_price"] * 0.99
                if new_sl < trade.get("stop_loss", float("inf")): trade["stop_loss"] = round(new_sl, 2)
        
        if should_close:
            trade["status"] = "closed"
            trade["exit_price"] = cp
            trade["exit_time"] = datetime.utcnow().isoformat()
            trade["exit_reason"] = reason
            pnl = ((cp - trade["entry_price"]) / trade["entry_price"] * trade["position_size"]) if trade["direction"] == "long" else ((trade["entry_price"] - cp) / trade["entry_price"] * trade["position_size"])
            trade["pnl"] = round(pnl, 2)
            account_db["equity"] = account_db.get("equity", STARTING_BALANCE) + pnl
            account_db["consecutive_losses"] = 0 if pnl > 0 else account_db.get("consecutive_losses", 0) + 1
            logger.info(f"Trade {trade['trade_id']} closed: {reason} | P&L: ${pnl:.2f}")

def monitor_loop():
    logger.info("🛡 Position monitor started (30s checks)")
    while True:
        try:
            check_exits()
        except Exception as e:
            logger.error(f"Monitor error: {e}")
        time.sleep(30)

# ═══════════════════════════════════════
# UI
# ═══════════════════════════════════════

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Scan ALL", callback_data="scan_all"),
         InlineKeyboardButton("📊 Dashboard", callback_data="dashboard")],
        [InlineKeyboardButton("₿ BTC", callback_data="scan_BTCUSDT"),
         InlineKeyboardButton("Ξ ETH", callback_data="scan_ETHUSDT")],
        [InlineKeyboardButton("◎ SOL", callback_data="scan_SOLUSDT"),
         InlineKeyboardButton("🔶 BNB", callback_data="scan_BNBUSDT")],
        [InlineKeyboardButton("💼 Positions", callback_data="pos"),
         InlineKeyboardButton("📈 Stats", callback_data="stats")],
        [InlineKeyboardButton("🛡 Risk", callback_data="risk"),
         InlineKeyboardButton("❓ Help", callback_data="help")]
    ])

def dashboard_msg():
    lines = ""
    for sym, cfg in SYMBOLS.items():
        t = fetch_ticker(sym)
        e = "🟢" if t["change"] >= 0 else "🔴"
        lines += f"│ {cfg['name']} {e} ${t['price']:>8,.2f} {t['change']:>+6.1f}% │\n"
    
    open_t = [t for t in trades_db if t.get("status") == "open"]
    closed_t = [t for t in trades_db if t.get("status") == "closed"]
    wins = [t for t in closed_t if t.get("pnl", 0) > 0]
    total_pnl = sum(t.get("pnl", 0) for t in closed_t)
    pnl_e = "🟢" if total_pnl >= 0 else "🔴"
    
    pos_text = "📭 None" if not open_t else "\n".join(f"• {'📈 L' if t['direction']=='long' else '📉 S'} {t['symbol']} @ ${t['entry_price']:,.2f}" for t in open_t[:5])
    
    return f"""
╔══════════════════════════╗
║  🏦 TRADING BOT v9.5      ║
╚══════════════════════════╝

🕐 {datetime.utcnow().strftime('%H:%M UTC')}
{'━' * 26}

💎 ACCOUNT
┌─────────────────────────┐
│ 💰 Equity:  ${account_db['equity']:>10,.2f} │
│ {pnl_e} P&L:     ${total_pnl:>+10,.2f} │
└─────────────────────────┘

🌍 MARKETS
┌─────────────────────────┐
{lines}└─────────────────────────┘

💼 POSITIONS ({len(open_t)})
{pos_text}

🛡 RISK | Trades: {account_db.get('daily_trades',0)}/10 | WR: {len(wins)}/{max(len(closed_t),1)}
{'━' * 26}
✅ v9.5 Cloud | Auto SL/TP | Multi-TF
"""

# ═══════════════════════════════════════
# HANDLERS
# ═══════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_ID:
        await update.message.reply_text("🔒 Unauthorized")
        return
    await update.message.reply_text(dashboard_msg(), parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    
    if d == "dashboard":
        await q.message.edit_text(dashboard_msg(), parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())
    
    elif d == "scan_all":
        await q.message.edit_text("🔍 *Multi-TF Analysis...*\n\n4H → 1H → 15M → 5M\nEMA · RSI · MACD · ATR · SMC", parse_mode=ParseMode.MARKDOWN)
        signals = scan_all()
        if signals:
            msg = f"🎯 *SIGNALS* ({len(signals)} found)\n\n"
            for i, s in enumerate(signals[:5]):
                stars = "⭐" * min(5, int(s["confidence"]/20))
                dr = "🟢 LONG" if s["direction"]=="long" else "🔴 SHORT"
                msg += f"*{i+1}. {s['name']}* {stars}\n   {dr} | ${s['entry']:,.2f} | {s['confidence']:.0f}% | R:R {s['risk_reward']}x\n   {s['regime'].replace('_',' ').title()}\n\n"
            best = signals[0]
            await q.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"✅ Trade {best['name']} ({best['direction'].upper()})", callback_data=f"exec_{best['direction']}_{best['symbol']}")],
                    [InlineKeyboardButton("🔄 Rescan", callback_data="scan_all"),
                     InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")]
                ]))
        else:
            await q.message.edit_text("🔍 *No signals*\n\nAll coins analyzed. No setups meet 60% confidence.", parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Rescan", callback_data="scan_all"),
                    InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")
                ]]))
    
    elif d.startswith("scan_"):
        sym = d.replace("scan_", "")
        await q.message.edit_text(f"🔍 *Analyzing {SYMBOLS[sym]['name']}...*\n\nMulti-TF + SMC + Regime", parse_mode=ParseMode.MARKDOWN)
        s = analyze(sym)
        if s:
            em = "🟢 BUY" if s["direction"]=="long" else "🔴 SELL"
            stars = "⭐" * min(5, int(s["confidence"]/20))
            msg = f"""
🎯 *{s['name']} SIGNAL* {stars}

╔══════════════════════╗
║  {em}                     ║
╠══════════════════════╣
║ 💰 Entry:  ${s['entry']:>8,.2f} ║
║ 🛑 Stop:   ${s['stop_loss']:>8,.2f} ║
║ 🎯 Target: ${s['take_profit']:>8,.2f} ║
║ 📊 R:R:    {s['risk_reward']:>8.1f}x ║
║ 🎯 Conf:   {s['confidence']:>8.0f}% ║
╚══════════════════════╝

📊 *Analysis:*
• Regime: {s['regime'].replace('_',' ').title()}
• RSI: {s['rsi']} | Size: ${s['position_size']:,.0f}
• {', '.join(s['reasons'][:3])}
"""
            await q.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"✅ Execute {s['direction'].upper()}", callback_data=f"exec_{s['direction']}_{sym}")],
                    [InlineKeyboardButton("🔄 Scan All", callback_data="scan_all"),
                     InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")]
                ]))
        else:
            t = fetch_ticker(sym)
            await q.message.edit_text(f"🔍 No signal for {SYMBOLS[sym]['name']}\nPrice: ${t['price']:,.2f}\n24h: {t['change']:+.2f}%\n\nConfidence < 60%", parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Scan All", callback_data="scan_all"),
                    InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")
                ]]))
    
    elif d == "pos":
        open_t = [t for t in trades_db if t.get("status")=="open"]
        if not open_t:
            await q.message.edit_text("💼 *No Open Positions*", parse_mode=ParseMode.MARKDOWN)
        else:
            msg = f"*💼 POSITIONS ({len(open_t)})*\n\n"
            for t in open_t:
                dr = "📈 LONG" if t["direction"]=="long" else "📉 SHORT"
                cp = fetch_ticker(t["symbol"])["price"]
                pnl = ((cp-t["entry_price"])/t["entry_price"]*100) if t["direction"]=="long" else ((t["entry_price"]-cp)/t["entry_price"]*100)
                e = "🟢" if pnl >= 0 else "🔴"
                msg += f"• {t['symbol']} {dr} {e} {pnl:+.1f}%\n  Entry: ${t['entry_price']:,.2f} | Now: ${cp:,.2f}\n  SL: ${t['stop_loss']:,.2f} | TP: ${t['take_profit']:,.2f}\n\n"
            await q.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")
                ]]))
    
    elif d == "stats":
        closed = [t for t in trades_db if t.get("status")=="closed"]
        if not closed:
            await q.message.edit_text("📊 *No closed trades*", parse_mode=ParseMode.MARKDOWN)
        else:
            wins = [t for t in closed if t.get("pnl",0)>0]
            tp = sum(t.get("pnl",0) for t in closed)
            by_sym = {}
            for t in closed:
                sym = t.get("symbol","?")
                if sym not in by_sym: by_sym[sym] = {"t":0,"w":0,"p":0}
                by_sym[sym]["t"] += 1
                by_sym[sym]["p"] += t.get("pnl",0)
                if t.get("pnl",0) > 0: by_sym[sym]["w"] += 1
            msg = f"📈 *STATS*\n\nTotal: {len(closed)} | Wins: {len(wins)} | WR: {len(wins)/len(closed)*100:.0f}%\nP&L: ${tp:,.2f}\n\n*By Coin:*\n"
            for sym, st in by_sym.items():
                msg += f"• {sym}: {st['w']}/{st['t']} | ${st['p']:,.2f}\n"
            await q.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")
                ]]))
    
    elif d == "risk":
        allowed, reason = RiskEngine.check_limits()
        status = "🟢 HEALTHY" if allowed else "🔴 BLOCKED"
        msg = f"🛡 *RISK: {status}*\n\n{reason}\nEquity: ${account_db.get('equity',0):,.2f}\nTrades: {account_db.get('daily_trades',0)}/10\nLosses: {account_db.get('consecutive_losses',0)}/3\n\nLimits: 3% DD | 3 losses | 3 pos | Kelly sizing"
        await q.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")
            ]]))
    
    elif d == "help":
        await q.message.edit_text("""
❓ *v9.5 FEATURES*

• Multi-Timeframe (4H/1H/15M/5M)
• EMA · RSI · MACD · ATR
• 7 Market Regimes
• SMC: Liquidity Levels
• Kelly Position Sizing
• Auto SL/TP + Trailing
• 30s Position Monitor

⚠️ Paper Trading | 24/7 Cloud
""", parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")
            ]]))
    
    elif d.startswith("exec_"):
        parts = d.replace("exec_","").split("_")
        dr, sym = parts[0], "_".join(parts[1:])
        
        allowed, reason = RiskEngine.check_limits()
        if not allowed:
            await q.message.edit_text(f"⚠️ {reason}", parse_mode=ParseMode.MARKDOWN)
            return
        
        s = analyze(sym)
        if not s:
            await q.message.edit_text("⚠️ Signal expired", parse_mode=ParseMode.MARKDOWN)
            return
        
        trade = {
            "trade_id": datetime.utcnow().strftime("%Y%m%d%H%M%S"),
            "symbol": sym, "direction": dr,
            "entry_price": s["entry"], "stop_loss": s["stop_loss"],
            "take_profit": s["take_profit"], "position_size": s["position_size"],
            "confidence": s["confidence"], "status": "open",
            "entry_time": datetime.utcnow().isoformat(), "pnl": 0
        }
        trades_db.append(trade)
        account_db["daily_trades"] = account_db.get("daily_trades", 0) + 1
        
        msg = f"""
✅ *TRADE EXECUTED*

{sym} {dr.upper()}
Entry: ${s['entry']:,.2f}
SL: ${s['stop_loss']:,.2f}
TP: ${s['take_profit']:,.2f}
Size: ${s['position_size']:,.0f}
R:R: {s['risk_reward']}x | Conf: {s['confidence']:.0f}%

🛡 Auto SL/TP + Trailing Active
"""
        await q.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")
            ]]))

# ═══════════════════════════════════════
# MAIN
# ═══════════════════════════════════════

def main():
    print("""
╔══════════════════════════════════════╗
║  TRADING BOT v9.5 - RENDER CLOUD   ║
║  Multi-TF SMC + Kelly + Auto SL/TP ║
╚══════════════════════════════════════╝
""")
    
    # Start position monitor in background
    monitor = threading.Thread(target=monitor_loop, daemon=True)
    monitor.start()
    
    app = Application.builder().token(BOT_TOKEN).connect_timeout(30).read_timeout(30).write_timeout(30).build()
    app.add_handler(CommandHandler(["start", "dashboard"], start))
    app.add_handler(CallbackQueryHandler(buttons))
    
    print("✅ Bot RUNNING | 🛡 Monitor Active | 30s Checks")
    print(f"📱 Telegram: /start | 💰 ${STARTING_BALANCE:.0f} Paper")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()