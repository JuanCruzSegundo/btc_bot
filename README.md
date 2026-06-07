# 🤖 Bot BTC/USDT 5m – Guía de configuración

## Estructura del proyecto

```
btc_bot/
├── config.py        ← PARÁMETROS (editá esto primero)
├── indicators.py    ← Cálculo de MA, RSI, pivotes, divergencias
├── exchange.py      ← Conexión con Binance Futures
├── notifier.py      ← Alertas por Telegram
├── bot.py           ← Loop principal
└── requirements.txt
```

---

## 1. Instalación

```bash
pip install -r requirements.txt
```

---

## 2. Configurar `config.py`

### Binance API
1. Entrá a [binance.com](https://binance.com) → Perfil → API Management
2. Creá una API key con permisos de **Futures**
3. Pegá el `API_KEY` y `API_SECRET` en `config.py`
4. Empezá con `TESTNET = True` para probar sin dinero real
   - Testnet: https://testnet.binancefuture.com

### Telegram Bot
1. Hablá con [@BotFather](https://t.me/BotFather) en Telegram
2. Creá un bot nuevo con `/newbot` → copiá el **token**
3. Hablá con [@userinfobot](https://t.me/userinfobot) → copiá tu **chat_id**
4. Pegá ambos en `config.py`

### Parámetros clave
```python
TRADE_USDT   = 100    # capital por operación
LEVERAGE     = 5      # apalancamiento
MA_PERIOD    = 12     # debe coincidir con tu indicador
TP_RATIO     = 1.7    # ratio riesgo/beneficio del TP1
PARTIAL_CLOSE= 0.75   # % que se cierra en TP1
```

---

## 3. Ejecutar el bot

```bash
python bot.py
```

El bot queda corriendo en loop. Los logs se guardan en `bot.log`.

Para correrlo en segundo plano (Linux/Mac):
```bash
nohup python bot.py &
```

---

## 4. Flujo de una operación

```
Señal detectada
    ↓
Orden LIMIT colocada en Binance (precio de cierre de vela)
    ↓
Alerta Telegram con Entry / SL / TP1
    ↓
Precio testea la zona → orden ejecutada
    ↓
Precio llega a TP1 (ratio 1.7)
    → Cierre automático del 75%
    → SL movido a breakeven (entrada)
    → Alerta Telegram
    ↓
Precio sigue corriendo (25% restante)
    ↓
Bot monitorea divergencias RSI en 1H
    → Alerta Telegram cuando detecta divergencia
    → VOS decidís cerrar el 25% restante
```

---

## 5. Filtros activos (señales que el bot descarta)

| Filtro | Descripción |
|--------|-------------|
| Compresión contra MA | Precio lleva 10+ velas pegado a la MA (<0.3% de distancia) |
| RSI sin direccionalidad | RSI lateral sin haber tocado zona extrema |
| Riesgo negativo | SL más cercano que la entrada |
| Pivote viejo | Último pivote hace más de 15 velas |

---

## 6. Lo que el bot NO automatiza (requiere tu ojo)

- Contexto de tendencia general (aunque monitorea 1H)
- Calidad subjetiva del setup
- Cierre final del 25% (te avisa, decidís vos)

---

## 7. Ajustes recomendados

- **`MA_PERIOD`**: asegurate de que coincida con la media de tu indicador "Pupupu 12 400"
- **`PIVOT_LOOKBACK`**: si el indicador marca pivotes distintos, ajustá este valor
- **`POLL_SECONDS = 30`**: el bot verifica cada 30 segundos; no hace falta bajar más
- Siempre probá primero en **testnet** varias semanas antes de pasar a real

---

## ⚠️ Aviso importante

Este bot es una herramienta de asistencia. El trading con apalancamiento
conlleva riesgo de pérdida total del capital. Usalo con responsabilidad,
con sizing conservador y siempre en testnet primero.
