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
   SPARKPLUG_GROUP_ID=GianCa04_IIoT
   SPARKPLUG_NODE_ID=PLC_Gateway_01
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

