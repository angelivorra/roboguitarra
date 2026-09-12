"""Roboguitarra — servidor Flask.

Sirve la interfaz web móvil y expone una API REST para controlar el motor
FluidSynth (cargar SoundFonts, elegir instrumento, modular y tocar notas).
"""
import socket
import time

from flask import Flask, jsonify, render_template, request

import config
import mastil
import midi
import soundfonts
from synth import engine

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True


@app.route("/")
@app.route("/robot")
def index():
    # /robot es la ruta que abre el panel central (lgptclient) al pulsar
    # sobre el dispositivo en su pantalla de inicio.
    return render_template("index.html")


@app.get("/api/health")
def api_health():
    """Health check: 200 si el motor de audio está arrancado, 503 si no.

    Mismo formato que el /api/health de lgptclient (status/name/timestamp),
    más el estado del motor FluidSynth.
    """
    ok = engine.started and engine.error is None
    body = {
        "status": "ok" if ok else "error",
        "name": socket.gethostname(),
        "timestamp": time.time(),
        "engine_started": engine.started,
        "error": engine.error,
        "soundfont": engine.current_sf2,
    }
    return jsonify(body), (200 if ok else 503)


# --------------------------------------------------------------- presets
@app.get("/api/presets")
def api_presets():
    import presets as preset_catalog

    state = engine.get_state()
    current = state.get("preset_index") or 0
    items = []
    for i, item in enumerate(preset_catalog.CATALOG):
        items.append(
            {
                "index": i,
                "name": item["name"],
                "filename": item["filename"],
                "bank": item["bank"],
                "preset": item["preset"],
                "effect_up": engine.effects_for_index(i)["up"],
                "effect_down": engine.effects_for_index(i)["down"],
                "active": i == current,
            }
        )
    return jsonify({"presets": items, "index": current})


@app.get("/api/effects")
def api_effects():
    import effects as fxcat

    state = engine.get_state()
    p = state.get("params") or {}
    return jsonify(
        {
            "effects": fxcat.list_effects(),
            "delays": fxcat.list_delays(),
            "up": p.get("effect_up"),
            "down": p.get("effect_down"),
            "delay": p.get("delay_kind") or "none",
        }
    )


@app.post("/api/effect")
def api_effect_set():
    data = request.get_json(force=True)
    effect_id = data.get("id") or data.get("effect")
    side = data.get("side") or "up"
    if not effect_id:
        return jsonify({"error": "Falta 'id'"}), 400
    try:
        engine.set_effect(effect_id, side=side)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    return jsonify(engine.get_state())


@app.post("/api/effect/default")
def api_effect_default():
    """Guarda efectos, gain y sala del preset activo."""
    try:
        engine.save_effect_default()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    return jsonify(engine.get_state())


@app.post("/api/presets/select")
def api_preset_select():
    data = request.get_json(force=True)
    try:
        engine.select_preset(int(data["index"]))
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Se requiere 'index'"}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    return jsonify(engine.get_state())


@app.post("/api/presets/step")
def api_preset_step():
    data = request.get_json(silent=True) or {}
    try:
        engine.step_preset(int(data.get("delta", 1)))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    return jsonify(engine.get_state())


# --------------------------------------------------------------- soundfonts
@app.get("/api/soundfonts")
def api_soundfonts():
    import presets as preset_catalog

    keep = {n.lower() for n in preset_catalog.catalog_filenames()}
    return jsonify(
        [sf for sf in soundfonts.list_soundfonts() if sf["filename"].lower() in keep]
    )


@app.post("/api/soundfont/load")
def api_soundfont_load():
    data = request.get_json(force=True)
    filename = data.get("filename")
    if not filename:
        return jsonify({"error": "Falta 'filename'"}), 400
    import presets as preset_catalog

    for i, item in enumerate(preset_catalog.CATALOG):
        if item["filename"] == filename:
            try:
                engine.select_preset(i)
            except Exception as exc:  # noqa: BLE001
                return jsonify({"error": str(exc)}), 500
            return jsonify(engine.get_state())
    return jsonify({"error": "Ese SoundFont no está en el catálogo"}), 404


@app.get("/api/instruments")
def api_instruments():
    import presets as preset_catalog

    return jsonify(
        [
            {
                "index": i,
                "name": item["name"],
                "filename": item["filename"],
                "bank": item["bank"],
                "preset": item["preset"],
            }
            for i, item in enumerate(preset_catalog.CATALOG)
        ]
    )


