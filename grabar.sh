#!/usr/bin/env bash
# Graba a un WAV lo que sale por la salida de audio por defecto (lo que produce
# el panel) hasta que pulses Ctrl+C. Pensado para el equipo de escritorio
# (PipeWire/PulseAudio), no para la Raspberry Pi.
#
# Uso:
#   ./grabar.sh [archivo_salida.wav]
#
# Si no pasas nombre, crea grabacion_AAAAMMDD_HHMMSS.wav en la carpeta actual.
# Nota: graba TODO lo que suene por la salida por defecto, así que no reproduzcas
# otra cosa mientras grabas el panel.
set -euo pipefail

OUT="${1:-grabacion_$(date +%Y%m%d_%H%M%S).wav}"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Falta ffmpeg (instálalo con: sudo apt install ffmpeg)." >&2
  exit 1
fi

# Monitor del sink por defecto (lo que se está reproduciendo)
SINK="$(pactl get-default-sink 2>/dev/null || true)"
if [ -n "$SINK" ]; then
  MON="${SINK}.monitor"
else
  MON="$(pactl list short sources 2>/dev/null | awk '/\.monitor/{print $2; exit}')"
fi

if [ -z "${MON:-}" ]; then
  echo "No encuentro ninguna fuente 'monitor' (¿hay PipeWire/PulseAudio?)." >&2
  exit 1
fi

echo "🎙  Grabando la salida de audio:"
echo "    fuente : $MON"
echo "    destino: $OUT"
echo "    (Ctrl+C para parar y cerrar el WAV)"

# exec: Ctrl+C va directo a ffmpeg, que finaliza la cabecera del WAV al recibirlo.
exec ffmpeg -hide_banner -loglevel warning -y -f pulse -i "$MON" -ac 2 -ar 44100 "$OUT"
