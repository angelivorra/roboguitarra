"use strict";

const $ = (id) => document.getElementById(id);
const api = (path, body) =>
  fetch(path, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  }).then((r) => r.json());

// ----------------------------------------------------------------- estado UI
function setStatus(state) {
  const el = $("status");
  if (state.started) {
    el.textContent = "motor: ok";
    el.className = "status status--on";
  } else {
    el.textContent = "motor: " + (state.error ? "error" : "off");
    el.className = "status status--off";
    if (state.error) el.title = state.error;
  }
}

// ----------------------------------------------------------------- SoundFonts
async function loadSoundfontList() {
  const list = await api("/api/soundfonts");
  const sel = $("sf2-select");
  sel.innerHTML = "";
  if (!list.length) {
    sel.innerHTML = '<option value="">(carpeta soundfonts vacía)</option>';
    return;
  }
  for (const sf of list) {
    const o = document.createElement("option");
    o.value = sf.filename;
    o.textContent = `${sf.filename} (${sf.size_mb} MB)`;
    sel.appendChild(o);
  }
}

$("sf2-load").addEventListener("click", async () => {
  const filename = $("sf2-select").value;
  if (!filename) return;
  $("sf2-load").disabled = true;
  $("sf2-load").textContent = "Cargando…";
  try {
    const res = await api("/api/soundfont/load", { filename });
    if (res.error) throw new Error(res.error);
    $("sf2-current").textContent = `Cargado: ${res.soundfont}`;
    fillInstruments(res.instruments);
  } catch (e) {
    $("sf2-current").textContent = "Error: " + e.message;
  } finally {
    $("sf2-load").disabled = false;
    $("sf2-load").textContent = "Cargar";
  }
});

// ----------------------------------------------------------------- Instrumentos
function fillInstruments(instruments) {
  const sel = $("inst-select");
  sel.innerHTML = "";
  for (const ins of instruments) {
    const o = document.createElement("option");
    o.value = `${ins.bank}:${ins.preset}`;
    o.textContent = `${ins.bank}:${ins.preset} — ${ins.name}`;
    sel.appendChild(o);
  }
  const has = instruments.length > 0;
  sel.disabled = !has;
  $("inst-select-btn").disabled = !has;
}

$("inst-select-btn").addEventListener("click", async () => {
  const v = $("inst-select").value;
  if (!v) return;
  const [bank, preset] = v.split(":").map(Number);
  const res = await api("/api/instrument", { bank, preset });
  if (res.instrument) {
    $("inst-current").textContent =
      `Activo: banco ${res.instrument.bank}, preset ${res.instrument.preset}`;
  }
});

// ----------------------------------------------------------------- Knobs
// Potenciómetros giratorios. `onChange(value)` se llama al girar (Gain ya está
// conectado al motor; Reverb y Chorus aún son visuales).
const knobs = {};

function setupKnob(el, { onChange, format } = {}) {
  const min = parseFloat(el.dataset.min);
  const max = parseFloat(el.dataset.max);
  const def = parseFloat(el.dataset.value);
  let value = def;
  const dial = el.querySelector(".knob__dial");
  const pointer = el.querySelector(".knob__pointer");
  const out = el.querySelector(".knob__val");
  const SWEEP = 135; // grados de giro a cada lado del centro

  function render() {
    const t = (value - min) / (max - min);
    pointer.style.transform = `translateX(-50%) rotate(${-SWEEP + t * 2 * SWEEP}deg)`;
    out.textContent = format ? format(value) : value.toFixed(2);
    el.style.setProperty("--t", t);
  }
  function setValue(v, fire) {
    value = Math.min(max, Math.max(min, v));
    render();
    if (fire && onChange) onChange(value);
  }
  render();

  let startY = 0, startVal = 0, dragging = false;
  dial.addEventListener("pointerdown", (e) => {
    dragging = true;
    startY = e.clientY;
    startVal = value;
    dial.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  dial.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const dy = startY - e.clientY; // arrastrar hacia arriba = subir
    setValue(startVal + (dy / 150) * (max - min), true);
  });
  const stop = () => (dragging = false);
  dial.addEventListener("pointerup", stop);
  dial.addEventListener("pointercancel", stop);
  dial.addEventListener("dblclick", () => setValue(def, true)); // reset

  return { setValue: (v) => setValue(v, false), getValue: () => value };
}

