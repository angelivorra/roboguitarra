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

// ----------------------------------------------------------------- Presets
async function applyPresetState(state) {
  const data = await api("/api/presets");
  const items = data.presets || [];
  window.__presetCount = items.length;
  const idx =
    typeof state.preset_index === "number" ? state.preset_index : data.index || 0;
  const item = items[idx] || {};
  $("preset-name").textContent = item.name || "—";
  $("preset-meta").textContent = items.length
    ? `${idx + 1} / ${items.length} · ${item.bank}:${item.preset}`
    : "sin presets";
  syncEffectSelects(state);
}

function fillEffectSelect(sel, effects, current) {
  sel.innerHTML = "";
  for (const fx of effects) {
    const o = document.createElement("option");
    o.value = fx.id;
    o.textContent = fx.name;
    sel.appendChild(o);
  }
  if (current) sel.value = current;
}

function syncEffectSelects(state) {
  const p = (state && state.params) || {};
  const up = $("effect-up");
  const down = $("effect-down");
  const delay = $("delay-kind");
  if (up && p.effect_up) up.value = p.effect_up;
  if (down && p.effect_down) down.value = p.effect_down;
  if (delay && p.delay_kind) delay.value = p.delay_kind;
  syncDelayTempo(state);
}

function syncDelayTempo(state) {
  const el = $("delay-tempo");
  if (!el) return;
  const bpm = state && state.bpm;
  const tcp = state && state.tcp_connected;
  if (typeof bpm === "number") {
    el.textContent = tcp
      ? `Tempo: ${Math.round(bpm)} BPM (TCP)`
      : `Tempo: ${Math.round(bpm)} BPM`;
  } else if (tcp) {
    el.textContent = "Tempo: esperando BPM por TCP";
  } else {
    el.textContent = "Tempo: 120 BPM (sin TCP)";
  }
}

async function loadEffectList(state) {
  const data = await api("/api/effects");
  const effects = data.effects || [];
  const p = (state && state.params) || {};
  fillEffectSelect($("effect-up"), effects, p.effect_up || data.up);
  fillEffectSelect($("effect-down"), effects, p.effect_down || data.down);
  fillEffectSelect($("delay-kind"), data.delays || [], p.delay_kind || data.delay || "none");
}

async function onEffectSideChange(side, selId) {
  const id = $(selId).value;
  if (!id) return;
  const state = await api("/api/effect", { id, side });
  if (state.error) {
    $("preset-meta").textContent = "Error: " + state.error;
    return;
  }
  applyState(state);
}

$("effect-up").addEventListener("change", () => onEffectSideChange("up", "effect-up"));
$("effect-down").addEventListener("change", () => onEffectSideChange("down", "effect-down"));

$("delay-kind").addEventListener("change", async () => {
  const kind = $("delay-kind").value;
  const state = await api("/api/params", { delay_kind: kind });
  if (state.error) {
    $("preset-meta").textContent = "Error: " + state.error;
    return;
  }
  applyState(state);
});

document.querySelectorAll(".js-save-preset").forEach((btn) => {
  btn.addEventListener("click", savePresetMix);
});

async function savePresetMix() {
  const buttons = document.querySelectorAll(".js-save-preset");
  const state = await api("/api/effect/default");
  if (state.error) {
    $("preset-meta").textContent = "Error: " + state.error;
    return;
  }
  applyState(state);
  buttons.forEach((btn) => {
    btn.textContent = "Guardado";
    btn.disabled = true;
  });
  setTimeout(() => {
    buttons.forEach((btn) => {
      btn.textContent = "Guardar";
      btn.disabled = false;
    });
  }, 1200);
}

