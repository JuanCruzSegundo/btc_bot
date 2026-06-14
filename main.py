# ============================================================
#  main.py  –  Arranca bot 5m y bot 1H en paralelo
# ============================================================
import threading
import logging
import bot
import bot_1h

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    print("Iniciando bot 5m y bot 1H en paralelo...")

    t1 = threading.Thread(target=bot.main_loop,    name="bot_5m", daemon=True)
    t2 = threading.Thread(target=bot_1h.main_loop, name="bot_1h", daemon=True)

    t1.start()
    t2.start()

    t1.join()
    t2.join()
