"""Configuración de Roboguitarra.

Todos los valores se pueden sobreescribir con variables de entorno, lo que
permite ajustar el dispositivo de audio del HAT en la Raspberry Pi sin tocar
el código (ver README).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Carpeta donde se buscan los archivos .sf2
SOUNDFONT_DIR = Path(
    os.environ.get("ROBOGUITARRA_SF2_DIR", BASE_DIR / "soundfonts")
)

# Archivo donde se guarda la última sesión (sf2 + instrumento) para
# restaurarla automáticamente al arrancar.
STATE_FILE = Path(
    os.environ.get("ROBOGUITARRA_STATE_FILE", BASE_DIR / "last_session.json")
)

# --- Audio ---
# En la Raspberry Pi con HAT de sonido usa "alsa". En un equipo de desarrollo
# con PipeWire/PulseAudio puede que necesites "pulseaudio" o dejar que
# FluidSynth elija el driver por defecto.
AUDIO_DRIVER = os.environ.get("ROBOGUITARRA_AUDIO_DRIVER", "alsa")
# Dispositivo ALSA concreto del HAT, p.ej. "hw:0" o "hw:sndrpihifiberry".
# Descúbrelo con `aplay -l`. Si se deja vacío, FluidSynth usa el predeterminado.
ALSA_DEVICE = os.environ.get("ROBOGUITARRA_ALSA_DEVICE") or None

SAMPLE_RATE = float(os.environ.get("ROBOGUITARRA_SAMPLE_RATE", "44100"))
POLYPHONY = int(os.environ.get("ROBOGUITARRA_POLYPHONY", "128"))
DEFAULT_GAIN = float(os.environ.get("ROBOGUITARRA_GAIN", "0.5"))

# --- MIDI ---
# "alsa_seq" crea un puerto de entrada ALSA al que conectar el teclado USB
# o el hardware roboguitarra con `aconnect`.
MIDI_DRIVER = os.environ.get("ROBOGUITARRA_MIDI_DRIVER", "alsa_seq")

# Nº de canales MIDI que comparten instrumento (roboguitarra emite en 0..N-1).
MIDI_CHANNELS = int(os.environ.get("ROBOGUITARRA_MIDI_CHANNELS", "4"))

# CC del joystick (eje A5) que controla el efecto robot (bipolar, centro 64 =
# limpio). El servidor lo intercepta y lo mapea a set_robot(); no llega a
# FluidSynth. Debe coincidir con CC_ROBOT del firmware.
ROBOT_CC = int(os.environ.get("ROBOGUITARRA_ROBOT_CC", "20"))

# --- Servidor web ---
HOST = os.environ.get("ROBOGUITARRA_HOST", "0.0.0.0")
PORT = int(os.environ.get("ROBOGUITARRA_PORT", "5000"))
