# ============================================================
#  bot.py  –  Detector de señales 5m → Alerta Telegram
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

# Señal pendiente de testear (orden limit colocada, esperando que el precio vuelva)
# dict: {"direction", "entry", "sl", "tp1", "candle_id", "candles_waited"}
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


# ── Helpers de pivotes ───────────────────────────────────────
def _last_pivot_price(df, pivot_series, col):
    idx = pivot_series[pivot_series].index
    if len(idx) == 0:
        return None
    return float(df[col].loc[idx[-1]])


def _recent_pivot(pivot_series, max_candles=20):
    return bool(pivot_series.iloc[-max_candles:].any())


# ── Gestión de señal pendiente (testeo de la orden limit) ────
def _check_pending_signal(df: pd.DataFrame):
    """
    Revisa si la señal pendiente fue testeada por el precio, o si
    ya se debe cancelar porque el precio llegó al TP sin volver a tocar
    la entrada (el trade ya se completó).
    """
    global pending_signal
    if pending_signal is None:
        return

    last_high = df["high"].iloc[-1]
    last_low  = df["low"].iloc[-1]
    direction = pending_signal["direction"]
    entry     = pending_signal["entry"]
    tp1       = pending_signal["tp1"]

    # ¿El precio volvió a testear el nivel de nuestra entrada limit?
    tested = (last_low <= entry <= last_high)
    if tested:
        logger.info(f"✅ Señal {direction} TESTEADA en {entry:.2f}. Asumimos entrada ejecutada.")
        pending_signal = None
        return

    pending_signal["candles_waited"] += 1

    # Corregido: Condicional de testeo según la dirección de la señal
    if direction == "LONG":
        reached_tp = (last_high >= tp1)
    else:  # SHORT
        reached_tp = (last_low <= tp1)

    # Cancelar operativa si se completó el recorrido o excedió el tiempo límite de espera
    if reached_tp or pending_signal["candles_waited"] >= MAX_CANDLES_TO_TEST_ENTRY:
        logger.info(f"❌ Señal {direction} CANCELADA: no testeó entrada ({entry:.2f}) "
                     f"en {pending_signal['candles_waited']} velas.")
        send_telegram(msg_signal_cancelled(direction, SYMBOL, entry, pending_signal["candles_waited"]))
        pending_signal = None


