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
    """
    Devuelve una Serie booleana: True en la vela que es pivote alto.
    Un pivote alto: su 'high' es mayor que las n velas previas Y las n siguientes.
    Solo se puede confirmar con n velas de lag (esperamos que se formen las siguientes).
    """
    highs = df["high"]
    pivot = pd.Series(False, index=df.index)
    for i in range(n, len(df) - n):
        window = highs.iloc[i - n : i + n + 1]
        if highs.iloc[i] == window.max():
            pivot.iloc[i] = True
    return pivot


def detect_pivot_low(df: pd.DataFrame, n: int = PIVOT_LOOKBACK) -> pd.Series:
    """Devuelve una Serie booleana: True en la vela que es pivote bajo."""
    lows  = df["low"]
    pivot = pd.Series(False, index=df.index)
    for i in range(n, len(df) - n):
        window = lows.iloc[i - n : i + n + 1]
        if lows.iloc[i] == window.min():
            pivot.iloc[i] = True
    return pivot


# ── Divergencia RSI (para alertas en 1H) ────────────────────
def detect_rsi_divergence(df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    Detecta divergencias simples en las últimas `lookback` velas.
    Retorna: {"bullish": bool, "bearish": bool}

    Divergencia alcista: precio hace mínimo más bajo, RSI hace mínimo más alto.
    Divergencia bajista: precio hace máximo más alto, RSI hace máximo más bajo.
    """
    rsi    = calculate_rsi(df)
    closes = df["close"]
    result = {"bullish": False, "bearish": False}

    if len(df) < lookback + 2:
        return result

    window_price = closes.iloc[-lookback:]
    window_rsi   = rsi.iloc[-lookback:]

    # Índices de mínimos / máximos locales simples
    price_min_idx = window_price.idxmin()
    price_max_idx = window_price.idxmax()
    rsi_min_idx   = window_rsi.idxmin()
    rsi_max_idx   = window_rsi.idxmax()

    prev_price_min = closes.iloc[-lookback * 2 : -lookback].min()
    prev_price_max = closes.iloc[-lookback * 2 : -lookback].max()
    prev_rsi_min   = rsi.iloc[-lookback * 2 : -lookback].min()
    prev_rsi_max   = rsi.iloc[-lookback * 2 : -lookback].max()

    # Alcista
    if (window_price.min() < prev_price_min) and (window_rsi.min() > prev_rsi_min):
        result["bullish"] = True

    # Bajista
    if (window_price.max() > prev_price_max) and (window_rsi.max() < prev_rsi_max):
        result["bearish"] = True

    return result


# ── RSI saliendo de zona extrema ─────────────────────────────
def rsi_leaving_extreme(rsi: pd.Series) -> dict:
    """
    Detecta si el RSI estaba en zona extrema y acaba de salir.
    Retorna: {"from_oversold": bool, "from_overbought": bool}
    """
    if len(rsi) < 2:
        return {"from_oversold": False, "from_overbought": False}

    prev, curr = rsi.iloc[-2], rsi.iloc[-1]
    return {
        "from_oversold":   (prev <= RSI_OS) and (curr > RSI_OS),
        "from_overbought": (prev >= RSI_OB) and (curr < RSI_OB),
    }


# ── Filtro: compresión contra la MA ─────────────────────────
def is_compressed_against_ma(df: pd.DataFrame, ma: pd.Series,
                              lookback: int = 10, threshold: float = 0.003) -> bool:
    """
    Retorna True si el precio lleva `lookback` velas comprimido muy cerca
    de la MA (dentro de `threshold` = 0.3 % por defecto).
    Señal de peligro: probable continuación, no rebote.
    """
    recent_closes = df["close"].iloc[-lookback:]
    recent_ma     = ma.iloc[-lookback:]
    diffs = abs(recent_closes - recent_ma) / recent_ma
    return bool((diffs < threshold).all())


# ── Filtro: RSI pierde direccionalidad sin llegar a extremo ──
def rsi_losing_direction(rsi: pd.Series, lookback: int = 6,
                          extreme_margin: float = 5.0) -> bool:
    """
    Retorna True si el RSI lleva `lookback` velas lateralizando
    (pendiente cercana a 0) sin haber tocado zona extrema.
    """
    if len(rsi) < lookback:
        return False

    recent = rsi.iloc[-lookback:]
    touched_extreme = (recent <= RSI_OS + extreme_margin).any() or \
                      (recent >= RSI_OB - extreme_margin).any()
    if touched_extreme:
        return False  # sí llegó a zona extrema, filtro no aplica

    slope = np.polyfit(range(lookback), recent.values, 1)[0]
    return abs(slope) < 0.3   # pendiente casi plana = sin direccionalidad
