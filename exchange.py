# ============================================================
#  exchange.py  –  Solo datos de mercado (con soporte de Proxy)
# ============================================================
import logging
import os
from binance.um_futures import UMFutures
from config import BINANCE_API_KEY, BINANCE_API_SECRET, TESTNET, SYMBOL

try:
    from config import PROXY
except ImportError:
    PROXY = None

logger = logging.getLogger(__name__)

_client: UMFutures = None

# Credenciales Demo de Respaldo Físico (evita fallos si config.py no se actualizó en producción)
DEMO_API_KEY = "6CX9TYEZddRFBijMI2TMQCEOiCP72evlYvjeIlTsgkKRu36oeIqTjgDEdDFWyqjV"
DEMO_API_SECRET = "q25LGKmq70bJf1mkTP18AnPElUOwJON7o543w6u7UBHSqohMsBTa3zIHrxkaxAog"


def get_client_config() -> dict:
    """Prepara la configuración del cliente de Binance."""
    config_args = {}

    # 1. Configurar la URL base según el entorno
    if TESTNET:
        config_args["base_url"] = "https://testnet.binancefuture.com"

    # 2. Resolver API Key con fallback directo
    api_key = BINANCE_API_KEY
    if not api_key or api_key == "TU_API_KEY_AQUI":
        api_key = DEMO_API_KEY

    # 3. Resolver API Secret con fallback directo
    api_secret = BINANCE_API_SECRET
    if not api_secret or api_secret == "TU_API_SECRET_AQUI":
        api_secret = DEMO_API_SECRET

    # Asignar credenciales validadas al cliente
    config_args["key"] = api_key
    config_args["secret"] = api_secret

    # 4. Configurar Proxies para saltar el geobloqueo (Error 451)
    proxy_url = PROXY or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy_url:
        config_args["proxies"] = {
            "http": proxy_url,
            "https": proxy_url
        }
        logger.info(f"[Exchange] Aplicando proxy de salida: {proxy_url}")

    return config_args


def get_client() -> UMFutures:
    """Retorna o inicializa el cliente firmado de Binance."""
    global _client
    if _client is None:
        client_args = get_client_config()
        _client = UMFutures(**client_args)
    return _client


def get_klines(symbol: str = SYMBOL, interval: str = "5m", limit: int = 200) -> list:
    """Retorna las últimas `limit` velas."""
    try:
        client_args = get_client_config()
        client = UMFutures(**client_args)
        return client.klines(symbol=symbol, interval=interval, limit=limit)
    except Exception as e:
        logger.error(f"[Exchange] Error al obtener klines: {e}")

        if "451" in str(e):
            logger.error(
                "❌ ERROR 451: Binance bloqueó la IP de este servidor. "
                "Para solucionarlo, coloca un proxy en la variable PROXY de config.py "
                "o define las variables de entorno HTTP_PROXY/HTTPS_PROXY en Railway."
            )
        return []


# ============================================================
#  Funciones de ejecución / cuenta (requieren autenticación)
# ============================================================

def set_leverage(symbol: str = SYMBOL, leverage: int = None):
    """Configura el apalancamiento para el símbolo. Si no se pasa leverage,
    usa el valor de config.LEVERAGE."""
    from config import LEVERAGE
    lev = leverage if leverage is not None else LEVERAGE
    client = get_client()
    try:
        return client.change_leverage(symbol=symbol, leverage=lev)
    except Exception as e:
        logger.error(f"[Exchange] Error configurando leverage: {e}")
        return None


def get_symbol_info(symbol: str = SYMBOL) -> dict:
    """Retorna la info de exchange_info para el símbolo (filtros LOT_SIZE, PRICE_FILTER, etc)."""
    client = get_client()
    info = client.exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            return s
    raise ValueError(f"Símbolo {symbol} no encontrado en exchange_info")


def round_qty(qty: float, step_size: float) -> float:
    """Redondea la cantidad al múltiplo de step_size más cercano hacia abajo."""
    if step_size <= 0:
        return qty
    precision = max(0, len(str(step_size).split(".")[1].rstrip("0"))) if "." in str(step_size) else 0
    factor = 10 ** precision
    return int(qty * factor) / factor


def round_price(price: float, tick_size: float) -> float:
    """Redondea el precio al múltiplo de tick_size más cercano."""
    if tick_size <= 0:
        return price
    precision = max(0, len(str(tick_size).split(".")[1].rstrip("0"))) if "." in str(tick_size) else 0
    factor = 10 ** precision
    return round(int(price * factor) / factor, precision)


def get_position(symbol: str = SYMBOL):
    """Retorna la posición abierta en el símbolo, o None si no hay posición (cantidad ~0)."""
    client = get_client()
    try:
        positions = client.get_position_risk(symbol=symbol)
        if not positions:
            return None
        pos = positions[0]
        if abs(float(pos.get("positionAmt", 0))) < 0.00001:
            return None
        return pos
    except Exception as e:
        logger.error(f"[Exchange] Error obteniendo posición: {e}")
        return None


def cancel_all_orders(symbol: str = SYMBOL) -> dict:
    """Cancela todas las órdenes abiertas (SL/TP pendientes) para el símbolo."""
    client = get_client()
    try:
        return client.cancel_open_orders(symbol=symbol)
    except Exception as e:
        logger.error(f"[Exchange] Error cancelando órdenes abiertas: {e}")
        return None


def get_open_orders(symbol: str = SYMBOL) -> list:
    client = get_client()
    try:
        return client.get_orders(symbol=symbol)
    except Exception as e:
        logger.error(f"[Exchange] Error obteniendo órdenes abiertas: {e}")
        return []
