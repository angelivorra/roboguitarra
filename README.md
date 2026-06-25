# 🎸 Roboguitarra

Interfaz web móvil para usar una **Raspberry Pi 3 con HAT de sonido** como
sintetizador de **SoundFonts (.sf2)**. Desde el móvil puedes:

1. Elegir y **cargar** un `.sf2` de la carpeta `soundfonts/`.
2. **Seleccionar el instrumento** (banco/preset) dentro del SoundFont.
3. **Modular el sonido** con los mismos controles que QSynth: volumen, reverb y chorus.
4. **Tocar** con el teclado en pantalla, un teclado MIDI USB o el hardware roboguitarra.

El motor de audio es **FluidSynth** (QSynth es solo su GUI) controlado desde Python
con [pyFluidSynth](https://github.com/nwhitehead/pyfluidsynth). El backend es **Flask**.

## Arquitectura

```
Navegador móvil ──HTTP──> Flask (app.py)
                              │
                              ├── SynthEngine (synth.py) ─> pyfluidsynth ─> ALSA (HAT de sonido)
                              │        ▲ puerto ALSA-seq MIDI IN
                              │        └── teclado USB / hardware roboguitarra (aconnect)
                              └── soundfonts.py (parsea el chunk phdr: lista instrumentos del .sf2)
```

Un único proceso mantiene vivo el sintetizador (singleton protegido por un lock).
El teclado en pantalla llama a la API; el MIDI externo entra por el puerto ALSA-seq.

## Requisitos del sistema

```bash
sudo apt update
sudo apt install -y fluidsynth libfluidsynth3 python3-venv
# Efecto robótico (frequency shifter LADSPA) del knob "Robot":
sudo apt install -y swh-plugins
# SoundFont General MIDI de ejemplo (opcional):
sudo apt install -y fluid-soundfont-gm
```

El knob **Robot** inserta el *Bode frequency shifter* de swh-plugins en la salida
de FluidSynth (vía su motor LADSPA integrado, controlado por `ladspa.py`). Si el
plugin no está instalado, la app arranca igual y el efecto queda inactivo.

`pyFluidSynth` necesita la librería nativa `libfluidsynth` instalada (lo anterior la cubre).

## Puesta en marcha

### Con VS Code (recomendado)
- **Ctrl/Cmd+Shift+B** → ejecuta la tarea *Roboguitarra: Ejecutar*, que crea el
  entorno virtual `.venv` (si no existe), instala dependencias y arranca el servidor.
- Hay también la tarea *Roboguitarra: Crear entorno* para solo preparar el `.venv`.

### Manual
```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python app.py
```

Abre `http://<IP-de-la-Pi>:5000` desde el móvil (misma red Wi-Fi).

## SoundFonts

Copia tus archivos `.sf2` en la carpeta [`soundfonts/`](soundfonts/). Aparecerán en
el desplegable de la web. Si instalaste `fluid-soundfont-gm`:

```bash
ln -s /usr/share/sounds/sf2/FluidR3_GM.sf2 soundfonts/
```

## Configuración del HAT de sonido (ALSA)

1. Identifica la tarjeta del HAT:
   ```bash
   aplay -l
   ```
2. Exporta el dispositivo antes de arrancar (o edítalo en `roboguitarra.service`):
   ```bash
   export ROBOGUITARRA_ALSA_DEVICE=hw:0   # ajusta al número/nombre de tu HAT
   ```

Variables de entorno disponibles (ver [`config.py`](config.py)): `ROBOGUITARRA_SF2_DIR`,
`ROBOGUITARRA_AUDIO_DRIVER`, `ROBOGUITARRA_ALSA_DEVICE`, `ROBOGUITARRA_SAMPLE_RATE`,
`ROBOGUITARRA_POLYPHONY`, `ROBOGUITARRA_GAIN`, `ROBOGUITARRA_MIDI_DRIVER`,
`ROBOGUITARRA_HOST`, `ROBOGUITARRA_PORT`.

## MIDI externo (teclado USB / hardware roboguitarra)

FluidSynth abre un puerto de entrada ALSA-seq. **Desde la propia interfaz web**
(sección *Entrada MIDI*) se listan los dispositivos disponibles y se conecta el
elegido con un botón; la selección se guarda en `last_session.json` y se
reconecta sola al arrancar. Por dentro usa `aconnect` y localiza nuestro
FluidSynth por el PID del proceso.

El hardware roboguitarra (solenoides) aparece como una fuente MIDI más en esa
lista. Si prefieres hacerlo a mano:

```bash
aconnect -l                 # lista puertos; busca el cliente "FLUID Synth (<pid>)"
aconnect 16:0 128:0         # <origen>  <FLUID Synth>
```

## Arranque automático en la Pi

```bash
sudo cp roboguitarra.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now roboguitarra
```

## API REST

| Método | Ruta                     | Descripción                                  |
|--------|--------------------------|----------------------------------------------|
| GET    | `/api/soundfonts`        | Lista de `.sf2` en la carpeta                |
| POST   | `/api/soundfont/load`    | `{filename}` carga el sf2 y devuelve presets |
| GET    | `/api/instruments`       | Instrumentos del sf2 cargado                 |
| POST   | `/api/instrument`        | `{bank, preset, channel?}` selecciona preset |
| GET    | `/api/params`            | Estado actual (gain/reverb/chorus/instrumento)|
| POST   | `/api/params`            | `{gain, reverb:{…}, chorus:{…}}` modula       |
| POST   | `/api/note/on`           | `{key, vel?}` (teclado en pantalla)          |
| POST   | `/api/note/off`          | `{key}`                                      |
| POST   | `/api/panic`             | Apaga todas las notas                        |
