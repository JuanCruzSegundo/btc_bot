# ============================================================
#  indicators.py  –  Cálculo de indicadores técnicos
# ============================================================
import pandas as pd
import numpy as np
from config import MA_PERIOD, RSI_PERIOD, RSI_OB, RSI_OS, PIVOT_LOOKBACK, DIVERGENCE_LOOKBACK_5M


# ── Media Móvil ─────────────────────────────────────────────
def calculate_ma(df: pd.DataFrame, period: int = MA_PERIOD) -> pd.Series:
    """EMA del cierre."""
    return df["close"].ewm(span=period, adjust=False).mean()


# ── RSI ─────────────────────────────────────────────────────
def calculate_rsi(df: pd.DataFrame, period: int = RSI_PERIOD) -> pd.Series:
    delta  = df["close"].diff()
    gain   = delta.clip(lower=0)
    loss   = (-delta).clip(lower=0)
    avg_g  = gain.ewm(com=period - 1, adjust=False).mean()
    avg_l  = loss.ewm(com=period - 1, adjust=False).mean()
    rs     = avg_g / avg_l
    return 100 - (100 / (1 + rs))


# ── Pivotes ─────────────────────────────────────────────────
def detect_pivot_high(df: pd.DataFrame, n: int = PIVOT_LOOKBACK) -> pd.Series:
    highs = df["high"]
    pivot = pd.Series(False, index=df.index)
    for i in range(n, len(df) - n):
        window = highs.iloc[i - n : i + n + 1]
        if highs.iloc[i] == window.max():
            pivot.iloc[i] = True
    return pivot


def detect_pivot_low(df: pd.DataFrame, n: int = PIVOT_LOOKBACK) -> pd.Series:
    lows  = df["low"]
    pivot = pd.Series(False, index=df.index)
    for i in range(n, len(df) - n):
        window = lows.iloc[i - n : i + n + 1]
        if lows.iloc[i] == window.min():
            pivot.iloc[i] = True
    return pivot


# ── Tendencia en 1H ──────────────────────────────────────────
def get_trend_1h(df_1h: pd.DataFrame) -> str:
    """Determina la tendencia del gráfico horario."""
    if len(df_1h) < 55:
        return "neutral"

    ema20 = df_1h["close"].ewm(span=20, adjust=False).mean()
    ema50 = df_1h["close"].ewm(span=50, adjust=False).mean()

    last_close = df_1h["close"].iloc[-1]
    last_e20   = ema20.iloc[-1]
    last_e50   = ema50.iloc[-1]

    if last_close > last_e20 and last_e20 > last_e50:
        return "bullish"
    elif last_close < last_e20 and last_e20 < last_e50:
        return "bearish"
    else:
        return "neutral"


# ── Divergencia RSI genérica ─────────────────────────────────
def detect_divergence(df: pd.DataFrame, lookback: int = 20) -> dict:
    """Detecta divergencias en el RSI en base al precio."""
    rsi    = calculate_rsi(df)
    closes = df["close"]
    result = {"bullish": False, "bearish": False}

    if len(df) < lookback * 2 + 2:
        return result

    window_price = closes.iloc[-lookback:]
    window_rsi   = rsi.iloc[-lookback:]
    prev_price   = closes.iloc[-lookback * 2 : -lookback]
    prev_rsi     = rsi.iloc[-lookback * 2 : -lookback]

    if (window_price.min() < prev_price.min()) and (window_rsi.min() > prev_rsi.min()):
        result["bullish"] = True

    if (window_price.max() > prev_price.max()) and (window_rsi.max() < prev_rsi.max()):
        result["bearish"] = True

    return result


def detect_rsi_divergence(df: pd.DataFrame, lookback: int = 20) -> dict:
    return detect_divergence(df, lookback)


