# ============================================================
#  config.py  –  Parámetros globales del bot
# ============================================================

# --- Binance API (Credenciales Demo Reales) ---
BINANCE_API_KEY    = "6CX9TYEZddRFBijMI2TMQCEOiCP72evlYvjeIlTsgkKRu36oeIqTjgDEdDFWyqjV"
BINANCE_API_SECRET = "q25LGKmq70bJf1mkTP18AnPElUOwJON7o543w6u7UBHSqohMsBTa3zIHrxkaxAog"
TESTNET            = True   # True = usa testnet demo, False = real

# --- Telegram (Canal @mibtc5min_bot) ---
TELEGRAM_BOT_TOKEN = "8963387948:AAFT3dPT-rJ9I-hsdGsidIqQMp-HROgd1e4"
TELEGRAM_CHAT_ID   = "5309144694"

# --- Instrumento ---
SYMBOL     = "BTCUSDT"
TIMEFRAME  = "5m"        # vela de entrada
TF_TREND   = "1h"        # temporalidad de tendencia

# --- Indicadores (Valores Estándar Optimizados) ---
MA_PERIOD  = 12          # periodo de la media móvil (EMA de Pupu)
RSI_PERIOD = 14
RSI_OB     = 70          # overbought (sobrecompra estricta)
RSI_OS     = 30          # oversold (sobreventa estricta)

# --- Gestión de la orden ---
TRADE_USDT      = 100    # capital por operación en USDT
LEVERAGE        = 5      # apalancamiento
TP_RATIO        = 1.7    # ratio para el 1er TP (restablecido a tu configuración original)
PARTIAL_CLOSE   = 0.75   # porcentaje que se cierra en el 1er TP (75%)

# --- Pivot detection ---
PIVOT_LOOKBACK  = 5      # velas a cada lado para confirmar un pivote

# --- Divergencia como gatillo de entrada (en 5m) ---
DIVERGENCE_LOOKBACK_5M = 20   # velas hacia atrás para buscar divergencia en 5m

# --- Cancelación de orden limit no testeada ---
MAX_CANDLES_TO_TEST_ENTRY = 10   # si en N velas no testea la entrada Y ya tocó el TP, se cancela

# --- Loop ---
POLL_SECONDS    = 30     # cada cuántos segundos se verifica el mercado

# --- Proxy para saltar geobloqueo (Railway) ---
PROXY = None
