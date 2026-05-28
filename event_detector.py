import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("PLC-MQTT.EventDetector")


# ──────────────────────────────────────────────
#  Estructuras de datos
# ──────────────────────────────────────────────

@dataclass
class EventRule:
    """Regla declarativa de un evento de flanco, parseada desde tags_plc.json."""
    name: str               # Nombre descriptivo (ej. "Motor_Encendido")
    source_key: str          # Clave compuesta "equipo/var_name" de la variable fuente
    event_type: str          # "rising_edge" | "falling_edge"
    tag_equipo: str          # Equipo destino (heredado del padre en el JSON)
    tag_name: str            # Nombre del tag de evento (ej. "MOTOR_01_ON")


@dataclass
class EventResult:
    """Resultado emitido cuando un evento de flanco se dispara."""
    tag_equipo: str
    tag_name: str
    value: int               # Siempre 1 (pulso/marca)
    message: str             # Mensaje legible para log


# ──────────────────────────────────────────────
#  Motor de Detección de Eventos por Flanco
# ──────────────────────────────────────────────

class EventDetector:
    """
    Detector de eventos por flanco (rising/falling edge).
    
    Solo emite pulsos (value=1) en transiciones de estado:
    - rising_edge:  dispara cuando el valor pasa de 0→1 (o estado inicial = 1)
    - falling_edge: dispara cuando el valor pasa de 1→0 (o estado inicial = 0)
    
    Para registrar nuevos eventos, solo se añade la configuración
    en tags_plc.json — sin modificar código.
    """

    def __init__(self, marcas: dict):
        self._rules: list[EventRule] = []
        self._previous_values: dict[str, Optional[float]] = {}
        
        self._parse_rules(marcas)

    def _parse_rules(self, marcas: dict):
        """Extrae todas las reglas de evento del diccionario de tags del PLC."""
        for equipo, variables in marcas.items():
            for var_name, config_list in variables.items():
                # El 7mo elemento (índice 6) es un dict opcional con "events"
                if len(config_list) < 7:
                    continue
                
                event_config = config_list[6]
                if not isinstance(event_config, dict) or "events" not in event_config:
                    continue

                source_key = f"{equipo}/{var_name}"

                for ev in event_config["events"]:
                    event_type = ev["type"]
                    if event_type not in ("rising_edge", "falling_edge"):
                        logger.warning(f"Tipo de evento desconocido: {event_type}")
                        continue

                    rule = EventRule(
                        name=ev["name"],
                        source_key=source_key,
                        event_type=event_type,
                        tag_equipo=equipo,  # Heredado del padre
                        tag_name=ev["tag_name"],
                    )
                    self._rules.append(rule)
                    # Inicializar estado previo en None (primera lectura)
                    self._previous_values[source_key] = None

                    logger.info(
                        f"Evento registrado: [{rule.event_type}] "
                        f"{rule.name} → {rule.tag_equipo}/{rule.tag_name}"
                    )

    def get_event_tags(self) -> list[dict]:
        """
        Retorna la lista de tags virtuales de evento para registrarlos en DBIRTH.
        Cada dict tiene: tag_equipo, tag_name.
        """
        tags = []
        seen = set()
        for rule in self._rules:
            key = f"{rule.tag_equipo}/{rule.tag_name}"
            if key not in seen:
                seen.add(key)
                tags.append({
                    "tag_equipo": rule.tag_equipo,
                    "tag_name": rule.tag_name,
                })
        return tags

    def evaluate(self, equipo: str, var_name: str, current_value: float) -> list[EventResult]:
        """
        Evalúa todas las reglas vinculadas a la variable fuente.
        Solo retorna resultados cuando hay una transición de estado (pulso).
        
        Args:
            equipo: Identificador del equipo (ej. "MOTOR_01")
            var_name: Nombre de la variable (ej. "Running")
            current_value: Valor actual leído del PLC (0.0 o 1.0)
            
        Returns:
            Lista de EventResult para cada evento disparado (puede estar vacía).
        """
        source_key = f"{equipo}/{var_name}"
        results: list[EventResult] = []

        # Filtrar solo las reglas que aplican a esta variable fuente
        applicable_rules = [r for r in self._rules if r.source_key == source_key]
        if not applicable_rules:
            return results

        previous = self._previous_values.get(source_key)

        for rule in applicable_rules:
            result = self._evaluate_edge(rule, previous, current_value)
            if result is not None:
                results.append(result)

        # Actualizar el estado previo DESPUÉS de evaluar todas las reglas
        self._previous_values[source_key] = current_value

        return results

    def _evaluate_edge(
        self, rule: EventRule, previous: Optional[float], current: float
    ) -> Optional[EventResult]:
        """
        Evalúa un evento de flanco y retorna un pulso (value=1) si se dispara.
        
        - rising_edge:  primera lectura con valor=1, o transición 0→1
        - falling_edge: primera lectura con valor=0, o transición 1→0
        """
        is_on = current == 1.0
        
        if rule.event_type == "rising_edge":
            # Primera lectura: disparar si el motor ya está encendido
            if previous is None and is_on:
                return EventResult(
                    tag_equipo=rule.tag_equipo,
                    tag_name=rule.tag_name,
                    value=1,
                    message=f"⚡ EVENTO [{rule.name}]: Estado inicial → ON"
                )
            # Transición 0→1
            if previous == 0.0 and is_on:
                return EventResult(
                    tag_equipo=rule.tag_equipo,
                    tag_name=rule.tag_name,
                    value=1,
                    message=f"⚡ EVENTO [{rule.name}]: OFF → ON"
                )

        elif rule.event_type == "falling_edge":
            # Primera lectura: disparar si el motor ya está apagado
            if previous is None and not is_on:
                return EventResult(
                    tag_equipo=rule.tag_equipo,
                    tag_name=rule.tag_name,
                    value=1,
                    message=f"⚡ EVENTO [{rule.name}]: Estado inicial → OFF"
                )
            # Transición 1→0
            if previous == 1.0 and not is_on:
                return EventResult(
                    tag_equipo=rule.tag_equipo,
                    tag_name=rule.tag_name,
                    value=1,
                    message=f"⚡ EVENTO [{rule.name}]: ON → OFF"
                )

        return None
