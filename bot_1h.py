# ============================================================
#  bot_1h.py  –  Bot BTC/USDT en temporalidad 1H (FIXED)
# ============================================================
import time
import logging
import pandas as pd
from config import (SYMBOL, POLL_SECONDS, LEVERAGE, TRADE_USDT)
from indicators import (calculate_ma, volume_confirms)
from exchange  import (get_klines, get_position, get_client, set_leverage, 
                        get_symbol_info, round_qty, round_price, get_available_balance)
from notifier  import send_telegram, msg_signal_1h, msg_tp1_1h, msg_tp2_1h, msg_sl_hit, msg_startup

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
    balance = get_available_balance("USDT")
    usable_balance = balance * 0.98
    notional = usable_balance * LEVERAGE
    qty = notional / entry_price
    return round_qty(qty, step)

def place_orders_1h(direction: str, entry: float, sl: float, tp1: float, tp2: float):
    side    = "BUY"  if direction == "LONG"  else "SELL"
    sl_side = "SELL" if direction == "LONG"  else "BUY"
    tp_side = "SELL" if direction == "LONG"  else "BUY"

    info = get_symbol_info()
    tick = float(next(f["tickSize"] for f in info["filters"] if f["filterType"] == "PRICE_FILTER"))

    qty = calc_quantity_1h(entry)
    qty_half = round_qty(qty * PARTIAL_1H, tick)

    client = get_client()
    set_leverage(SYMBOL, LEVERAGE)

    # Entrada a mercado (MARKET) para asegurar ejecucion inmediata al cierre de vela
    entry_order = client.new_order(
        symbol=SYMBOL, side=side, type="MARKET", quantity=qty
    )

    # Redondear precios segun las reglas de tickSize de Binance
    sl_rounded = round_price(sl, tick)
    tp1_rounded = round_price(tp1, tick)

    # Stop Loss de proteccion total
    client.new_order(
        symbol=SYMBOL, side=sl_side, type="STOP_MARKET",
        stopPrice=sl_rounded, closePosition=True,
    )

    # Take Profit 1 Parcial (50%)
    client.new_order(
        symbol=SYMBOL, side=tp_side, type="TAKE_PROFIT_MARKET",
        stopPrice=tp1_rounded, quantity=qty_half, reduceOnly=True,
    )

    logger.info(f"🚀 Posición Ejecutada a Mercado: entry={entry} sl={sl_rounded} tp1={tp1_rounded}")
    return entry_order

def move_sl_to_entry_1h(direction: str, entry: float):
    from exchange import cancel_all_orders, get_client, get_symbol_info
    cancel_all_orders()
    
    info = get_symbol_info()
    tick = float(next(f["tickSize"] for f in info["filters"] if f["filterType"] == "PRICE_FILTER"))
    entry_rounded = round_price(entry, tick)
    
    sl_side = "SELL" if direction == "LONG" else "BUY"
    get_client().new_order(
        symbol=SYMBOL, side=sl_side, type="STOP_MARKET",
        stopPrice=entry_rounded, closePosition=True,
    )
    logger.info(f"SL 1H movido a breakeven: {entry_rounded}")

def place_tp2_order(direction: str, tp2: float, qty_half: float):
    from exchange import get_symbol_info
    info = get_symbol_info()
    tick = float(next(f["tickSize"] for f in info["filters"] if f["filterType"] == "PRICE_FILTER"))
    tp2_rounded = round_price(tp2, tick)
    
    tp_side = "SELL" if direction == "LONG" else "BUY"
    get_client().new_order(
        symbol=SYMBOL, side=tp_side, type="TAKE_PROFIT_MARKET",
        stopPrice=tp2_rounded, quantity=qty_half, reduceOnly=True,
    )
    logger.info(f"TP2 1H colocado en {tp2_rounded}")

