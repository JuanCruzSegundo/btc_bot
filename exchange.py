# ============================================================
#  exchange.py  –  Interfaz con Binance Futures
# ============================================================
import logging
from binance.um_futures import UMFutures
from config import (BINANCE_API_KEY, BINANCE_API_SECRET, TESTNET,
                    SYMBOL, LEVERAGE, TRADE_USDT, PARTIAL_CLOSE)

logger = logging.getLogger(__name__)

# Cliente global
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


# ── Helpers ──────────────────────────────────────────────────

def set_leverage(symbol: str = SYMBOL, leverage: int = LEVERAGE):
    try:
        get_client().change_leverage(symbol=symbol, leverage=leverage)
        logger.info(f"Apalancamiento seteado a {leverage}x en {symbol}")
    except Exception as e:
        logger.error(f"Error seteando leverage: {e}")


def get_symbol_info(symbol: str = SYMBOL) -> dict:
    info = get_client().exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            return s
    return {}


def round_qty(qty: float, step: float) -> float:
    """Redondea qty al stepSize del instrumento."""
    import math
    precision = int(round(-math.log(step, 10)))
    return round(qty, precision)


def calc_quantity(entry_price: float) -> float:
    """Calcula la cantidad en BTC para TRADE_USDT con el leverage configurado."""
    info   = get_symbol_info()
    step   = float(next(f["stepSize"] for f in info["filters"]
                        if f["filterType"] == "LOT_SIZE"))
    notional = TRADE_USDT * LEVERAGE
    qty    = notional / entry_price
    return round_qty(qty, step)


# ── Órdenes ──────────────────────────────────────────────────

def place_limit_order(direction: str, entry: float, sl: float, tp1: float) -> dict:
    """
    Coloca:
      - 1 orden LIMIT de entrada
      - 1 orden STOP_MARKET de SL
      - 1 orden TAKE_PROFIT_MARKET de TP1 (75% de la qty)
    Retorna info de la orden de entrada.
    """
    side     = "BUY"  if direction == "LONG"  else "SELL"
    sl_side  = "SELL" if direction == "LONG"  else "BUY"
    tp_side  = "SELL" if direction == "LONG"  else "BUY"

    qty      = calc_quantity(entry)
    qty_tp1  = round(qty * PARTIAL_CLOSE, 3)

    client   = get_client()
    set_leverage()

    try:
        # Entrada limit
        entry_order = client.new_order(
            symbol      = SYMBOL,
            side        = side,
            type        = "LIMIT",
            timeInForce = "GTC",
            quantity    = qty,
            price       = entry,
        )
        logger.info(f"Orden limit {direction} colocada: {entry} x {qty} BTC")

        # Stop Loss
        client.new_order(
            symbol        = SYMBOL,
            side          = sl_side,
            type          = "STOP_MARKET",
            stopPrice     = sl,
            closePosition = True,
        )
        logger.info(f"Stop Loss colocado en {sl}")

        # TP parcial (75%)
        client.new_order(
            symbol      = SYMBOL,
            side        = tp_side,
            type        = "TAKE_PROFIT_MARKET",
            stopPrice   = tp1,
            quantity    = qty_tp1,
            reduceOnly  = True,
        )
        logger.info(f"TP1 (75%) colocado en {tp1}")

        return entry_order

    except Exception as e:
        logger.error(f"Error colocando órdenes: {e}")
        raise


def cancel_all_orders(symbol: str = SYMBOL):
    try:
        get_client().cancel_open_orders(symbol=symbol)
        logger.info("Todas las órdenes abiertas canceladas.")
    except Exception as e:
        logger.error(f"Error cancelando órdenes: {e}")


def move_sl_to_entry(direction: str, entry: float):
    """Cancela el SL actual y coloca uno nuevo en el punto de entrada (breakeven)."""
    cancel_all_orders()
    sl_side = "SELL" if direction == "LONG" else "BUY"
    try:
        get_client().new_order(
            symbol        = SYMBOL,
            side          = sl_side,
            type          = "STOP_MARKET",
            stopPrice     = entry,
            closePosition = True,
        )
        logger.info(f"SL movido a breakeven: {entry}")
    except Exception as e:
        logger.error(f"Error moviendo SL: {e}")


def get_position() -> dict | None:
    """Retorna la posición abierta de SYMBOL o None si no hay."""
    try:
        positions = get_client().get_position_risk(symbol=SYMBOL)
        for p in positions:
            if float(p.get("positionAmt", 0)) != 0:
                return p
    except Exception as e:
        logger.error(f"Error obteniendo posición: {e}")
    return None


def get_klines(symbol: str = SYMBOL, interval: str = "5m",
               limit: int = 200) -> list:
    """Retorna las últimas `limit` velas del símbolo."""
    try:
        return get_client().klines(symbol=symbol, interval=interval, limit=limit)
    except Exception as e:
        logger.error(f"Error obteniendo klines: {e}")
        return []
