# ============================================================
#  config.py  –  Parámetros globales del bot
# ============================================================

# --- Binance API ---
BINANCE_API_KEY    = "TU_API_KEY_AQUI"
BINANCE_API_SECRET = "TU_API_SECRET_AQUI"
TESTNET            = True   # True = usa testnet, False = real

# --- Telegram ---
TELEGRAM_BOT_TOKEN = "TU_TOKEN_BOT_AQUI"
TELEGRAM_CHAT_ID   = "TU_CHAT_ID_AQUI"

# --- Instrumento ---
SYMBOL     = "BTCUSDT"
TIMEFRAME  = "5m"        # vela de entrada
TF_TREND   = "1h"        # temporalidad de tendencia / divergencias

# --- Indicadores ---
MA_PERIOD  = 12          # periodo de la media móvil (ajustá al de tu indicador)
RSI_PERIOD = 14
RSI_OB     = 60          # overbought
RSI_OS     = 40          # oversold

# --- Gestión de la orden ---
TRADE_USDT      = 100    # capital por operación en USDT
LEVERAGE        = 5      # apalancamiento (futures)
TP_RATIO        = 1.7    # ratio riesgo/beneficio para el 1er TP
PARTIAL_CLOSE   = 0.75   # porcentaje que se cierra en el 1er TP (75 %)

# --- Pivot detection ---
PIVOT_LOOKBACK  = 5      # velas a cada lado para confirmar un pivote

# --- Loop ---
POLL_SECONDS    = 30     # cada cuántos segundos se verifica el mercado
