"""Motor de sonido: envoltorio sobre FluidSynth (pyfluidsynth).

QSynth es la GUI de FluidSynth; aquí replicamos sus controles (gain, reverb,
chorus, selección de banco/preset) a través de la API de pyfluidsynth.

El objeto Synth de FluidSynth debe vivir durante toda la ejecución, así que se
usa como singleton (`engine`) protegido por un lock, porque FluidSynth no es
seguro para cambios de estado concurrentes.
"""
import json
import threading

import fluidsynth

import config

# Tipo de evento MIDI Control Change (nibble alto del byte de estado).
_MIDI_CONTROL_CHANGE = 0xB0


class SynthEngine:
    def __init__(self):
        self._lock = threading.RLock()
        self.fs = None
        self.started = False
        self.error = None

        self.current_sfid = None
        self.current_sf2 = None          # nombre de archivo cargado
        self.current_instrument = None   # {"bank", "preset", "channel"}
        self.midi_source = None          # clave estable de la fuente MIDI elegida

        # Estado de los parámetros tipo QSynth (valores de arranque de FluidSynth)
        self.params = {
            "gain": config.DEFAULT_GAIN,
            # Unidades de efecto preconfiguradas a valores musicales. Los knobs
            # de la UI controlan el "send" (cuánta señal entra a cada efecto),
            # no estos parámetros internos.
            "reverb": {
                "on": True,
                "roomsize": 0.4,
                "damping": 0.5,
                "width": 0.6,
                "level": 0.5,
            },
            "chorus": {
                "on": True,
                "nr": 3,
                "level": 4.0,
                "speed": 0.3,
                "depth": 12.0,
                "type": 0,  # 0 = seno, 1 = triángulo
            },
            # Envíos por canal (CC91 reverb, CC93 chorus), 0..127
            "reverb_send": 20,
            "chorus_send": 15,
            # Pitch bend MIDI: 0..16383, centro 8192 = sin bend (transitorio,
            # no se guarda en la sesión; arranca centrado).
            "pitch": 8192,
            # Efecto robótico LADSPA, knob bipolar [-1, 1]: 0 = limpio,
            # >0 = phaser + bits muy reducidos, <0 = bitcrusher (bits+sr).
            "robot": 0.0,
        }
        self.shifter = None
        self.ladspa_error = None

    # ------------------------------------------------------------------ ciclo
    def start(self):
        """Arranca el motor. Si falla (p.ej. sin audio en el equipo de dev),
        guarda el error pero no rompe la app: la UI sigue cargando."""
        with self._lock:
            if self.started:
                return
            try:
                # synth.ladspa.active debe fijarse al crear el synth (los kwargs
                # de pyfluidsynth se aplican antes de new_fluid_synth).
                self.fs = fluidsynth.Synth(
                    gain=self.params["gain"],
                    samplerate=config.SAMPLE_RATE,
                    **{"synth.ladspa.active": 1},
                )
                self.fs.setting("synth.polyphony", config.POLYPHONY)
                if config.ALSA_DEVICE:
                    self.fs.setting("audio.alsa.device", config.ALSA_DEVICE)
                if config.AUDIO_DRIVER == "jack":
                    # Conecta los puertos a system:playback sin depender de
                    # un gestor de conexiones externo (Patchbox OS).
                    self.fs.setting("audio.jack.autoconnect", 1)
                # Cargar el efecto LADSPA antes de arrancar el driver de audio.
                self._load_ladspa()
                # midi_router = callback propio: intercepta el CC del joystick
                # que controla el efecto robot y reenvía el resto a FluidSynth.
                self.fs.start(
                    driver=config.AUDIO_DRIVER,
                    midi_driver=config.MIDI_DRIVER,
                    midi_router=self._midi_router,
                )
                self._apply_reverb()
                self._apply_chorus()
                self._apply_sends()
                self.started = True
                self.error = None
            except Exception as exc:  # noqa: BLE001
                self.error = f"{type(exc).__name__}: {exc}"
                self.started = False

    def _ensure(self):
        if not self.started:
            self.start()
        if not self.started:
            raise RuntimeError(self.error or "El motor de audio no está iniciado")

    # ------------------------------------------------------------- soundfonts
    def _program_all(self, bank, preset):
        """Asigna el mismo bank/preset a los canales 0..MIDI_CHANNELS-1.

        El hardware roboguitarra emite note-in por varios canales; así todos
        suenan con el instrumento elegido, no con el preset por defecto.
        """
        for ch in range(config.MIDI_CHANNELS):
            self.fs.program_select(ch, self.current_sfid, bank, preset)

    def load_soundfont(self, path, filename):
        with self._lock:
            self._ensure()
            if self.current_sfid is not None:
                try:
                    self.fs.sfunload(self.current_sfid)
                except Exception:  # noqa: BLE001
                    pass
                self.current_sfid = None
            sfid = self.fs.sfload(str(path))
            if sfid == -1:
                raise RuntimeError(f"No se pudo cargar el SoundFont: {filename}")
            self.current_sfid = sfid
            self.current_sf2 = filename
            # Selecciona el primer instrumento por defecto en todos los canales.
            self._program_all(0, 0)
            self.current_instrument = {"bank": 0, "preset": 0, "channel": 0}
            self._apply_sends()  # el cambio de programa puede resetear los CC
            self._save_session()
            return sfid

    def select_instrument(self, bank, preset, channel=0):
        with self._lock:
            self._ensure()
            if self.current_sfid is None:
                raise RuntimeError("Carga primero un SoundFont")
            # El mismo instrumento suena en los canales 0..MIDI_CHANNELS-1, así
            # que el hardware roboguitarra suena igual venga por el canal que venga.
            self._program_all(bank, preset)
            self.current_instrument = {
                "bank": bank,
                "preset": preset,
                "channel": channel,
            }
            self._apply_sends()  # el cambio de programa puede resetear los CC
            self._save_session()

    # ----------------------------------------------------------------- sesión
    def _save_session(self):
        """Guarda sf2, instrumento y fuente MIDI para restaurarlos al arrancar."""
        try:
            config.STATE_FILE.write_text(
                json.dumps(
                    {
                        "soundfont": self.current_sf2,
                        "instrument": self.current_instrument,
                        "midi_source": self.midi_source,
                    }
                )
            )
        except Exception:  # noqa: BLE001
            pass

    def set_midi_source(self, key):
        """Recuerda la fuente MIDI seleccionada (la conexión la hace midi.py)."""
        with self._lock:
            self.midi_source = key
            self._save_session()

    def restore_last_session(self):
        """Carga el último sf2 e instrumento usados, si el archivo aún existe."""
        try:
            data = json.loads(config.STATE_FILE.read_text())
        except Exception:  # noqa: BLE001
            return
        # La fuente MIDI se recuerda aquí; la conexión real la hace app.py
        self.midi_source = data.get("midi_source")
        filename = data.get("soundfont")
        if not filename:
            return
        import soundfonts  # import diferido para evitar acoplamiento

        try:
            path = soundfonts.resolve_soundfont(filename)
        except FileNotFoundError:
            return  # el sf2 ya no está; se ignora silenciosamente
        try:
            self.load_soundfont(path, filename)
            inst = data.get("instrument") or {}
            if "bank" in inst and "preset" in inst:
                self.select_instrument(
                    inst["bank"], inst["preset"], inst.get("channel", 0)
                )
        except Exception as exc:  # noqa: BLE001
            self.error = f"restaurar sesión: {exc}"

    # -------------------------------------------------------------- modulación
    def set_gain(self, value):
        with self._lock:
            self._ensure()
            self.params["gain"] = float(value)
            self.fs.setting("synth.gain", float(value))

    def set_reverb(self, **kwargs):
        with self._lock:
            self._ensure()
            self.params["reverb"].update(
                {k: v for k, v in kwargs.items() if v is not None}
            )
            self._apply_reverb()

    def set_chorus(self, **kwargs):
        with self._lock:
            self._ensure()
            self.params["chorus"].update(
                {k: v for k, v in kwargs.items() if v is not None}
            )
            self._apply_chorus()

    def _apply_reverb(self):
        # En FluidSynth 2.x el on/off es el setting 'synth.reverb.active'
        # (no existe set_reverb_on en pyfluidsynth 1.4).
        r = self.params["reverb"]
        self.fs.setting("synth.reverb.active", 1 if r["on"] else 0)
        self.fs.set_reverb(
            roomsize=float(r["roomsize"]),
            damping=float(r["damping"]),
            width=float(r["width"]),
            level=float(r["level"]),
        )

    def _apply_chorus(self):
        c = self.params["chorus"]
        self.fs.setting("synth.chorus.active", 1 if c["on"] else 0)
        self.fs.set_chorus(
            nr=int(c["nr"]),
            level=float(c["level"]),
            speed=float(c["speed"]),
            depth=float(c["depth"]),
            type=int(c["type"]),
        )

    def set_sends(self, reverb=None, chorus=None):
        """Cantidad de señal enviada a cada efecto (lo que controlan los knobs).

        Es el envío por canal vía CC91 (reverb) / CC93 (chorus), 0..127.
        """
        with self._lock:
            self._ensure()
            if reverb is not None:
                self.params["reverb_send"] = max(0, min(127, int(reverb)))
            if chorus is not None:
                self.params["chorus_send"] = max(0, min(127, int(chorus)))
            self._apply_sends()

    def _apply_sends(self):
        # Se aplica a los 16 canales para que valga tanto para el teclado en
        # pantalla (canal 0) como para el MIDI externo (USB / roboguitarra).
        for ch in range(16):
            self.fs.cc(ch, 91, int(self.params["reverb_send"]))
            self.fs.cc(ch, 93, int(self.params["chorus_send"]))

    def _load_ladspa(self):
        """Carga la cadena LADSPA del efecto robótico (phaser + crush +
        bitcrusher). Si falla, se ignora: el resto del motor sigue
        funcionando sin el efecto."""
        try:
            from ladspa import RobotFx

            fx = RobotFx(self.fs.synth)
            if fx.load():
                self.shifter = fx
            else:
                self.shifter = None
                self.ladspa_error = fx.error
        except Exception as exc:  # noqa: BLE001
            self.shifter = None
            self.ladspa_error = str(exc)

    def set_robot(self, t):
        """Efecto robótico, knob bipolar [-1, 1] (0 = limpio)."""
        with self._lock:
            self.params["robot"] = max(-1.0, min(1.0, float(t)))
            if self.shifter:
                self.shifter.set_robot(self.params["robot"])

    def _midi_router(self, data, event):
        """Router MIDI custom (se ejecuta en el hilo del driver MIDI).

        Intercepta el CC del joystick que controla el efecto robot para
        replicar el knob bipolar de la web, y reenvía todo lo demás (notas,
        pitch bend, otros CC) a FluidSynth sin cambios. Debe devolver
        FLUID_OK (0).
        """
        try:
            if fluidsynth.fluid_midi_event_get_type(event) == _MIDI_CONTROL_CHANGE:
                if fluidsynth.fluid_midi_event_get_control(event) == config.ROBOT_CC:
                    val = fluidsynth.fluid_midi_event_get_value(event)
                    # CC 0..127 con centro 64 -> t bipolar [-1, 1] (igual que
                    # el knob de la web). El firmware lo manda en varios canales,
                    # así que evitamos recalcular si no cambia.
                    t = max(-1.0, min(1.0, (val - 64) / 63.0))
                    if t != self.params["robot"]:
                        self.set_robot(t)
                    return 0  # FLUID_OK; no reenviar a FluidSynth
        except Exception:  # noqa: BLE001
            pass  # nunca romper el flujo MIDI por un fallo aquí
        # Resto de eventos: comportamiento por defecto de FluidSynth.
        return fluidsynth.fluid_synth_handle_midi_event(self.fs.synth, event)

    def set_pitch_bend(self, value):
        """Pitch bend MIDI (0..16383, centro 8192). Afecta a todos los canales.

        Ojo: pyfluidsynth.pitch_bend() espera un valor CON SIGNO (0 = sin bend)
        y le suma 8192 internamente, así que convertimos el valor MIDI crudo
        restándole 8192.
        """
        with self._lock:
            self._ensure()
            v = max(0, min(16383, int(value)))
            self.params["pitch"] = v
            for ch in range(16):
                self.fs.pitch_bend(ch, v - 8192)

    # ------------------------------------------------------------------ notas
    def note_on(self, key, velocity=100, channel=0):
        with self._lock:
            self._ensure()
            self.fs.noteon(channel, int(key), int(velocity))

    def note_off(self, key, channel=0):
        with self._lock:
            self._ensure()
            self.fs.noteoff(channel, int(key))

    def panic(self):
        """Apaga todas las notas en todos los canales (CC 123)."""
        with self._lock:
            self._ensure()
            for ch in range(16):
                self.fs.cc(ch, 123, 0)

    # ------------------------------------------------------------------ estado
    def get_state(self):
        with self._lock:
            return {
                "started": self.started,
                "error": self.error,
                "soundfont": self.current_sf2,
                "instrument": self.current_instrument,
                "midi_source": self.midi_source,
                "ladspa": bool(self.shifter),
                "ladspa_error": self.ladspa_error,
                "params": self.params,
            }


# Singleton compartido por toda la app
engine = SynthEngine()
