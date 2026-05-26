import logging
from config import AppConfig
from gateway import IIoTGateway

logger = logging.getLogger("PLC-MQTT.Main")

def main():
    try:
        config = AppConfig()
        gateway = IIoTGateway(config)
        gateway.start()
    except Exception as e:
        logger.critical(f"Fallo crítico al iniciar el Edge Gateway IIoT: {e}", exc_info=True)

if __name__ == "__main__":
    main()