# ── Ciclo principal ──────────────────────────────────────────
def run_cycle():
    global last_signal_candle, pending_signal

    # 1. Obtener velas
    raw_5m = get_klines(symbol=SYMBOL, interval=TIMEFRAME, limit=200)
    raw_1h = get_klines(symbol=SYMBOL, interval=TF_TREND,  limit=100)

    if not raw_5m or not raw_1h:
        logger.warning("No se pudieron obtener klines, reintentando...")
        return

    df_5m = klines_to_df(raw_5m)
    df_1h = klines_to_df(raw_1h)

    # 2. Tendencia 1H
    trend_1h = get_trend_1h(df_1h)
    logger.info(f"Tendencia 1H: {trend_1h}")

    # 3. Trabajar solo con velas cerradas (excluir la última que se está formando)
    df = df_5m.iloc[:-1]
    ma  = calculate_ma(df)
    rsi = calculate_rsi(df)

    close_last = df["close"].iloc[-1]
    ma_last    = ma.iloc[-1]
    rsi_last   = rsi.iloc[-1]

    # --- Lógica de Colores del Indicador Pupu ---
    # Pivote Rojo (piso técnico local) se usa para LONGs
    # Pivote Verde (techo técnico local) se usa para SHORTs
    pivot_red_longs   = detect_pivot_low(df, n=PIVOT_LOOKBACK)   
    pivot_green_shorts = detect_pivot_high(df, n=PIVOT_LOOKBACK) 
    
    last_piv_red   = _last_pivot_price(df, pivot_red_longs,  "low")
    last_piv_green = _last_pivot_price(df, pivot_green_shorts, "high")
    
    rsi_extreme   = rsi_leaving_extreme(rsi)
    divergence_5m = detect_divergence(df, lookback=DIVERGENCE_LOOKBACK_5M)

    # 4. Revisar si hay señal pendiente de testeo (evaluando sobre df_5m completo)
    _check_pending_signal(df_5m)

    # 5. Log de estado
    logger.info(
        f"Precio={close_last:.2f} | MA={ma_last:.2f} | RSI={rsi_last:.1f} | "
        f"PivRojo(L)={last_piv_red} | PivVerde(S)={last_piv_green} | "
        f"RSI_OS={rsi_extreme['from_oversold']} | RSI_OB={rsi_extreme['from_overbought']} | "
        f"DivBull={divergence_5m['bullish']} | DivBear={divergence_5m['bearish']}"
    )

    # 6. Filtro: RSI pierde direccionalidad
    if rsi_losing_direction(rsi):
        logger.info("Filtro: RSI pierde direccionalidad → señal ignorada.")
        return

    # 7. Evitar señal duplicada en la misma vela o si ya hay una pendiente de testeo
    candle_id = df.index[-1]
    if candle_id == last_signal_candle or pending_signal is not None:
        return

    # ── SEÑAL LONG ───────────────────────────────────────────
    cond_trend       = trend_1h in ("bullish", "neutral")
    cond_pivot_red   = last_piv_red is not None and _recent_pivot(pivot_red_longs)
    cond_precio_long = close_last > ma_last        
    cond_rsi_extreme = rsi_extreme["from_oversold"]
    cond_rsi_div     = divergence_5m["bullish"]
    cond_rsi_long    = cond_rsi_extreme or cond_rsi_div   # Zona extrema O divergencia

    logger.info(
        f"LONG → tendencia={cond_trend}({trend_1h}) | "
        f"pivote_rojo={cond_pivot_red}({last_piv_red}) | "
        f"precio>MA={cond_precio_long} | rsi_OS={cond_rsi_extreme} | rsi_div={cond_rsi_div}"
    )

    if cond_trend and cond_pivot_red and cond_precio_long and cond_rsi_long:
        risk = close_last - last_piv_red
        if risk <= 0 or risk > close_last * 0.05:
            logger.info(f"LONG descartado: riesgo fuera de rango ({risk:.2f})")
        else:
            sl  = last_piv_red * 0.999
            tp1 = close_last + risk * TP_RATIO
            trigger = "divergencia alcista" if cond_rsi_div else "zona extrema (sobreventa)"
            logger.info(f"✅ SEÑAL LONG | Entry={close_last:.2f} SL={sl:.2f} TP1={tp1:.2f} | Trigger={trigger}")
            send_telegram(msg_signal("LONG", SYMBOL, close_last, sl, tp1, last_piv_red,
                                      rsi_last, has_divergence=cond_rsi_div, trigger=trigger))
            last_signal_candle = candle_id
            pending_signal = {"direction": "LONG", "entry": close_last, "sl": sl,
                               "tp1": tp1, "candle_id": candle_id, "candles_waited": 0}
            return

    # ── SEÑAL SHORT ──────────────────────────────────────────
    cond_trend        = trend_1h in ("bearish", "neutral")
    cond_pivot_green  = last_piv_green is not None and _recent_pivot(pivot_green_shorts)
    cond_precio_short = close_last < ma_last        
    cond_rsi_extreme  = rsi_extreme["from_overbought"]
    cond_rsi_div      = divergence_5m["bearish"]
    cond_rsi_short    = cond_rsi_extreme or cond_rsi_div   # Zona extrema O divergencia

    logger.info(
        f"SHORT → tendencia={cond_trend}({trend_1h}) | "
        f"pivote_verde={cond_pivot_green}({last_piv_green}) | "
        f"precio<MA={cond_precio_short} | rsi_OB={cond_rsi_extreme} | rsi_div={cond_rsi_div}"
    )

    if cond_trend and cond_pivot_green and cond_precio_short and cond_rsi_short:
        risk = last_piv_green - close_last
        if risk <= 0 or risk > close_last * 0.05:
            logger.info(f"SHORT descartado: riesgo fuera de rango ({risk:.2f})")
        else:
            sl  = last_piv_green * 1.001
            tp1 = close_last - risk * TP_RATIO
            trigger = "divergencia bajista" if cond_rsi_div else "zona extrema (sobrecompra)"
            logger.info(f"✅ SEÑAL SHORT | Entry={close_last:.2f} SL={sl:.2f} TP1={tp1:.2f} | Trigger={trigger}")
            send_telegram(msg_signal("SHORT", SYMBOL, close_last, sl, tp1, last_piv_green,
                                      rsi_last, has_divergence=cond_rsi_div, trigger=trigger))
            last_signal_candle = candle_id
            pending_signal = {"direction": "SHORT", "entry": close_last, "sl": sl,
                               "tp1": tp1, "candle_id": candle_id, "candles_waited": 0}
            return

    logger.info("Sin señal en este ciclo.")


# ── Entry point ──────────────────────────────────────────────
def main_loop():
    logger.info("=" * 50)
    logger.info("Bot BTC/USDT 5m — Modo ALERTAS iniciado con TP=1.7")
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


if __name__ == "__main__":
    main_loop()
