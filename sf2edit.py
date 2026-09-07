"""Parches mínimos sobre archivos SF2 (RIFF)."""
import math
import struct
from pathlib import Path

# Generator IDs en el chunk igen.
_GEN_OVERRIDING_ROOT_KEY = 58
_GEN_SAMPLE_ID = 53
_GEN_SAMPLE_MODES = 54
_SHDR_RECORD = 46
_IGEN_RECORD = 4
_IBAG_RECORD = 4
# sampleModes: 1 = loop continuo (también en el release; no se oye el resto).
_SAMPLE_MODE_LOOP_CONTINUOUS = 1
_INTERP_PAD = 8


def patch_root_key(src, dst, midi_note, cents=0):
    """Copia un .sf2 corrigiendo la nota raíz del sample.

    Pone origPitch y chPitchCorrection en el chunk shdr, y
    overridingRootKey en igen. `cents` es la corrección de afinación
    (SoundFont: sample X cents agudo → pasar -X).
    """
    data = bytearray(Path(src).read_bytes())
    midi_note = int(midi_note)
    cents = int(cents)
    if not 0 <= midi_note <= 127:
        raise ValueError(f"nota MIDI fuera de rango: {midi_note}")
    if not -99 <= cents <= 99:
        raise ValueError(f"cents fuera de rango: {cents}")
    _patch_shdr_orig_pitch(data, midi_note, cents)
    _patch_igen_root_key(data, midi_note)
    Path(dst).write_bytes(data)


def _find_subchunk(data, list_type, sub_id):
    """Devuelve (offset, size) del subchunk dentro de LIST list_type."""
    if data[0:4] != b"RIFF" or data[8:12] != b"sfbk":
        raise ValueError("No es un SoundFont RIFF/sfbk")
    pos = 12
    end = len(data)
    while pos + 8 <= end:
        chunk_id = bytes(data[pos : pos + 4])
        size = struct.unpack_from("<I", data, pos + 4)[0]
        payload = pos + 8
        if chunk_id == b"LIST" and bytes(data[payload : payload + 4]) == list_type:
            inner = payload + 4
            list_end = payload + size
            while inner + 8 <= list_end:
                sid = bytes(data[inner : inner + 4])
                ssize = struct.unpack_from("<I", data, inner + 4)[0]
                if sid == sub_id:
                    return inner + 8, ssize
                inner += 8 + ssize + (ssize & 1)
        pos = payload + size + (size & 1)
    raise ValueError(f"No se encontró {list_type!r}/{sub_id!r}")


