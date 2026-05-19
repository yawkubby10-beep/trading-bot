"""
🏦 TRADING BOT - MULTI-COIN EDITION
Trades: BTC, ETH, SOL, BNB
"""
import os
import json
import logging
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler
from telegram.constants import ParseMode
import requests

# Setup
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("TELEGRAM_ADMIN_ID", "0"))
STARTING_BALANCE = float(os.getenv("STARTING_BALANCE", "100"))

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# All coins we trade
SYMBOLS = {
    "BTCUSDT": "₿ Bitcoin",
    "ETHUSDT": "Ξ Ethereum", 
    "SOLUSDT": "◎ Solana",
    "BNBUSDT": "🔶 BNB"
}

# Storage
def load_json(filename):
    path = DATA_DIR / filename
    return json.load(open(path)) if path.exists() else {}

def save_json(filename, data):
    json.dump(data, open(DATA_DIR / filename, "w"), indent=2, default=str)

# Market Data - Gets ALL coins
def get_all_markets():
    results = {}
    for symbol, name in SYMBOLS.items():
        try:
            url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
            response = requests.get(url, timeout=10)
            data = response.json()
            results[symbol] = {
                'name': name,
                'price': float(data['lastPrice']),
                'change_24h': float(data['priceChangePercent']),
                'high': float(data['highPrice']),
                'low': float(data['lowPrice']),
                'volume': float(data['quoteVolume'])
            }
        except Exception as e:
            logger.error(f"Error fetching {symbol}: {e}")
    return results

def get_market(symbol="BTCUSDT"):
    try:
        url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
        response = requests.get(url, timeout=10)
        data = response.json()
        return {
            'symbol': symbol,
            'name': SYMBOLS.get(symbol, symbol),
            'price': float(data['lastPrice']),
            'change_24h': float(data['priceChangePercent']),
            'high': float(data['highPrice']),
            'low': float(data['lowPrice']),
            'volume': float(data['quoteVolume'])
        }
    except:
        return None

# Signal Generator - Checks ALL coins
def scan_all_signals():
    markets = get_all_markets()
    all_signals = []
    
    for symbol, market in markets.items():
        price = market['price']
        change = market['change_24h']
        
        if change > 0.5:
            direction = 'long'
            confidence = min(50 + abs(change) * 3, 95)
            stop_loss = round(price * 0.97, 2)
            take_profit = round(price * 1.09, 2)
        elif change < -0.5:
            direction = 'short'
            confidence = min(50 + abs(change) * 3, 95)
            stop_loss = round(price * 1.03, 2)
            take_profit = round(price * 0.91, 2)
        else:
            continue
        
        if confidence < 50:
            continue
        
        risk = abs(price - stop_loss)
        reward = abs(take_profit - price)
        rr = reward / risk if risk > 0 else 0
        
        all_signals.append({
            'symbol': symbol,
            'name': market['name'],
            'direction': direction,
            'entry': price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'risk_reward': round(rr, 1),
            'confidence': round(confidence, 1),
            'change': change
        })
    
    # Sort by confidence (highest first)
    all_signals.sort(key=lambda x: x['confidence'], reverse=True)
    return all_signals

# Keyboards
def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💼 Positions", callback_data="positions"),
         InlineKeyboardButton("📈 Analytics", callback_data="analytics")],
        [InlineKeyboardButton("🎯 Scan ALL Coins", callback_data="signals"),
         InlineKeyboardButton("🛡 Risk", callback_data="risk")],
        [InlineKeyboardButton("₿ BTC Only", callback_data="scan_BTCUSDT"),
         InlineKeyboardButton("Ξ ETH Only", callback_data="scan_ETHUSDT")],
        [InlineKeyboardButton("◎ SOL Only", callback_data="scan_SOLUSDT"),
         InlineKeyboardButton("🔶 BNB Only", callback_data="scan_BNBUSDT")],
        [InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="dashboard"),
         InlineKeyboardButton("❓ Help", callback_data="help")]
    ])

def signal_keyboard(direction, symbol):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Execute {direction.upper()}", callback_data=f"execute_{direction}_{symbol}"),
         InlineKeyboardButton("❌ Skip", callback_data="signals")],
        [InlineKeyboardButton("🔄 Scan All", callback_data="signals")]
    ])

