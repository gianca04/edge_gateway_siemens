import time
import logging
import signal
import pysparkplug as psp
from config import AppConfig
from plc_client import PLCClient
from mqtt_publisher import MQTTPublisher
from event_detector import EventDetector

logger = logging.getLogger("PLC-MQTT.IIoTGateway")


class IIoTGateway:
    """
    Orquestador principal del Edge Gateway en entorno LXC.
    
    Gestiona el ciclo de vida completo de Sparkplug B:
    - NBIRTH: El Gateway LXC arranca y anuncia su presencia e IP.
    - NDEATH: Configurado como LWT en el broker para caídas del LXC, y emitido en apagado ordenado.
    - DBIRTH: Cuando se establece comunicación con el PLC Siemens, presenta el catálogo de 16 variables.
    - DDEATH: Si se desconecta el cable Ethernet del PLC (mientras el LXC sigue vivo), avisa la pérdida de enlace.
    - DDATA : Publicación de telemetría de las 16 variables por excepción (Deadband) o periódicamente.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.plc_client = PLCClient(config)
        self.mqtt_publisher = MQTTPublisher(config)
        self.event_detector = EventDetector(config.MARCAS)
        
        self.running = False
        self.plc_connected = False
        self.device_id = config.sparkplug_device_id

        # Capturar señales POSIX (esencial para LXC y daemon systemd)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, sig, frame):
        sig_name = "SIGINT (Ctrl+C)" if sig == signal.SIGINT else "SIGTERM (Apagado LXC / Systemd)"
        logger.info(f"Senal recibida: {sig_name}. Iniciando apagado ordenado...")
        self.stop()

    def start(self):
        """Inicia el gateway, conecta con el Broker EMQX y entra al bucle de telemetría con el PLC."""
        logger.info("Iniciando Edge Gateway Siemens Sparkplug B...")
        self.running = True

        # 1. Arrancar publicador MQTT (registra NDEATH LWT, conecta a EMQX y emite NBIRTH)
        self.mqtt_publisher.start()

        # 2. Iniciar bucle principal de adquisición y telemetría de dispositivos
        self._run_loop()

    def _run_loop(self):
        """Bucle continuo de supervisión de conexión con el PLC y despacho de DBIRTH, DDEATH y DDATA."""
        logger.info(f"Iniciando ciclo de adquisicion de datos hacia PLC {self.config.plc_ip}...")

        while self.running:
            try:
                # ── GESTIÓN DE ENLACE CON EL PLC SIEMENS ──
                if not self.plc_client.is_connected():
                    # Si previamente estaba conectado y se perdió el enlace (ej: cable Ethernet desconectado)
                    if self.plc_connected:
                        logger.warning(
                            f"[ENLACE PERDIDO] Se perdió comunicacion con el PLC Siemens {self.config.plc_ip}. "
                            f"El Gateway LXC sigue vivo. Emitiendo DDEATH para '{self.device_id}'..."
                        )
                        self.plc_connected = False
                        self.mqtt_publisher.ddeath(self.device_id)

                    logger.info(f"Intentando conectar con PLC Siemens {self.config.plc_ip}...")
                    if not self.plc_client.connect(lambda: self.running):
                        # Si no pudo conectar o se solicitó apagado durante el reintento
                        continue

                    # Conexión establecida exitosamente (o cable reconectado)
                    logger.info(f"Conectado exitosamente con PLC Siemens {self.config.plc_ip}.")
                    self.plc_connected = True

                    # Realizar lectura inicial para catálogo completo
                    initial_readings = self.plc_client.read_all_vars()

                    # Emitir DBIRTH: El PLC se presenta al sistema con su catálogo de 16 variables
                    self.mqtt_publisher.dbirth(self.device_id, initial_readings)

                # ── OPERACIÓN NORMAL: LECTURA Y PUBLICACIÓN DDATA ──
                readings = self.plc_client.read_all_vars()

                # Si no hubo lecturas pero decía estar conectado, verificar estado de conexión
                if not readings and not self.plc_client.is_connected():
                    continue

                current_time = time.time()
                ts_ms = int(current_time * 1000)
                metrics_to_publish = []

                for info, valor in readings:
                    equipo = info['equipo']
                    var_name = info['var_name']
                    dtype = info['type']
                    last_val = info['last_value']
                    metric_key = f"{equipo}/{var_name}"

                    should_publish = False

                    if dtype == 'BOOL':
                        # Detección de cambio de estado digital
                        if last_val is None or last_val != valor:
                            should_publish = True
                    else:
                        # Detección por Banda Muerta (Deadband / RBE)
                        if last_val is None or abs(last_val - valor) >= info['deadband']:
                            should_publish = True

                    # Evaluación por tiempo máximo de refresco (Sanity Check Period)
                    if not should_publish and (current_time - info['last_publish']) >= info['freq']:
                        should_publish = True

                    # ── Evaluar eventos virtuales por flanco ──
                    events = self.event_detector.evaluate(equipo, var_name, valor)
                    for ev in events:
                        logger.info(ev.message)
                        ev_metric = psp.Metric(
                            timestamp=ts_ms,
                            name=f"{ev.tag_equipo}/{ev.tag_name}",
                            datatype=psp.DataType.INT32,
                            value=ev.value
                        )
                        metrics_to_publish.append(ev_metric)

                    if should_publish:
                        info['last_value'] = valor
                        info['last_publish'] = current_time

                        if dtype == "REAL":
                            psp_dt = psp.DataType.FLOAT
                            val_to_send = float(valor)
                        elif dtype == "BOOL":
                            psp_dt = psp.DataType.BOOLEAN
                            val_to_send = bool(valor)
                        else:
                            psp_dt = psp.DataType.INT32
                            val_to_send = int(valor)

                        m = psp.Metric(
                            timestamp=ts_ms,
                            name=metric_key,
                            datatype=psp_dt,
                            value=val_to_send
                        )
                        metrics_to_publish.append(m)

                # Si hay cambios detectados, emitir DDATA
                if metrics_to_publish:
                    self.mqtt_publisher.ddata(self.device_id, metrics_to_publish)

                # Intervalo de lectura configurado (ej: 1.0s o sensor_read_interval)
                time.sleep(self.config.sensor_read_interval)

            except Exception as e:
                logger.error(f"Error en bucle de gateway: {e}")
                time.sleep(2.0)

        # Fin del bucle
        self._cleanup()

    def _cleanup(self):
        """Cierra ordenadamente los enlaces del PLC y del publicador MQTT."""
        logger.info("Realizando limpieza de recursos...")
        if self.plc_connected:
            try:
                # Avisar desconexión del dispositivo PLC
                self.mqtt_publisher.ddeath(self.device_id)
            except Exception:
                pass
            self.plc_connected = False

        if self.plc_client.is_connected():
            self.plc_client.disconnect()

        self.mqtt_publisher.stop()
        logger.info("Gateway detenido exitosamente.")

    def stop(self):
        """Detiene el bucle de ejecución."""
        self.running = False