import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("PLC-MQTT.EventDetector")


# ──────────────────────────────────────────────
#  Estructuras de datos
# ──────────────────────────────────────────────

@dataclass
class EventRule:
    """Regla declarativa de un evento, parseada desde tags_plc.json."""
    name: str               # Nombre descriptivo (ej. "Motor_Status_Change")
    source_key: str          # Clave compuesta "equipo/var_name" de la variable fuente
    event_type: str          # "change" | "threshold_above" | "threshold_below"
    tag_equipo: str          # Equipo destino donde se publica el tag de evento
    tag_name: str            # Nombre del tag virtual de evento
    threshold: Optional[float] = None  # Umbral (solo para threshold_above/below)


@dataclass
class EventResult:
    """Resultado emitido cuando un evento se dispara."""
    tag_equipo: str
    tag_name: str
    value: float             # Valor emitido (0/1 para change y threshold, o valor real)
    message: str             # Mensaje legible para log


# ──────────────────────────────────────────────
#  Motor de Detección de Eventos
# ──────────────────────────────────────────────

class EventDetector:
    """
    Motor genérico de detección de eventos.
    
    Parsea las reglas de evento definidas en el JSON de tags,
    mantiene el estado previo de cada variable fuente, y evalúa
    si un evento debe dispararse en cada ciclo de lectura.
    
    Para registrar un nuevo evento, solo se necesita añadir
    la configuración en tags_plc.json — sin modificar código.
    """

    def __init__(self, marcas: dict):
        self._rules: list[EventRule] = []
        self._previous_values: dict[str, Optional[float]] = {}
        # Para threshold: estado previo de cruce (True = activo, False = inactivo)
        self._threshold_states: dict[str, bool] = {}
        
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
                    rule = EventRule(
                        name=ev["name"],
                        source_key=source_key,
                        event_type=ev["type"],
                        tag_equipo=ev.get("tag_equipo", equipo),
                        tag_name=ev["tag_name"],
                        threshold=ev.get("threshold")
                    )
                    self._rules.append(rule)
                    # Inicializar estado previo en None (primera lectura)
                    self._previous_values[source_key] = None
                    # Inicializar estado de umbral como inactivo
                    if rule.event_type in ("threshold_above", "threshold_below"):
                        self._threshold_states[rule.name] = False

                    logger.info(
                        f"Evento registrado: [{rule.event_type}] "
                        f"{rule.name} → {rule.tag_equipo}/{rule.tag_name}"
                    )

    def get_event_tags(self) -> list[dict]:
        """
        Retorna la lista de tags virtuales de evento para registrarlos en DBIRTH.
        Cada dict tiene: tag_equipo, tag_name, dtype ('INT32').
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
        
        Args:
            equipo: Identificador del equipo (ej. "MOTOR_01")
            var_name: Nombre de la variable (ej. "Running")
            current_value: Valor actual leído del PLC
            
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
            result = self._evaluate_rule(rule, previous, current_value)
            if result is not None:
                results.append(result)

        # Actualizar el estado previo DESPUÉS de evaluar todas las reglas
        self._previous_values[source_key] = current_value

        return results

    def _evaluate_rule(
        self, rule: EventRule, previous: Optional[float], current: float
    ) -> Optional[EventResult]:
        """Evalúa una regla individual y retorna un EventResult si se dispara."""
        
        if rule.event_type == "change":
            return self._eval_change(rule, previous, current)
        elif rule.event_type == "threshold_above":
            return self._eval_threshold_above(rule, previous, current)
        elif rule.event_type == "threshold_below":
            return self._eval_threshold_below(rule, previous, current)
        else:
            logger.warning(f"Tipo de evento desconocido: {rule.event_type}")
            return None

    # ── Evaluadores por tipo ──────────────────

    def _eval_change(
        self, rule: EventRule, previous: Optional[float], current: float
    ) -> Optional[EventResult]:
        """Dispara cuando el valor actual difiere del anterior, o en la primera lectura."""
        # Primera lectura: publicar estado inicial
        if previous is None:
            if current in (0.0, 1.0):
                state_str = "ON" if current == 1.0 else "OFF"
                msg = f"⚡ EVENTO [{rule.name}]: Estado inicial → {state_str}"
            else:
                msg = f"⚡ EVENTO [{rule.name}]: Estado inicial → {current}"
            return EventResult(
                tag_equipo=rule.tag_equipo,
                tag_name=rule.tag_name,
                value=current,
                message=msg
            )
        
        if previous != current:
            # Para BOOL (0/1): mensaje descriptivo
            if current in (0.0, 1.0) and previous in (0.0, 1.0):
                from_str = "ON" if previous == 1.0 else "OFF"
                to_str = "ON" if current == 1.0 else "OFF"
                msg = f"⚡ EVENTO [{rule.name}]: {from_str} → {to_str}"
            else:
                msg = f"⚡ EVENTO [{rule.name}]: {previous} → {current}"
            
            return EventResult(
                tag_equipo=rule.tag_equipo,
                tag_name=rule.tag_name,
                value=current,
                message=msg
            )
        return None

    def _eval_threshold_above(
        self, rule: EventRule, previous: Optional[float], current: float
    ) -> Optional[EventResult]:
        """Dispara cuando el valor cruza el umbral hacia arriba (o regresa debajo)."""
        if rule.threshold is None:
            return None
        
        was_above = self._threshold_states.get(rule.name, False)
        is_above = current >= rule.threshold

        if is_above != was_above:
            self._threshold_states[rule.name] = is_above
            value = 1.0 if is_above else 0.0
            direction = "SUPERADO" if is_above else "NORMALIZADO"
            msg = (
                f"⚡ EVENTO [{rule.name}]: Umbral {rule.threshold} "
                f"{direction} (valor: {current})"
            )
            return EventResult(
                tag_equipo=rule.tag_equipo,
                tag_name=rule.tag_name,
                value=value,
                message=msg
            )
        return None

    def _eval_threshold_below(
        self, rule: EventRule, previous: Optional[float], current: float
    ) -> Optional[EventResult]:
        """Dispara cuando el valor cruza el umbral hacia abajo (o regresa arriba)."""
        if rule.threshold is None:
            return None
        
        was_below = self._threshold_states.get(rule.name, False)
        is_below = current <= rule.threshold

        if is_below != was_below:
            self._threshold_states[rule.name] = is_below
            value = 1.0 if is_below else 0.0
            direction = "INFERIOR" if is_below else "NORMALIZADO"
            msg = (
                f"⚡ EVENTO [{rule.name}]: Umbral {rule.threshold} "
                f"{direction} (valor: {current})"
            )
            return EventResult(
                tag_equipo=rule.tag_equipo,
                tag_name=rule.tag_name,
                value=value,
                message=msg
            )
        return None