$("preset-prev").addEventListener("click", async () => {
  const state = await api("/api/presets/step", { delta: -1 });
  if (state.error) {
    $("preset-meta").textContent = "Error: " + state.error;
    return;
  }
  applyState(state);
  await applyPresetState(state);
});
$("preset-next").addEventListener("click", async () => {
  const state = await api("/api/presets/step", { delta: 1 });
  if (state.error) {
    $("preset-meta").textContent = "Error: " + state.error;
    return;
  }
  applyState(state);
  await applyPresetState(state);
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
  const stop = () => {
    dragging = false;
    if (onChange && onChange.flush) onChange.flush();
  };
  dial.addEventListener("pointerup", stop);
  dial.addEventListener("pointercancel", stop);
  dial.addEventListener("dblclick", () => setValue(def, true)); // reset

  return { setValue: (v) => setValue(v, false), getValue: () => value };
}

// Mientras se arrastra: primer valor al momento, luego como mucho cada `ms`.
// Al soltar, flush manda el valor final. (Un debounce solo enviaba al parar.)
function throttle(fn, ms) {
  let last = 0;
  let timer = null;
  let pending = null;
  const send = (v) => {
    last = Date.now();
    timer = null;
    pending = null;
    fn(v);
  };
  const wrapped = (v) => {
    pending = v;
    const wait = ms - (Date.now() - last);
    if (last === 0 || wait <= 0) {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      send(v);
      return;
    }
    if (!timer) timer = setTimeout(() => send(pending), wait);
  };
  wrapped.flush = () => {
    if (pending == null) return;
    if (timer) clearTimeout(timer);
    send(pending);
  };
  return wrapped;
}

// Configuración por knob: cómo se envía al motor y cómo se muestra el valor.
//  - gain  : volumen real (0..2)
//  - pitch : bend MIDI 0..16383 (centro 8192); el knob 0..1 -> 0..16383
//  - robot : bipolar, centro limpio; ↑ effect_up, ↓ effect_down
//  - reverb/chorus : envío 0..1 → CC 0..127 (unidades nativas de FluidSynth)
const PITCH_RANGE_ST = 2;
const KNOB_CONFIG = {
  gain: {
    onChange: throttle((v) => api("/api/params", { gain: v }), 40),
    format: (v) => v.toFixed(2),
  },
  pitch: {
    onChange: throttle((v) => api("/api/params", { pitch: Math.round(v * 16383) }), 30),
    format: (v) => {
      const st = (v - 0.5) * 2 * PITCH_RANGE_ST;
      return (st >= 0 ? "+" : "") + st.toFixed(1) + " st";
    },
  },
  robot: {
    onChange: throttle((v) => api("/api/params", { robot: (v - 0.5) * 2 }), 40),
    format: (v) => {
      const t = (v - 0.5) * 2;
      if (t > 0.02) return "↑ " + Math.round(t * 100) + "%";
      if (t < -0.02) return "↓ " + Math.round(-t * 100) + "%";
      return "limpio";
    },
  },
  reverb: {
    onChange: throttle((v) => api("/api/params", { reverb_send: Math.round(v * 127) }), 40),
    format: (v) => Math.round(v * 100) + "%",
  },
  chorus: {
    onChange: throttle((v) => api("/api/params", { chorus_send: Math.round(v * 127) }), 40),
    format: (v) => Math.round(v * 100) + "%",
  },
};

document.querySelectorAll(".knob").forEach((el) => {
  const name = el.dataset.knob;
  knobs[name] = setupKnob(el, KNOB_CONFIG[name] || {});
});

function syncSounding(sounding) {
  const el = $("midi-notes");
  if (!el) return;
  const list = Array.isArray(sounding) ? sounding : [];
  if (!list.length) {
    el.textContent = "Notas MIDI: ninguna";
    el.classList.remove("hint--warn");
    return;
  }
  const bits = list.map((n) => {
    const s = Math.round((n.ms || 0) / 1000);
    return `ch${(n.ch || 0) + 1} n${n.note} ${s}s`;
  });
  el.textContent = "Notas MIDI: " + bits.join(" · ");
  const hung = list.some((n) => (n.ms || 0) >= 8000);
  el.classList.toggle("hint--warn", hung);
}

function applyState(state) {
  setStatus(state);
  syncSounding(state.sounding);
  const p = state.params;
  if (!p) return;
  if (knobs.gain && typeof p.gain === "number") knobs.gain.setValue(p.gain);
  if (knobs.pitch && typeof p.pitch === "number") knobs.pitch.setValue(p.pitch / 16383);
  if (knobs.robot && typeof p.robot === "number")
    knobs.robot.setValue(p.robot / 2 + 0.5);
  if (knobs.reverb && typeof p.reverb_send === "number")
    knobs.reverb.setValue(p.reverb_send / 127);
  if (knobs.chorus && typeof p.chorus_send === "number")
    knobs.chorus.setValue(p.chorus_send / 127);
  syncSpaceToggle(p.space_on !== false);
  syncEffectSelects(state);
}

function syncSpaceToggle(on) {
  const btn = $("space-toggle");
  const row = document.querySelector(".knobs--space");
  if (!btn) return;
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.textContent = on ? "Reverb y chorus: on" : "Reverb y chorus: off";
  btn.classList.toggle("btn--primary", on);
  if (row) row.classList.toggle("is-off", !on);
}

$("space-toggle").addEventListener("click", async () => {
  const on = $("space-toggle").getAttribute("aria-pressed") !== "true";
  const state = await api("/api/params", { space_on: on });
  if (state.error) {
    $("preset-meta").textContent = "Error: " + state.error;
    return;
  }
  applyState(state);
});

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

// ----------------------------------------------------------------- cuerdas
const FRETS = 17;
let mastilTimer = null;

function buildMastilStrings() {
  const root = $("mastil-strings");
  if (!root || root.childElementCount) return;
  const names = ["1ª Mi", "2ª Si", "3ª Sol"];
  names.forEach((name, i) => {
    const card = document.createElement("article");
    card.className = "string-card";
    card.id = `string-card-${i}`;
    const frets = Array.from({ length: FRETS }, (_, n) =>
      `<span class="fret" data-fret="${n + 1}">${n + 1}</span>`
    ).join("");
    card.innerHTML =
      `<div class="string-card__head">` +
      `<span class="string-card__name">${name}</span>` +
      `<span class="string-card__meta" id="string-meta-${i}">ADC — · traste —</span>` +
      `</div>` +
      `<div class="string-card__adc" aria-hidden="true"><span id="string-adc-${i}"></span></div>` +
      `<div class="frets">${frets}</div>`;
    root.appendChild(card);
  });
}

function paintMastil(data) {
  const badge = $("mastil-serial");
  if (badge) {
    if (data.serial_connected) {
      badge.textContent = "serie: ok";
      badge.className = "status status--on";
      badge.title = data.port || "";
    } else {
      badge.textContent = "serie: off";
      badge.className = "status status--off";
      badge.title = data.error || "";
    }
  }
  const strings = data.strings || [];
  for (let i = 0; i < 3; i++) {
    const s = strings[i] || {};
    const card = $(`string-card-${i}`);
    const meta = $(`string-meta-${i}`);
    const bar = $(`string-adc-${i}`);
    if (!card || !meta || !bar) continue;
    const adc = typeof s.adc === "number" ? s.adc : null;
    const fret = typeof s.fret === "number" ? s.fret : null;
    const src = s.source === "midi" ? " · MIDI" : "";
    meta.textContent =
      `ADC ${adc == null ? "—" : adc} · ` +
      (s.finger ? `traste ${fret == null ? "?" : fret}` : "al aire") +
      src;
    bar.style.width = adc == null ? "0%" : `${Math.min(100, (adc / 1023) * 100)}%`;
    card.classList.toggle("is-finger", !!s.finger);
    card.querySelectorAll(".fret").forEach((el) => {
      el.classList.toggle("is-on", fret != null && Number(el.dataset.fret) === fret);
    });
  }
}

async function pollMastil() {
  try {
    paintMastil(await api("/api/mastil"));
  } catch (err) {
    /* el siguiente ciclo reintenta */
  }
}

function openMastil() {
  buildMastilStrings();
  $("mastil-screen").hidden = false;
  pollMastil();
  if (mastilTimer) clearInterval(mastilTimer);
  mastilTimer = setInterval(pollMastil, 120);
}

function closeMastil() {
  $("mastil-screen").hidden = true;
  if (mastilTimer) {
    clearInterval(mastilTimer);
    mastilTimer = null;
  }
}

$("mastil-open").addEventListener("click", openMastil);
$("mastil-close").addEventListener("click", closeMastil);

// ----------------------------------------------------------------- arranque
(async function init() {
  await loadMidiSources();
  const state = await api("/api/params");
  applyState(state);
  await loadEffectList(state);
  await applyPresetState(state);
  setInterval(async () => {
    try {
      const live = await api("/api/params");
      syncDelayTempo(live);
      syncSounding(live.sounding);
    } catch (err) {
      /* el tempo se actualizará en el siguiente ciclo */
    }
  }, 2000);
})();
