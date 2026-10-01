"""
test/simulador_eventos.py — Simulador autónomo Sparkplug B

Publica al mismo broker EMQX que el gateway real, SIN necesitar main.py activo.
Útil para testear suscriptores, reglas EMQX y decodificadores sin el PLC físico.

Ciclo completo que emite:
  NBIRTH  → anuncia el nodo simulador
  DBIRTH  → presenta el catálogo de 16 variables con valores iniciales
  DDATA   → secuencia de arranque/parada de todos los actuadores con eventos

Secuencia de actuadores (bucle continuo):
  1. Bomba enciende → válvulas abren → compresor enciende → motor enciende
  2. Espera 5 s a plena carga
  3. Motor apaga → compresor apaga → válvulas cierran → bomba apaga
  4. Espera 5 s en reposo → repite

Uso:
    python test/simulador_eventos.py
"""

import os
import sys
import time
import logging
import random

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

# ── Valores base de sensores analógicos (simulados con ruido) ─────────────────
SENSOR_BASE = {
    "PIT_001/Pressure":             6.2,
    "FIT_001_MAS/Mass_Flow":        0.85,
    "FIT_001_DENS/Density":         0.996,
    "FIT_001_TEMP/Temperature":     28.5,
    "FIT_001_VOL/Volumetric_Flow":  0.85,
    "LIT_001/Level":                32.5,
    "LIT_002/Level":                60.5,
    "TT_001/Temp":                  30.5,
    "Bomba_Agua_001_REF/Speed_Ref": 0.0,
}

# ── Estado inicial de todos los actuadores BOOL ───────────────────────────────
ESTADO_ACTUADORES = {
    "Bomba_Agua_001_STATUS/State": False,
    "Val_001/State":               False,
    "Val_002/State":               False,
    "Val_003/State":               False,
    "Val_004/State":               False,
    "Mot_Comp_001/State":          False,
    "MOTOR_01/Running":            False,
}

# ── Secuencia de arranque / parada ─────────────────────────────────────────────
# Formato: (equipo, var_name, nuevo_valor, descripcion, pausa_s)
SECUENCIAS = [
    # ARRANQUE
    ("Bomba_Agua_001_STATUS", "State",   True,  "Bomba de agua ENCIENDE",          1.0),
    ("Val_001",               "State",   True,  "Valvula 1 ABRE",                  0.5),
    ("Val_002",               "State",   True,  "Valvula 2 ABRE",                  0.5),
    ("Mot_Comp_001",          "State",   True,  "Compresor ENCIENDE",              1.0),
    ("Val_003",               "State",   True,  "Valvula 3 ABRE (presion ok)",     0.5),
    ("MOTOR_01",              "Running", True,  "Motor de laboratorio ENCIENDE",   1.0),
    ("Val_004",               "State",   True,  "Valvula 4 ABRE",                  0.5),
    # OPERACIÓN
    (None, None, None, "--- Sistema a plena carga (5s) ---",                       5.0),
    # PARADA
    ("MOTOR_01",              "Running", False, "Motor de laboratorio APAGA",      1.0),
    ("Val_004",               "State",   False, "Valvula 4 CIERRA",                0.5),
    ("Val_003",               "State",   False, "Valvula 3 CIERRA",                0.5),
    ("Mot_Comp_001",          "State",   False, "Compresor APAGA",                 1.0),
    ("Val_002",               "State",   False, "Valvula 2 CIERRA",                0.5),
    ("Val_001",               "State",   False, "Valvula 1 CIERRA",                0.5),
    ("Bomba_Agua_001_STATUS", "State",   False, "Bomba de agua APAGA",             1.0),
    # REPOSO
    (None, None, None, "--- Sistema en reposo (5s) ---",                           5.0),
]


# ── Helpers de métricas ───────────────────────────────────────────────────────

def _noisy(base: float, pct: float = 0.02) -> float:
    """Agrega ruido gaussiano proporcional al valor base (±2% por defecto)."""
    return base * (1 + random.gauss(0, pct))

def _initial_readings(estado_bool: dict) -> list:
    """
    Genera lecturas iniciales combinando sensores analógicos (con ruido)
    y el estado inicial de los actuadores BOOL, en formato compatible con
    mqtt_publisher.dbirth(initial_readings=...).
    """
    readings = []

    # Analógicos
    analog_map = {
        ("PIT_001",          "Pressure"):            "PIT_001/Pressure",
        ("FIT_001_MAS",      "Mass_Flow"):           "FIT_001_MAS/Mass_Flow",
        ("FIT_001_DENS",     "Density"):             "FIT_001_DENS/Density",
        ("FIT_001_TEMP",     "Temperature"):         "FIT_001_TEMP/Temperature",
        ("FIT_001_VOL",      "Volumetric_Flow"):     "FIT_001_VOL/Volumetric_Flow",
        ("LIT_001",          "Level"):               "LIT_001/Level",
        ("LIT_002",          "Level"):               "LIT_002/Level",
        ("TT_001",           "Temp"):                "TT_001/Temp",
        ("Bomba_Agua_001_REF","Speed_Ref"):          "Bomba_Agua_001_REF/Speed_Ref",
    }
    for (equipo, var_name), sensor_key in analog_map.items():
        info = {"equipo": equipo, "var_name": var_name}
        readings.append((info, _noisy(SENSOR_BASE[sensor_key])))

    # Digitales
    bool_map = {
        ("Bomba_Agua_001_STATUS", "State"):   "Bomba_Agua_001_STATUS/State",
        ("Val_001",               "State"):   "Val_001/State",
        ("Val_002",               "State"):   "Val_002/State",
        ("Val_003",               "State"):   "Val_003/State",
        ("Val_004",               "State"):   "Val_004/State",
        ("Mot_Comp_001",          "State"):   "Mot_Comp_001/State",
        ("MOTOR_01",              "Running"): "MOTOR_01/Running",
    }
    for (equipo, var_name), key in bool_map.items():
        info = {"equipo": equipo, "var_name": var_name}
        readings.append((info, 1.0 if estado_bool[key] else 0.0))

    return readings


