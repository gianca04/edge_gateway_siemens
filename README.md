# Edge Gateway IIoT para PLC Siemens

Este repositorio contiene un servicio Edge Gateway desarrollado en Python para la captura de datos desde PLCs Siemens y su transmisión a un broker MQTT utilizando el estándar Sparkplug B. Está diseñado para operar de forma continua, resiliente y eficiente como un daemon del sistema (systemd) en entornos Linux (como contenedores LXC en Debian).

## Arquitectura y Componentes

- **Lectura Optimizada (Snap7):** Utiliza lecturas multi-variable (`read_multi_vars`) para agrupar las peticiones en un solo ciclo de red hacia el PLC, minimizando la latencia.
- **Procesamiento de Excepciones (Report by Exception - RBE):** Filtra transmisiones utilizando bandas muertas (deadbands) para valores analógicos (`REAL`) y cambios de estado (flancos) para valores digitales (`BOOL`).
- **Publicación Asíncrona (MQTT / Sparkplug B):** Cuenta con un hilo (worker) dedicado y una cola de mensajes para evitar que la latencia de red hacia el broker bloquee los ciclos de escaneo del PLC.
- **Tolerancia a Fallos (Crash-Only):** El bucle principal delega el control de errores persistentes (ej. pérdida prolongada de conexión, fallos de hardware) al gestor del sistema operativo (systemd) abortando el proceso, garantizando así reinicios en un estado limpio (sin fugas de sockets o memoria).

## Requisitos Previos

- Python 3.8 o superior.
- Entorno virtual (venv) configurado.
- Broker MQTT compatible con Sparkplug B.
- PLC Siemens (S7-1200, S7-1500, S7-300) con el acceso PUT/GET habilitado y los bloques de datos (DB) configurados sin acceso optimizado.

## Configuración del Sistema

### 1. Variables de Entorno (.env)
Copiar el archivo `.env example` a `.env` en la raíz del proyecto y definir los parámetros:
- `PLC_IP`, `PLC_RACK`, `PLC_SLOT`: Parámetros de red de red del PLC objetivo.
- `MQTT_BROKER`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASSWORD`: Credenciales del servidor MQTT.
- `SPARKPLUG_GROUP_ID`, `SPARKPLUG_NODE_ID`: Identificadores taxonómicos para la estructura Sparkplug B.
- `RETRY_DELAY`, `SENSOR_READ_INTERVAL`: Temporizaciones principales y de reintento.

### 2. Diccionario de Datos (tags_plc.json)
Define las variables (marcas) a leer en la memoria del PLC. La estructura JSON agrupa las variables por equipo.

Formato del array de configuración por variable:
`[DB, ByteOffset, BitOffset, "Tipo", FrecuenciaSanidad, BandaMuerta]`

- **DB:** Número de Data Block del PLC.
- **ByteOffset / BitOffset:** Ubicación del dato en la memoria.
- **Tipo:** `REAL` o `BOOL`.
- **FrecuenciaSanidad:** Período máximo en segundos antes de forzar la publicación del valor actual hacia el broker, aunque este no haya cambiado.
- **BandaMuerta:** (Solo `REAL`) Variación flotante mínima requerida entre el valor anterior y el actual para gatillar un evento de actualización.

## Despliegue como Servicio (systemd)

El servicio debe ser ejecutado mediante `systemd` para asegurar su persistencia. Se debe crear un archivo de unidad estandarizado en `/etc/systemd/system/plc-mqtt.service`:

```ini
[Unit]
Description=Servicio de Edge Gateway Siemens
Wants=network-online.target
After=network-online.target
StartLimitBurst=5
StartLimitIntervalSec=30

[Service]
ExecStart=/root/captura_datos/venv/bin/python3 /root/captura_datos/main.py
WorkingDirectory=/root/captura_datos
Restart=on-failure
RestartSec=5
User=root
StandardOutput=append:/var/log/edge.log
StandardError=append:/var/log/edge.log

[Install]
WantedBy=multi-user.target
```

### Comandos de gestión

- **Recargar unidad:** `sudo systemctl daemon-reload`
- **Habilitar en el arranque e iniciar:** `sudo systemctl enable --now plc-mqtt.service`
- **Revisar estado:** `sudo systemctl status plc-mqtt.service`
- **Seguimiento de logs:** `sudo journalctl -u plc-mqtt.service -f`
