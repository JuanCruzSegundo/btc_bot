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
                        detect_rsi_divergence)
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
        self.in_trade      = False
        self.direction     = None   # "LONG" | "SHORT"
        self.entry_price   = None
        self.sl_price      = None
        self.tp1_price     = None
        self.tp1_hit       = False  # ya se cerró el 75%
        self.last_signal_candle = None


state = BotState()


# ── Lógica principal por ciclo ────────────────────────────────
def run_cycle():
    # 1. Obtener datos
    raw_5m = get_klines(symbol=SYMBOL, interval=TIMEFRAME, limit=200)
    raw_1h = get_klines(symbol=SYMBOL, interval=TF_TREND,  limit=100)

    if not raw_5m or not raw_1h:
        logger.warning("No se pudieron obtener klines, reintentando...")
        return

    df_5m = klines_to_df(raw_5m)
    df_1h = klines_to_df(raw_1h)

    # 2. Calcular indicadores en 5m
    ma  = calculate_ma(df_5m)
    rsi = calculate_rsi(df_5m)

    last_close = df_5m["close"].iloc[-1]
    last_ma    = ma.iloc[-1]
    last_rsi   = rsi.iloc[-1]

    # 3. Si hay posición abierta → gestionar
    if state.in_trade:
        _manage_open_trade(df_1h, last_close)
        return

    # 4. Buscar nueva señal (solo en vela cerrada → iloc[-2])
    df_closed = df_5m.iloc[:-1]   # excluye vela en formación
    ma_closed = calculate_ma(df_closed)
    rsi_closed = calculate_rsi(df_closed)

    pivot_highs = detect_pivot_high(df_closed)
    pivot_lows  = detect_pivot_low(df_closed)

    last_pivot_high = _last_pivot_price(df_closed, pivot_highs, "high")
    last_pivot_low  = _last_pivot_price(df_closed, pivot_lows,  "low")

    close_prev = df_closed["close"].iloc[-1]
    ma_prev    = ma_closed.iloc[-1]
    rsi_prev   = rsi_closed.iloc[-1]

    rsi_extreme = rsi_leaving_extreme(rsi_closed)

    # 5. Filtros (descartar señal si aplican)
    if is_compressed_against_ma(df_closed, ma_closed):
        logger.info("Filtro: precio comprimido contra MA, señal ignorada.")
        return

    if rsi_losing_direction(rsi_closed):
        logger.info("Filtro: RSI pierde direccionalidad sin extremo, señal ignorada.")
        return

    # 6. Señal SHORT
    if (last_pivot_high is not None
            and _recent_pivot(pivot_highs)
            and close_prev < ma_prev
            and rsi_extreme["from_overbought"]):

        candle_id = df_closed.index[-1]
        if candle_id == state.last_signal_candle:
            return  # evitar doble entrada en la misma vela

        entry = close_prev          # limit en el cierre de la vela
        risk  = last_pivot_high - entry
        if risk <= 0:
            logger.info("SHORT: riesgo negativo, señal descartada.")
            return

        sl   = last_pivot_high * 1.001   # pequeño buffer encima del pivote
        tp1  = entry - risk * TP_RATIO

        logger.info(f"🔴 SEÑAL SHORT | Entry={entry:.2f} SL={sl:.2f} TP1={tp1:.2f}")
        _open_trade("SHORT", entry, sl, tp1, last_pivot_high, rsi_prev)
        state.last_signal_candle = candle_id

    # 7. Señal LONG
    elif (last_pivot_low is not None
            and _recent_pivot(pivot_lows)
            and close_prev > ma_prev
            and rsi_extreme["from_oversold"]):

        candle_id = df_closed.index[-1]
        if candle_id == state.last_signal_candle:
            return

        entry = close_prev
        risk  = entry - last_pivot_low
        if risk <= 0:
            logger.info("LONG: riesgo negativo, señal descartada.")
            return

        sl   = last_pivot_low * 0.999    # pequeño buffer debajo del pivote
        tp1  = entry + risk * TP_RATIO

        logger.info(f"🟢 SEÑAL LONG | Entry={entry:.2f} SL={sl:.2f} TP1={tp1:.2f}")
        _open_trade("LONG", entry, sl, tp1, last_pivot_low, rsi_prev)
        state.last_signal_candle = candle_id


# ── Helpers internos ──────────────────────────────────────────

def _open_trade(direction, entry, sl, tp1, pivot_price, rsi_val):
    """Coloca las órdenes y actualiza el estado."""
    try:
        place_limit_order(direction, entry, sl, tp1)
        state.in_trade    = True
        state.direction   = direction
        state.entry_price = entry
        state.sl_price    = sl
        state.tp1_price   = tp1
        state.tp1_hit     = False
        send_telegram(msg_signal(direction, SYMBOL, entry, sl, tp1, pivot_price, rsi_val))
    except Exception as e:
        logger.error(f"Error abriendo trade: {e}")


def _manage_open_trade(df_1h: pd.DataFrame, current_price: float):
    """
    Gestión mientras hay posición abierta:
    - Detecta si TP1 fue alcanzado (precio tocó tp1)
    - Detecta si SL fue tocado (precio cruzó sl)
    - Detecta divergencias en 1H para aviso de cierre
    """
    pos = get_position()
    if pos is None:
        # No hay posición → fue cerrada por SL o TP
        logger.info("Posición cerrada externamente. Bot listo para nueva señal.")
        send_telegram(msg_sl_hit(SYMBOL, state.direction, state.sl_price))
        state.reset()
        return

    # TP1: mover SL a breakeven
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

    # Divergencia en 1H → aviso (no cierra automáticamente)
    if state.tp1_hit:
        div = detect_rsi_divergence(df_1h)
        if (state.direction == "LONG"  and div["bearish"]) or \
           (state.direction == "SHORT" and div["bullish"]):
            div_type = "BAJISTA" if state.direction == "LONG" else "ALCISTA"
            send_telegram(msg_divergence_alert(SYMBOL, div_type))
            logger.info(f"Divergencia {div_type} detectada en 1H.")
            # Reseteamos para no repetir el aviso en cada ciclo
            state.tp1_hit = False


def _last_pivot_price(df: pd.DataFrame, pivot_series: pd.Series,
                      col: str) -> float | None:
    """Precio del último pivote detectado."""
    idx = pivot_series[pivot_series].index
    if len(idx) == 0:
        return None
    return df[col].loc[idx[-1]]


def _recent_pivot(pivot_series: pd.Series, max_candles: int = 15) -> bool:
    """True si hay un pivote en las últimas max_candles velas."""
    recent = pivot_series.iloc[-max_candles:]
    return recent.any()


# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info(f"Bot BTC/USDT 5m iniciando...")
    send_telegram(msg_startup(SYMBOL, TIMEFRAME))

    while True:
        try:
            run_cycle()
        except KeyboardInterrupt:
            logger.info("Bot detenido por el usuario.")
            break
        except Exception as e:
            logger.error(f"Error inesperado en ciclo: {e}", exc_info=True)

        time.sleep(POLL_SECONDS)
