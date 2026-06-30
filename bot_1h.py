# ============================================================
#  bot_1h.py  –  Estrategia 1H "Gatillo Fácil" (Alta Frecuencia)
# ============================================================
import time
import logging
import pandas as pd
from config import (SYMBOL, POLL_SECONDS, LEVERAGE, TRADE_USDT, PIVOT_LOOKBACK)
from indicators import (calculate_ma, calculate_rsi, detect_pivot_high, 
                        detect_pivot_low, volume_confirms)
from exchange  import (get_klines, get_position, get_client, set_leverage, 
                        get_symbol_info, round_qty, round_price, get_available_balance)
from notifier  import send_telegram

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [1H][%(levelname)s] %(message)s",
    handlers= [
        logging.FileHandler("bot_1h.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("bot_1h")

# ── NUEVA CONFIGURACIÓN GATILLO FÁCIL ────────────────────────
RSI_OB_1H    = 55    # Atrapa rebotes bajistas mucho antes
RSI_OS_1H    = 45    # Atrapa rebotes alcistas mucho antes
VOL_MULT_1H  = 1.0   # Solo exige volumen promedio (sin picos)
TP_RATIO_1H  = 1.7
PARTIAL_1H   = 1.0   

class State1H:
    def __init__(self):
        self.reset()

    def reset(self):
        self.in_trade           = False
        self.direction          = None
        self.entry_price        = None
        self.sl_price           = None
        self.tp1_price          = None
        self.last_signal_candle = None

state = State1H()

def klines_to_df(raw: list) -> pd.DataFrame:
    df = pd.DataFrame(raw, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qav","trades","tbav","tbqv","ignore"
    ])
    for col in ["open","high","low","close","volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("open_time", inplace=True)
    return df

def calc_quantity_1h(entry_price: float) -> float:
    info = get_symbol_info()
    step = float(next(f["stepSize"] for f in info["filters"] if f["filterType"] == "LOT_SIZE"))
    margin_usdt = 50.0  
    notional = margin_usdt * LEVERAGE
    return round_qty(notional / entry_price, step)

def _last_pivot_price(df, pivot_series, col):
    idx = pivot_series[pivot_series].index
    if len(idx) == 0:
        return None
    return float(df[col].loc[idx[-1]])

def _recent_pivot(pivot_series, max_candles=20):
    return bool(pivot_series.iloc[-max_candles:].any())

# Wrapper Anti-Crashes para Telegram
def safe_send_telegram(msg):
    try:
        send_telegram(msg)
    except Exception as e:
        logger.error(f"Fallo ignorado en Telegram: {e}")

def place_orders_1h(direction: str, entry: float, sl: float, tp1: float):
    side    = "BUY"  if direction == "LONG"  else "SELL"
    sl_side = "SELL" if direction == "LONG"  else "BUY"
    tp_side = "SELL" if direction == "LONG"  else "BUY"

    info = get_symbol_info()
    tick = float(next(f["tickSize"] for f in info["filters"] if f["filterType"] == "PRICE_FILTER"))
    step = float(next(f["stepSize"] for f in info["filters"] if f["filterType"] == "LOT_SIZE"))

    qty = calc_quantity_1h(entry)
    qty_partial = round_qty(qty * PARTIAL_1H, step)

    client = get_client()
    set_leverage(SYMBOL, LEVERAGE)

    entry_order = client.new_order(symbol=SYMBOL, side=side, type="MARKET", quantity=qty)
    
    sl_rounded = round_price(sl, tick)
    tp1_rounded = round_price(tp1, tick)

    client.new_order(symbol=SYMBOL, side=sl_side, type="STOP_MARKET", stopPrice=sl_rounded, closePosition=True)
    client.new_order(symbol=SYMBOL, side=tp_side, type="TAKE_PROFIT_MARKET", stopPrice=tp1_rounded, quantity=qty_partial, reduceOnly=True)

    logger.info(f"🚀 Posición Ejecutada Fija: {direction}")
    return entry_order

def run_cycle_1h():
    raw_1h = get_klines(symbol=SYMBOL, interval="1h", limit=200)
    if not raw_1h:
        return

    df_1h = klines_to_df(raw_1h)

    if state.in_trade:
        pos = get_position()
        if pos is None:
            logger.info("Posición cerrada en Binance. Reseteando estado.")
            safe_send_telegram(f"🏁 <b>Operación 1H Finalizada (SL/TP)</b>")
            state.reset()
        return

    df = df_1h.iloc[:-1]
    ma = calculate_ma(df)
    rsi = calculate_rsi(df)  
    
    close_last = df["close"].iloc[-1]
    ma_last    = ma.iloc[-1]
    rsi_last   = rsi.iloc[-1]

    pivot_highs   = detect_pivot_high(df, n=PIVOT_LOOKBACK)
    pivot_lows    = detect_pivot_low(df, n=PIVOT_LOOKBACK)
    last_piv_high = _last_pivot_price(df, pivot_highs, "high")
    last_piv_low  = _last_pivot_price(df, pivot_lows, "low")

    # Flexibilizamos el chequeo del RSI
    rsi_past = rsi.iloc[-3:]
    from_oversold   = any(val < RSI_OS_1H for val in rsi_past[:-1]) and (rsi_last >= RSI_OS_1H)
    from_overbought = any(val > RSI_OB_1H for val in rsi_past[:-1]) and (rsi_last <= RSI_OB_1H)

    vol_data = volume_confirms(df, lookback=20, multiplier=VOL_MULT_1H)

    candle_id = df.index[-1]
    if candle_id == state.last_signal_candle:
        return

    # ── GATILLO LONG (Tolerancia Ampliada) ───────────────────
    cond_pivlow      = last_piv_low is not None and _recent_pivot(pivot_lows, max_candles=20)
    cond_precio_long = close_last > ma_last
    cond_volume      = vol_data.get("confirmed", False) if isinstance(vol_data, dict) else vol_data

    # Ahora pedimos que venga de zona baja O que el RSI simplemente esté subiendo con fuerza
    if cond_pivlow and cond_precio_long and (from_oversold or rsi_last > rsi_past.iloc[0]) and cond_volume:
        risk = close_last - last_piv_low
        if 0 < risk <= close_last * 0.05:
            sl  = last_piv_low * 0.995 # Stop Loss blindado (+0.5% extra)
            tp1 = close_last + risk * TP_RATIO_1H
            place_orders_1h("LONG", close_last, sl, tp1)
            
            state.in_trade    = True
            state.direction   = "LONG"
            state.last_signal_candle = candle_id
            safe_send_telegram(f"🟢 <b>LONG GATILLO FÁCIL 1H</b>\nEntrada: {close_last:.2f}\nSL: {sl:.2f}")
            return

    # ── GATILLO SHORT (Tolerancia Ampliada) ──────────────────
    cond_pivhigh      = last_piv_high is not None and _recent_pivot(pivot_highs, max_candles=20)
    cond_precio_short = close_last < ma_last

    if cond_pivhigh and cond_precio_short and (from_overbought or rsi_last < rsi_past.iloc[0]) and cond_volume:
        risk = last_piv_high - close_last
        if 0 < risk <= close_last * 0.05:
            sl  = last_piv_high * 1.005 # Stop Loss blindado (+0.5% extra)
            tp1 = close_last - risk * TP_RATIO_1H
            place_orders_1h("SHORT", close_last, sl, tp1)
            
            state.in_trade    = True
            state.direction   = "SHORT"
            state.last_signal_candle = candle_id
            safe_send_telegram(f"🔴 <b>SHORT GATILLO FÁCIL 1H</b>\nEntrada: {close_last:.2f}\nSL: {sl:.2f}")
            return

def main_loop():
    logger.info("Bot BTC/USDT 1H — GATILLO FÁCIL ACTIVO")
    safe_send_telegram("🤖 <b>Bot 1H (Gatillo Fácil) Inicializado</b>")

    while True:
        try:
            run_cycle_1h()
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Error en loop: {e}", exc_info=True)
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main_loop()
