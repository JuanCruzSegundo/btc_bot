# ============================================================
#  notifier.py  –  Alertas por Telegram
# ============================================================
import requests
import logging
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def send_telegram(message: str) -> bool:
    """Envía un mensaje de texto a Telegram. Retorna True si fue exitoso."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       message,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"[Telegram] Error al enviar mensaje: {e}")
        return False


# ── Plantillas de mensajes ───────────────────────────────────

def msg_signal(direction: str, symbol: str, entry: float,
               sl: float, tp1: float, pivot_price: float, rsi_val: float) -> str:
    icon   = "🟢" if direction == "LONG" else "🔴"
    sl_dir = "encima" if direction == "SHORT" else "debajo"
    return (
        f"{icon} <b>SEÑAL {direction} – {symbol}</b>\n\n"
        f"📌 <b>Entrada (limit):</b> {entry:.2f} USDT\n"
        f"🛑 <b>Stop Loss ({sl_dir} del pivote):</b> {sl:.2f} USDT\n"
        f"🎯 <b>TP1 (75% – ratio 1.7):</b> {tp1:.2f} USDT\n\n"
        f"📊 Pivote detectado en: {pivot_price:.2f}\n"
        f"📉 RSI actual: {rsi_val:.1f}\n\n"
        f"⚠️ <i>Orden limit colocada. El precio debe volver a testear la zona.</i>\n"
        f"👁 Revisá divergencias en 1H para el cierre final."
    )


def msg_tp1_hit(symbol: str, direction: str, entry: float, tp1: float) -> str:
    return (
        f"✅ <b>TP1 alcanzado – {symbol} {direction}</b>\n\n"
        f"Se cerró el <b>75%</b> de la posición en {tp1:.2f} USDT\n"
        f"🔒 Stop Loss movido al punto de entrada: {entry:.2f} USDT\n\n"
        f"🏃 Dejando correr el 25% restante.\n"
        f"👁 Monitoreá divergencias RSI en <b>1H</b> para cerrar el resto."
    )


def msg_sl_hit(symbol: str, direction: str, sl: float) -> str:
    return (
        f"🛑 <b>Stop Loss tocado – {symbol} {direction}</b>\n\n"
        f"Posición cerrada en {sl:.2f} USDT.\n"
        f"El bot quedó listo para la próxima señal."
    )


def msg_divergence_alert(symbol: str, div_type: str) -> str:
    return (
        f"⚡ <b>DIVERGENCIA {div_type} detectada en 1H – {symbol}</b>\n\n"
        f"Posible momento de cerrar el 25% restante de la posición.\n"
        f"<i>Revisá el gráfico y decidí vos el cierre.</i>"
    )


def msg_filter_skip(symbol: str, reason: str) -> str:
    return (
        f"⚠️ <b>Señal descartada – {symbol}</b>\n"
        f"Motivo: {reason}"
    )


def msg_startup(symbol: str, timeframe: str) -> str:
    return (
        f"🤖 <b>Bot iniciado</b>\n"
        f"Monitoreando <b>{symbol}</b> en <b>{timeframe}</b>\n"
        f"Filtros activos ✅ | Gestión automática ✅"
    )


# ── Mensajes 1H ──────────────────────────────────────────────

def msg_signal_1h(direction: str, symbol: str, entry: float,
                   sl: float, tp1: float, tp2: float) -> str:
    icon = "🟢" if direction == "LONG" else "🔴"
    return (
        f"{icon} <b>SEÑAL {direction} 1H – {symbol}</b>\n\n"
        f"📌 <b>Entrada (limit):</b> {entry:.2f} USDT\n"
        f"🛑 <b>Stop Loss:</b> {sl:.2f} USDT\n"
        f"🎯 <b>TP1 (50% – ratio 1.5):</b> {tp1:.2f} USDT\n"
        f"🎯 <b>TP2 (50% – ratio 2.5):</b> {tp2:.2f} USDT\n\n"
        f"⏱ <i>Temporalidad: 1H | Gestión por tercios</i>\n"
        f"⚠️ <i>Orden limit colocada. El precio debe retroceder a la zona.</i>"
    )


def msg_tp1_1h(symbol: str, direction: str, entry: float,
                tp1: float, tp2: float) -> str:
    return (
        f"✅ <b>TP1 alcanzado – {symbol} {direction} 1H</b>\n\n"
        f"Se cerró el <b>50%</b> de la posición en {tp1:.2f} USDT\n"
        f"🔒 Stop Loss movido a breakeven: {entry:.2f} USDT\n"
        f"🎯 TP2 colocado en: {tp2:.2f} USDT\n\n"
        f"🏃 Dejando correr el 50% restante hacia TP2."
    )


def msg_tp2_1h(symbol: str, direction: str, tp2: float) -> str:
    return (
        f"🏆 <b>TP2 alcanzado – {symbol} {direction} 1H</b>\n\n"
        f"Se cerró el <b>50% restante</b> en {tp2:.2f} USDT\n"
        f"✅ Trade completo. Bot listo para la próxima señal."
    )
