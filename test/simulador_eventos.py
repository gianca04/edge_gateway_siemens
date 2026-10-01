"""
test/simulador_eventos.py — Simulador de eventos de actuadores

Publica directamente al broker EMQX usando Sparkplug B,
simulando cambios de estado en todos los actuadores BOOL del sistema.

Secuencia simulada (bucle continuo):
  1. Bomba enciende → válvulas 1 y 2 abren → compresor enciende → motor enciende
  2. Espera 5 s con todo activo
  3. Motor apaga → compresor apaga → válvulas cierran → bomba apaga
  4. Espera 5 s en reposo → vuelve al inicio

Todos los cambios pasan por el EventDetector real → los eventos
(rising_edge / falling_edge) se publican como métricas Sparkplug B.

Uso:
    python test/simulador_eventos.py
"""

import os
import sys
import time
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import AppConfig
from mqtt_publisher import MQTTPublisher
from event_detector import EventDetector
import pysparkplug as psp

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("Simulador")

# ── Actuadores simulados (equipo, var_name, tipo PLC) ─────────────────────────
ACTUADORES = [
    ("Bomba_Agua_001_STATUS", "State"),
    ("Val_001",               "State"),
    ("Val_002",               "State"),
    ("Val_003",               "State"),
    ("Val_004",               "State"),
    ("Mot_Comp_001",          "State"),
    ("MOTOR_01",              "Running"),
]

# ── Secuencias: lista de (equipo, var_name, nuevo_valor, descripcion, pausa_s) ─
SECUENCIAS = [
    # ── ARRANQUE ──────────────────────────────────────────────────────────────
    ("Bomba_Agua_001_STATUS", "State", True,  "Bomba de agua ENCIENDE",         1.0),
    ("Val_001",               "State", True,  "Valvula 1 ABRE",                 0.5),
    ("Val_002",               "State", True,  "Valvula 2 ABRE",                 0.5),
    ("Mot_Comp_001",          "State", True,  "Compresor ENCIENDE",             1.0),
    ("Val_003",               "State", True,  "Valvula 3 ABRE (presion ok)",    0.5),
    ("MOTOR_01",              "Running", True,"Motor de laboratorio ENCIENDE",  1.0),
    ("Val_004",               "State", True,  "Valvula 4 ABRE",                 0.5),

    # ── OPERACION ─────────────────────────────────────────────────────────────
    (None, None, None, "--- Sistema operando a plena carga (5s) ---",           5.0),

    # ── PARADA ────────────────────────────────────────────────────────────────
    ("MOTOR_01",              "Running", False,"Motor de laboratorio APAGA",    1.0),
    ("Val_004",               "State", False, "Valvula 4 CIERRA",               0.5),
    ("Val_003",               "State", False, "Valvula 3 CIERRA",               0.5),
    ("Mot_Comp_001",          "State", False, "Compresor APAGA",                1.0),
    ("Val_002",               "State", False, "Valvula 2 CIERRA",               0.5),
    ("Val_001",               "State", False, "Valvula 1 CIERRA",               0.5),
    ("Bomba_Agua_001_STATUS", "State", False, "Bomba de agua APAGA",            1.0),

    # ── REPOSO ────────────────────────────────────────────────────────────────
    (None, None, None, "--- Sistema en reposo (5s) ---",                        5.0),
]


def _make_bool_metric(ts_ms: int, equipo: str, var_name: str, value: bool) -> psp.Metric:
    return psp.Metric(
        timestamp=ts_ms,
        name=f"{equipo}/{var_name}",
        datatype=psp.DataType.BOOLEAN,
        value=value
    )

def _make_event_metric(ts_ms: int, equipo: str, tag_name: str) -> psp.Metric:
    return psp.Metric(
        timestamp=ts_ms,
        name=f"{equipo}/{tag_name}",
        datatype=psp.DataType.INT32,
        value=1
    )


def run(publisher: MQTTPublisher, detector: EventDetector):
    ciclo = 0
    while True:
        ciclo += 1
        logger.info(f"=== CICLO #{ciclo} ===")

        for equipo, var_name, valor, desc, pausa in SECUENCIAS:

            # Paso de espera sin acción
            if equipo is None:
                logger.info(desc)
                time.sleep(pausa)
                continue

            logger.info(f"  >> {desc}")

            ts_ms = int(time.time() * 1000)
            metrics_to_publish = []

            # Métrica de estado del actuador
            metrics_to_publish.append(
                _make_bool_metric(ts_ms, equipo, var_name, valor)
            )

            # Evaluar eventos de flanco mediante el EventDetector real
            float_val = 1.0 if valor else 0.0
            events = detector.evaluate(equipo, var_name, float_val)

            for ev in events:
                logger.info(f"     ⚡ Evento disparado: {ev.tag_equipo}/{ev.tag_name} = {ev.value}  ({ev.message})")
                metrics_to_publish.append(
                    _make_event_metric(ts_ms, ev.tag_equipo, ev.tag_name)
                )

            # Publicar como DDATA Sparkplug B
            publisher.ddata(metrics=metrics_to_publish)

            time.sleep(pausa)


def main():
    config = AppConfig()
    publisher = MQTTPublisher(config)
    detector = EventDetector(config.MARCAS)

    logger.info(f"Conectando al Broker {config.mqtt_broker}:{config.mqtt_port}...")

    connected = False

    def on_connected():
        nonlocal connected
        connected = True

    publisher.start(on_connected_callback=on_connected)

    # Esperar conexión
    timeout = 10
    start = time.time()
    while not connected and (time.time() - start) < timeout:
        time.sleep(0.1)

    if not connected:
        logger.error("No se pudo conectar al broker. Saliendo.")
        return

    logger.info("Conexion establecida. Iniciando simulacion de eventos...")
    logger.info(f"Actuadores simulados: {[a[0] for a in ACTUADORES]}")
    logger.info("Presiona Ctrl+C para detener.\n")

    try:
        run(publisher, detector)
    except KeyboardInterrupt:
        logger.info("Simulacion detenida por el usuario.")
    finally:
        publisher.stop()


if __name__ == "__main__":
    main()
