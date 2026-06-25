"""Roboguitarra — servidor Flask.

Sirve la interfaz web móvil y expone una API REST para controlar el motor
FluidSynth (cargar SoundFonts, elegir instrumento, modular y tocar notas).
"""
from flask import Flask, jsonify, render_template, request

import config
import midi
import soundfonts
from synth import engine

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


# --------------------------------------------------------------- soundfonts
@app.get("/api/soundfonts")
def api_soundfonts():
    return jsonify(soundfonts.list_soundfonts())


@app.post("/api/soundfont/load")
def api_soundfont_load():
    data = request.get_json(force=True)
    filename = data.get("filename")
    if not filename:
        return jsonify({"error": "Falta 'filename'"}), 400
    try:
        path = soundfonts.resolve_soundfont(filename)
        engine.load_soundfont(path, filename)
        instruments = soundfonts.list_instruments(path)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    return jsonify({"soundfont": filename, "instruments": instruments})


@app.get("/api/instruments")
def api_instruments():
    if not engine.current_sf2:
        return jsonify([])
    path = soundfonts.resolve_soundfont(engine.current_sf2)
    return jsonify(soundfonts.list_instruments(path))


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
    return jsonify(engine.get_state())


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
        if "pitch" in data:
            engine.set_pitch_bend(data["pitch"])
        if "robot" in data:
            engine.set_robot(data["robot"])
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    return jsonify(engine.get_state())


# --------------------------------------------------------------------- MIDI
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
