# Edge Gateway - Siemens PLC a MQTT (Sparkplug B)

Este proyecto implementa un Edge Gateway en Python para realizar la captura de datos desde un PLC Siemens y publicarlos de forma asíncrona hacia un broker MQTT siguiendo el estándar Sparkplug B o similar.

![alt text](<LABORATORIO_SAT.jpeg>)

## Estructura del proyecto
- `main.py`: Punto de entrada de la aplicación.
- `config.py`: Gestor de la configuración y variables de entorno usando `dotenv`.
- `plc_client.py`: Maneja la comunicación y lectura de variables del PLC.
- `mqtt_publisher.py`: Administra la conexión, publicación y colas de mensajes MQTT.
- `gateway.py`: Orquesta la interacción entre el cliente PLC y el publicador MQTT.
- `tags_plc.json`: Diccionario configurable con los tags/marcas a leer en el PLC.
- `utils/`: Contiene archivos de configuración recomendados para despliegue en Linux (ej. servicio systemd, bashrc, motd).

## Convención de Tópicos Sparkplug B

La estructura de los tópicos sigue estrictamente la especificación Sparkplug B:

```text
spBv1.0 / <group_id> / <message_type> / <edge_node_id> [ / <device_id> ]
```

### Elementos del Tópico:
1. `spBv1.0`: Identificador de versión del estándar (Sparkplug B v1.0).
2. `<group_id>` (`sat_lab`): Agrupación lógica de la planta o laboratorio.
3. `<message_type>`: Tipo de mensaje (`NBIRTH`, `NDEATH`, `DBIRTH`, `DDEATH`, `DDATA`).
4. `<edge_node_id>` (`gw_extraccion_112`): Identificador del Gateway o contenedor LXC.
5. `<device_id>` (`plc_siemens_lab`): Identificador del autómata conectado.

### Los 5 Tópicos del Sistema:

| Nivel | Mensaje | Tópico Completo | Significado en Lenguaje Simple |
|---|---|---|---|
| **Nodo** | `NBIRTH` | `spBv1.0/sat_lab/NBIRTH/gw_extraccion_112` | **LXC Conectado:** *"Estoy vivo, mi IP es tal y reportaré 16 equipos"*. |
| **Nodo** | `NDEATH` | `spBv1.0/sat_lab/NDEATH/gw_extraccion_112` | **LXC Caído (LWT):** *"El contenedor LXC perdió energía o se apagó"*. |
| **Dispositivo** | `DBIRTH` | `spBv1.0/sat_lab/DBIRTH/gw_extraccion_112/plc_siemens_lab` | **PLC Conectado:** *"El PLC Siemens está en línea con su catálogo de 16 variables"*. |
| **Dispositivo** | `DDATA` | `spBv1.0/sat_lab/DDATA/gw_extraccion_112/plc_siemens_lab` | **Telemetría:** *"Aquí van las lecturas en tiempo real de los sensores"*. |
| **Dispositivo** | `DDEATH` | `spBv1.0/sat_lab/DDEATH/gw_extraccion_112/plc_siemens_lab` | **PLC Desconectado:** *"El LXC sigue vivo, pero se desconectó el cable del PLC"*. |


## Requisitos Previos
* **Python 3.8+**
* Acceso a un broker MQTT (por ejemplo, Mosquitto, EMQX).
* Conectividad IP estándar hacia el PLC y el broker MQTT.

## Configuración y Despliegue Manual (Desarrollo)

1. **Clonar/Copiar el repositorio:**
   Ubicar el código fuente, usualmente en `/root/edge_siemens/` si es en el Edge container.

2. **Crear e inicializar un entorno virtual:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Instalar las dependencias:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configurar Variables de Entorno (.env):**
   Crea un archivo `.env` en el directorio raíz basándote en que `config.py` espera variables como:
   ```ini
   PLC_IP=192.168.0.1
   PLC_RACK=0
   PLC_SLOT=3
   RETRY_DELAY=10
   SENSOR_READ_INTERVAL=2.0
   MQTT_BROKER=localhost
   MQTT_PORT=1883
   MQTT_USER=tu_usuario
   MQTT_PASSWORD=tu_password
   SPARKPLUG_GROUP_ID=sat_lab
   SPARKPLUG_NODE_ID=gw_extraccion_112
   SPARKPLUG_DEVICE_ID=plc_siemens_lab
   MQTT_KEEPALIVE=10
   ```

5. **Configurar los Tags de PLC:**
   Asegúrate de definir adecuadamente los registros en el archivo `tags_plc.json`.

6. **Ejecutar el script:**
   ```bash
   python main.py
   ```

## Despliegue con Systemd (Producción en Linux/LXC Debian)

En entornos de producción, se recomienda ejecutar el script como un servicio daemon para su gestión, arranque automático y control de errores:

1. Modifica la extensión (si aplica) y copia el esquema proveído en `utils/etc_systemd_system_edge.TOML` hacia la carpeta de servicios:
   ```bash
   cp utils/etc_systemd_system_edge.TOML /etc/systemd/system/edge-gateway.service
   ```

2. Aplicar los permisos si corresponde y recargar el daemon:
   ```bash
   systemctl daemon-reload
   ```

3. Activar e iniciar el servicio:
   ```bash
   systemctl enable edge-gateway.service
   systemctl start edge-gateway.service
   ```

4. **Solucionar Problemas:**
   Puedes monitorear el flujo de datos y comprobar reinicios por estado fallido con:
   ```bash
   journalctl -u edge-gateway.service -f
   ```

