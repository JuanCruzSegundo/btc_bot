# ============================================================
#  main.py  –  Arranca ambos bots en paralelo con threading
# ============================================================
import threading
import logging
import sys
import bot

# Configuración básica de logs en consola
logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s [%(levelname)s] %(message)s",
    handlers = [
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Intentamos importar el bot de 1H de forma segura
try:
    import bot_1h
except ImportError:
    bot_1h = None

if __name__ == "__main__":
    logger.info("Iniciando hilos para bots en paralelo...")
    threads = []

    # 1. Crear e iniciar el hilo para el bot de 5m (bot.py)
    t_5m = threading.Thread(target=bot.main_loop, name="Bot_5M")
    threads.append(t_5m)
    t_5m.start()
    logger.info("-> Hilo de Bot de 5M iniciado.")

    # 2. Crear e iniciar el hilo para el bot de 1H (bot_1h.py) si está disponible
    if bot_1h and hasattr(bot_1h, "main_loop"):
        t_1h = threading.Thread(target=bot_1h.main_loop, name="Bot_1H")
        threads.append(t_1h)
        t_1h.start()
        logger.info("-> Hilo de Bot de 1H detectado e iniciado correctamente.")
    else:
        logger.warning("⚠️ bot_1h.py no encontrado o no contiene la funcion main_loop. Solo correra el bot de 5m.")

    # Mantener el proceso principal vivo esperando a los hilos de fondo
    for t in threads:
        t.join()