# Formatters
def format_dashboard():
    markets = get_all_markets()
    account = load_json("account.json")
    if not account:
        account = {"equity": STARTING_BALANCE, "balance": STARTING_BALANCE, "daily_trades": 0, "consecutive_losses": 0}
        save_json("account.json", account)
    
    trades = load_json("trades.json")
    if not trades:
        trades = []
    
    open_trades = [t for t in trades if t.get('status') == 'open']
    closed_trades = [t for t in trades if t.get('status') == 'closed']
    wins = [t for t in closed_trades if t.get('pnl', 0) > 0]
    total_pnl = sum(t.get('pnl', 0) for t in closed_trades)
    
    pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
    
    # Build market section
    market_lines = ""
    for symbol, m in markets.items():
        emoji = "🟢" if m['change_24h'] >= 0 else "🔴"
        market_lines += f"│ {m['name'][:2]} {emoji} ${m['price']:>10,.2f} {m['change_24h']:>+7.2f}% │\n"
    
    return f"""
╔══════════════════════════╗
║   🏦 TRADING DASHBOARD    ║
╚══════════════════════════╝

🕐 {datetime.utcnow().strftime('%H:%M UTC')}
📅 {datetime.utcnow().strftime('%d %b %Y')}
{'━' * 26}

💎 *ACCOUNT*
┌─────────────────────────┐
│ 💰 Equity:  ${account['equity']:>10,.2f} │
│ {pnl_emoji} P&L:     ${total_pnl:>+10,.2f} │
└─────────────────────────┘

🌍 *MARKETS*
┌─────────────────────────┐
{market_lines}└─────────────────────────┘

💼 *POSITIONS* ({len(open_trades)} open)
"""
    + ("📭 None\n" if not open_trades else 
       "\n".join(f"• {'📈 L' if t['direction']=='long' else '📉 S'} {t['symbol']} @ ${t['entry_price']:,.2f}" for t in open_trades[:4]))
    + f"""

🛡 *RISK* | Trades: {account['daily_trades']}/10 | Win: {len(wins)}/{max(len(closed_trades),1)} ({len(wins)/max(len(closed_trades),1)*100:.0f}%)
{'━' * 26}
✅ Bot Running | ${STARTING_BALANCE:.0f} Paper Account
"""

def format_signal(signal):
    d = signal['direction'].upper()
    emoji = "🟢 BUY" if d == 'LONG' else "🔴 SELL"
    stars = "⭐" * min(5, int(signal['confidence'] / 20))
    
    return f"""
🎯 *{signal['name']} SIGNAL*

╔══════════════════════════╗
║  {emoji}                       ║
║  {signal['symbol']}                        ║
║  Quality: {stars}                 ║
╠══════════════════════════╣
║  💰 Entry:  ${signal['entry']:>10,.2f} ║
║  🛑 Stop:   ${signal['stop_loss']:>10,.2f} ║
║  🎯 Target: ${signal['take_profit']:>10,.2f} ║
║  📊 R:R:    {signal['risk_reward']:>10.1f}x ║
║  🎯 Conf:   {signal['confidence']:>9.0f}% ║
║  📈 24h:    {signal['change']:>+9.2f}% ║
╚══════════════════════════╝
"""

def format_all_signals(signals):
    if not signals:
        return "🔍 *No signals found across any coins*\n\nMarkets are quiet. Try again later."
    
    msg = f"🎯 *TOP SIGNALS* ({len(signals)} found)\n\n"
    for i, s in enumerate(signals[:5]):
        d = "🟢 LONG" if s['direction'] == 'long' else "🔴 SHORT"
        msg += f"*{i+1}. {s['name']}* {d}\n"
        msg += f"   Entry: ${s['entry']:,.2f} | Conf: {s['confidence']:.0f}% | R:R: {s['risk_reward']}x\n\n"
    
    return msg

