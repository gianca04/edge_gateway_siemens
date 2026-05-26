import os
import json
import logging
from dotenv import load_dotenv

# 1. Configuración de Logging Profesional
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
    datefmt='%H:%M:%S'
)

class AppConfig:
    """Clase encargada de cargar y estructurar las configuraciones desde variables de entorno."""
    
    def __init__(self):
        load_dotenv()
        
        # Parámetros PLC
        self.plc_ip = os.getenv("PLC_IP")
        self.plc_rack = int(os.getenv("PLC_RACK", 0))
        self.plc_slot = int(os.getenv("PLC_SLOT", 3))
        self.retry_delay = int(os.getenv("RETRY_DELAY", 10))
        self.sensor_read_interval = float(os.getenv("SENSOR_READ_INTERVAL", 2.0))
        
        # Parámetros MQTT
        self.mqtt_broker = os.getenv("MQTT_BROKER", "localhost")
        self.mqtt_port = int(os.getenv("MQTT_PORT", 1883))
        self.mqtt_user = os.getenv("MQTT_USER")
        self.mqtt_password = os.getenv("MQTT_PASSWORD")
        
        # Parámetros de Cola / Multihilo
        self.queue_maxsize = 5000

        # Parámetros Sparkplug B
        self.sparkplug_group_id = os.getenv("SPARKPLUG_GROUP_ID", "GianCa04_IIoT")
        self.sparkplug_node_id = os.getenv("SPARKPLUG_NODE_ID", "PLC_Gateway_01")
        
        # Cargar variables del PLC desde JSON
        self.MARCAS = self._load_tags("tags_plc.json")

    def _load_tags(self, filename):
        """Carga el diccionario de tags desde un archivo JSON."""
        file_path = os.path.join(os.path.dirname(__file__), filename)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error cargando {filename}: {e}")
            return {}