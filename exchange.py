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
