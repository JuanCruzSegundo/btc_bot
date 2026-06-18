# ============================================================
#  bot.py  –  Detector de señales 5m → Alerta Telegram
#  MODO: Solo alertas. Vos entrás manualmente.
# ============================================================
import time
import logging
import pandas as pd
from config import (SYMBOL, TIMEFRAME, TF_TREND, POLL_SECONDS,
                    PIVOT_LOOKBACK, TP_RATIO, RSI_OB, RSI_OS)
from indicators import (calculate_ma, calculate_rsi, detect_pivot_high,
                        detect_pivot_low, rsi_leaving_extreme,
                        rsi_losing_direction, detect_rsi_divergence,
                        get_trend_1h)
from exchange  import get_klines
from notifier  import send_telegram, msg_signal, msg_startup

logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s [%(levelname)s] %(message)s",
    handlers = [
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ── Estado mínimo (solo para evitar señales duplicadas) ──────
last_signal_candle = None


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


# ── Ciclo principal ──────────────────────────────────────────
def run_cycle():
    global last_signal_candle

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

    # 3. Trabajar solo con velas cerradas (excluir la última que está formándose)
    df = df_5m.iloc[:-1]
    ma  = calculate_ma(df)
    rsi = calculate_rsi(df)

    close_last = df["close"].iloc[-1]
    ma_last    = ma.iloc[-1]
    rsi_last   = rsi.iloc[-1]

    pivot_highs    = detect_pivot_high(df)
    pivot_lows     = detect_pivot_low(df)
    last_piv_high  = _last_pivot_price(df, pivot_highs, "high")
    last_piv_low   = _last_pivot_price(df, pivot_lows,  "low")
    rsi_extreme    = rsi_leaving_extreme(rsi)

    # 4. Log de estado
    logger.info(
        f"Precio={close_last:.2f} | MA={ma_last:.2f} | RSI={rsi_last:.1f} | "
        f"PivHigh={last_piv_high} | PivLow={last_piv_low} | "
        f"RSI_OS={rsi_extreme['from_oversold']} | RSI_OB={rsi_extreme['from_overbought']}"
    )

    # 5. Filtro: RSI pierde direccionalidad ("escalerita de la muerte")
    if rsi_losing_direction(rsi):
        logger.info("Filtro: RSI pierde direccionalidad → señal ignorada.")
        return

    # 6. Evitar señal duplicada en la misma vela
    candle_id = df.index[-1]
    if candle_id == last_signal_candle:
        return

    # ── SEÑAL LONG ───────────────────────────────────────────
    cond_trend  = trend_1h in ("bullish", "neutral")
    cond_pivlow = last_piv_low is not None and _recent_pivot(pivot_lows)
    cond_precio = close_last > ma_last        # precio cerró ENCIMA de la MA
    cond_rsi    = rsi_extreme["from_oversold"]

    logger.info(
        f"LONG → tendencia={cond_trend}({trend_1h}) | "
        f"pivot_low={cond_pivlow}({last_piv_low}) | "
        f"precio>MA={cond_precio} | rsi_OS={cond_rsi}"
    )

    if cond_trend and cond_pivlow and cond_precio and cond_rsi:
        risk = close_last - last_piv_low
        if risk <= 0 or risk > close_last * 0.05:
            logger.info(f"LONG descartado: riesgo fuera de rango ({risk:.2f})")
        else:
            sl  = last_piv_low * 0.999
            tp1 = close_last + risk * TP_RATIO
            logger.info(f"✅ SEÑAL LONG | Entry={close_last:.2f} SL={sl:.2f} TP1={tp1:.2f}")
            send_telegram(msg_signal("LONG", SYMBOL, close_last, sl, tp1, last_piv_low, rsi_last))
            last_signal_candle = candle_id
            return

    # ── SEÑAL SHORT ──────────────────────────────────────────
    cond_trend  = trend_1h in ("bearish", "neutral")
    cond_pivhigh = last_piv_high is not None and _recent_pivot(pivot_highs)
    cond_precio = close_last < ma_last        # precio cerró DEBAJO de la MA
    cond_rsi    = rsi_extreme["from_overbought"]

    logger.info(
        f"SHORT → tendencia={cond_trend}({trend_1h}) | "
        f"pivot_high={cond_pivhigh}({last_piv_high}) | "
        f"precio<MA={cond_precio} | rsi_OB={cond_rsi}"
    )

    if cond_trend and cond_pivhigh and cond_precio and cond_rsi:
        risk = last_piv_high - close_last
        if risk <= 0 or risk > close_last * 0.05:
            logger.info(f"SHORT descartado: riesgo fuera de rango ({risk:.2f})")
        else:
            sl  = last_piv_high * 1.001
            tp1 = close_last - risk * TP_RATIO
            logger.info(f"✅ SEÑAL SHORT | Entry={close_last:.2f} SL={sl:.2f} TP1={tp1:.2f}")
            send_telegram(msg_signal("SHORT", SYMBOL, close_last, sl, tp1, last_piv_high, rsi_last))
            last_signal_candle = candle_id
            return

    logger.info("Sin señal en este ciclo.")


# ── Entry point ──────────────────────────────────────────────
def main_loop():
    logger.info("=" * 50)
    logger.info("Bot BTC/USDT 5m — Modo ALERTAS iniciado")
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
