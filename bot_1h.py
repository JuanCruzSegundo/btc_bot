# ============================================================
#  bot_1h.py  –  Estrategia 1H Espejo con Pre-Alertas de Telegram
# ============================================================
import time
import logging
import pandas as pd
from config import (SYMBOL, POLL_SECONDS, LEVERAGE, TRADE_USDT, PIVOT_LOOKBACK)
from indicators import (calculate_ma, calculate_rsi, detect_pivot_high, 
                        detect_pivot_low, volume_confirms)
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
RSI_OB_1H    = 65
RSI_OS_1H    = 35
VOL_MULT_1H  = 1.2
TP_RATIO_1H  = 1.7
PARTIAL_1H   = 0.75

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
        self.last_pre_alert_hour = None  # Evita spamear pre-alertas en la misma hora

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

    entry_order = client.new_order(symbol=SYMBOL, side=side, type="MARKET", quantity=qty)

    sl_rounded = round_price(sl, tick)
    tp1_rounded = round_price(tp1, tick)

    client.new_order(symbol=SYMBOL, side=sl_side, type="STOP_MARKET", stopPrice=sl_rounded, closePosition=True)
    client.new_order(symbol=SYMBOL, side=tp_side, type="TAKE_PROFIT_MARKET", stopPrice=tp1_rounded, quantity=qty_partial, reduceOnly=True)

    logger.info(f"🚀 [Binance Testnet] Posición Abierta {direction}")
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

    # ── DETECTOR DE PRE-ALERTA (10-15 Minutos Antes) ──────────
    # Obtenemos la hora y minutos actuales en UTC (mismo huso que Binance)
    now_utc = pd.Timestamp.utcnow().tz_localize(None)
    current_minute = now_utc.minute
    current_hour   = now_utc.hour

    # Si estamos en el rango de anticipación (Minuto 45 al 50 de la vela en curso)
    if 45 <= current_minute <= 50 and state.last_pre_alert_hour != current_hour:
        # Hacemos el chequeo usando TODAS las velas (incluyendo la actual que se está formando)
        ma_pre = calculate_ma(df_1h)
        
        # RSI nativo para el bloque pre-alerta
        delta_pre = df_1h["close"].diff()
        gain_pre = (delta_pre.where(delta_pre > 0, 0)).rolling(window=14).mean()
        loss_pre = (-delta_pre.where(delta_pre < 0, 0)).rolling(window=14).mean()
        rsi_pre = 100 - (100 / (100 + (gain_pre / loss_pre)))

        close_live = df_1h["close"].iloc[-1]
        ma_live    = ma_pre.iloc[-1]
        rsi_live   = rsi_pre.iloc[-1]

        piv_highs_pre = detect_pivot_high(df_1h, n=PIVOT_LOOKBACK)
        piv_lows_pre  = detect_pivot_low(df_1h, n=PIVOT_LOOKBACK)
        last_ph_pre   = _last_pivot_price(df_1h, piv_highs_pre, "high")
        last_pl_pre   = _last_pivot_price(df_1h, piv_lows_pre, "low")

        # Cambios de zona del RSI simulados
        rsi_past_pre = rsi_pre.iloc[-3:]
        pre_long_rsi = any(val < RSI_OS_1H for val in rsi_past_pre[:-1]) and (rsi_live >= RSI_OS_1H)
        pre_short_rsi = any(val > RSI_OB_1H for val in rsi_past_pre[:-1]) and (rsi_live <= RSI_OB_1H)

        # Pre-checks básicos estructurales
        pre_long_possible  = (last_pl_pre is not None) and (close_live > ma_live) and pre_long_rsi
        pre_short_possible = (last_ph_pre is not None) and (close_live < ma_live) and pre_short_reversible = pre_short_rsi

        if pre_long_possible:
            state.last_pre_alert_hour = current_hour
            send_telegram(f"⚠️ <b>PRE-ALERTA LONG (1H)</b>\nSe están alineando las condiciones. Posible entrada cerca de: {close_live:.2f} USDT.\nLa vela cierra en aproximadamente {60 - current_minute} minutos. ¡Atento al gráfico!")
            logger.info("⚠️ Pre-alerta LONG enviada a Telegram.")

        elif pre_short_possible:
            state.last_pre_alert_hour = current_hour
            send_telegram(f"⚠️ <b>PRE-ALERTA SHORT (1H)</b>\nSe están alineando las condiciones. Posible entrada cerca de: {close_live:.2f} USDT.\nLa vela cierra en aproximadamente {60 - current_minute} minutos. ¡Atento al gráfico!")
            logger.info("⚠️ Pre-alerta SHORT enviada a Telegram.")


    # ── OPERACIÓN NORMAL AL CIERRE DE VELA (Velas Cerradas) ───
    df = df_1h.iloc[:-1]
    ma = calculate_ma(df)
    
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rsi = 100 - (100 / (100 + (gain / loss)))

    close_last = df["close"].iloc[-1]
    ma_last    = ma.iloc[-1]
    rsi_last   = rsi.iloc[-1]

    pivot_highs   = detect_pivot_high(df, n=PIVOT_LOOKBACK)
    pivot_lows    = detect_pivot_low(df, n=PIVOT_LOOKBACK)
    last_piv_high = _last_pivot_price(df, pivot_highs, "high")
    last_piv_low  = _last_pivot_price(df, pivot_lows, "low")

    rsi_past = rsi.iloc[-3:]
    from_oversold   = any(val < RSI_OS_1H for val in rsi_past[:-1]) and (rsi_last >= RSI_OS_1H)
    from_overbought = any(val > RSI_OB_1H for val in rsi_past[:-1]) and (rsi_last <= RSI_OB_1H)

    vol_data = volume_confirms(df, lookback=20, multiplier=VOL_MULT_1H)

    candle_id = df.index[-1]
    if candle_id == state.last_signal_candle:
        return

    # Evaluaciones estándar de disparo
    cond_pivlow      = last_piv_low is not None and _recent_pivot(pivot_lows, max_candles=20)
    cond_precio_long = close_last > ma_last
    cond_rsi_long    = from_oversold
    cond_volume      = vol_data.get("confirmed", False) if isinstance(vol_data, dict) else vol_data

    if cond_pivlow and cond_precio_long and cond_rsi_long and cond_volume:
        risk = close_last - last_piv_low
        if 0 < risk <= close_last * 0.05:
            sl  = last_piv_low * 0.999
            tp1 = close_last + risk * TP_RATIO_1H
            place_orders_1h("LONG", close_last, sl, tp1)
            state.in_trade    = True
            state.direction   = "LONG"
            state.entry_price = close_last
            state.sl_price    = sl
            state.tp1_price   = tp1
            state.last_signal_candle = candle_id
            send_telegram(f"🟢 <b>LONG AUTO-DEMO 1H</b>\nEntrada: {close_last:.2f}\nSL: {sl:.2f}\nTP1 (75%): {tp1:.2f}")
            return

    cond_pivhigh      = last_piv_high is not None and _recent_pivot(pivot_highs, max_candles=20)
    cond_precio_short = close_last < ma_last
    cond_rsi_short    = from_overbought

    if cond_pivhigh and cond_precio_short and cond_rsi_short and cond_volume:
        risk = last_piv_high - close_last
        if 0 < risk <= close_last * 0.05:
            sl  = last_piv_high * 1.001
            tp1 = close_last - risk * TP_RATIO_1H
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
    logger.info("Bot BTC/USDT 1H — Motor de Anticipación Activo")
    send_telegram("🤖 <b>Bot 1H (Con Pre-Alertas a Minuto 45) Inicializado</b>")

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
