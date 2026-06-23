# ============================================================
#  bot_1h.py  –  Bot BTC/USDT en temporalidad 1H
# ============================================================
import time
import logging
import pandas as pd
from config import (SYMBOL, POLL_SECONDS, LEVERAGE, TRADE_USDT)
from indicators import (calculate_ma, detect_reversal_candle,
                        volume_confirms, get_trend_1h_ema)
from exchange  import get_klines, get_position, get_client, set_leverage, get_symbol_info, round_qty
from notifier  import send_telegram, msg_signal_1h, msg_tp1_1h, msg_tp2_1h, msg_sl_hit, msg_startup

import math

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [1H][%(levelname)s] %(message)s",
    handlers= [
        logging.FileHandler("bot_1h.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("bot_1h")

# ── Ratios 1H ────────────────────────────────────────────────
TP1_RATIO_1H = 1.5   # cierra 50% aqui
TP2_RATIO_1H = 2.5   # cierra 50% restante aqui
PARTIAL_1H   = 0.50  # 50% en cada TP

TREND_FILE_1H = "last_trend_1h_bot.txt"


# ── Estado ───────────────────────────────────────────────────
class State1H:
    def __init__(self):
        self.reset()

    def reset(self):
        self.in_trade           = False
        self.direction          = None
        self.entry_price        = None
        self.sl_price           = None
        self.tp1_price          = None
        self.tp2_price          = None
        self.tp1_hit            = False
        self.last_signal_candle = None


state = State1H()


# ── Helpers ──────────────────────────────────────────────────
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
    step = float(next(f["stepSize"] for f in info["filters"]
                      if f["filterType"] == "LOT_SIZE"))
    notional = TRADE_USDT * LEVERAGE
    qty = notional / entry_price
    return round_qty(qty, step)


def place_orders_1h(direction: str, entry: float, sl: float,
                     tp1: float, tp2: float):
    side    = "BUY"  if direction == "LONG"  else "SELL"
    sl_side = "SELL" if direction == "LONG"  else "BUY"
    tp_side = "SELL" if direction == "LONG"  else "BUY"

    qty      = calc_quantity_1h(entry)
    qty_half = round(qty * PARTIAL_1H, 3)

    client = get_client()
    set_leverage(SYMBOL, LEVERAGE)

    # Entrada limit
    entry_order = client.new_order(
        symbol=SYMBOL, side=side, type="LIMIT",
        timeInForce="GTC", quantity=qty, price=entry,
    )

    # Stop Loss
    client.new_order(
        symbol=SYMBOL, side=sl_side, type="STOP_MARKET",
        stopPrice=sl, closePosition=True,
    )

    # TP1 (50%)
    client.new_order(
        symbol=SYMBOL, side=tp_side, type="TAKE_PROFIT_MARKET",
        stopPrice=tp1, quantity=qty_half, reduceOnly=True,
    )

    logger.info(f"Órdenes 1H colocadas: entry={entry} sl={sl} tp1={tp1} tp2={tp2}")
    return entry_order


def move_sl_to_entry_1h(direction: str, entry: float):
    from exchange import cancel_all_orders, get_client
    cancel_all_orders()
    sl_side = "SELL" if direction == "LONG" else "BUY"
    get_client().new_order(
        symbol=SYMBOL, side=sl_side, type="STOP_MARKET",
        stopPrice=entry, closePosition=True,
    )
    logger.info(f"SL 1H movido a breakeven: {entry}")


def place_tp2_order(direction: str, tp2: float, qty_half: float):
    tp_side = "SELL" if direction == "LONG" else "BUY"
    get_client().new_order(
        symbol=SYMBOL, side=tp_side, type="TAKE_PROFIT_MARKET",
        stopPrice=tp2, quantity=qty_half, reduceOnly=True,
    )
    logger.info(f"TP2 1H colocado en {tp2}")


# ── Ciclo principal ───────────────────────────────────────────
def run_cycle_1h():
    raw_1h = get_klines(symbol=SYMBOL, interval="1h", limit=300)
    raw_4h = get_klines(symbol=SYMBOL, interval="4h", limit=100)

    if not raw_1h or not raw_4h:
        logger.warning("No se pudieron obtener klines 1H/4H")
        return

    df_1h = klines_to_df(raw_1h)

    # Calcular EMAs
    ema50  = df_1h["close"].ewm(span=50,  adjust=False).mean()
    ema200 = df_1h["close"].ewm(span=200, adjust=False).mean()

    # Gestión si hay trade abierto
    if state.in_trade:
        current_price = df_1h["close"].iloc[-1]
        _manage_trade_1h(current_price)
        return

    # Trabajar con velas cerradas
    df_closed = df_1h.iloc[:-1]
    ema50_c   = df_closed["close"].ewm(span=50,  adjust=False).mean()
    ema200_c  = df_closed["close"].ewm(span=200, adjust=False).mean()

    last_close  = df_closed["close"].iloc[-1]
    last_ema50  = ema50_c.iloc[-1]
    last_ema200 = ema200_c.iloc[-1]
    last_high   = df_closed["high"].iloc[-1]
    last_low    = df_closed["low"].iloc[-1]

    logger.info(
        f"Precio={last_close:.2f} EMA50={last_ema50:.2f} EMA200={last_ema200:.2f}"
    )

    candle_id = df_closed.index[-1]
    if candle_id == state.last_signal_candle:
        return

    # ── SEÑAL LONG ────────────────────────────────────────────
    # Estructura: precio > EMA50 > EMA200
    # Gatillo: precio retrocede a zona entre EMA50 y EMA200
    # Confirmación: vela reversión alcista + volumen
    trend_long = (last_close > last_ema50) and (last_ema50 > last_ema200)

    logger.info(
        f"LONG 1H → precio>EMA50={last_close > last_ema50} | "
        f"EMA50>EMA200={last_ema50 > last_ema200} | trend_long={trend_long}"
    )

    if trend_long:
        prev_lows  = df_closed["low"].iloc[-5:-1]
        near_ema50 = any(low <= last_ema50 * 1.005 for low in prev_lows)
        in_zone    = last_ema200 <= last_close <= last_ema50 * 1.01

        if near_ema50 or in_zone:
            # Condicion simplificada: vela verde + volumen
            vela_verde = last_close > df_closed["open"].iloc[-1]
            vol_ok     = volume_confirms(df_closed, multiplier=1.5)

            logger.info(
                f"LONG 1H check → trend={trend_long} near_ema50={near_ema50} "
                f"in_zone={in_zone} vela_verde={vela_verde} volume={vol_ok}"
            )

            precio_ok = last_close > last_ema50
            if vela_verde and vol_ok and precio_ok:
                entry = last_close
                sl    = df_closed["low"].iloc[-10:].min() * 0.999
                risk  = entry - sl

                if risk <= 0 or risk > entry * 0.05:
                    logger.info(f"LONG 1H: riesgo fuera de rango ({risk:.2f})")
                    return

                tp1 = entry + risk * TP1_RATIO_1H
                tp2 = entry + risk * TP2_RATIO_1H

                logger.info(f"🟢 SEÑAL LONG 1H | Entry={entry:.2f} SL={sl:.2f} TP1={tp1:.2f} TP2={tp2:.2f}")
                _open_trade_1h("LONG", entry, sl, tp1, tp2)
                state.last_signal_candle = candle_id
                return

    # ── SEÑAL SHORT ───────────────────────────────────────────
    # Estructura: precio < EMA50 < EMA200
    # Gatillo: precio rebota hasta tocar EMA50 por abajo
    # Confirmación: vela reversión bajista + volumen
    trend_short = (last_close < last_ema50) and (last_ema50 < last_ema200)

    logger.info(
        f"SHORT 1H → precio<EMA50={last_close < last_ema50} | "
        f"EMA50<EMA200={last_ema50 < last_ema200} | trend_short={trend_short}"
    )

    if trend_short:
        prev_highs = df_closed["high"].iloc[-5:-1]
        near_ema50 = any(high >= last_ema50 * 0.995 for high in prev_highs)
        in_zone    = last_ema50 * 0.99 <= last_close <= last_ema200

        if near_ema50 or in_zone:
            # Condicion simplificada: vela roja + volumen
            vela_roja = last_close < df_closed["open"].iloc[-1]
            vol_ok    = volume_confirms(df_closed, multiplier=1.5)

            logger.info(
                f"SHORT 1H check → trend={trend_short} near_ema50={near_ema50} "
                f"in_zone={in_zone} vela_roja={vela_roja} volume={vol_ok}"
            )

            precio_ok = last_close < last_ema50
            if vela_roja and vol_ok and precio_ok:
                entry = last_close
                sl    = df_closed["high"].iloc[-10:].max() * 1.001
                risk  = sl - entry

                if risk <= 0 or risk > entry * 0.05:
                    logger.info(f"SHORT 1H: riesgo fuera de rango ({risk:.2f})")
                    return

                tp1 = entry - risk * TP1_RATIO_1H
                tp2 = entry - risk * TP2_RATIO_1H

                logger.info(f"🔴 SEÑAL SHORT 1H | Entry={entry:.2f} SL={sl:.2f} TP1={tp1:.2f} TP2={tp2:.2f}")
                _open_trade_1h("SHORT", entry, sl, tp1, tp2)
                state.last_signal_candle = candle_id
                return

    logger.info("Sin señal 1H en este ciclo.")


def _open_trade_1h(direction, entry, sl, tp1, tp2):
    try:
        place_orders_1h(direction, entry, sl, tp1, tp2)
        state.in_trade    = True
        state.direction   = direction
        state.entry_price = entry
        state.sl_price    = sl
        state.tp1_price   = tp1
        state.tp2_price   = tp2
        state.tp1_hit     = False
        send_telegram(msg_signal_1h(direction, SYMBOL, entry, sl, tp1, tp2))
    except Exception as e:
        logger.error(f"Error abriendo trade 1H: {e}")


def _manage_trade_1h(current_price: float):
    pos = get_position()
    if pos is None:
        logger.info("Posición 1H cerrada. Bot listo.")
        send_telegram(msg_sl_hit(SYMBOL, state.direction, state.sl_price))
        state.reset()
        return

    # TP1: cerrar 50% y mover SL a breakeven
    if not state.tp1_hit:
        tp1_reached = (
            (state.direction == "LONG"  and current_price >= state.tp1_price) or
            (state.direction == "SHORT" and current_price <= state.tp1_price)
        )
        if tp1_reached:
            state.tp1_hit = True
            move_sl_to_entry_1h(state.direction, state.entry_price)

            # Colocar TP2
            qty = calc_quantity_1h(state.entry_price)
            qty_half = round(qty * PARTIAL_1H, 3)
            place_tp2_order(state.direction, state.tp2_price, qty_half)

            send_telegram(msg_tp1_1h(SYMBOL, state.direction,
                                      state.entry_price, state.tp1_price, state.tp2_price))
            logger.info("TP1 1H alcanzado. SL movido a entrada. TP2 colocado.")

    # TP2: posición cerrada por la orden limit
    if state.tp1_hit:
        pos_size = abs(float(pos.get("positionAmt", 0)))
        if pos_size < 0.001:
            send_telegram(msg_tp2_1h(SYMBOL, state.direction, state.tp2_price))
            logger.info("TP2 1H alcanzado. Trade completo.")
            state.reset()


# ── Entry point ───────────────────────────────────────────────
def main_loop():
    logger.info("=" * 50)
    logger.info("Bot BTC/USDT 1H iniciando...")
    send_telegram(f"🤖 <b>Bot 1H iniciado</b>\nMonitoreando <b>{SYMBOL}</b> en <b>1H</b>")

    while True:
        try:
            run_cycle_1h()
        except KeyboardInterrupt:
            logger.info("Bot 1H detenido.")
            break
        except Exception as e:
            logger.error(f"Error inesperado 1H: {e}", exc_info=True)

        time.sleep(60)


if __name__ == "__main__":
    main_loop()
