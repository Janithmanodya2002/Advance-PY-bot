import pandas as pd
import time
from binance.client import Client
import keys
import asyncio
import telegram
import numpy as np
import json
import os

def get_swing_points(klines, window=5):
    """
    Identify swing points from kline data.
    """
    highs = np.array([float(k[2]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])
    
    swing_highs = []
    swing_lows = []

    for i in range(window, len(highs) - window):
        is_swing_high = True
        for j in range(1, window + 1):
            if highs[i] < highs[i-j] or highs[i] < highs[i+j]:
                is_swing_high = False
                break
        if is_swing_high:
            swing_highs.append((klines[i][0], highs[i]))

        is_swing_low = True
        for j in range(1, window + 1):
            if lows[i] > lows[i-j] or lows[i] > lows[i+j]:
                is_swing_low = False
                break
        if is_swing_low:
            swing_lows.append((klines[i][0], lows[i]))
            
    return swing_highs, swing_lows

def get_trend(swing_highs, swing_lows):
    """
    Determine the trend based on swing points.
    """
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "undetermined"

    last_high = swing_highs[-1][1]
    prev_high = swing_highs[-2][1]
    last_low = swing_lows[-1][1]
    prev_low = swing_lows[-2][1]

    if last_high > prev_high and last_low > prev_low:
        return "uptrend"
    elif last_high < prev_high and last_low < prev_low:
        return "downtrend"
    else:
        return "undetermined"

def get_fib_retracement(p1, p2, trend):
    """
    Calculate Fibonacci retracement levels.
    """
    price_range = abs(p1 - p2)
    
    if trend == "downtrend":
        golden_zone_start = p1 - (price_range * 0.5)
        golden_zone_end = p1 - (price_range * 0.618)
    else: # Uptrend
        golden_zone_start = p2 + (price_range * 0.5)
        golden_zone_end = p2 + (price_range * 0.618)

    entry_price = (golden_zone_start + golden_zone_end) / 2
    
    return entry_price

def get_atr(klines, period=14):
    """
    Calculate the Average True Range (ATR).
    """
    highs = np.array([float(k[2]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])
    closes = np.array([float(k[4]) for k in klines])
    
    tr1 = highs - lows
    tr2 = np.abs(highs - np.roll(closes, 1))
    tr3 = np.abs(lows - np.roll(closes, 1))
    
    tr = np.amax([tr1, tr2, tr3], axis=0)
    
    atr = np.zeros(len(tr))
    atr[period-1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
        
    return atr[-1]

def get_klines(client, symbol, interval='15m', limit=100):
    """
    Get historical kline data from Binance.
    """
    try:
        klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
        return klines
    except Exception as e:
        print(f"Error fetching klines for {symbol}: {e}")
        return None

def update_trade_report(trades):
    """
    Update the trade report JSON file.
    """
    with open('trades.json', 'w') as f:
        json.dump(trades, f, indent=4)

def place_market_order(client, symbol, side, quantity):
    """
    Place a market order on Binance.
    """
    try:
        # TODO: Implement actual order placement
        print(f"Placing {side} market order for {quantity} {symbol}...")
        return {"status": "success"}
    except Exception as e:
        print(f"Error placing market order for {symbol}: {e}")
        return {"status": "error", "message": str(e)}

async def send_start_message(bot):
    try:
        await bot.send_message(chat_id=keys.telegram_chat_id, text="🤖 Bot started!")
    except Exception as e:
        print(f"Error sending start message: {e}")

async def order_status_monitor(client, bot):
    """
    Continuously monitor the status of open and pending trades.
    """
    while True:
        try:
            for trade in trades:
                if trade['status'] not in ['pending', 'running', 'tp1_hit', 'tp2_hit']:
                    continue

                symbol = trade['symbol']
                # Fetch the last 5 seconds of k-line data
                klines = get_klines(client, symbol, interval=Client.KLINE_INTERVAL_1SECOND, limit=5)
                if not klines:
                    continue

                # Check for expired orders
                if time.time() * 1000 - trade['timestamp'] > 4 * 60 * 60 * 1000:
                    if trade['status'] == 'pending':
                        try:
                            await bot.send_message(chat_id=keys.telegram_chat_id, text=f"⚠️ TRADE INVALIDATED ⚠️\nSymbol: {symbol}\nSide: {trade['side']}\nReason: Order expired (4 hours)")
                        except Exception as e:
                            print(f"Error sending Telegram message: {e}")
                        trade['status'] = 'rejected'
                        update_trade_report(trades)
                        if symbol in virtual_orders:
                            del virtual_orders[symbol]
                    continue

                prices = [float(k[4]) for k in klines]

                for price in prices:
                    # Check for SL/TP hits
                    if trade['status'] in ['running', 'tp1_hit', 'tp2_hit']:
                        if (trade['side'] == 'long' and price <= trade['sl']) or \
                           (trade['side'] == 'short' and price >= trade['sl']):
                            try:
                                await bot.send_message(chat_id=keys.telegram_chat_id, text=f"🛑 STOP LOSS HIT 🛑\nSymbol: {symbol}\nSide: {trade['side']}\nPrice: {price:.8f}")
                            except Exception as e:
                                print(f"Error sending Telegram message: {e}")
                            trade['status'] = 'sl_hit'
                            update_trade_report(trades)
                            if symbol in virtual_orders:
                                del virtual_orders[symbol]
                            break

                        if trade['status'] == 'running':
                            if (trade['side'] == 'long' and price >= trade['entry_price'] * 1.005) or \
                               (trade['side'] == 'short' and price <= trade['entry_price'] * 0.995):
                                new_sl = trade['entry_price'] * 1.001 if trade['side'] == 'long' else trade['entry_price'] * 0.999
                                if (trade['side'] == 'long' and new_sl > trade['sl']) or \
                                   (trade['side'] == 'short' and new_sl < trade['sl']):
                                    trade['sl'] = new_sl
                                    update_trade_report(trades)
                                    try:
                                        await bot.send_message(chat_id=keys.telegram_chat_id, text=f"🔒 STOP LOSS UPDATED 🔒\nSymbol: {symbol}\nSide: {trade['side']}\nNew SL: {trade['sl']:.8f}")
                                    except Exception as e:
                                        print(f"Error sending Telegram message: {e}")

                        if trade['status'] == 'running' and ((trade['side'] == 'long' and price >= trade['tp1']) or \
                           (trade['side'] == 'short' and price <= trade['tp1'])):
                            trade['status'] = 'tp1_hit'
                            trade['sl'] = trade['entry_price']
                            trade['quantity'] *= 0.5
                            update_trade_report(trades)
                            try:
                                await bot.send_message(chat_id=keys.telegram_chat_id, text=f"🎉 TAKE PROFIT 1 HIT 🎉\nSymbol: {symbol}\nSide: {trade['side']}\nPrice: {price:.8f}\nClosing 50% of the position.\nNew SL: {trade['sl']:.8f}")
                            except Exception as e:
                                print(f"Error sending Telegram message: {e}")

                        if trade['status'] == 'tp1_hit' and ((trade['side'] == 'long' and price >= trade['tp2']) or \
                           (trade['side'] == 'short' and price <= trade['tp2'])):
                            trade['status'] = 'tp2_hit'
                            trade['sl'] = trade['tp1']
                            trade['quantity'] *= 0.6
                            update_trade_report(trades)
                            try:
                                await bot.send_message(chat_id=keys.telegram_chat_id, text=f"🎉 TAKE PROFIT 2 HIT 🎉\nSymbol: {symbol}\nSide: {trade['side']}\nPrice: {price:.8f}\nClosing 40% of the position.\nNew SL: {trade['sl']:.8f}")
                            except Exception as e:
                                print(f"Error sending Telegram message: {e}")

                    # Check for triggered orders
                    elif trade['status'] == 'pending':
                        if (trade['side'] == 'long' and price >= trade['entry_price']) or \
                           (trade['side'] == 'short' and price <= trade['entry_price']):
                            
                            # In a real scenario, you would place a market order here
                            trade['status'] = 'running'
                            update_trade_report(trades)
                            try:
                                await bot.send_message(chat_id=keys.telegram_chat_id, text=f"✅ TRADE TRIGGERED ✅\nSymbol: {symbol}\nEntry: {trade['entry_price']:.8f}\nSide: {trade['side']}\nTP1: {trade['tp1']:.8f}\nTP2: {trade['tp2']:.8f}\nTP3: {trade['tp3']}\nSL: {trade['sl']:.8f}\nLeverage: {leverage}x")
                            except Exception as e:
                                print(f"Error sending Telegram message: {e}")
                            break
        except Exception as e:
            print(f"Error in order status monitor: {e}")

        await asyncio.sleep(1)


# Global variables for trades
virtual_orders = {}
trades = []
leverage = 0

async def main():
    """
    Main function to run the Binance trading bot.
    """
    rejected_symbols = {}
    print("Starting bot...")
    
    bot = telegram.Bot(token=keys.telegram_bot_token)
    # Send start message
    await send_start_message(bot)

    # Load configuration
    global leverage
    try:
        config = pd.read_csv('configuration.csv').iloc[0]
        risk_per_trade = config['risk_per_trade']
        leverage = config['leverage']
        atr_value = int(config['atr_value'])
        lookback_candles = int(config['lookback_candles'])
        swing_window = int(config['swing_window'])
        print("Configuration loaded.")
    except FileNotFoundError:
        print("Error: configuration.csv not found.")
        return
    except Exception as e:
        print(f"Error loading configuration: {e}")
        return

    # Load symbols
    try:
        symbols = pd.read_csv('symbols.csv', header=None)[0].tolist()
        print("Symbols loaded.")
    except FileNotFoundError:
        print("Error: symbols.csv not found.")
        return
    except Exception as e:
        print(f"Error loading symbols: {e}")
        return

    # Initialize Binance client
    try:
        client = Client(keys.api_mainnet, keys.secret_mainnet)
        print("Binance client initialized.")
    except Exception as e:
        print(f"Error initializing Binance client: {e}")
        return

    # Start the order status monitor
    asyncio.create_task(order_status_monitor(client, bot))

    # Get user input for mode
    while True:
        mode = input("Select (1)Live / (2)Signal: ")
        if mode in ['1', '2']:
            break
        else:
            print("Invalid input. Please select 1 or 2.")

    if mode == '1':
        print("Running in Live mode.")
        live_mode = True
    else:
        print("Running in Signal mode.")
        live_mode = False

    # Load trades from JSON
    if os.path.exists('trades.json'):
        with open('trades.json', 'r') as f:
            try:
                trades.extend(json.load(f))
                for trade in trades:
                    if trade['status'] in ['running', 'tp1_hit', 'tp2_hit', 'pending']:
                        virtual_orders[trade['symbol']] = trade
            except json.JSONDecodeError:
                pass


    # Main scanning loop
    print("Entering main loop...")
    while True:
        print("Starting new scan cycle...")
        for symbol in symbols:
            print(f"Scanning {symbol}...")
            klines = get_klines(client, symbol, interval=Client.KLINE_INTERVAL_15MINUTE, limit=lookback_candles)
            if not klines:
                continue

            swing_highs, swing_lows = get_swing_points(klines, swing_window)
            trend = get_trend(swing_highs, swing_lows)
            
            # Check for rejected symbols cooldown
            if symbol in rejected_symbols and time.time() - rejected_symbols[symbol] < 4 * 60 * 60:
                continue

            # Check for new signals
            if symbol not in virtual_orders:
                if trend == "downtrend" and len(swing_highs) > 1 and len(swing_lows) > 1:
                    last_swing_high = swing_highs[-1][1]
                    last_swing_low = swing_lows[-1][1]
                    entry_price = get_fib_retracement(last_swing_high, last_swing_low, trend)
                    atr = get_atr(klines, atr_value)
                    
                    sl = last_swing_high
                    tp1 = entry_price - (sl - entry_price)
                    tp2 = entry_price - (sl - entry_price) * 2
                    tp3 = 0 # Floating TP

                    new_trade = {'symbol': symbol, 'side': 'short', 'entry_price': entry_price, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'tp3': tp3, 'status': 'pending', 'quantity': 1, 'timestamp': klines[-1][0]}
                    trades.append(new_trade)
                    virtual_orders[symbol] = new_trade
                    update_trade_report(trades)
                    try:
                        await bot.send_message(chat_id=keys.telegram_chat_id, text=f"🚀 NEW TRADE SIGNAL 🚀\nSymbol: {symbol}\nSide: Short\nLeverage: {leverage}x\nRisk : {risk_per_trade}%\nProposed Entry: {entry_price:.8f}\nStop Loss: {sl:.8f}\nTake Profit 1: {tp1:.8f}\nTake Profit 2: {tp2:.8f}\nTake Profit 3: Floating")
                    except Exception as e:
                        print(f"Error sending Telegram message: {e}")

                elif trend == "uptrend" and len(swing_highs) > 1 and len(swing_lows) > 1:
                    last_swing_high = swing_highs[-1][1]
                    last_swing_low = swing_lows[-1][1]
                    entry_price = get_fib_retracement(last_swing_low, last_swing_high, trend)
                    atr = get_atr(klines, atr_value)

                    sl = last_swing_low
                    tp1 = entry_price + (entry_price - sl)
                    tp2 = entry_price + (entry_price - sl) * 2
                    tp3 = 0 # Floating TP

                    new_trade = {'symbol': symbol, 'side': 'long', 'entry_price': entry_price, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'tp3': tp3, 'status': 'pending', 'quantity': 1, 'timestamp': klines[-1][0]}
                    trades.append(new_trade)
                    virtual_orders[symbol] = new_trade
                    update_trade_report(trades)
                    try:
                        await bot.send_message(chat_id=keys.telegram_chat_id, text=f"🚀 NEW TRADE SIGNAL 🚀\nSymbol: {symbol}\nSide: Long\nLeverage: {leverage}x\nRisk : {risk_per_trade}%\nProposed Entry: {entry_price:.8f}\nStop Loss: {sl:.8f}\nTake Profit 1: {tp1:.8f}\nTake Profit 2: {tp2:.8f}\nTake Profit 3: Floating")
                    except Exception as e:
                        print(f"Error sending Telegram message: {e}")


        print("Scan cycle complete. Cooling down for 2 minutes...")
        await asyncio.sleep(120)

if __name__ == "__main__":
    asyncio.run(main())
