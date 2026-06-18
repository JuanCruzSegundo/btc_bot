# ============================================================
#  exchange.py  –  Solo datos de mercado (sin ejecución)
# ============================================================
import logging
from binance.um_futures import UMFutures
from config import BINANCE_API_KEY, BINANCE_API_SECRET, TESTNET, SYMBOL

logger = logging.getLogger(__name__)

_client: UMFutures = None


def get_client() -> UMFutures:
    global _client
    if _client is None:
        if TESTNET:
            _client = UMFutures(
                key=BINANCE_API_KEY,
                secret=BINANCE_API_SECRET,
                base_url="https://testnet.binancefuture.com",
            )
        else:
            _client = UMFutures(key=BINANCE_API_KEY, secret=BINANCE_API_SECRET)
    return _client


def get_klines(symbol: str = SYMBOL, interval: str = "5m", limit: int = 200) -> list:
    """Retorna las últimas `limit` velas. No requiere autenticación."""
    try:
        # get_klines es un endpoint público, no necesita API key
        client = UMFutures()  # sin credenciales, solo datos públicos
        return client.klines(symbol=symbol, interval=interval, limit=limit)
    except Exception as e:
        logger.error(f"Error obteniendo klines: {e}")
        return []
