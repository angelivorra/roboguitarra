#!/usr/bin/env bash
# Sube el firmware (en MODO_CALIBRACION) y captura las lecturas del SoftPot a un CSV.
# Uso:  ./capturar_lecturas.sh [nombre_salida.csv]
# Para terminar la captura pulsa Ctrl-C cuando hayas acabado el gesto.
set -e

PROJ="/home/angel/Documentos/PlatformIO/Projects/Roboguitarra"
PIO="$HOME/.platformio/penv/bin/pio"
OUT="${1:-$PROJ/lecturas_$(date +%Y%m%d_%H%M%S).csv}"

cd "$PROJ"

echo ">> Subiendo firmware al Leonardo..."
"$PIO" run --target upload

echo ">> Captura iniciada. Archivo: $OUT"
echo ">> Haz el gesto ahora. Pulsa Ctrl-C cuando termines."
"$PIO" device monitor -b 9600 --quiet | tee "$OUT"