# Handlers
async def start(update: Update, context):
    chat_id = update.effective_chat.id
    if chat_id != ADMIN_ID:
        await update.message.reply_text("🔒 Unauthorized")
        return
    
    if not (DATA_DIR / "account.json").exists():
        save_json("account.json", {
            "equity": STARTING_BALANCE, "balance": STARTING_BALANCE,
            "daily_pnl": 0, "daily_trades": 0, "consecutive_losses": 0,
            "last_reset": datetime.utcnow().strftime("%Y-%m-%d")
        })
        save_json("trades.json", [])
    
    msg = format_dashboard()
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

async def handle_buttons(update: Update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "dashboard":
        msg = format_dashboard()
        await query.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())
    
    elif data == "positions":
        trades = load_json("trades.json")
        open_trades = [t for t in trades if t.get('status') == 'open']
        
        if not open_trades:
            await query.message.edit_text("💼 *No Open Positions*", parse_mode=ParseMode.MARKDOWN)
        else:
            msg = f"*💼 OPEN POSITIONS ({len(open_trades)})*\n\n"
            for t in open_trades:
                d = "📈 LONG" if t['direction'] == 'long' else "📉 SHORT"
                msg += f"• {t['symbol']} {d} @ ${t['entry_price']:,.2f}\n"
                msg += f"  SL: ${t['stop_loss']:,.2f} | TP: ${t['take_profit']:,.2f}\n\n"
            await query.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Refresh", callback_data="positions"),
                    InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")
                ]]))
    
    elif data == "signals":
        await query.message.edit_text("🔍 *Scanning all coins...*", parse_mode=ParseMode.MARKDOWN)
        
        signals = scan_all_signals()
        
        if signals:
            msg = format_all_signals(signals)
            # Best signal gets the execute button
            best = signals[0]
            await query.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"✅ Trade {best['name'][:10]}", callback_data=f"execute_{best['direction']}_{best['symbol']}")],
                    [InlineKeyboardButton("🔄 Rescan All", callback_data="signals"),
                     InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")]
                ]))
        else:
            await query.message.edit_text("🔍 *No signals found*\n\nAll coins are quiet right now.", parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Rescan", callback_data="signals"),
                    InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")
                ]]))
    
    elif data.startswith("scan_"):
        symbol = data.replace("scan_", "")
        market = get_market(symbol)
        
        await query.message.edit_text(f"🔍 *Scanning {SYMBOLS.get(symbol, symbol)}...*", parse_mode=ParseMode.MARKDOWN)
        
        if market:
            # Use same logic as scan_all_signals for single coin
            price = market['price']
            change = market['change_24h']
            
            signal = None
            if change > 0.5:
                confidence = min(50 + abs(change) * 3, 95)
                if confidence >= 50:
                    signal = {
                        'symbol': symbol, 'name': market['name'],
                        'direction': 'long', 'entry': price,
                        'stop_loss': round(price * 0.97, 2),
                        'take_profit': round(price * 1.09, 2),
                        'risk_reward': round((price*1.09-price)/(price-price*0.97), 1),
                        'confidence': round(confidence, 1), 'change': change
                    }
            elif change < -0.5:
                confidence = min(50 + abs(change) * 3, 95)
                if confidence >= 50:
                    signal = {
                        'symbol': symbol, 'name': market['name'],
                        'direction': 'short', 'entry': price,
                        'stop_loss': round(price * 1.03, 2),
                        'take_profit': round(price * 0.91, 2),
                        'risk_reward': round((price-price*0.91)/(price*1.03-price), 1),
                        'confidence': round(confidence, 1), 'change': change
                    }
            
            if signal:
                msg = format_signal(signal)
                await query.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=signal_keyboard(signal['direction'], symbol))
            else:
                await query.message.edit_text(f"🔍 No signal for {market['name']} | 24h: {change:+.2f}%", parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔄 Scan All", callback_data="signals"),
                        InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")
                    ]]))
    
    elif data == "analytics":
        trades = load_json("trades.json")
        closed = [t for t in trades if t.get('status') == 'closed']
        
        if not closed:
            msg = "📊 *No closed trades yet*"
        else:
            wins = [t for t in closed if t.get('pnl', 0) > 0]
            total_pnl = sum(t.get('pnl', 0) for t in closed)
            
            # Group by symbol
            by_symbol = {}
            for t in closed:
                sym = t.get('symbol', 'Unknown')
                if sym not in by_symbol:
                    by_symbol[sym] = {'total': 0, 'wins': 0}
                by_symbol[sym]['total'] += 1
                if t.get('pnl', 0) > 0:
                    by_symbol[sym]['wins'] += 1
            
            msg = f"📈 *PERFORMANCE*\n\nTotal: {len(closed)} trades | {len(wins)} wins | {len(wins)/len(closed)*100:.0f}% WR\nP&L: ${total_pnl:,.2f}\n\n*By Coin:*\n"
            for sym, stats in by_symbol.items():
                msg += f"{sym}: {stats['wins']}/{stats['total']}\n"
        
        await query.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")
            ]]))
    
    elif data == "risk":
        account = load_json("account.json")
        if not account:
            account = {"daily_trades": 0, "consecutive_losses": 0, "equity": STARTING_BALANCE}
        
        msg = f"🛡 *RISK*\n\nDaily: {account.get('daily_trades',0)}/10 trades\nLosses: {account.get('consecutive_losses',0)}/3\nEquity: ${account.get('equity',0):,.2f}"
        await query.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")
            ]]))
    
    elif data == "help":
        msg = """
❓ *COMMANDS*

/start - Dashboard

*Scan Buttons:*
🎯 Scan ALL Coins - Best signal
₿ BTC | Ξ ETH | ◎ SOL | 🔶 BNB

*Other:*
💼 Positions | 📈 Analytics | 🛡 Risk
"""
        await query.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")
            ]]))
    
    elif data.startswith("execute_"):
        parts = data.replace("execute_", "").split("_")
        direction = parts[0]
        symbol = "_".join(parts[1:]) if len(parts) > 1 else "BTCUSDT"
        
        market = get_market(symbol)
        if not market:
            await query.message.edit_text("⚠️ Cannot fetch price. Try again.")
            return
        
        # Generate fresh signal
        price = market['price']
        change = market['change_24h']
        confidence = min(50 + abs(change) * 3, 95)
        
        if direction == 'long':
            sl = round(price * 0.97, 2)
            tp = round(price * 1.09, 2)
        else:
            sl = round(price * 1.03, 2)
            tp = round(price * 0.91, 2)
        
        account = load_json("account.json")
        trade = {
            'trade_id': datetime.utcnow().strftime('%Y%m%d%H%M%S'),
            'symbol': symbol,
            'direction': direction,
            'entry_price': price,
            'stop_loss': sl,
            'take_profit': tp,
            'position_size': 10,
            'confidence': confidence,
            'status': 'open',
            'entry_time': datetime.utcnow().isoformat(),
            'pnl': 0
        }
        
        trades = load_json("trades.json")
        trades.append(trade)
        save_json("trades.json", trades)
        
        account['daily_trades'] = account.get('daily_trades', 0) + 1
        save_json("account.json", account)
        
        msg = f"""
✅ *TRADE OPENED*

{symbol} {direction.upper()}
Entry: ${price:,.2f}
SL: ${sl:,.2f}
TP: ${tp:,.2f}
Conf: {confidence:.0f}%
"""
        await query.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")
            ]]))

def main():
    print("""
╔══════════════════════════════════════╗
║   🏦 MULTI-COIN TRADING BOT       ║
║   BTC | ETH | SOL | BNB           ║
║   Paper Trading Mode                ║
╚══════════════════════════════════════╝
""")
    
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ ERROR: Set TELEGRAM_BOT_TOKEN in .env file!")
        return
    
    if ADMIN_ID == 0:
        print("❌ ERROR: Set TELEGRAM_ADMIN_ID in .env file!")
        return
    
    app = Application.builder().token(BOT_TOKEN).connect_timeout(30).read_timeout(30).write_timeout(30).build()
    app.add_handler(CommandHandler(["start", "dashboard"], start))
    app.add_handler(CallbackQueryHandler(handle_buttons))
    
    print(f"✅ Bot RUNNING!")
    print(f"📱 Telegram: /start")
    print(f"💰 Balance: ${STARTING_BALANCE:.0f}")
    print(f"🎯 Scanning: BTC, ETH, SOL, BNB")
    print(f"Press Ctrl+C to stop\n")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()