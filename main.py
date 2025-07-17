import pandas as pd
import time
from binance.client import Client
import keys
import asyncio
import telegram

async def send_telegram_message(message):
    """
    Send a message to the Telegram bot.
    """
    try:
        bot = telegram.Bot(token=keys.telegram_bot_token)
        await bot.send_message(chat_id=keys.telegram_chat_id, text=message)
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        # To avoid infinite loops, we don't send a message here

def main():
    """
    Main function to run the Binance trading bot.
    """
    # Load configuration
    try:
        config = pd.read_csv('configuration.csv').iloc[0]
        risk_per_trade = config['risk_per_trade']
        leverage = config['leverage']
        atr_value = config['atr_value']
        lookback_candles = config['lookback_candles']
    except FileNotFoundError:
        print("Error: configuration.csv not found.")
        return
    except Exception as e:
        error_message = f"Error loading configuration: {e}"
        print(error_message)
        asyncio.run(send_telegram_message(error_message))
        return

    # Load symbols
    try:
        symbols = pd.read_csv('symbols.csv', header=None)[0].tolist()
    except FileNotFoundError:
        print("Error: symbols.csv not found.")
        return
    except Exception as e:
        error_message = f"Error loading symbols: {e}"
        print(error_message)
        asyncio.run(send_telegram_message(error_message))
        return

    # Initialize Binance client
    try:
        client = Client(keys.api_mainnet, keys.secret_mainnet)
    except Exception as e:
        error_message = f"Error initializing Binance client: {e}"
        print(error_message)
        asyncio.run(send_telegram_message(error_message))
        return

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

import numpy as np

def get_swing_points(klines):
    """
    Identify swing points from kline data.
    """
    highs = np.array([float(k[2]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])

    swing_highs = []
    swing_lows = []

    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            swing_highs.append((klines[i][0], highs[i]))
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
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

def get_fib_retracement(swing_high, swing_low):
    """
    Calculate Fibonacci retracement levels.
    """
    price_range = swing_high - swing_low
    golden_zone_start = swing_high - (price_range * 0.5)
    golden_zone_end = swing_high - (price_range * 0.618)
    entry_price = (golden_zone_start + golden_zone_end) / 2
    return entry_price

def get_klines(client, symbol, interval='15m', limit=100):
    """
    Get historical kline data from Binance.
    """
    try:
        klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
        return klines
    except Exception as e:
        error_message = f"Error fetching klines for {symbol}: {e}"
        print(error_message)
        asyncio.run(send_telegram_message(error_message))
        return None

import csv

def update_trade_report(symbol, side, entry_price, tp1, tp2, tp3, sl):
    """
    Update the trade report CSV file.
    """
    with open('trades.csv', 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([symbol, side, entry_price, tp1, tp2, tp3, sl, 'triggered'])

def place_market_order(client, symbol, side, quantity):
    """
    Place a market order on Binance.
    """
    try:
        # TODO: Implement actual order placement
        print(f"Placing {side} market order for {quantity} {symbol}...")
        return {"status": "success"}
    except Exception as e:
        error_message = f"Error placing market order for {symbol}: {e}"
        print(error_message)
        asyncio.run(send_telegram_message(error_message))
        return {"status": "error", "message": str(e)}

    virtual_orders = {}

    # Main scanning loop
    while True:
        print("Starting new scan cycle...")
        for symbol in symbols:
            print(f"Scanning {symbol}...")
            klines = get_klines(client, symbol, interval=Client.KLINE_INTERVAL_15MINUTE, limit=lookback_candles)
            if not klines:
                continue

            swing_highs, swing_lows = get_swing_points(klines)
            trend = get_trend(swing_highs, swing_lows)

            # Check for invalidated trades
            if symbol in virtual_orders:
                order = virtual_orders[symbol]
                if (order['side'] == 'long' and trend == 'downtrend') or \
                   (order['side'] == 'short' and trend == 'uptrend'):
                    asyncio.run(send_telegram_message(f"🔔 TRADE INVALIDATED 🔔\nSymbol: {symbol}\nSide: {order['side']}\nReason: Trend changed"))
                    del virtual_orders[symbol]

            # Check for new signals
            if symbol not in virtual_orders:
                if trend == "downtrend" and len(swing_highs) > 1 and len(swing_lows) > 1:
                    last_swing_high = swing_highs[-1][1]
                    last_swing_low = swing_lows[-1][1]
                    entry_price = get_fib_retracement(last_swing_high, last_swing_low)

                    sl = swing_highs[-1][1]
                    tp1 = last_swing_low
                    tp2 = last_swing_low - (last_swing_high - last_swing_low) * 0.5
                    tp3 = 0 # Floating TP

                    virtual_orders[symbol] = {'side': 'short', 'entry_price': entry_price, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'tp3': tp3, 'status': 'new'}
                    asyncio.run(send_telegram_message(f"🔔 NEW TRADE SIGNAL 🔔\nSymbol: {symbol}\nLeverage: {leverage}x\nRisk : {risk_per_trade}%\nProposed Entry: {entry_price}\nStop Loss: {sl}\nTake Profit 1: {tp1}\nTake Profit 2: {tp2}\nTake Profit 3: Floating"))

                elif trend == "uptrend" and len(swing_highs) > 1 and len(swing_lows) > 1:
                    last_swing_high = swing_highs[-1][1]
                    last_swing_low = swing_lows[-1][1]
                    entry_price = get_fib_retracement(last_swing_low, last_swing_high)

                    sl = swing_lows[-1][1]
                    tp1 = last_swing_high
                    tp2 = last_swing_high + (last_swing_high - last_swing_low) * 0.5
                    tp3 = 0 # Floating TP

                    virtual_orders[symbol] = {'side': 'long', 'entry_price': entry_price, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'tp3': tp3, 'status': 'new'}
                    asyncio.run(send_telegram_message(f"🔔 NEW TRADE SIGNAL 🔔\nSymbol: {symbol}\nLeverage: {leverage}x\nRisk : {risk_per_trade}%\nProposed Entry: {entry_price}\nStop Loss: {sl}\nTake Profit 1: {tp1}\nTake Profit 2: {tp2}\nTake Profit 3: Floating"))

            # Check for triggered orders
            if symbol in virtual_orders and virtual_orders[symbol]['status'] == 'new':
                order = virtual_orders[symbol]
                current_price = float(klines[-1][4])

                if (order['side'] == 'long' and current_price >= order['entry_price']) or \
                   (order['side'] == 'short' and current_price <= order['entry_price']):

                    if live_mode:
                        # TODO: Calculate quantity
                        quantity = 1
                        market_order = place_market_order(client, symbol, order['side'].upper(), quantity)
                        if market_order['status'] == 'error':
                            asyncio.run(send_telegram_message(f"🔔 TRADE REJECTED 🔔\nSymbol: {symbol}\nSide: {order['side']}\nReason: {market_order['message']}"))
                            del virtual_orders[symbol]
                            continue

                    virtual_orders[symbol]['status'] = 'triggered'
                    asyncio.run(send_telegram_message(f"🔔 TRADE TRIGGERED 🔔\nSymbol: {symbol}\nEntry: {order['entry_price']}\nSide: {order['side']}\nTP1: {order['tp1']}\nTP2: {order['tp2']}\nTP3: {order['tp3']}\nSL: {order['sl']}\nLeverage: {leverage}x"))
                    update_trade_report(symbol, order['side'], order['entry_price'], order['tp1'], order['tp2'], order['tp3'], order['sl'])


        print("Scan cycle complete. Cooling down for 2 minutes...")
        time.sleep(120)

if __name__ == "__main__":
    main()