def _patch_shdr_orig_pitch(data, midi_note, cents=0):
    offset, size = _find_subchunk(data, b"pdta", b"shdr")
    for i in range(size // _SHDR_RECORD):
        rec = offset + i * _SHDR_RECORD
        name = bytes(data[rec : rec + 20]).split(b"\x00", 1)[0]
        if not name or name == b"EOS":
            continue
        data[rec + 40] = midi_note  # byOriginalPitch
        struct.pack_into("b", data, rec + 41, cents)  # chPitchCorrection


def _patch_igen_root_key(data, midi_note):
    offset, size = _find_subchunk(data, b"pdta", b"igen")
    for i in range(size // _IGEN_RECORD):
        rec = offset + i * _IGEN_RECORD
        oper = struct.unpack_from("<H", data, rec)[0]
        if oper == _GEN_OVERRIDING_ROOT_KEY:
            struct.pack_into("<H", data, rec + 2, midi_note)


def patch_sustain_loop(src, dst, start_sec, end_sec):
    """Copia un .sf2 dejando un loop continuo en la zona estable del sample.

    Reproduce el ataque hasta `start_sec`, luego repite [start, end) sin el
    slide posterior. Un crossfade iguala el empalme para que el ciclo no
    se oiga como un clic ni como un glissando.
    """
    data = bytearray(Path(src).read_bytes())
    smpl_off, smpl_size = _find_subchunk(data, b"sdta", b"smpl")
    shdr_off, shdr_size = _find_subchunk(data, b"pdta", b"shdr")

    patched = False
    for i in range(shdr_size // _SHDR_RECORD):
        rec = shdr_off + i * _SHDR_RECORD
        name = bytes(data[rec : rec + 20]).split(b"\x00", 1)[0]
        if not name or name == b"EOS":
            continue
        start, end, _sloop, _eloop, rate = struct.unpack_from("<IIIII", data, rec + 20)
        n = end - start
        if n <= 32 or rate <= 0:
            continue
        samples = _read_samples(data, smpl_off, smpl_size, start, n)
        loop_s, loop_e, fade_n = _find_loop_points(samples, rate, start_sec, end_sec)
        _crossfade_loop(samples, loop_s, loop_e, fade_n)
        _write_loop_pad(samples, loop_s, loop_e)
        _write_samples(data, smpl_off, smpl_size, start, samples)
        # dwEnd = primer sample que ya no se toca: 8 de interpolación tras el loop.
        new_end = start + loop_e + _INTERP_PAD
        if new_end > start + n:
            new_end = start + n
        struct.pack_into("<I", data, rec + 24, new_end)
        struct.pack_into("<I", data, rec + 28, start + loop_s)
        struct.pack_into("<I", data, rec + 32, start + loop_e)
        patched = True
        break
    if not patched:
        raise ValueError("No hay sample que parchear")
    _ensure_sample_modes(data, _SAMPLE_MODE_LOOP_CONTINUOUS)
    Path(dst).write_bytes(data)


def _read_samples(data, smpl_off, smpl_size, start, n):
    samples = []
    for k in range(n):
        byte_off = smpl_off + (start + k) * 2
        if byte_off + 2 > smpl_off + smpl_size:
            break
        samples.append(struct.unpack_from("<h", data, byte_off)[0])
    return samples


def _write_samples(data, smpl_off, smpl_size, start, samples):
    for k, value in enumerate(samples):
        byte_off = smpl_off + (start + k) * 2
        if byte_off + 2 > smpl_off + smpl_size:
            break
        struct.pack_into("<h", data, byte_off, int(value))


def _clip16(value):
    return max(-32768, min(32767, int(round(value))))


def _acf_period(samples, rate, fmin=90.0, fmax=160.0):
    pmin = max(1, int(rate / fmax))
    pmax = min(len(samples) // 2, int(rate / fmin))
    mean = sum(samples) / len(samples)
    centered = [v - mean for v in samples]
    best_p, best = pmin, None
    for period in range(pmin, pmax + 1):
        score = 0.0
        span = len(centered) - period
        for i in range(span):
            score += centered[i] * centered[i + period]
        if best is None or score > best:
            best, best_p = score, period
    return best_p


def _find_loop_points(samples, rate, start_sec, end_sec):
    """Elige start/end de loop en la zona estable, alineados al periodo."""
    lo = max(_INTERP_PAD, int(start_sec * rate))
    hi = min(len(samples) - _INTERP_PAD, int(end_sec * rate))
    if hi - lo < 64:
        raise ValueError("ventana de sustain demasiado corta")
    period = _acf_period(samples[lo:hi], rate)
    fade_n = period
    lo = max(lo, fade_n + _INTERP_PAD)

    def splice_score(start, end):
        # El crossfade solo toca [end-fade_n, end); el inicio del loop no cambia.
        err = 0.0
        look = min(32, fade_n)
        for i in range(1, look + 1):
            fade_i = fade_n - i
            t = (fade_i + 1) / fade_n
            w_end = math.cos(t * math.pi / 2)
            w_pre = math.sin(t * math.pi / 2)
            faded_end = (
                w_end * samples[end - fade_n + fade_i]
                + w_pre * samples[start - fade_n + fade_i]
            )
            delta = faded_end - samples[start - i]
            err += delta * delta
        return err

    best = None
    min_periods = 2
    max_periods = max(min_periods, (hi - lo) // period)
    for start in range(lo, hi - min_periods * period):
        for nper in range(min_periods, max_periods + 1):
            nominal = start + nper * period
            if nominal > hi:
                break
            for delta in range(-8, 9):
                end = nominal + delta
                if end <= start + period + 8 or end > hi:
                    continue
                if end + _INTERP_PAD > len(samples):
                    continue
                err = splice_score(start, end)
                # Preferir loops un poco más largos si el empalme es parecido.
                score = -err + 40.0 * nper
                if best is None or score > best[0]:
                    best = (score, start, end)
    if best is None:
        raise ValueError("no se encontró un loop válido")
    return best[1], best[2], fade_n


def _crossfade_loop(samples, start, end, fade_n):
    """Mezcla el final del loop con los samples que ya llevan al inicio.

    Así sample[end-1] ≈ sample[start-1] y el salto a start continúa la onda.
    """
    for i in range(fade_n):
        t = (i + 1) / fade_n
        w_end = math.cos(t * math.pi / 2)
        w_pre = math.sin(t * math.pi / 2)
        idx = end - fade_n + i
        mixed = w_end * samples[idx] + w_pre * samples[start - fade_n + i]
        samples[idx] = _clip16(mixed)


def _write_loop_pad(samples, start, end):
    """Copia el inicio del loop justo después de end (interpolación SF2)."""
    for i in range(_INTERP_PAD):
        dst = end + i
        if dst >= len(samples):
            break
        samples[dst] = samples[start + i]


def _ensure_sample_modes(data, amount):
    """Pone sampleModes en la zona de instrumento, justo antes de sampleID."""
    igen_off, igen_size = _find_subchunk(data, b"pdta", b"igen")
    n_gen = igen_size // _IGEN_RECORD
    sample_id_index = None
    for i in range(n_gen):
        rec = igen_off + i * _IGEN_RECORD
        oper = struct.unpack_from("<H", data, rec)[0]
        if oper == _GEN_SAMPLE_MODES:
            struct.pack_into("<H", data, rec + 2, amount)
            return
        if oper == _GEN_SAMPLE_ID and sample_id_index is None:
            sample_id_index = i
    if sample_id_index is None:
        raise ValueError("igen sin sampleID")

    insert_at = igen_off + sample_id_index * _IGEN_RECORD
    record = struct.pack("<HH", _GEN_SAMPLE_MODES, amount)
    data[insert_at:insert_at] = record

    # Tamaños RIFF / LIST pdta / igen.
    _bump_chunk_size(data, igen_off - 4, _IGEN_RECORD)
    pdta_list_off = _find_list_offset(data, b"pdta")
    _bump_chunk_size(data, pdta_list_off + 4, _IGEN_RECORD)
    _bump_chunk_size(data, 4, _IGEN_RECORD)

    ibag_off, ibag_size = _find_subchunk(data, b"pdta", b"ibag")
    for i in range(ibag_size // _IBAG_RECORD):
        rec = ibag_off + i * _IBAG_RECORD
        gen_ndx = struct.unpack_from("<H", data, rec)[0]
        if gen_ndx > sample_id_index:
            struct.pack_into("<H", data, rec, gen_ndx + 1)


def _bump_chunk_size(data, size_field_off, delta):
    size = struct.unpack_from("<I", data, size_field_off)[0]
    struct.pack_into("<I", data, size_field_off, size + delta)


def _find_list_offset(data, list_type):
    pos = 12
    end = len(data)
    while pos + 8 <= end:
        chunk_id = bytes(data[pos : pos + 4])
        size = struct.unpack_from("<I", data, pos + 4)[0]
        payload = pos + 8
        if chunk_id == b"LIST" and bytes(data[payload : payload + 4]) == list_type:
            return pos
        pos = payload + size + (size & 1)
    raise ValueError(f"No se encontró LIST {list_type!r}")


_GEN_KEY_RANGE = 43


def cover_keyboard_gaps(src, dst, midi_min=0, midi_max=127):
    """Rellena notas sin sample clonando las zonas existentes por octavas.

    Un SF2 de acordes suele mapear solo una octava (p.ej. 52-64). El mástil
    de la roboguitarra llega a MIDI 81: esas notas quedan mudas. Se copian
    las zonas al resto del teclado, transponiendo el mismo acorde.
    """
    data = bytearray(Path(src).read_bytes())
    zones = _parse_instrument_zones(data)
    extra = _missing_octave_zones(zones, midi_min, midi_max)
    if not extra:
        Path(dst).write_bytes(data)
        return 0
    global_zones = [z for z in zones if z["sample_id"] is None]
    originals = [z for z in zones if z["sample_id"] is not None]
    _write_instrument_zones(data, global_zones + originals + extra)
    Path(dst).write_bytes(data)
    return len(extra)


def _parse_instrument_zones(data):
    ibag_off, ibag_size = _find_subchunk(data, b"pdta", b"ibag")
    igen_off, igen_size = _find_subchunk(data, b"pdta", b"igen")
    n_bags = ibag_size // _IBAG_RECORD
    bags = [
        struct.unpack_from("<HH", data, ibag_off + i * _IBAG_RECORD)
        for i in range(n_bags)
    ]
    zones = []
    for i in range(n_bags - 1):
        g0, g1 = bags[i][0], bags[i + 1][0]
        zone = {
            "key_lo": 0,
            "key_hi": 127,
            "root": None,
            "sample_id": None,
        }
        for gi in range(g0, g1):
            oper, amt = struct.unpack_from("<HH", data, igen_off + gi * _IGEN_RECORD)
            if oper == _GEN_KEY_RANGE:
                zone["key_lo"] = amt & 0xFF
                zone["key_hi"] = (amt >> 8) & 0xFF
            elif oper == _GEN_OVERRIDING_ROOT_KEY:
                zone["root"] = amt
            elif oper == _GEN_SAMPLE_ID:
                zone["sample_id"] = amt
        zones.append(zone)
    return zones


def _missing_octave_zones(zones, midi_min, midi_max):
    prototypes = [z for z in zones if z["sample_id"] is not None]
    covered = [False] * 128
    for zone in prototypes:
        for note in range(zone["key_lo"], zone["key_hi"] + 1):
            covered[note] = True
    by_pc = {}
    for zone in prototypes:
        root = zone["root"]
        for note in range(zone["key_lo"], zone["key_hi"] + 1):
            stretch = abs(note - root) if root is not None else 0
            prev = by_pc.get(note % 12)
            if prev is None or stretch < prev[0]:
                by_pc[note % 12] = (stretch, zone)

    assigned = [None] * 128
    lo = max(0, int(midi_min))
    hi = min(127, int(midi_max))
    for note in range(lo, hi + 1):
        if covered[note]:
            continue
        hit = by_pc.get(note % 12)
        if not hit:
            continue
        proto = hit[1]
        assigned[note] = (proto["sample_id"], proto["root"])

    extra = []
    note = lo
    while note <= hi:
        ident = assigned[note]
        if ident is None:
            note += 1
            continue
        start = note
        while note <= hi and assigned[note] == ident:
            note += 1
        extra.append(
            {
                "key_lo": start,
                "key_hi": note - 1,
                "root": ident[1],
                "sample_id": ident[0],
            }
        )
    return extra


def _write_instrument_zones(data, zones):
    igen = bytearray()
    ibag = bytearray()
    for zone in zones:
        ibag += struct.pack("<HH", len(igen) // _IGEN_RECORD, 0)
        key_amt = zone["key_lo"] | (zone["key_hi"] << 8)
        igen += struct.pack("<HH", _GEN_KEY_RANGE, key_amt)
        if zone["root"] is not None:
            igen += struct.pack("<HH", _GEN_OVERRIDING_ROOT_KEY, zone["root"])
        if zone["sample_id"] is not None:
            igen += struct.pack("<HH", _GEN_SAMPLE_ID, zone["sample_id"])
    # Terminadores SF2.
    ibag += struct.pack("<HH", len(igen) // _IGEN_RECORD, 0)
    igen += struct.pack("<HH", 0, 0)

    _replace_subchunk_payload(data, b"pdta", b"ibag", bytes(ibag))
    _replace_subchunk_payload(data, b"pdta", b"igen", bytes(igen))

    inst_off, inst_size = _find_subchunk(data, b"pdta", b"inst")
    n_inst = inst_size // 22
    if n_inst >= 2:
        # EOI apunta al bag terminador (= número de zonas).
        struct.pack_into("<H", data, inst_off + 22 + 20, len(zones))


def _replace_subchunk_payload(data, list_type, sub_id, new_payload):
    off, size = _find_subchunk(data, list_type, sub_id)
    old_padded = size + (size & 1)
    new_size = len(new_payload)
    new_padded = new_size + (new_size & 1)
    pad = b"\x00" if (new_size & 1) else b""
    data[off : off + old_padded] = new_payload + pad
    struct.pack_into("<I", data, off - 4, new_size)
    delta = new_padded - old_padded
    if delta:
        list_off = _find_list_offset(data, list_type)
        _bump_chunk_size(data, list_off + 4, delta)
        _bump_chunk_size(data, 4, delta)


def fade_sample_tail(path, fade_ms=80):
    """Aplica un fade-out al final de cada sample y desactiva el loop.

    Evita el 'golpe' de relanzar el ataque (loop que incluye el golpe inicial)
    y el clic al terminar el one-shot.
    """
    data = bytearray(Path(path).read_bytes())
    smpl_off, smpl_size = _find_subchunk(data, b"sdta", b"smpl")
    shdr_off, shdr_size = _find_subchunk(data, b"pdta", b"shdr")
    fade_ms = max(1, int(fade_ms))

    for i in range(shdr_size // _SHDR_RECORD):
        rec = shdr_off + i * _SHDR_RECORD
        name = bytes(data[rec : rec + 20]).split(b"\x00", 1)[0]
        if not name or name == b"EOS":
            continue
        start, end, _sloop, _eloop, rate = struct.unpack_from("<IIIII", data, rec + 20)
        n = end - start
        if n <= 1 or rate <= 0:
            continue
        fade_n = min(n - 1, max(1, int(rate * fade_ms / 1000)))
        fade_from = n - fade_n
        for k in range(fade_from, n):
            t = (k - fade_from) / fade_n
            gain = 1.0 - t
            abs_index = start + k
            byte_off = smpl_off + abs_index * 2
            if byte_off + 2 > smpl_off + smpl_size:
                break
            sample = struct.unpack_from("<h", data, byte_off)[0]
            struct.pack_into("<h", data, byte_off, int(sample * gain))
        # Sin loop: startloop = endloop = último sample
        struct.pack_into("<I", data, rec + 28, start)
        struct.pack_into("<I", data, rec + 32, start)

    Path(path).write_bytes(data)