# ── RSI saliendo de zona extrema (FIXED) ─────────────────────
def rsi_leaving_extreme(rsi: pd.Series, lookback: int = 2) -> dict:
    """
    Detecta si el RSI estuvo por debajo de 30 o por encima de 70 
    en las últimas 2 velas y ahora regresó a la zona neutral.
    """
    if len(rsi) < lookback + 1:
        return {"from_oversold": False, "from_overbought": False}

    recent = rsi.iloc[-(lookback + 1):]
    curr   = rsi.iloc[-1]

    was_oversold   = (recent.iloc[:-1] <= RSI_OS).any()
    was_overbought = (recent.iloc[:-1] >= RSI_OB).any()

    return {
        "from_oversold":   was_oversold   and curr > RSI_OS,
        "from_overbought": was_overbought and curr < RSI_OB,
    }


# ── Filtros Secundarios ──────────────────────────────────────
def is_compressed_against_ma(df: pd.DataFrame, ma: pd.Series,
                              lookback: int = 4, threshold: float = 0.012) -> bool:
    if len(df) < lookback or len(ma) < lookback:
        return False
    recent_closes = df["close"].iloc[-lookback:]
    recent_ma     = ma.iloc[-lookback:]
    diffs = abs(recent_closes - recent_ma) / recent_ma
    return bool((diffs < threshold).all())


def rsi_losing_direction(rsi: pd.Series, lookback: int = 4,
                          extreme_margin: float = 8.0) -> bool:
    if len(rsi) < lookback:
        return False

    recent = rsi.iloc[-lookback:]
    touched_extreme = (recent <= RSI_OS + extreme_margin).any() or \
                      (recent >= RSI_OB - extreme_margin).any()
    if touched_extreme:
        return False

    slope = np.polyfit(range(lookback), recent.values, 1)[0]
    return abs(slope) < 0.15


def detect_reversal_candle(df: pd.DataFrame, direction: str) -> bool:
    if len(df) < 2:
        return False

    o  = df["open"].iloc[-1]
    h  = df["high"].iloc[-1]
    l  = df["low"].iloc[-1]
    c  = df["close"].iloc[-1]
    body = abs(c - o)
    candle_range = h - l

    if candle_range == 0:
        return False

    if direction == "bullish":
        lower_wick = min(o, c) - l
        upper_wick = h - max(o, c)
        hammer = (lower_wick >= 2 * body) and (upper_wick <= body) and (c > o)

        prev_o = df["open"].iloc[-2]
        prev_c = df["close"].iloc[-2]
        engulfing = (c > o) and (prev_c < prev_o) and (c > prev_o) and (o < prev_c)

        return hammer or engulfing

    elif direction == "bearish":
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        shooting_star = (upper_wick >= 2 * body) and (lower_wick <= body) and (c < o)

        prev_o = df["open"].iloc[-2]
        prev_c = df["close"].iloc[-2]
        engulfing = (c < o) and (prev_c > prev_o) and (c < prev_o) and (o > prev_c)

        return shooting_star or engulfing

    return False


def volume_confirms(df: pd.DataFrame, lookback: int = 4, multiplier: float = 1.5) -> bool:
    if len(df) < lookback + 1:
        return False

    last_vol = df["volume"].iloc[-1]
    avg_vol  = df["volume"].iloc[-(lookback + 1):-1].mean()

    return last_vol > avg_vol * multiplier


def get_trend_1h_ema(df_1h: pd.DataFrame) -> str:
    if len(df_1h) < 205:
        return "neutral"

    ema50  = df_1h["close"].ewm(span=50,  adjust=False).mean()
    ema200 = df_1h["close"].ewm(span=200, adjust=False).mean()

    last_close = df_1h["close"].iloc[-1]
    e50        = ema50.iloc[-1]
    e200       = ema200.iloc[-1]

    if last_close > e50 and e50 > e200:
        return "bullish"
    elif last_close < e50 and e50 < e200:
        return "bearish"
    else:
        return "neutral"
