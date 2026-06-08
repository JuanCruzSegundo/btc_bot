# ============================================================
#  indicators.py  –  Cálculo de indicadores técnicos
# ============================================================
import pandas as pd
import numpy as np
from config import MA_PERIOD, RSI_PERIOD, RSI_OB, RSI_OS, PIVOT_LOOKBACK


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
    """
    Determina la tendencia del gráfico horario.
    Retorna: "bullish", "bearish", o "neutral"

    Lógica:
    - Calcula EMA rápida (20) y lenta (50) en 1H
    - Si precio > EMA20 > EMA50 → tendencia alcista
    - Si precio < EMA20 < EMA50 → tendencia bajista
    - Caso contrario → neutral (no operar)
    """
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


# ── Divergencia RSI (para alertas en 1H) ────────────────────
def detect_rsi_divergence(df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    Detecta divergencias simples en las últimas `lookback` velas.
    Divergencia alcista: precio hace mínimo más bajo, RSI hace mínimo más alto.
    Divergencia bajista: precio hace máximo más alto, RSI hace máximo más bajo.
    """
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


# ── RSI saliendo de zona extrema ─────────────────────────────
def rsi_leaving_extreme(rsi: pd.Series, lookback: int = 3) -> dict:
    """
    Detecta si el RSI estuvo en zona extrema en las últimas `lookback` velas
    y ahora está saliendo. Más flexible que solo mirar 1 vela atrás.
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


# ── Filtro: compresión contra la MA ─────────────────────────
def is_compressed_against_ma(df: pd.DataFrame, ma: pd.Series,
                              lookback: int = 4, threshold: float = 0.012) -> bool:
    """
    Retorna True solo si el precio lleva TODAS las últimas `lookback` velas
    pegado a la MA dentro del threshold. Umbral más alto = menos restrictivo.
    """
    if len(df) < lookback or len(ma) < lookback:
        return False
    recent_closes = df["close"].iloc[-lookback:]
    recent_ma     = ma.iloc[-lookback:]
    diffs = abs(recent_closes - recent_ma) / recent_ma
    return bool((diffs < threshold).all())


# ── Filtro: RSI pierde direccionalidad sin llegar a extremo ──
def rsi_losing_direction(rsi: pd.Series, lookback: int = 4,
                          extreme_margin: float = 8.0) -> bool:
    """
    Retorna True si el RSI lleva `lookback` velas lateral sin tocar zona extrema.
    Umbral de pendiente más bajo = menos restrictivo.
    """
    if len(rsi) < lookback:
        return False

    recent = rsi.iloc[-lookback:]
    touched_extreme = (recent <= RSI_OS + extreme_margin).any() or \
                      (recent >= RSI_OB - extreme_margin).any()
    if touched_extreme:
        return False

    slope = np.polyfit(range(lookback), recent.values, 1)[0]
    return abs(slope) < 0.15
