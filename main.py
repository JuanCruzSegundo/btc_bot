# ============================================================
#  main.py  –  Arranca el bot de 1H (5m pausado por decisión del usuario)
#  CON SOPORTE DE HEALTH CHECK PARA RAILWAY
# ============================================================
import logging
import sys
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import bot_1h

# Configuración básica de logs en consola
logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s [%(levelname)s] %(message)s",
    handlers = [
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# --- Servidor de Health Check para Railway ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        # Silenciar logs de peticiones HTTP del Health Check para no ensuciar la consola
        return

    def log_error(self, format, *args):
        # Silenciar logs de errores de conexión rutinarios del Health Check
        return

def start_health_check_server():
    """Levanta un servidor web básico en el puerto que pide Railway."""
    port = int(os.environ.get("PORT", 8080))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logger.info(f"⚡ Servidor de Health Check levantado con éxito en el puerto {port}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Error al iniciar servidor de Health Check: {e}")

if __name__ == "__main__":
    logger.info("Iniciando bot de BTC en temporalidad de 1 HORA (5m pausado)...")

    # 1. Levantar servidor en un hilo paralelo ("daemon" para que muera con el principal)
    health_thread = threading.Thread(target=start_health_check_server, daemon=True)
    health_thread.start()

    # 2. Iniciar el loop de análisis técnico 1H
    try:
        bot_1h.main_loop()
    except KeyboardInterrupt:
        logger.info("Proceso terminado por el usuario.")
    except Exception as e:
        logger.critical(f"Fallo crítico al iniciar el bot: {e}", exc_info=True)
