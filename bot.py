# ============================================================
#  bot.py  –  Detector de señales 5m → Alerta Telegram (FIXED)
#  MODO: Solo alertas. Vos entrás manualmente.
# ============================================================
import time
import logging
import pandas as pd
from config import (SYMBOL, TIMEFRAME, TF_TREND, POLL_SECONDS,
                    PIVOT_LOOKBACK, TP_RATIO, RSI_OB, RSI_OS,
                    DIVERGENCE_LOOKBACK_5M, MAX_CANDLES_TO_TEST_ENTRY)
from indicators import (calculate_ma, calculate_rsi, detect_pivot_high,
                        detect_pivot_low, rsi_leaving_extreme,
                        rsi_losing_direction, detect_divergence,
                        get_trend_1h)
from exchange  import get_klines
from notifier  import send_telegram, msg_signal, msg_signal_cancelled, msg_startup

logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s [%(levelname)s] %(message)s",
    handlers = [
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ── Estado mínimo ─────────────────────────────────────────────
last_signal_candle = None
pending_signal = None


# ── Convertir klines a DataFrame ────────────────────────────
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


# ── Helpers de pivotes estrictos ─────────────────────────────
def _last_pivot_price(df, pivot_series, col):
    idx = pivot_series[pivot_series].index
    if len(idx) == 0:
        return None
    return float(df[col].loc[idx[-1]])


def _recent_pivot(pivot_series, max_candles=5):
    """Filtro estricto: El pivote tiene que haberse formado en las últimas 5 velas."""
    return bool(pivot_series.iloc[-max_candles:].any())


# ── Gestión de señal pendiente (testeo de la orden limit) ────
def _check_pending_signal(df_completo: pd.DataFrame):
    """
    Revisa sobre el precio en tiempo real (df_completo) si tocó la entrada 
    o si el recorrido ya se completó y fue al TP sin nosotros adentro.
    """
    global pending_signal
    if pending_signal is None:
        return

    last_high = df_completo["high"].iloc[-1]
    last_low  = df_completo["low"].iloc[-1]
    direction = pending_signal["direction"]
    entry     = pending_signal["entry"]
    tp1       = pending_signal["tp1"]

    # ¿El precio actual testeó nuestra entrada limit?
    if last_low <= entry <= last_high:
        logger.info(f"✅ Señal {direction} TESTEADA en {entry:.2f}. Orden simulada ejecutada.")
        pending_signal = None
        return

    pending_signal["candles_waited"] += 1

    # Evaluar correctamente el TP según la dirección
    if direction == "LONG":
        reached_tp = last_high >= tp1
    else:
        reached_tp = last_low <= tp1

    # Cancelar si tocó el TP o expiró por tiempo
    if reached_tp or pending_signal["candles_waited"] >= (MAX_CANDLES_TO_TEST_ENTRY * 10): 
        logger.info(f"❌ Señal {direction} CANCELADA: no testeó entrada ({entry:.2f}). Recorrido completado.")
        send_telegram(msg_signal_cancelled(direction, SYMBOL, entry, pending_signal["candles_waited"] // 10))
        pending_signal = None


# ── Ciclo principal ──────────────────────────────────────────
def run_cycle():
    global last_signal_candle, pending_signal

    # 1. Obtener velas de Binance
    raw_5m = get_klines(symbol=SYMBOL, interval=TIMEFRAME, limit=200)
    raw_1h = get_klines(symbol=SYMBOL, interval=TF_TREND,  limit=100)

    if not raw_5m or not raw_1h:
        logger.warning("No se pudieron obtener klines, reintentando...")
        return

    df_5m = klines_to_df(raw_5m)
    df_1h = klines_to_df(raw_1h)

    # 2. Controlar la orden en juego usando la vela viva actual
    _check_pending_signal(df_5m)

    # 3. FILTRO SÓLIDO: Operar la estrategia ÚNICAMENTE con velas cerradas
    df = df_5m.iloc[:-1]
    ma  = calculate_ma(df)
    rsi = calculate_rsi(df)

    close_last = df["close"].iloc[-1]
    ma_last    = ma.iloc[-1]
    rsi_last   = rsi.iloc[-1]

    # Detectar estructuras de pivotes en el histórico cerrado
    pivot_highs    = detect_pivot_high(df, n=PIVOT_LOOKBACK)
    pivot_lows     = detect_pivot_low(df, n=PIVOT_LOOKBACK)
    last_piv_high  = _last_pivot_price(df, pivot_highs, "high")
    last_piv_low   = _last_pivot_price(df, pivot_lows,  "low")
    
    rsi_extreme    = rsi_leaving_extreme(rsi)
    divergence_5m  = detect_divergence(df, lookback=DIVERGENCE_LOOKBACK_5M)
    trend_1h       = get_trend_1h(df_1h)

    logger.info(
        f"Precio={close_last:.2f} | MA12={ma_last:.2f} | RSI={rsi_last:.1f} | "
        f"PivHigh={last_piv_high} | PivLow={last_piv_low} | Trend1H={trend_1h}"
    )

    # 4. Filtro: RSI pierde direccionalidad ("escalerita de la muerte")
    if rsi_losing_direction(rsi):
        logger.info("Filtro: RSI perdiendo direccionalidad → señal descartada.")
        return

    # 5. Evitar duplicados en la misma vela o si hay una orden limit esperando testeo
    candle_id = df.index[-1]
    if candle_id == last_signal_candle or pending_signal is not None:
        return

    # ── EVALUAR SEÑAL LONG ───────────────────────────────────
    cond_trend       = trend_1h in ("bullish", "neutral")
    cond_pivlow      = last_piv_low is not None and _recent_pivot(pivot_lows, max_candles=5)
    cond_precio_long = close_last > ma_last  # Cierre por encima de la MA12
    cond_rsi_extreme = rsi_extreme["from_oversold"]
    cond_rsi_div     = divergence_5m["bullish"]
    cond_rsi_long    = cond_rsi_extreme or cond_rsi_div

    if cond_trend and cond_pivlow and cond_precio_long and cond_rsi_long:
        risk = close_last - last_piv_low
        if 0 < risk <= close_last * 0.05:
            sl  = last_piv_low * 0.999
            tp1 = close_last + risk * TP_RATIO
            trigger = "divergencia alcista" if cond_rsi_div else "zona extrema (sobreventa)"
            
            logger.info(f"✅ GATILLO LONG | Entrada={close_last:.2f} SL={sl:.2f} TP1={tp1:.2f}")
            send_telegram(msg_signal("LONG", SYMBOL, close_last, sl, tp1, last_piv_low,
                                      rsi_last, has_divergence=cond_rsi_div, trigger=trigger))
            
            last_signal_candle = candle_id
            pending_signal = {"direction": "LONG", "entry": close_last, "sl": sl,
                               "tp1": tp1, "candle_id": candle_id, "candles_waited": 0}
            return

    # ── EVALUAR SEÑAL SHORT ──────────────────────────────────
    cond_trend        = trend_1h in ("bearish", "neutral")
    cond_pivhigh      = last_piv_high is not None and _recent_pivot(pivot_highs, max_candles=5)
    cond_precio_short = close_last < ma_last  # Cierre por debajo de la MA12
    cond_rsi_extreme  = rsi_extreme["from_overbought"]
    cond_rsi_div      = divergence_5m["bearish"]
    cond_rsi_short    = cond_rsi_extreme or cond_rsi_div

    if cond_trend and cond_pivhigh and cond_precio_short and cond_rsi_short:
        risk = last_piv_high - close_last
        if 0 < risk <= close_last * 0.05:
            sl  = last_piv_high * 1.001
            tp1 = close_last - risk * TP_RATIO
            trigger = "divergencia bajista" if cond_rsi_div else "zona extrema (sobrecompra)"
            
            logger.info(f"✅ GATILLO SHORT | Entrada={close_last:.2f} SL={sl:.2f} TP1={tp1:.2f}")
            send_telegram(msg_signal("SHORT", SYMBOL, close_last, sl, tp1, last_piv_high,
                                      rsi_last, has_divergence=cond_rsi_div, trigger=trigger))
            
            last_signal_candle = candle_id
            pending_signal = {"direction": "SHORT", "entry": close_last, "sl": sl,
                               "tp1": tp1, "candle_id": candle_id, "candles_waited": 0}
            return

    logger.info("Sin señal en este ciclo.")


# ── Loop de Ejecución ────────────────────────────────────────
def main_loop():
    logger.info("=" * 50)
    logger.info("Bot BTC/USDT 5m — Modo ALERTAS con filtros corregidos activo")
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