def _bool_metric(ts_ms, equipo, var_name, value) -> psp.Metric:
    return psp.Metric(
        timestamp=ts_ms,
        name=f"{equipo}/{var_name}",
        datatype=psp.DataType.BOOLEAN,
        value=value
    )

def _event_metric(ts_ms, equipo, tag_name) -> psp.Metric:
    return psp.Metric(
        timestamp=ts_ms,
        name=f"{equipo}/{tag_name}",
        datatype=psp.DataType.INT32,
        value=1
    )

def _analog_metrics(ts_ms) -> list:
    """Genera métricas de sensores analógicos con ruido para DDATA periódico."""
    metrics = []
    for sensor_key, base in SENSOR_BASE.items():
        equipo, var_name = sensor_key.split("/", 1)
        metrics.append(psp.Metric(
            timestamp=ts_ms,
            name=sensor_key,
            datatype=psp.DataType.FLOAT,
            value=float(_noisy(base))
        ))
    return metrics


# ── Bucle principal de simulación ─────────────────────────────────────────────

def run(publisher: MQTTPublisher, detector: EventDetector):
    ciclo = 0

    while True:
        ciclo += 1
        logger.info(f"\n{'='*50}")
        logger.info(f"  CICLO #{ciclo}")
        logger.info(f"{'='*50}")

        for equipo, var_name, valor, desc, pausa in SECUENCIAS:

            # Paso de espera sin acción de actuador
            if equipo is None:
                logger.info(f"  {desc}")
                # Durante la espera, seguir publicando analógicos cada 2 s
                elapsed = 0.0
                while elapsed < pausa:
                    ts_ms = int(time.time() * 1000)
                    publisher.ddata(metrics=_analog_metrics(ts_ms))
                    sleep_step = min(2.0, pausa - elapsed)
                    time.sleep(sleep_step)
                    elapsed += sleep_step
                continue

            logger.info(f"  >> {desc}")

            ts_ms = int(time.time() * 1000)
            metrics = []

            # Métrica del actuador
            metrics.append(_bool_metric(ts_ms, equipo, var_name, valor))

            # Actualizar estado interno para coherencia
            ESTADO_ACTUADORES[f"{equipo}/{var_name}"] = valor

            # Detectar y emitir eventos de flanco
            float_val = 1.0 if valor else 0.0
            events = detector.evaluate(equipo, var_name, float_val)
            for ev in events:
                logger.info(f"     ⚡ {ev.message}  →  {ev.tag_equipo}/{ev.tag_name} = 1")
                metrics.append(_event_metric(ts_ms, ev.tag_equipo, ev.tag_name))

            # Incluir analógicos con ruido en el mismo paquete
            metrics.extend(_analog_metrics(ts_ms))

            publisher.ddata(metrics=metrics)
            time.sleep(pausa)


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main():
    config = AppConfig()
    publisher = MQTTPublisher(config)
    # Reutilizar el EventDetector que ya vive dentro de MQTTPublisher
    detector = publisher.event_detector

    logger.info(f"Broker destino : {config.mqtt_broker}:{config.mqtt_port}")
    logger.info(f"Node ID        : {config.sparkplug_node_id}")
    logger.info(f"Device ID      : {config.sparkplug_device_id}")
    logger.info("Presiona Ctrl+C para detener.\n")

    connected = False

    def on_connected():
        nonlocal connected
        # NBIRTH ya lo emite publisher.start() internamente
        logger.info("Conexion establecida. Publicando DBIRTH con valores iniciales...")
        initial = _initial_readings(ESTADO_ACTUADORES)
        publisher.dbirth(initial_readings=initial)
        connected = True

    publisher.start(on_connected_callback=on_connected)

    # Esperar conexión (máx 10 s)
    start = time.time()
    while not connected and (time.time() - start) < 10:
        time.sleep(0.1)

    if not connected:
        logger.error("No se pudo conectar al broker. Saliendo.")
        return

    logger.info("NBIRTH y DBIRTH publicados. Iniciando simulacion...\n")

    try:
        run(publisher, detector)
    except KeyboardInterrupt:
        logger.info("Simulacion detenida.")
    finally:
        publisher.stop()


if __name__ == "__main__":
    main()
