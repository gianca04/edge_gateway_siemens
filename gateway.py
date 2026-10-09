import time
import logging
import signal
import threading
import pysparkplug as psp
from config import AppConfig
from plc_client import PLCClient
from mqtt_publisher import MQTTPublisher

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
        
        self.running = False
        self.plc_connected = False
        self.device_id = config.sparkplug_device_id

        # Hilo secundario para streaming en vivo
        self.live_thread = None
        self.plc_lock = threading.Lock()

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

        # 2. Iniciar hilo secundario de Live Stream (1 seg de barrido)
        self.live_thread = threading.Thread(target=self._live_stream_loop, daemon=True)
        self.live_thread.start()

        # 3. Iniciar bucle principal de adquisición y telemetría de dispositivos
        self._run_loop()

    def _live_stream_loop(self):
        """Bucle secundario que lee y publica todas las variables cada segundo sin respetar deadband."""
        logger.info("Iniciando hilo secundario de Live Stream (1s)...")
        while self.running:
            try:
                if self.plc_connected and self.plc_client.is_connected():
                    # Leer variables del PLC usando el Lock para evitar colisiones con _run_loop
                    with self.plc_lock:
                        readings = self.plc_client.read_all_vars()

                    if readings:
                        current_time = time.time()
                        ts_ms = int(current_time * 1000)
                        metrics_to_publish = []
                        
                        for info, valor in readings:
                            metric_key = f"{info['equipo']}/{info['var_name']}"
                            dtype = info['type']
                            
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
                        
                        # Publicar por MQTT en Protobuf exacto
                        if metrics_to_publish:
                            self.mqtt_publisher.publish_live(self.device_id, metrics_to_publish)
            except Exception as e:
                logger.error(f"Error en hilo de live stream: {e}")
                # Forzar desconexión si es un error de socket/TCP de Snap7
                with self.plc_lock:
                    if self.plc_connected:
                        self.plc_connected = False
                        try:
                            self.plc_client.disconnect()
                        except:
                            pass
            
            # Barrido cada 1 segundo independiente de self.config.sensor_read_interval
            time.sleep(1.0)

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
                    
                    # Realizar lectura inicial para catálogo completo (Protegido por Lock)
                    with self.plc_lock:
                        initial_readings = self.plc_client.read_all_vars()

                    self.plc_connected = True

                    # Emitir DBIRTH: El PLC se presenta al sistema con su catálogo de 16 variables
                    self.mqtt_publisher.dbirth(self.device_id, initial_readings)

                # ── OPERACIÓN NORMAL: LECTURA Y PUBLICACIÓN DDATA ──
                with self.plc_lock:
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
                # Forzar desconexión para entrar en modo reconexión
                with self.plc_lock:
                    if self.plc_connected:
                        self.plc_connected = False
                        try:
                            self.plc_client.disconnect()
                        except:
                            pass
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