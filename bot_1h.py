# ============================================================
#  bot_1h.py  –  Estrategia Pura 1H (Pivotes + RSI + Vol)
#  FIX: ventana RSI ampliada de 3 → 12 velas para capturar
#       salidas de zona extrema que la versión original perdía.
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

# ── Configuración de Estrategia 1H ───────────────────────────
RSI_OB_1H         = 65
RSI_OS_1H         = 35
RSI_EXIT_LOOKBACK = 12   # ← FIX: ventana ampliada (era 3, que perdía el 80% de señales)
VOL_MULT_1H       = 1.2
TP_RATIO_1H       = 1.7
PARTIAL_1H        = 1.0


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
    step = float(next(f["stepSize"] for f in info["filters"] if f["filterType"] == "LOT_SIZE"))

    qty = calc_quantity_1h(entry)
    qty_partial = round_qty(qty * PARTIAL_1H, step)

    client = get_client()
    set_leverage(SYMBOL, LEVERAGE)

    entry_order = client.new_order(symbol=SYMBOL, side=side, type="MARKET", quantity=qty)

    sl_rounded  = round_price(sl, tick)
    tp1_rounded = round_price(tp1, tick)

    client.new_order(symbol=SYMBOL, side=sl_side, type="STOP_MARKET",
                     stopPrice=sl_rounded, closePosition=True)
    client.new_order(symbol=SYMBOL, side=tp_side, type="TAKE_PROFIT_MARKET",
                     stopPrice=tp1_rounded, quantity=qty_partial, reduceOnly=True)

    logger.info(f"🚀 [Binance Demo] Posición Ejecutada: {direction}")
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

    # Solo velas cerradas para evitar señales falsas
    df = df_1h.iloc[:-1]

    ma  = calculate_ma(df)
    rsi = calculate_rsi(df)

    close_last = df["close"].iloc[-1]
    ma_last    = ma.iloc[-1]
    rsi_last   = rsi.iloc[-1]

    pivot_highs   = detect_pivot_high(df, n=PIVOT_LOOKBACK)
    pivot_lows    = detect_pivot_low(df, n=PIVOT_LOOKBACK)
    last_piv_high = _last_pivot_price(df, pivot_highs, "high")
    last_piv_low  = _last_pivot_price(df, pivot_lows, "low")

    # ── FIX PRINCIPAL: ventana deslizante de 12 velas ────────
    # Antes: rsi.iloc[-3:] → solo 2 velas de historia → perdía el 80% de señales
    # Ahora: buscamos si en alguna de las últimas 11 velas el RSI tocó la zona
    # extrema, y si la vela actual ya salió de ella. Esto replica el comportamiento
    # visual del indicador Pupupu en TradingView.
    rsi_window = rsi.iloc[-RSI_EXIT_LOOKBACK:]          # 12 velas (11 pasadas + actual)
    rsi_history = rsi_window.iloc[:-1]                  # las 11 pasadas
    from_oversold   = (rsi_history.min() < RSI_OS_1H) and (rsi_last >= RSI_OS_1H)
    from_overbought = (rsi_history.max() > RSI_OB_1H) and (rsi_last <= RSI_OB_1H)

    # Filtro de volumen
    vol_data  = volume_confirms(df, lookback=20, multiplier=VOL_MULT_1H)
    cond_volume = vol_data.get("confirmed", False) if isinstance(vol_data, dict) else vol_data

    candle_id = df.index[-1]
    if candle_id == state.last_signal_candle:
        return

    # ── GATILLO LONG ─────────────────────────────────────────
    cond_pivlow      = last_piv_low is not None and _recent_pivot(pivot_lows, max_candles=20)
    cond_precio_long = close_last > ma_last
    cond_rsi_long    = from_oversold

    if cond_pivlow and cond_precio_long and cond_rsi_long and cond_volume:
        risk = close_last - last_piv_low
        if 0 < risk <= close_last * 0.05:
            sl  = last_piv_low * 0.999
            tp1 = close_last + risk * TP_RATIO_1H
            place_orders_1h("LONG", close_last, sl, tp1)

            state.in_trade           = True
            state.direction          = "LONG"
            state.entry_price        = close_last
            state.sl_price           = sl
            state.tp1_price          = tp1
            state.last_signal_candle = candle_id

            send_telegram(
                f"🟢 <b>LONG AUTO-DEMO 1H</b>\n"
                f"Entrada: {close_last:.2f}\nSL: {sl:.2f}\nTP (100%): {tp1:.2f}"
            )
            logger.info(
                f"LONG | entry={close_last:.2f} sl={sl:.2f} tp1={tp1:.2f} "
                f"rsi={rsi_last:.1f} rsi_min12={rsi_history.min():.1f}"
            )
            return

    # ── GATILLO SHORT ────────────────────────────────────────
    cond_pivhigh      = last_piv_high is not None and _recent_pivot(pivot_highs, max_candles=20)
    cond_precio_short = close_last < ma_last
    cond_rsi_short    = from_overbought

    if cond_pivhigh and cond_precio_short and cond_rsi_short and cond_volume:
        risk = last_piv_high - close_last
        if 0 < risk <= close_last * 0.05:
            sl  = last_piv_high * 1.001
            tp1 = close_last - risk * TP_RATIO_1H
            place_orders_1h("SHORT", close_last, sl, tp1)

            state.in_trade           = True
            state.direction          = "SHORT"
            state.entry_price        = close_last
            state.sl_price           = sl
            state.tp1_price          = tp1
            state.last_signal_candle = candle_id

            send_telegram(
                f"🔴 <b>SHORT AUTO-DEMO 1H</b>\n"
                f"Entrada: {close_last:.2f}\nSL: {sl:.2f}\nTP (100%): {tp1:.2f}"
            )
            logger.info(
                f"SHORT | entry={close_last:.2f} sl={sl:.2f} tp1={tp1:.2f} "
                f"rsi={rsi_last:.1f} rsi_max12={rsi_history.max():.1f}"
            )
            return


def _manage_trade_1h(current_price: float):
    pos = get_position()
    if pos is None:
        logger.info("Posición cerrada en Binance. Reseteando estado.")
        send_telegram("🏁 <b>Operación 1H Finalizada</b>\nSalida por SL o TP.")
        state.reset()


def main_loop():
    logger.info("=" * 50)
    logger.info("Bot BTC/USDT 1H — RSI ventana 12v Activo")
    send_telegram("🤖 <b>Bot 1H (RSI fix 12v) Inicializado</b>")

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