// Envío al motor con debounce (para no saturar al arrastrar el knob).
function debounce(fn, ms) {
  let t;
  return (v) => {
    clearTimeout(t);
    t = setTimeout(() => fn(v), ms);
  };
}

// Configuración por knob: cómo se envía al motor y cómo se muestra el valor.
//  - gain  : volumen real (0..2)
//  - pitch : bend MIDI 0..16383 (centro 8192); el knob 0..1 -> 0..16383
//  - shift : frequency shifter robótico (0..1 -> 0..MAX_SHIFT_HZ Hz)
const PITCH_RANGE_ST = 2; // semitonos a cada lado (pitch_wheel_sens por defecto)
const MAX_SHIFT_HZ = 1500; // recorrido útil del frequency shifter (plugin llega a 5000)
const KNOB_CONFIG = {
  gain: {
    onChange: debounce((v) => api("/api/params", { gain: v }), 40),
    format: (v) => v.toFixed(2),
  },
  pitch: {
    onChange: debounce((v) => api("/api/params", { pitch: Math.round(v * 16383) }), 30),
    format: (v) => {
      const st = (v - 0.5) * 2 * PITCH_RANGE_ST;
      return (st >= 0 ? "+" : "") + st.toFixed(1) + " st";
    },
  },
  shift: {
    onChange: debounce((v) => api("/api/params", { shift: Math.round(v * MAX_SHIFT_HZ) }), 40),
    format: (v) => Math.round(v * MAX_SHIFT_HZ) + " Hz",
  },
};

document.querySelectorAll(".knob").forEach((el) => {
  const name = el.dataset.knob;
  knobs[name] = setupKnob(el, KNOB_CONFIG[name] || {});
});

// Estado básico (motor + soundfont) y sincroniza los knobs con el motor.
function applyState(state) {
  setStatus(state);
  if (state.soundfont) $("sf2-current").textContent = `Cargado: ${state.soundfont}`;
  const p = state.params;
  if (!p) return;
  if (knobs.gain && typeof p.gain === "number") knobs.gain.setValue(p.gain);
  if (knobs.pitch && typeof p.pitch === "number") knobs.pitch.setValue(p.pitch / 16383);
  if (knobs.shift && typeof p.shift === "number")
    knobs.shift.setValue(p.shift / MAX_SHIFT_HZ);
}

// ----------------------------------------------------------------- MIDI
async function loadMidiSources() {
  const data = await api("/api/midi");
  const sel = $("midi-select");
  sel.innerHTML = "";
  if (!data.sources || !data.sources.length) {
    sel.innerHTML = '<option value="">(sin dispositivos MIDI)</option>';
  } else {
    for (const s of data.sources) {
      const o = document.createElement("option");
      o.value = s.key;
      o.textContent = s.label;
      sel.appendChild(o);
    }
  }
  if (data.connected) {
    sel.value = data.connected;
    $("midi-current").textContent = `Conectado: ${data.connected}`;
  } else {
    $("midi-current").textContent = data.error ? `Error: ${data.error}` : "Sin conectar";
  }
}

$("midi-refresh").addEventListener("click", loadMidiSources);
$("midi-connect").addEventListener("click", async () => {
  const key = $("midi-select").value;
  if (!key) return;
  const res = await api("/api/midi/connect", { key });
  $("midi-current").textContent = res.error
    ? `Error: ${res.error}`
    : `Conectado: ${res.connected}`;
});
$("panic").addEventListener("click", () => api("/api/panic"));

// ----------------------------------------------------------------- arranque
(async function init() {
  await loadSoundfontList();
  await loadMidiSources();
  const state = await api("/api/params");
  applyState(state);
  if (state.soundfont) {
    const instruments = await api("/api/instruments");
    fillInstruments(instruments);
    if (state.instrument) {
      $("inst-select").value = `${state.instrument.bank}:${state.instrument.preset}`;
      $("inst-current").textContent =
        `Activo: banco ${state.instrument.bank}, preset ${state.instrument.preset}`;
    }
  }
})();
