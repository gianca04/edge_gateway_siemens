import os
import sys
import time
from datetime import datetime
import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import pysparkplug as psp
from config import AppConfig

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

DTYPE_LABELS = {
    "FLOAT": "Float",
    "BOOLEAN": "Bool",
    "INT32": "Int32",
    "INT64": "Int64",
    "STRING": "String",
}

def _ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]

def _val(m):
    return f"{m.value:.4f}" if isinstance(m.value, float) else str(m.value)

def _type(m):
    raw = m.datatype.name if hasattr(m.datatype, "name") else str(m.datatype)
    return DTYPE_LABELS.get(raw, raw)


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe("spBv1.0/#")

def on_message(client, userdata, msg):
    try:
        spb   = psp.Message.from_mqtt_message(msg)
        topic = spb.topic
        pay   = spb.payload
        mtype = topic.message_type.name if hasattr(topic.message_type, "name") else "?"

        metrics = []
        if hasattr(pay, "metrics"):
            metrics = pay.metrics
        elif hasattr(pay, "bd_seq_metric") and pay.bd_seq_metric:
            metrics = [pay.bd_seq_metric]

        print(f"[{_ts()}] {mtype} | {topic}")
        for m in metrics:
            desc = ""
            if hasattr(m, "metadata") and m.metadata and hasattr(m.metadata, "description"):
                desc = f"  # {m.metadata.description}" if m.metadata.description else ""
            print(f"  {m.name} = {_val(m)}  [{_type(m)}]{desc}")
        print()

    except Exception as e:
        print(f"[ERROR] {msg.topic}: {e}\n")


def main():
    config = AppConfig()
    client = mqtt.Client(client_id="diag_spbv1", userdata={})
    if config.mqtt_user:
        client.username_pw_set(config.mqtt_user, config.mqtt_password)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(config.mqtt_broker, config.mqtt_port, 60)
    client.loop_start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()
