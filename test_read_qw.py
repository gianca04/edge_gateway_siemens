import os
import sys
import time
import argparse
from dotenv import load_dotenv
import snap7
from snap7.types import Areas
from snap7.util import get_word, get_int

# Asegurar codificación utf-8 en consola de Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def read_tags(client, tags):
    for tag in tags:
        byte_offset = tag["byte"]
        symbol = tag["symbol"]
        desc = tag["desc"]
        addr_str = tag["address"]

        try:
            # Leer 2 bytes (WORD) del área de salidas (Areas.PA)
            data = client.read_area(Areas.PA, 0, byte_offset, 2)
            
            # Decodificar valores
            val_word = get_word(data, 0)
            val_int = get_int(data, 0)
            hex_raw = f"0x{val_word:04X}"
            # Escalamiento estándar Siemens S7 analógico (0 - 27648 equivale a 0.0% - 100.0%)
            pct_str = f"{(val_int / 27648.0) * 100.0:.2f}%" if 0 <= val_int <= 27648 else "N/A"

            print(f"[{time.strftime('%H:%M:%S')}] {addr_str:6s} | {symbol:16s} | Word: {val_word:5d} | Int: {val_int:5d} | Raw: {hex_raw} | Ref: {pct_str} ({desc})")

        except Exception as read_err:
            print(f"❌ Error al leer {addr_str}: {read_err}")

def main():
    parser = argparse.ArgumentParser(description="Lectura de variables Siemens QW vía Snap7")
    parser.add_argument("--loop", action="store_true", help="Leer en bucle continuo cada N segundos")
    parser.add_argument("--interval", type=float, default=1.0, help="Intervalo de lectura en bucle (segundos)")
    args = parser.parse_args()

    # 1. Cargar variables de entorno desde .env
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    load_dotenv(dotenv_path=env_path)

    plc_ip = os.getenv("PLC_IP", "192.168.0.50")
    plc_rack = int(os.getenv("PLC_RACK", 0))
    plc_slot = int(os.getenv("PLC_SLOT", 3))

    print("=" * 80)
    print("TEST DE LECTURA DE VARIABLE PLC CON SNAP7")
    print("=" * 80)
    print(f"PLC IP   : {plc_ip}")
    print(f"Rack     : {plc_rack} | Slot: {plc_slot}")
    print("=" * 80)

    # 2. Inicializar cliente Snap7
    client = snap7.client.Client()

    try:
        print(f"Conectando a {plc_ip}...")
        client.connect(plc_ip, plc_rack, plc_slot)

        if not client.get_connected():
            print("❌ No se pudo conectar al PLC.")
            return

        print("✅ Conexión establecida con éxito.\n")

        # Variables a probar según la tabla de símbolos (AO8x12Bit):
        # QW 525 -> KM_BOMBA_001
        # QW 529 -> KM_BOMBA_001- (Bomba de Agua)
        tags_to_test = [
            {"address": "QW 525", "byte": 525, "symbol": "KM_BOMBA_001", "desc": "Canal 0 AO8x12Bit"},
            {"address": "QW 529", "byte": 529, "symbol": "KM_BOMBA_001-", "desc": "Bomba de Agua"},
        ]

        print("Hora     | Direc. | Símbolo          | Word  | Int   | Raw    | Escala 0-100%")
        print("-" * 80)

        if args.loop:
            print(f"Modo continuo activo (Intervalo: {args.interval}s). Presiona Ctrl+C para detener.\n")
            while True:
                read_tags(client, tags_to_test)
                time.sleep(args.interval)
        else:
            read_tags(client, tags_to_test)
            print("-" * 80)

    except KeyboardInterrupt:
        print("\nLectura detenida por el usuario.")
    except Exception as e:
        print(f"❌ Error general de comunicación: {e}")
    finally:
        if client.get_connected():
            client.disconnect()
            print("Conexión con el PLC cerrada.")

if __name__ == "__main__":
    main()
