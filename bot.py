# ============================================================
#  bot.py  –  Loop principal del bot
# ============================================================
import time
import logging
import pandas as pd
from config import (SYMBOL, TIMEFRAME, TF_TREND, POLL_SECONDS,
                    PIVOT_LOOKBACK, TP_RATIO)
from indicators import (calculate_ma, calculate_rsi, detect_pivot_high,
                        detect_pivot_low, rsi_leaving_extreme,
                        is_compressed_against_ma, rsi_losing_direction,
                        detect_rsi_divergence, get_trend_1h)
from exchange  import get_klines, place_limit_order, move_sl_to_entry, get_position
from notifier  import (send_telegram, msg_signal, msg_tp1_hit, msg_sl_hit,
                       msg_divergence_alert, msg_filter_skip, msg_startup)

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(message)s",
    handlers= [
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# ── Convertir klines de Binance a DataFrame ──────────────────
def klines_to_df(raw: list) -> pd.DataFrame:
    df = pd.DataFrame(raw, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qav","trades","tbav","tbqv","ignore"
    ])
    for col in ["open","high","low","close"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("open_time", inplace=True)
    return df


# ── Estado del bot ────────────────────────────────────────────
class BotState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.in_trade           = False
        self.direction          = None
        self.entry_price        = None
        self.sl_price           = None
        self.tp1_price          = None
        self.tp1_hit            = False
        self.last_signal_candle = None
        self.div_alerted        = False


state = BotState()


# ── Lógica principal por ciclo ────────────────────────────────
def run_cycle():
    # 1. Obtener datos 5m y 1H
    raw_5m = get_klines(symbol=SYMBOL, interval=TIMEFRAME, limit=200)
    raw_1h = get_klines(symbol=SYMBOL, interval=TF_TREND,  limit=100)

    if not raw_5m or not raw_1h:
        logger.warning("No se pudieron obtener klines, reintentando...")
        return

    df_5m = klines_to_df(raw_5m)
    df_1h = klines_to_df(raw_1h)

    # 2. Tendencia en 1H — filtra dirección de las operativas
    trend_1h = get_trend_1h(df_1h)
    logger.info(f"Tendencia 1H: {trend_1h}")

    # 3. Si hay posición abierta → gestionar
    if state.in_trade:
        current_price = df_5m["close"].iloc[-1]
        _manage_open_trade(df_1h, current_price)
        return

    # 4. Trabajar solo con velas cerradas
    df_closed  = df_5m.iloc[:-1]
    ma_closed  = calculate_ma(df_closed)
    rsi_closed = calculate_rsi(df_closed)

    close_prev = df_closed["close"].iloc[-1]
    ma_prev    = ma_closed.iloc[-1]
    rsi_prev   = rsi_closed.iloc[-1]

    pivot_highs = detect_pivot_high(df_closed)
    pivot_lows  = detect_pivot_low(df_closed)

    last_pivot_high = _last_pivot_price(df_closed, pivot_highs, "high")
    last_pivot_low  = _last_pivot_price(df_closed, pivot_lows,  "low")

    rsi_extreme = rsi_leaving_extreme(rsi_closed)

    # 5. Log de estado para debugging
    logger.info(
        f"Precio={close_prev:.2f} MA={ma_prev:.2f} RSI={rsi_prev:.1f} | "
        f"PivHigh={last_pivot_high} PivLow={last_pivot_low} | "
        f"RSI_oversold={rsi_extreme['from_oversold']} RSI_overbought={rsi_extreme['from_overbought']}"
    )

    # 6. Filtro de compresión (más permisivo ahora)
    if is_compressed_against_ma(df_closed, ma_closed):
        logger.info("Filtro: precio comprimido contra MA, señal ignorada.")
        return

    # 7. Filtro RSI sin direccionalidad (más permisivo ahora)
    if rsi_losing_direction(rsi_closed):
        logger.info("Filtro: RSI pierde direccionalidad, señal ignorada.")
        return

    candle_id = df_closed.index[-1]
    if candle_id == state.last_signal_candle:
        return

    # 8. SEÑAL LONG
    # Condición: tendencia 1H alcista + pivote bajo reciente +
    #            precio cierra SOBRE la MA + RSI sale de oversold
    if (trend_1h in ("bullish", "neutral")          # a favor o sin tendencia clara
            and last_pivot_low is not None
            and _recent_pivot(pivot_lows)
            and close_prev > ma_prev
            and rsi_extreme["from_oversold"]):

        entry = close_prev
        risk  = entry - last_pivot_low
        if risk <= 0 or risk > entry * 0.05:        # descarta riesgo negativo o >5%
            logger.info(f"LONG: riesgo fuera de rango ({risk:.2f}), descartado.")
            return

        sl  = last_pivot_low * 0.999
        tp1 = entry + risk * TP_RATIO

        logger.info(f"🟢 SEÑAL LONG | Entry={entry:.2f} SL={sl:.2f} TP1={tp1:.2f} | Tendencia 1H={trend_1h}")
        _open_trade("LONG", entry, sl, tp1, last_pivot_low, rsi_prev)
        state.last_signal_candle = candle_id

    # 9. SEÑAL SHORT
    # Condición: tendencia 1H bajista + pivote alto reciente +
    #            precio cierra BAJO la MA + RSI sale de overbought
    elif (trend_1h in ("bearish", "neutral")
            and last_pivot_high is not None
            and _recent_pivot(pivot_highs)
            and close_prev < ma_prev
            and rsi_extreme["from_overbought"]):

        entry = close_prev
        risk  = last_pivot_high - entry
        if risk <= 0 or risk > entry * 0.05:
            logger.info(f"SHORT: riesgo fuera de rango ({risk:.2f}), descartado.")
            return

        sl  = last_pivot_high * 1.001
        tp1 = entry - risk * TP_RATIO

        logger.info(f"🔴 SEÑAL SHORT | Entry={entry:.2f} SL={sl:.2f} TP1={tp1:.2f} | Tendencia 1H={trend_1h}")
        _open_trade("SHORT", entry, sl, tp1, last_pivot_high, rsi_prev)
        state.last_signal_candle = candle_id

    else:
        logger.info("Sin señal en este ciclo.")


# ── Helpers internos ──────────────────────────────────────────

def _open_trade(direction, entry, sl, tp1, pivot_price, rsi_val):
    try:
        place_limit_order(direction, entry, sl, tp1)
        state.in_trade    = True
        state.direction   = direction
        state.entry_price = entry
        state.sl_price    = sl
        state.tp1_price   = tp1
        state.tp1_hit     = False
        state.div_alerted = False
        send_telegram(msg_signal(direction, SYMBOL, entry, sl, tp1, pivot_price, rsi_val))
    except Exception as e:
        logger.error(f"Error abriendo trade: {e}")


def _manage_open_trade(df_1h: pd.DataFrame, current_price: float):
    pos = get_position()
    if pos is None:
        logger.info("Posición cerrada. Bot listo para nueva señal.")
        send_telegram(msg_sl_hit(SYMBOL, state.direction, state.sl_price))
        state.reset()
        return

    # TP1: cerrar 75% y mover SL a entrada
    if not state.tp1_hit:
        tp1_reached = (
            (state.direction == "LONG"  and current_price >= state.tp1_price) or
            (state.direction == "SHORT" and current_price <= state.tp1_price)
        )
        if tp1_reached:
            state.tp1_hit = True
            move_sl_to_entry(state.direction, state.entry_price)
            send_telegram(msg_tp1_hit(SYMBOL, state.direction,
                                      state.entry_price, state.tp1_price))
            logger.info("TP1 alcanzado. SL movido a entrada.")

    # Divergencia en 1H → aviso único para cerrar el 25% restante
    if state.tp1_hit and not state.div_alerted:
        div = detect_rsi_divergence(df_1h)
        if (state.direction == "LONG"  and div["bearish"]) or \
           (state.direction == "SHORT" and div["bullish"]):
            div_type = "BAJISTA" if state.direction == "LONG" else "ALCISTA"
            send_telegram(msg_divergence_alert(SYMBOL, div_type))
            logger.info(f"Divergencia {div_type} en 1H detectada.")
            state.div_alerted = True  # no repetir el aviso


def _last_pivot_price(df: pd.DataFrame, pivot_series: pd.Series,
                      col: str) -> float | None:
    idx = pivot_series[pivot_series].index
    if len(idx) == 0:
        return None
    return float(df[col].loc[idx[-1]])


def _recent_pivot(pivot_series: pd.Series, max_candles: int = 20) -> bool:
    """True si hay un pivote en las últimas max_candles velas."""
    recent = pivot_series.iloc[-max_candles:]
    return bool(recent.any())


# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("Bot BTC/USDT 5m iniciando (v2 con tendencia 1H)...")
    send_telegram(msg_startup(SYMBOL, TIMEFRAME))

    while True:
        try:
            run_cycle()
        except KeyboardInterrupt:
            logger.info("Bot detenido por el usuario.")
            break
        except Exception as e:
            logger.error(f"Error inesperado: {e}", exc_info=True)

        time.sleep(POLL_SECONDS)
