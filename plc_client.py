import time
import logging
import ctypes
from ctypes import POINTER, c_ubyte
import snap7
from snap7.util import get_real, get_bool

# Resolver compatibilidad de snap7
try:
    from snap7.types import S7DataItem, Areas, WordLen
except ImportError:
    from snap7.snap7types import S7DataItem, Areas, WordLen

logger = logging.getLogger("PLC-MQTT.PLCClient")


class PLCClient:
    """Clase OOP para gestionar la comunicación y lectura multi-variable con un PLC Siemens."""
    
    def __init__(self, config):
        self.config = config
        self.plc = snap7.client.Client()
        self.tags_info = []
        self.data_items = None
        self._initialize_tags_and_buffers()
        
    def _initialize_tags_and_buffers(self):
        """Pre-configura los buffers de memoria contiguos para optimizar la lectura por red."""
        for equipo, variables in self.config.MARCAS.items():
            for var_name, config_list in variables.items():
                db, byte_off, bit_off, dtype = config_list[:4]
                # Default a fall-back global interval si no existe en el JSON
                freq = config_list[4] if len(config_list) > 4 else self.config.sensor_read_interval
                deadband = config_list[5] if len(config_list) > 5 else 0.0
                
                self.tags_info.append({
                    'equipo': equipo,
                    'var_name': var_name,
                    'db': db,
                    'offset': byte_off,
                    'bit': bit_off,
                    'type': dtype,
                    'amount': 4 if dtype == 'REAL' else 1,
                    'freq': freq,
                    'deadband': deadband,
                    'last_publish': 0.0, # Para llevar control del tiempo
                    'last_value': None   # Para control de Banda Muerta (RBE)
                })
        
        items_count = len(self.tags_info)
        # Crear estructura C de S7DataItem
        self.data_items = (S7DataItem * items_count)()
        
        for i, info in enumerate(self.tags_info):
            info['buffer'] = (c_ubyte * info['amount'])()
            self.data_items[i].Area = ctypes.c_int32(Areas.DB.value)
            self.data_items[i].WordLen = ctypes.c_int32(WordLen.Byte.value)
            self.data_items[i].DBNumber = ctypes.c_int32(info['db'])
            self.data_items[i].Start = ctypes.c_int32(info['offset'])
            self.data_items[i].Amount = ctypes.c_int32(info['amount'])
            self.data_items[i].pData = ctypes.cast(ctypes.pointer(info['buffer']), POINTER(c_ubyte))

    def connect(self, is_running_func=None) -> bool:
        """Establece una conexión robusta con reintentos detallados al PLC."""
        attempt = 1
        while is_running_func is None or is_running_func():
            try:
                logger.info(f"Conectando PLC {self.config.plc_ip} - Intento {attempt}")
                self.plc.connect(self.config.plc_ip, self.config.plc_rack, self.config.plc_slot)
                if self.plc.get_connected():
                    logger.info("PLC conectado.")
                    return True
            except Exception as e:
                err_msg = str(e).encode('ascii', 'ignore').decode('ascii')
                logger.error(f"Fallo conexion PLC: {err_msg}")
                try:
                    self.plc.disconnect()
                except Exception:
                    pass
            
            # Chequear si nos pidieron apagar mientras dormimos
            for _ in range(self.config.retry_delay):
                if is_running_func is not None and not is_running_func():
                    return False
                time.sleep(1)
            
            attempt += 1
            
        return False

    def is_connected(self) -> bool:
        """Verifica si la conexión actual del PLC sigue activa."""
        return self.plc.get_connected()

    def read_all_vars(self):
        """
        Ejecuta una lectura multi-variable optimizada en un solo ciclo de red.
        Retorna una lista de tuplas (info, valor) de las variables leídas exitosamente.
        """
        ret_code, results = self.plc.read_multi_vars(self.data_items)
        readings = []
        
        for i, item in enumerate(results):
            if item.Result == 0:
                info = self.tags_info[i]
                data = bytearray(info['buffer'])
                
                if info['type'] == 'REAL':
                    valor = get_real(data, 0)
                else:
                    valor = 1.0 if get_bool(data, 0, info['bit']) else 0.0
                
                readings.append((info, valor))
            else:
                info = self.tags_info[i]
                logger.warning(f"Fallo lectura {info['equipo']}.{info['var_name']} (Res: {item.Result})")
                
        return readings

    def disconnect(self):
        """Cierra la conexión de forma segura con el PLC."""
        if self.is_connected():
            self.plc.disconnect()
            logger.info("PLC desconectado.")
