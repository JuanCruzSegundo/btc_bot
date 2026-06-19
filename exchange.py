def get_klines(symbol: str = SYMBOL, interval: str = "5m", limit: int = 200) -> list:
    try:
        from config import TESTNET
        base_url = "https://testnet.binancefuture.com" if TESTNET else None
        
        # Define el diccionario con el proxy elegido
        mis_proxies = {
            "http": "http://tu_proxy_ip:puerto",
            "https://": "http://tu_proxy_ip:puerto"
        }
        
        # Se lo pasamos directamente al cliente de Binance
        client = UMFutures(base_url=base_url, proxies=mis_proxies) 
        return client.klines(symbol=symbol, interval=interval, limit=limit)
    except Exception as e:
        logger.error(f"Error obteniendo klines: {e}")
        return []