@app.post("/api/instrument")
def api_instrument():
    data = request.get_json(force=True)
    try:
        engine.select_instrument(
            bank=int(data["bank"]),
            preset=int(data["preset"]),
            channel=int(data.get("channel", 0)),
        )
    except (KeyError, ValueError):
        return jsonify({"error": "Se requieren 'bank' y 'preset'"}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    return jsonify(engine.get_state())


# --------------------------------------------------------------- parámetros
@app.get("/api/params")
def api_get_params():
    import tcp_bpm

    state = engine.get_state()
    snap = tcp_bpm.current_state()
    state["bpm"] = engine.bpm if engine.bpm is not None else snap.get("bpm")
    state["tcp_connected"] = snap.get("tcp_connected", False)
    state["playing"] = snap.get("playing", False)
    return jsonify(state)


@app.post("/api/params")
def api_set_params():
    data = request.get_json(force=True)
    try:
        if "gain" in data:
            engine.set_gain(data["gain"])
        if "reverb" in data:
            engine.set_reverb(**data["reverb"])
        if "chorus" in data:
            engine.set_chorus(**data["chorus"])
        if "reverb_send" in data or "chorus_send" in data:
            engine.set_sends(
                reverb=data.get("reverb_send"), chorus=data.get("chorus_send")
            )
        if "space_on" in data:
            engine.set_space_on(data["space_on"])
        if "pitch" in data:
            engine.set_pitch_bend(data["pitch"])
        if "robot" in data:
            engine.set_robot(data["robot"])
        if "effect_up" in data:
            engine.set_effect(data["effect_up"], side="up")
        if "effect_down" in data:
            engine.set_effect(data["effect_down"], side="down")
        if "delay_kind" in data:
            engine.set_delay_kind(data["delay_kind"])
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    return jsonify(engine.get_state())


# --------------------------------------------------------------------- MIDI
@app.get("/api/mastil")
def api_mastil():
    """Estado en vivo de las 3 cuerdas (serie del Leonardo + fallback MIDI)."""
    mastil.monitor.watch()
    try:
        sounding = engine.get_state().get("sounding") or []
    except Exception:  # noqa: BLE001
        sounding = []
    mastil.monitor.apply_midi_notes(sounding)
    snap = mastil.monitor.snapshot()
    snap["names"] = list(mastil.STRING_NAMES)
    return jsonify(snap)


@app.get("/api/midi")
def api_midi():
    try:
        sources = midi.list_sources()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"sources": [], "connected": engine.midi_source, "error": str(exc)})
    return jsonify({"sources": sources, "connected": engine.midi_source})


@app.post("/api/midi/connect")
def api_midi_connect():
    data = request.get_json(force=True)
    key = data.get("key")
    if not key:
        return jsonify({"error": "Falta 'key'"}), 400
    addr = midi.addr_for_key(key)
    if not addr:
        return jsonify({"error": "La fuente MIDI ya no está disponible"}), 404
    try:
        midi.connect(addr)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    engine.set_midi_source(key)
    return jsonify({"connected": key})


@app.post("/api/midi/disconnect")
def api_midi_disconnect():
    try:
        midi.disconnect_all()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    engine.set_midi_source(None)
    return jsonify({"connected": None})


# -------------------------------------------------------------------- notas
@app.post("/api/note/on")
def api_note_on():
    data = request.get_json(force=True)
    try:
        engine.note_on(
            key=int(data["key"]),
            velocity=int(data.get("vel", 100)),
            channel=int(data.get("channel", 0)),
        )
    except (KeyError, ValueError):
        return jsonify({"error": "Se requiere 'key'"}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@app.post("/api/note/off")
def api_note_off():
    data = request.get_json(force=True)
    try:
        engine.note_off(
            key=int(data["key"]), channel=int(data.get("channel", 0))
        )
    except (KeyError, ValueError):
        return jsonify({"error": "Se requiere 'key'"}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@app.post("/api/panic")
def api_panic():
    try:
        engine.panic()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


def main():
    import tcp_bpm

    def _on_bpm(bpm):
        snap = tcp_bpm.current_state()
        engine.set_bpm(bpm, connected=snap.get("tcp_connected"))

    tcp_bpm.start_tcp_client(on_bpm=_on_bpm)
    mastil.monitor.start()
    # Intenta arrancar el motor al inicio (no bloquea si falla)
    engine.start()
    # Restaura el último sf2 e instrumento usados, si los hay
    if engine.started:
        engine.restore_last_session()
        # Reconecta la fuente MIDI guardada, si sigue presente
        if engine.midi_source:
            addr = midi.addr_for_key(engine.midi_source)
            if addr:
                try:
                    midi.connect(addr)
                except Exception:  # noqa: BLE001
                    pass
    try:
        from waitress import serve

        print(f"Roboguitarra escuchando en http://{config.HOST}:{config.PORT}")
        # Un solo hilo de trabajo no es necesario: el SynthEngine se protege con
        # un lock, así que varias conexiones del móvil conviven sin problema.
        serve(app, host=config.HOST, port=config.PORT, threads=4)
    except ImportError:
        app.run(host=config.HOST, port=config.PORT, threaded=True)


if __name__ == "__main__":
    main()
