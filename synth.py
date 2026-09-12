"""Motor de sonido: envoltorio sobre FluidSynth (pyfluidsynth).

QSynth es la GUI de FluidSynth; aquí replicamos sus controles (gain, reverb,
chorus, selección de banco/preset) a través de la API de pyfluidsynth.

El objeto Synth de FluidSynth debe vivir durante toda la ejecución, así que se
usa como singleton (`engine`) protegido por un lock, porque FluidSynth no es
seguro para cambios de estado concurrentes.
"""
import json
import threading
import time

import fluidsynth

import config

# Tipo de evento MIDI Control Change / Program Change (nibble alto del estado).
_MIDI_NOTE_OFF = 0x80
_MIDI_NOTE_ON = 0x90
_MIDI_CONTROL_CHANGE = 0xB0
_MIDI_PROGRAM_CHANGE = 0xC0


class SynthEngine:
    def __init__(self):
        self._lock = threading.RLock()
        self.fs = None
        self.started = False
        self.error = None

        self.current_sfid = None
        self.current_sf2 = None          # nombre de archivo cargado
        self.current_instrument = None   # {"bank", "preset", "channel", "name", "index"}
        self.current_preset_index = 0    # índice en presets.CATALOG
        self.sfids = {}                  # filename -> sfid (catálogo precargado)
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
            # Un interruptor aplica o no las unidades nativas de sala.
            "space_on": True,
            # Pitch bend MIDI: 0..16383, centro 8192 = sin bend (transitorio,
            # no se guarda en la sesión; arranca centrado).
            "pitch": 8192,
            # Efectos del knob Robot: arriba / abajo (centro = limpio).
            "effect_up": "phaser",
            "effect_down": "bitcrush",
            "robot": 0.0,
            "delay_kind": "none",
        }
        self.shifter = None
        self.ladspa_error = None
        self.effect_by_preset = {}  # index -> {"up": id, "down": id}
        self.bpm = None
        self.tcp_connected = False
        # Notas MIDI que el motor tiene encendidas (para cazar drones).
        self._sounding = {}  # (channel, key) -> time.monotonic()
        self._notes_lock = threading.Lock()
        self._notes_watch = None

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
                self._start_notes_watch()
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

    def load_catalog(self):
        """Precarga todos los .sf2 del catálogo (sin descargar los anteriores)."""
        import presets

        with self._lock:
            self._ensure()
            self.sfids = {}
            for item in presets.CATALOG:
                filename = item["filename"]
                if filename in self.sfids:
                    continue
                path = presets.resolve_catalog_path(filename)
                sfid = self.fs.sfload(str(path))
                if sfid == -1:
                    raise RuntimeError(f"No se pudo cargar el SoundFont: {filename}")
                self.sfids[filename] = sfid

    def select_preset(self, index):
        """Activa un preset del catálogo por índice (circular)."""
        import presets

        with self._lock:
            self._ensure()
            if not self.sfids:
                self.load_catalog()
            item, index = presets.catalog_item(int(index))
            filename = item["filename"]
            sfid = self.sfids.get(filename)
            if sfid is None:
                raise RuntimeError(f"SoundFont no cargado: {filename}")
            # Corta notas del preset anterior antes de cambiar de programa.
            self._silence_all()
            self.current_sfid = sfid
            self.current_sf2 = filename
            self.current_preset_index = index
            self._program_all(item["bank"], item["preset"])
            self.current_instrument = {
                "bank": item["bank"],
                "preset": item["preset"],
                "channel": 0,
                "name": item["name"],
                "filename": filename,
                "index": index,
            }
            self._apply_preset_effect(item, index)
            self._apply_sends()
            self._apply_reverb()
            self._apply_chorus()
            self._save_session()

    def step_preset(self, delta=1):
        """Avanza o retrocede por el catálogo (delta +1 / -1)."""
        import presets

        n = len(presets.CATALOG)
        if n == 0:
            raise RuntimeError("Catálogo de presets vacío")
        self.select_preset((self.current_preset_index + int(delta)) % n)

    def select_instrument(self, bank, preset, channel=0):
        """Compatibilidad: solo admite bank/preset que existan en el catálogo."""
        import presets

        for i, item in enumerate(presets.CATALOG):
            if item["bank"] == int(bank) and item["preset"] == int(preset):
                self.select_preset(i)
                return
        raise ValueError("Ese instrumento no está en el catálogo de presets")

    # ----------------------------------------------------------------- sesión
    def _save_session(self):
        """Guarda el preset del catálogo y la fuente MIDI para restaurarlos."""
        try:
            config.STATE_FILE.write_text(
                json.dumps(
                    {
                        "preset_index": self.current_preset_index,
                        "soundfont": self.current_sf2,
                        "instrument": self.current_instrument,
                        "midi_source": self.midi_source,
                        "effect_by_preset": self.effect_by_preset,
                        "gain": self.params["gain"],
                        "reverb_send": self.params["reverb_send"],
                        "chorus_send": self.params["chorus_send"],
                        "space_on": self.params["space_on"],
                        "delay_kind": self.params["delay_kind"],
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
        """Carga el catálogo y el último preset usado."""
        try:
            data = json.loads(config.STATE_FILE.read_text())
        except Exception:  # noqa: BLE001
            data = {}
        self.midi_source = data.get("midi_source")
        saved_effects = data.get("effect_by_preset") or {}
        overlay = {}
        for k, v in saved_effects.items():
            try:
                idx = int(k)
            except (TypeError, ValueError):
                continue
            if isinstance(v, dict):
                overlay[idx] = v
        self.effect_by_preset = overlay
        if "reverb_send" in data:
            try:
                self.params["reverb_send"] = max(0, min(127, int(data["reverb_send"])))
            except (TypeError, ValueError):
                pass
        if "chorus_send" in data:
            try:
                self.params["chorus_send"] = max(0, min(127, int(data["chorus_send"])))
            except (TypeError, ValueError):
                pass
        if "space_on" in data:
            self.params["space_on"] = bool(data["space_on"])
        if "gain" in data:
            try:
                self.params["gain"] = max(0.0, min(2.0, float(data["gain"])))
            except (TypeError, ValueError):
                pass
        if data.get("delay_kind") in ("none", "delay", "tape"):
            self.params["delay_kind"] = data["delay_kind"]
        try:
            self.load_catalog()
            self.select_preset(int(data.get("preset_index") or 0))
            if self.fs:
                self.fs.setting("synth.gain", float(self.params["gain"]))
            self._apply_reverb()
            self._apply_chorus()
        except Exception as exc:  # noqa: BLE001
            self.error = f"restaurar catálogo: {exc}"

    # -------------------------------------------------------------- modulación
    def set_gain(self, value):
        with self._lock:
            self._ensure()
            self.params["gain"] = max(0.0, min(2.0, float(value)))
            self.fs.setting("synth.gain", float(self.params["gain"]))
            self._remember_preset_mix()
            self._save_session()

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
        on = bool(self.params.get("space_on", True)) and r["on"]
        self.fs.setting("synth.reverb.active", 1 if on else 0)
        self.fs.set_reverb(
            roomsize=float(r["roomsize"]),
            damping=float(r["damping"]),
            width=float(r["width"]),
            level=float(r["level"]),
        )

    def _apply_chorus(self):
        c = self.params["chorus"]
        on = bool(self.params.get("space_on", True)) and c["on"]
        self.fs.setting("synth.chorus.active", 1 if on else 0)
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
            self._remember_preset_mix()
            self._save_session()

    def set_space_on(self, enabled):
        """Activa o silencia las unidades nativas de reverb y chorus."""
        with self._lock:
            self._ensure()
            self.params["space_on"] = bool(enabled)
            self._apply_reverb()
            self._apply_chorus()
            self._remember_preset_mix()
            self._save_session()

    def _apply_sends(self):
        # Se aplica a los 16 canales para que valga tanto para el teclado en
        # pantalla (canal 0) como para el MIDI externo (USB / roboguitarra).
        for ch in range(16):
            self.fs.cc(ch, 91, int(self.params["reverb_send"]))
            self.fs.cc(ch, 93, int(self.params["chorus_send"]))

    def _load_ladspa(self):
        """Prepara el host LADSPA. Los plugins se montan al usar el knob
        Robot; en el centro el grafo está vacío (audio limpio)."""
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

    def _apply_robot(self):
        """Aplica el efecto de arriba o de abajo según el signo de robot.

        Misma regla para la web y para el CC MIDI del joystick:
        t > 0 → effect_up, t < 0 → effect_down, ~0 → limpio.
        El delay de sala (si está elegido) se monta siempre, al tempo TCP.
        """
        if not self.shifter:
            return
        t = float(self.params["robot"])
        delay = self.params.get("delay_kind") or "none"
        bpm = self.bpm if self.bpm else config.DEFAULT_BPM
        if t > 0.02:
            self.shifter.apply(self.params.get("effect_up"), t, delay, bpm)
        elif t < -0.02:
            self.shifter.apply(self.params.get("effect_down"), -t, delay, bpm)
        else:
            self.shifter.apply(None, 0.0, delay, bpm)

    def set_delay_kind(self, delay_id):
        import effects as fxcat

        with self._lock:
            if not fxcat.known_delay(delay_id):
                raise ValueError(f"Delay desconocido: {delay_id}")
            self._ensure()
            self.params["delay_kind"] = delay_id
            self._remember_preset_mix()
            self._apply_robot()
            self._save_session()

    def set_bpm(self, bpm, connected=None):
        """Tempo del tracker (TCP). Recalcula el delay si está activo."""
        with self._lock:
            try:
                self.bpm = max(20.0, min(300.0, float(bpm)))
            except (TypeError, ValueError):
                return
            if connected is not None:
                self.tcp_connected = bool(connected)
            if self.shifter:
                self.shifter.set_tempo(self.bpm)

    def set_tcp_connected(self, connected):
        with self._lock:
            self.tcp_connected = bool(connected)

    def _snapshot_mix(self):
        return {
            "up": self.params.get("effect_up"),
            "down": self.params.get("effect_down"),
            "gain": self.params["gain"],
            "reverb_send": self.params["reverb_send"],
            "chorus_send": self.params["chorus_send"],
            "space_on": self.params["space_on"],
            "delay_kind": self.params.get("delay_kind") or "none",
        }

    def _remember_preset_mix(self):
        """Guarda en la sesión el mix actual de este preset (aún sin Guardar)."""
        idx = self.current_preset_index
        prev = self.effect_by_preset.get(idx) or {}
        if not isinstance(prev, dict):
            prev = {}
        mix = self._snapshot_mix()
        mix["up"] = mix["up"] or prev.get("up")
        mix["down"] = mix["down"] or prev.get("down")
        self.effect_by_preset[idx] = mix

    def _apply_mix(self, mix):
        if not mix:
            return
        if "gain" in mix:
            self.params["gain"] = float(mix["gain"])
            if self.fs:
                self.fs.setting("synth.gain", float(mix["gain"]))
        if "reverb_send" in mix:
            self.params["reverb_send"] = int(mix["reverb_send"])
        if "chorus_send" in mix:
            self.params["chorus_send"] = int(mix["chorus_send"])
        if "space_on" in mix:
            self.params["space_on"] = bool(mix["space_on"])
        if mix.get("delay_kind") in ("none", "delay", "tape"):
            self.params["delay_kind"] = mix["delay_kind"]

    def _apply_preset_effect(self, item, index):
        import effects as fxcat

        overlay = self.effect_by_preset.get(index)
        saved = fxcat.saved_default_for(index)
        up, down = fxcat.resolve_pair(item, overlay, saved)
        self.params["effect_up"] = up
        self.params["effect_down"] = down
        self._apply_mix(fxcat.resolve_mix(overlay, saved))
        self._apply_robot()

    def effects_for_index(self, index):
        import effects as fxcat
        import presets as preset_catalog

        item = {}
        if 0 <= index < len(preset_catalog.CATALOG):
            item = preset_catalog.CATALOG[index]
        up, down = fxcat.resolve_pair(
            item,
            self.effect_by_preset.get(index),
            fxcat.saved_default_for(index),
        )
        return {"up": up, "down": down}

    def set_effect(self, effect_id, side="up"):
        """Asigna el efecto de arriba o de abajo del preset actual."""
        import effects as fxcat

        side = "down" if side == "down" else "up"
        with self._lock:
            if not fxcat.known_effect(effect_id):
                raise ValueError(f"Efecto desconocido: {effect_id}")
            key = "effect_down" if side == "down" else "effect_up"
            self.params[key] = effect_id
            self._remember_preset_mix()
            self._apply_robot()
            self._save_session()

    def save_effect_default(self):
        """Guarda efectos, gain y sala como predeterminado de este preset."""
        import effects as fxcat

        with self._lock:
            up = self.params.get("effect_up")
            down = self.params.get("effect_down")
            if not fxcat.known_effect(up) or not fxcat.known_effect(down):
                raise ValueError("Efecto desconocido")
            idx = self.current_preset_index
            mix = self._snapshot_mix()
            fxcat.save_preset_default(idx, up, down, self.current_sf2, mix=mix)
            self.effect_by_preset[idx] = mix
            self._save_session()

    def set_robot(self, t):
        """Knob/joystick bipolar [-1, 1]. Web y MIDI usan este mismo método."""
        with self._lock:
            self.params["robot"] = max(-1.0, min(1.0, float(t)))
            self._apply_robot()

    def _midi_log(self, msg):
        print(f"[midi] {msg}", flush=True)

    def _note_on_track(self, channel, key, src="midi"):
        with self._notes_lock:
            self._sounding[(int(channel), int(key))] = time.monotonic()
        self._midi_log(f"ON  ch{int(channel)+1} nota {int(key)}  ({src})")

    def _note_off_track(self, channel, key, src="midi"):
        with self._notes_lock:
            self._sounding.pop((int(channel), int(key)), None)
        self._midi_log(f"OFF ch{int(channel)+1} nota {int(key)}  ({src})")

    def _notes_snapshot(self):
        now = time.monotonic()
        with self._notes_lock:
            items = sorted(self._sounding.items())
        return [
            {
                "ch": ch,
                "note": key,
                "ms": int((now - t0) * 1000),
            }
            for (ch, key), t0 in items
        ]

    def _start_notes_watch(self):
        if self._notes_watch and self._notes_watch.is_alive():
            return

        def _loop():
            while True:
                time.sleep(2.0)
                snap = self._notes_snapshot()
                if not snap:
                    continue
                bits = ", ".join(
                    f"ch{n['ch']+1} n{n['note']} {n['ms']/1000:.1f}s" for n in snap
                )
                self._midi_log(f"VIVAS {bits}")

        self._notes_watch = threading.Thread(
            target=_loop, name="midi-vivas", daemon=True
        )
        self._notes_watch.start()

    def _midi_router(self, data, event):
        """Router MIDI custom (se ejecuta en el hilo del driver MIDI).

        Intercepta el CC del joystick que controla el efecto robot para
        replicar el knob bipolar de la web, y reenvía todo lo demás (notas,
        pitch bend, otros CC) a FluidSynth sin cambios. Debe devolver
        FLUID_OK (0).
        """
        try:
            etype = fluidsynth.fluid_midi_event_get_type(event)
            if etype in (_MIDI_NOTE_ON, _MIDI_NOTE_OFF):
                ch = fluidsynth.fluid_midi_event_get_channel(event)
                key = fluidsynth.fluid_midi_event_get_key(event)
                vel = fluidsynth.fluid_midi_event_get_velocity(event)
                if etype == _MIDI_NOTE_OFF or vel == 0:
                    self._note_off_track(ch, key)
                else:
                    self._note_on_track(ch, key)
            if etype == _MIDI_CONTROL_CHANGE:
                cc = fluidsynth.fluid_midi_event_get_control(event)
                val = fluidsynth.fluid_midi_event_get_value(event)
                if cc in (120, 123):
                    snap = self._notes_snapshot()
                    if snap:
                        bits = ", ".join(
                            f"ch{n['ch']+1} n{n['note']}" for n in snap
                        )
                        self._midi_log(f"CC{cc} AllOff  cortaba {bits}")
                    with self._notes_lock:
                        self._sounding.clear()
                if cc == config.ROBOT_CC:
                    # CC 0..127 con centro 64 -> t bipolar [-1, 1] (igual que
                    # el knob de la web). El firmware lo manda en varios canales,
                    # así que evitamos recalcular si no cambia.
                    t = max(-1.0, min(1.0, (val - 64) / 63.0))
                    if t != self.params["robot"]:
                        self.set_robot(t)
                    return 0  # FLUID_OK; no reenviar a FluidSynth
                if val >= 64:
                    if cc == config.PRESET_BTN_CC:
                        self.step_preset(1)
                        return 0
                    if cc == config.SPACE_BTN_CC:
                        self.set_space_on(not self.params.get("space_on", True))
                        return 0
                    if cc == config.PANIC_BTN_CC:
                        self.panic()
                        return 0
            elif etype == _MIDI_PROGRAM_CHANGE:
                getter = getattr(fluidsynth, "fluid_midi_event_get_program", None)
                if getter is not None and self.sfids:
                    import presets as preset_catalog

                    prog = getter(event)
                    if 0 <= prog < len(preset_catalog.CATALOG):
                        self.select_preset(prog)
                    return 0
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
            self._note_on_track(channel, key, src="web")

    def note_off(self, key, channel=0):
        with self._lock:
            self._ensure()
            self.fs.noteoff(channel, int(key))
            self._note_off_track(channel, key, src="web")

    def _silence_all(self):
        """Corta voces ya disparadas. CC123 a veces no basta con samples en loop."""
        with self._notes_lock:
            self._sounding.clear()
        for ch in range(16):
            self.fs.cc(ch, 120, 0)  # All Sound Off
            self.fs.cc(ch, 123, 0)  # All Notes Off
            self.fs.cc(ch, 121, 0)  # Reset All Controllers
            for key in range(128):
                self.fs.noteoff(ch, key)

    def panic(self):
        """Corta todo lo que suena: notas, cola de delay, bend y Robot al centro.

        No cambia el preset: el delay y la sala se quedan como están guardados.
        """
        with self._lock:
            self._ensure()
            snap = self._notes_snapshot()
            if snap:
                bits = ", ".join(
                    f"ch{n['ch']+1} n{n['note']} {n['ms']/1000:.1f}s" for n in snap
                )
                self._midi_log(f"PANIC  cortaba {bits}")
            else:
                self._midi_log("PANIC  (tracker vacío; si suena, el Note On no llegó)")
            self._silence_all()
            self.params["pitch"] = 8192
            for ch in range(16):
                self.fs.pitch_bend(ch, 0)
            self.params["robot"] = 0.0
            if self.shifter:
                self.shifter._tear_down()
            self._apply_robot()
            self._apply_sends()
            self._apply_reverb()
            self._apply_chorus()

    # ------------------------------------------------------------------ estado
    def get_state(self):
        with self._lock:
            return {
                "started": self.started,
                "error": self.error,
                "soundfont": self.current_sf2,
                "instrument": self.current_instrument,
                "preset_index": self.current_preset_index,
                "midi_source": self.midi_source,
                "ladspa": bool(self.shifter),
                "ladspa_error": self.ladspa_error,
                "params": self.params,
                "bpm": self.bpm,
                "tcp_connected": self.tcp_connected,
                "sounding": self._notes_snapshot(),
            }


# Singleton compartido por toda la app
engine = SynthEngine()
