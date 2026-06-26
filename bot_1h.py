# ============================================================
#  bot_1h.py  –  Estrategia Pura de Pivotes + RSI + Vol en 1H
# ============================================================
import time
import logging
import pandas as pd
from config import (SYMBOL, POLL_SECONDS, LEVERAGE, TRADE_USDT, PIVOT_LOOKBACK)
from indicators import (calculate_ma, calculate_rsi, detect_pivot_high, 
                        detect_pivot_low, rsi_leaving_extreme, volume_confirms)
from exchange  import (get_klines, get_position, get_client, set_leverage, 
                        get_symbol_info, round_qty, round_price, get_available_balance)
from notifier  import send_telegram, msg_signal_1h, msg_tp1_1h, msg_tp2_1h, msg_sl_hit

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [1H][%(levelname)s] %(message)s",
    handlers= [
        logging.FileHandler("bot_1h.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("bot_1h")

# ── Configuración de Estrategia 1H ───────────────────────────
RSI_OB_1H    = 65    # Sobrecompra para 1H
RSI_OS_1H    = 35    # Sobreventa para 1H
VOL_MULT_1H  = 1.2   # Multiplicador de volumen saludable
TP_RATIO_1H  = 1.7   # Tu ratio confirmado 1:1.7
PARTIAL_1H   = 0.75  # Cierre parcial del 75% en TP1

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
    margin_usdt = 50.0  # Tu margen fijo solicitado
    notional = margin_usdt * LEVERAGE
    qty = notional / entry_price
    return round_qty(qty, step)

def _last_pivot_price(df, pivot_series, col):
    idx = pivot_series[pivot_series].index
    if len(idx) == 0:
        return None
    return float(df[col].loc[idx[-1]])

def _recent_pivot(pivot_series, max_candles=20):
    return bool(pivot_series.iloc[-max_candles:].any())

def place_orders_1h(direction: str, entry: float, sl: float, tp1: float):
    side    = "BUY"  if direction == "LONG"  else "SELL"
    sl_side = "SELL" if direction == "LONG"  else "BUY"
    tp_side = "SELL" if direction == "LONG"  else "BUY"

    info = get_symbol_info()
    tick = float(next(f["tickSize"] for f in info["filters"] if f["filterType"] == "PRICE_FILTER"))

    qty = calc_quantity_1h(entry)
    qty_partial = round_qty(qty * PARTIAL_1H, float(next(f["stepSize"] for f in info["filters"] if f["filterType"] == "LOT_SIZE")))

    client = get_client()
    set_leverage(SYMBOL, LEVERAGE)

    # Entrada a mercado
    entry_order = client.new_order(symbol=SYMBOL, side=side, type="MARKET", quantity=qty)

    sl_rounded = round_price(sl, tick)
    tp1_rounded = round_price(tp1, tick)

    # STOP LOSS
    client.new_order(symbol=SYMBOL, side=sl_side, type="STOP_MARKET", stopPrice=sl_rounded, closePosition=True)

    # TAKE PROFIT 1 (75%)
    client.new_order(symbol=SYMBOL, side=tp_side, type="TAKE_PROFIT_MARKET", stopPrice=tp1_rounded, quantity=qty_partial, reduceOnly=True)

    logger.info(f"🚀 [Binance Testnet] Posición Abierta {direction} | Margen: 50 USDT | Apalancamiento: {LEVERAGE}x")
    return entry_order

def run_cycle_1h():
    raw_1h = get_klines(symbol=SYMBOL, interval="1h", limit=200)
    if not raw_1h:
        return

    df_1h = klines_to_df(raw_1h)

    if state.in_trade:
        current_price = df_1h["close"].iloc[-1]
        _manage_trade_1h(current_price)
        return

    # Operamos con velas cerradas
    df = df_1h.iloc[:-1]
    ma = calculate_ma(df)
    
    # Calculamos RSI de forma nativa adaptado a los limites de 1H
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (100 + rs))

    close_last = df["close"].iloc[-1]
    ma_last    = ma.iloc[-1]
    rsi_last   = rsi.iloc[-1]

    # Estructura de pivotes (Lookback 5)
    pivot_highs   = detect_pivot_high(df, n=PIVOT_LOOKBACK)
    pivot_lows    = detect_pivot_low(df, n=PIVOT_LOOKBACK)
    last_piv_high = _last_pivot_price(df, pivot_highs, "high")
    last_piv_low  = _last_pivot_price(df, pivot_lows, "low")

    # Matematicas de salida de extremos
    rsi_past = rsi.iloc[-3:]
    from_oversold   = any(val < RSI_OS_1H for val in rsi_past[:-1]) and (rsi_last >= RSI_OS_1H)
    from_overbought = any(val > RSI_OB_1H for val in rsi_past[:-1]) and (rsi_last <= RSI_OB_1H)

    # Filtro de volumen detallado (20 horas de historial)
    vol_data = volume_confirms(df, lookback=20, multiplier=VOL_MULT_1H)

    logger.info(
        f"Precio={close_last:.2f} | MA12={ma_last:.2f} | RSI={rsi_last:.1f} | "
        f"PivHigh={last_piv_high} | PivLow={last_piv_low} | VolRatio={vol_data.get('ratio', 0):.2f}x"
    )

    candle_id = df.index[-1]
    if candle_id == state.last_signal_candle:
        return

    # ── EVALUAR GATILLO LONG ─────────────────────────────────
    cond_pivlow      = last_piv_low is not None and _recent_pivot(pivot_lows, max_candles=20)
    cond_precio_long = close_last > ma_last
    cond_rsi_long    = from_oversold
    cond_volume      = vol_data.get("confirmed", vol_data) if isinstance(vol_data, bool) else vol_data.get("confirmed", False)

    logger.info(f"LONG CHECK → pivlow={cond_pivlow} | precio>MA={cond_precio_long} | rsi_OS={cond_rsi_long} | vol={cond_volume}")

    if cond_pivlow and cond_precio_long and cond_rsi_long and cond_volume:
        risk = close_last - last_piv_low
        if 0 < risk <= close_last * 0.05:
            sl  = last_piv_low * 0.999
            tp1 = close_last + risk * TP_RATIO_1H
            
            logger.info(f"🟢 DISPARO LONG 1H CONFIRMADO")
            place_orders_1h("LONG", close_last, sl, tp1)
            state.in_trade    = True
            state.direction   = "LONG"
            state.entry_price = close_last
            state.sl_price    = sl
            state.tp1_price   = tp1
            state.last_signal_candle = candle_id
            send_telegram(f"🟢 <b>LONG AUTO-DEMO 1H</b>\nEntrada: {close_last:.2f}\nSL: {sl:.2f}\nTP1 (75%): {tp1:.2f}")
            return

    # ── EVALUAR GATILLO SHORT ────────────────────────────────
    cond_pivhigh      = last_piv_high is not None and _recent_pivot(pivot_highs, max_candles=20)
    cond_precio_short = close_last < ma_last
    cond_rsi_short    = from_overbought

    logger.info(f"SHORT CHECK → pivhigh={cond_pivhigh} | precio<MA={cond_precio_short} | rsi_OB={cond_rsi_short} | vol={cond_volume}")

    if cond_pivhigh and cond_precio_short and cond_rsi_short and cond_volume:
        risk = last_piv_high - close_last
        if 0 < risk <= close_last * 0.05:
            sl  = last_piv_high * 1.001
            tp1 = close_last - risk * TP_RATIO_1H
            
            logger.info(f"🔴 DISPARO SHORT 1H CONFIRMADO")
            place_orders_1h("SHORT", close_last, sl, tp1)
            state.in_trade    = True
            state.direction   = "SHORT"
            state.entry_price = close_last
            state.sl_price    = sl
            state.tp1_price   = tp1
            state.last_signal_candle = candle_id
            send_telegram(f"🔴 <b>SHORT AUTO-DEMO 1H</b>\nEntrada: {close_last:.2f}\nSL: {sl:.2f}\nTP1 (75%): {tp1:.2f}")
            return

def _manage_trade_1h(current_price: float):
    pos = get_position()
    if pos is None:
        logger.info("Posición cerrada en Binance. Reseteando estado.")
        send_telegram(f"🏁 <b>Operación 1H Finalizada</b>\nEl mercado ejecutó la salida (SL o TP).")
        state.reset()

def main_loop():
    logger.info("=" * 50)
    logger.info("Bot BTC/USDT 1H — Estrategia Espejo 5m Activa")
    send_telegram("🤖 <b>Bot 1H (Estrategia Espejo 5m) Inicializado</b>")

    while True:
        try:
            run_cycle_1h()
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main_loop()
