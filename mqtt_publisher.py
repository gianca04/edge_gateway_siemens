import time
import socket
import logging
from typing import Optional, List, Dict, Any
import pysparkplug as psp
from config import AppConfig

logger = logging.getLogger("PLC-MQTT.MQTTPublisher")


class MQTTPublisher:
    """
    Gestor de comunicación MQTT y ciclo de vida Sparkplug B (Node y Devices) en LXC.
    
    Implementa los 5 mensajes estándar de la convención Sparkplug B:
    - NBIRTH: Anuncio de vida del nodo (Gateway LXC) con IP y catálogo general.
    - NDEATH: Anuncio de muerte de nodo (LWT para caída de LXC / apagado).
    - DBIRTH: Catálogo completo de las 16 variables del PLC con tipos y unidades.
    - DDEATH: Alerta de pérdida de comunicación con el PLC Siemens (LXC vivo).
    - DDATA : Telemetría periódica o por cambio de valor (RBE / Banda Muerta).
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.bd_seq = 0
        self.seq = 0
        self.connected = False
        self.running = False
        
        # Cliente MQTT Sparkplug B subyacente
        self.client = psp.Client(
            client_id=f"{self.config.sparkplug_node_id}_client",
            username=self.config.mqtt_user,
            password=self.config.mqtt_password
        )

    def _next_seq(self) -> int:
        """Incrementa de forma cíclica el número de secuencia (0-255) según Sparkplug B."""
        self.seq = (self.seq + 1) % 256
        return self.seq

    def get_local_ip(self) -> str:
        """Obtiene la dirección IP de la interfaz local que enruta hacia el broker EMQX."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((self.config.mqtt_broker, self.config.mqtt_port))
                return s.getsockname()[0]
        except Exception:
            try:
                return socket.gethostbyname(socket.gethostname())
            except Exception:
                return "127.0.0.1"

    # ──────────────────────────────────────────────────────────────────────────
    #  1. GESTIÓN DE NODO (NBIRTH / NDEATH)
    # ──────────────────────────────────────────────────────────────────────────

    def ndeath(self, as_will: bool = False):
        """
        NDEATH (Node Death - Muerte de Nodo):
        - as_will=True: Configura el LWT en Broker EMQX para cortes de luz / caídas del LXC.
        - as_will=False: Publica NDEATH explícito en apagado ordenado del servicio.
        """
        topic = psp.Topic(
            message_type=psp.MessageType.NDEATH,
            group_id=self.config.sparkplug_group_id,
            edge_node_id=self.config.sparkplug_node_id
        )
        ts = psp.get_current_timestamp()
        bd_metric = psp.Metric(
            timestamp=ts,
            name="bdSeq",
            datatype=psp.DataType.INT64,
            value=self.bd_seq
        )
        payload = psp.NDeath(timestamp=ts, bd_seq_metric=bd_metric)
        msg = psp.Message(topic=topic, payload=payload, qos=psp.QoS.AT_LEAST_ONCE, retain=False)

        if as_will:
            self.client.set_will(msg)
            logger.info(
                f"[NDEATH LWT] Testamento configurado en Broker EMQX para topic '{topic}' "
                f"(bdSeq={self.bd_seq}). Activo ante corte de energía o caída del LXC."
            )
        else:
            if self.connected:
                self.client.publish(msg)
                logger.info(f"[NDEATH] Publicado aviso de muerte de nodo en '{topic}'. Nodo desconectado.")

    def nbirth(self):
        """
        NBIRTH (Node Birth - Nacimiento de Nodo):
        El gateway arranca y anuncia: 'Estoy vivo, tengo IP tal y reportaré los siguientes equipos'.
        """
        self.seq = 0  # NBIRTH siempre inicia la secuencia en 0
        topic = psp.Topic(
            message_type=psp.MessageType.NBIRTH,
            group_id=self.config.sparkplug_group_id,
            edge_node_id=self.config.sparkplug_node_id
        )
        ts = psp.get_current_timestamp()
        ip_addr = self.get_local_ip()
        hostname = socket.gethostname()
        equipos = list(self.config.MARCAS.keys())
        equipos_str = ", ".join(equipos)

        metrics = (
            psp.Metric(timestamp=ts, name="bdSeq", datatype=psp.DataType.INT64, value=self.bd_seq),
            psp.Metric(timestamp=ts, name="Node Control/Online", datatype=psp.DataType.BOOLEAN, value=True),
            psp.Metric(timestamp=ts, name="Properties/IP", datatype=psp.DataType.STRING, value=ip_addr),
            psp.Metric(timestamp=ts, name="Properties/Hostname", datatype=psp.DataType.STRING, value=hostname),
            psp.Metric(timestamp=ts, name="Properties/Reported_Devices", datatype=psp.DataType.STRING, value=equipos_str),
            psp.Metric(timestamp=ts, name="Properties/Device_Count", datatype=psp.DataType.INT32, value=len(equipos)),
        )

        payload = psp.NBirth(timestamp=ts, seq=0, metrics=metrics)
        msg = psp.Message(topic=topic, payload=payload, qos=psp.QoS.AT_MOST_ONCE, retain=False)
        self.client.publish(msg, include_dtypes=True)

        logger.info(f"[NBIRTH] Nacimiento de Nodo publicado en '{topic}'")
        logger.info(f"   -> Estado    : ONLINE ('Estoy vivo')")
        logger.info(f"   -> IP LXC    : {ip_addr}")
        logger.info(f"   -> Hostname  : {hostname}")
        logger.info(f"   -> Equipos ({len(equipos)}) : {equipos_str}")

    # ──────────────────────────────────────────────────────────────────────────
    #  2. GESTIÓN DE DISPOSITIVOS (DBIRTH / DDEATH / DDATA)
    # ──────────────────────────────────────────────────────────────────────────

    def dbirth(self, device_id: Optional[str] = None, initial_readings: Optional[list] = None):
        """
        DBIRTH (Device Birth - Nacimiento de Dispositivo):
        Se publica cuando el gateway logra comunicarse con el PLC Siemens.
        El PLC se presenta al sistema: 'Aquí está mi catálogo completo de 16 variables
        con sus nombres, unidades y tipos de datos'.
        """
        dev_id = device_id or self.config.sparkplug_device_id
        seq = self._next_seq()
        ts = psp.get_current_timestamp()

        # Diccionario para mapear lecturas iniciales si se proveen
        readings_map = {}
        if initial_readings:
            for info, val in initial_readings:
                key = f"{info['equipo']}/{info['var_name']}"
                readings_map[key] = val

        metrics_list = []

        # Construir catálogo de las 16 variables configuradas en tags_plc.json
        for equipo, variables in self.config.MARCAS.items():
            for var_name, config_list in variables.items():
                meta = config_list[6] if len(config_list) > 6 and isinstance(config_list[6], dict) else {}
                desc = meta.get("description", equipo)
                unit = meta.get("unit", "")
                desc_full = f"{desc} [{unit}]" if unit else desc

                dtype_str = str(config_list[3]).upper()
                metric_key = f"{equipo}/{var_name}"
                current_val = readings_map.get(metric_key, None)

                if dtype_str == "REAL":
                    psp_dtype = psp.DataType.FLOAT
                    val = float(current_val) if current_val is not None else 0.0
                elif dtype_str == "BOOL":
                    psp_dtype = psp.DataType.BOOLEAN
                    val = bool(current_val) if current_val is not None else False
                else:
                    psp_dtype = psp.DataType.INT32
                    val = int(current_val) if current_val is not None else 0

                m = psp.Metric(
                    timestamp=ts,
                    name=metric_key,
                    datatype=psp_dtype,
                    value=val,
                    metadata=psp.Metadata(description=desc_full)
                )
                metrics_list.append(m)

        topic = psp.Topic(
            message_type=psp.MessageType.DBIRTH,
            group_id=self.config.sparkplug_group_id,
            edge_node_id=self.config.sparkplug_node_id,
            device_id=dev_id
        )

        payload = psp.DBirth(timestamp=ts, seq=seq, metrics=tuple(metrics_list))
        msg = psp.Message(topic=topic, payload=payload, qos=psp.QoS.AT_MOST_ONCE, retain=False)
        self.client.publish(msg, include_dtypes=True)

        logger.info(f"[DBIRTH] Nacimiento de Dispositivo '{dev_id}' publicado en '{topic}' (seq={seq}).")
        logger.info(f"   -> Presentando catalogo de {len(metrics_list)} variables con nombres, tipos y unidades.")

    def ddeath(self, device_id: Optional[str] = None):
        """
        DDEATH (Device Death - Muerte de Dispositivo):
        Si el cable Ethernet del PLC se desconecta, pero el Gateway LXC sigue vivo.
        El gateway avisa: 'Sigo online, pero perdí comunicación con el PLC Siemens'.
        """
        dev_id = device_id or self.config.sparkplug_device_id
        seq = self._next_seq()
        ts = psp.get_current_timestamp()

        topic = psp.Topic(
            message_type=psp.MessageType.DDEATH,
            group_id=self.config.sparkplug_group_id,
            edge_node_id=self.config.sparkplug_node_id,
            device_id=dev_id
        )

        payload = psp.DDeath(timestamp=ts, seq=seq)
        msg = psp.Message(topic=topic, payload=payload, qos=psp.QoS.AT_MOST_ONCE, retain=False)
        self.client.publish(msg)

        logger.warning(
            f"[DDEATH] Muerte de Dispositivo publicada en '{topic}' (seq={seq}). "
            f"El Gateway LXC sigue online, pero se perdió comunicación con '{dev_id}'."
        )

    def ddata(self, device_id: Optional[str] = None, metrics: Optional[List[psp.Metric]] = None):
        """
        DDATA (Device Data - Datos del Dispositivo):
        En operación normal, cada segundo o cada vez que cambia un sensor.
        'Aquí van las lecturas actuales de presión, flujo, estado de válvulas, etc.'.
        """
        if not metrics:
            return

        dev_id = device_id or self.config.sparkplug_device_id
        seq = self._next_seq()
        ts = psp.get_current_timestamp()

        topic = psp.Topic(
            message_type=psp.MessageType.DDATA,
            group_id=self.config.sparkplug_group_id,
            edge_node_id=self.config.sparkplug_node_id,
            device_id=dev_id
        )

        payload = psp.DData(timestamp=ts, seq=seq, metrics=tuple(metrics))
        msg = psp.Message(topic=topic, payload=payload, qos=psp.QoS.AT_MOST_ONCE, retain=False)
        self.client.publish(msg, include_dtypes=True)

        logger.debug(f"[DDATA] Telemetria de '{dev_id}' enviada ({len(metrics)} metricas, seq={seq}).")

    # ──────────────────────────────────────────────────────────────────────────
    #  3. CONTROL DE CONEXIÓN
    # ──────────────────────────────────────────────────────────────────────────

    def start(self, on_connected_callback=None):
        """
        Inicia MQTTPublisher:
        1. Configura testamento NDEATH (LWT).
        2. Conecta asíncronamente con Broker EMQX.
        3. En on_connect emite NBIRTH y ejecuta el callback proporcionado.
        """
        logger.info(f"Iniciando MQTTPublisher hacia Broker {self.config.mqtt_broker}:{self.config.mqtt_port}...")
        self.running = True

        # 1. Configurar LWT (NDEATH)
        self.ndeath(as_will=True)

        def on_connect_cb(_client):
            self.connected = True
            logger.info("Conexion establecida con Broker EMQX.")
            # 2. Publicar NBIRTH del nodo
            self.nbirth()
            if on_connected_callback:
                on_connected_callback()

        try:
            self.client.connect(
                self.config.mqtt_broker,
                port=self.config.mqtt_port,
                keepalive=self.config.mqtt_keepalive,
                blocking=False,
                callback=on_connect_cb
            )
        except Exception as e:
            logger.error(f"Error al conectar con Broker EMQX: {e}")

    def stop(self):
        """
        Detiene la conexión:
        1. Emite NDEATH limpio para el nodo.
        2. Cierra la conexión MQTT.
        """
        logger.info("Deteniendo MQTTPublisher...")
        self.running = False
        if self.connected:
            self.ndeath(as_will=False)
            self.connected = False

        try:
            self.client.disconnect()
        except Exception as e:
            logger.warning(f"Error desconectando MQTT: {e}")
        logger.info("MQTTPublisher finalizado.")