def run_cycle_1h():
    raw_1h = get_klines(symbol=SYMBOL, interval="1h", limit=300)
    if not raw_1h:
        logger.warning("No se pudieron obtener klines 1H")
        return

    df_1h = klines_to_df(raw_1h)

    if state.in_trade:
        current_price = df_1h["close"].iloc[-1]
        _manage_trade_1h(current_price)
        return

    df_closed = df_1h.iloc[:-1]
    ema50_c   = df_closed["close"].ewm(span=50,  adjust=False).mean()
    ema200_c  = df_closed["close"].ewm(span=200, adjust=False).mean()

    last_close  = df_closed["close"].iloc[-1]
    last_ema50  = ema50_c.iloc[-1]
    last_ema200 = ema200_c.iloc[-1]

    logger.info(f"🕵️ Monitoreo 1H -> Precio={last_close:.2f} | EMA50={last_ema50:.2f} | EMA200={last_ema200:.2f}")

    candle_id = df_closed.index[-1]
    if candle_id == state.last_signal_candle:
        return

    # ── EVALUACIÓN SEÑAL LONG ─────────────────────────────────
    trend_long = (last_close > last_ema50) and (last_ema50 > last_ema200)
    if trend_long:
        prev_lows  = df_closed["low"].iloc[-5:-1]
        near_ema50 = any(low <= last_ema50 * 1.005 for low in prev_lows)
        in_zone    = last_ema200 <= last_close <= last_ema50 * 1.01

        if near_ema50 or in_zone:
            vela_verde = last_close > df_closed["open"].iloc[-1]
            vol_ok     = volume_confirms(df_closed, multiplier=1.5)

            if vela_verde and vol_ok:
                entry = last_close
                sl    = df_closed["low"].iloc[-10:].min() * 0.999
                risk  = entry - sl

                if 0 < risk <= entry * 0.05:
                    tp1 = entry + risk * TP1_RATIO_1H
                    tp2 = entry + risk * TP2_RATIO_1H
                    logger.info(f"🟢 GATILLO LONG 1H | Entry={entry:.2f} SL={sl:.2f}")
                    _open_trade_1h("LONG", entry, sl, tp1, tp2)
                    state.last_signal_candle = candle_id
                    return

    # ── EVALUACIÓN SEÑAL SHORT ────────────────────────────────
    trend_short = (last_close < last_ema50) and (last_ema50 < last_ema200)
    if trend_short:
        prev_highs = df_closed["high"].iloc[-5:-1]
        near_ema50 = any(high >= last_ema50 * 0.995 for high in prev_highs)
        in_zone    = last_ema50 * 0.99 <= last_close <= last_ema200

        if near_ema50 or in_zone:
            vela_roja = last_close < df_closed["open"].iloc[-1]
            vol_ok    = volume_confirms(df_closed, multiplier=1.5)

            if vela_roja and vol_ok:
                entry = last_close
                sl    = df_closed["high"].iloc[-10:].max() * 1.001
                risk  = sl - entry

                if 0 < risk <= entry * 0.05:
                    tp1 = entry - risk * TP1_RATIO_1H
                    tp2 = entry - risk * TP2_RATIO_1H
                    logger.info(f"🔴 GATILLO SHORT 1H | Entry={entry:.2f} SL={sl:.2f}")
                    _open_trade_1h("SHORT", entry, sl, tp1, tp2)
                    state.last_signal_candle = candle_id
                    return

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
        logger.error(f"Error abriendo trade 1H: {e}", exc_info=True)

def _manage_trade_1h(current_price: float):
    pos = get_position()
    if pos is None:
        logger.info("Posición 1H cerrada externa o por SL. Reseteando bot.")
        send_telegram(msg_sl_hit(SYMBOL, state.direction, state.sl_price))
        state.reset()
        return

    if not state.tp1_hit:
        tp1_reached = (
            (state.direction == "LONG"  and current_price >= state.tp1_price) or
            (state.direction == "SHORT" and current_price <= state.tp1_price)
        )
        if tp1_reached:
            state.tp1_hit = True
            move_sl_to_entry_1h(state.direction, state.entry_price)

            qty = calc_quantity_1h(state.entry_price)
            info = get_symbol_info()
            step = float(next(f["stepSize"] for f in info["filters"] if f["filterType"] == "LOT_SIZE"))
            qty_half = round_qty(qty * PARTIAL_1H, step)
            
            place_tp2_order(state.direction, state.tp2_price, qty_half)
            send_telegram(msg_tp1_1h(SYMBOL, state.direction, state.entry_price, state.tp1_price, state.tp2_price))

    if state.tp1_hit:
        pos_size = abs(float(pos.get("positionAmt", 0)))
        if pos_size < 0.001:
            send_telegram(msg_tp2_1h(SYMBOL, state.direction, state.tp2_price))
            logger.info("TP2 alcanzado de forma completa. Reseteando estado.")
            state.reset()

def main_loop():
    logger.info("=" * 50)
    logger.info("Bot BTC/USDT 1H iniciando...")
    send_telegram(f"🤖 <b>Bot 1H Iniciado Automático</b>\nMonitoreando <b>{SYMBOL}</b>")

    while True:
        try:
            run_cycle_1h()
        except KeyboardInterrupt:
            logger.info("Bot 1H detenido.")
            break
        except Exception as e:
            logger.error(f"Error inesperado 1H: {e}", exc_info=True)

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main_loop()
