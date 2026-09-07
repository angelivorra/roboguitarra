"""Listado de archivos .sf2 y enumeración de sus instrumentos (presets).

Los presets se leen parseando directamente el chunk `phdr` del archivo SF2
(formato RIFF). Así no dependemos de librerías externas (sf2utils arrastra
`audioop`, eliminado en Python 3.13+) y funciona en cualquier versión de Python.
"""
import struct
from pathlib import Path

import config

ALLOWLIST_FILE = "allowlist.txt"


def _allowlisted_names():
    """Nombres de .sf2 permitidos (minúsculas), o None si no hay filtro.

    Si existe soundfonts/allowlist.txt con al menos una entrada, solo esos
    archivos se ofrecen en la UI. Si el archivo no existe o está vacío,
    se listan todos los .sf2 de la carpeta.
    """
    path = config.SOUNDFONT_DIR / ALLOWLIST_FILE
    if not path.is_file():
        return None
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.append(line.lower())
    return names or None


def list_soundfonts():
    """Devuelve los .sf2 disponibles en la carpeta configurada."""
    folder = config.SOUNDFONT_DIR
    if not folder.exists():
        return []
    allow = _allowlisted_names()
    items = []
    for path in sorted(p for p in folder.iterdir() if p.suffix.lower() == ".sf2"):
        if allow is not None and path.name.lower() not in allow:
            continue
        items.append(
            {
                "filename": path.name,
                "size_mb": round(path.stat().st_size / (1024 * 1024), 1),
            }
        )
    return items


def resolve_soundfont(filename):
    """Resuelve un nombre de archivo a una ruta segura dentro de la carpeta."""
    name = Path(filename).name  # evita rutas con '..' o absolutas
    path = config.SOUNDFONT_DIR / name
    if not path.exists() or path.suffix.lower() != ".sf2":
        raise FileNotFoundError(f"SoundFont no encontrado: {name}")
    return path


def list_instruments(path):
    """Devuelve [{bank, preset, name}, ...] del .sf2, ordenado y sin el
    preset centinela 'EOP' que el formato añade al final."""
    with open(path, "rb") as fh:
        phdr = _read_phdr(fh)

    instruments = []
    # Cada registro del chunk phdr ocupa 38 bytes:
    #   nombre[20], wPreset(uint16), wBank(uint16), wPresetBagNdx(uint16),
    #   dwLibrary, dwGenre, dwMorphology (uint32 cada uno)
    record_size = 38
    for i in range(len(phdr) // record_size):
        rec = phdr[i * record_size : (i + 1) * record_size]
        name = rec[0:20].split(b"\x00", 1)[0].decode("latin-1", "replace").strip()
        preset, bank = struct.unpack_from("<HH", rec, 20)
        if not name or name == "EOP":  # último registro = centinela
            continue
        instruments.append({"bank": bank, "preset": preset, "name": name})

    instruments.sort(key=lambda x: (x["bank"], x["preset"]))
    return instruments


def _read_phdr(fh):
    """Recorre el RIFF del SF2 y devuelve los bytes del sub-chunk 'phdr'."""
    if fh.read(4) != b"RIFF":
        raise ValueError("No es un archivo RIFF/SF2 válido")
    fh.read(4)  # tamaño RIFF
    if fh.read(4) != b"sfbk":
        raise ValueError("No es un SoundFont (falta 'sfbk')")

    while True:
        header = fh.read(8)
        if len(header) < 8:
            break
        chunk_id, size = struct.unpack("<4sI", header)
        if chunk_id == b"LIST":
            list_type = fh.read(4)
            end = fh.tell() + size - 4
            if list_type == b"pdta":
                while fh.tell() < end:
                    sub = fh.read(8)
                    if len(sub) < 8:
                        break
                    sub_id, sub_size = struct.unpack("<4sI", sub)
                    if sub_id == b"phdr":
                        return fh.read(sub_size)
                    fh.seek(sub_size + (sub_size & 1), 1)  # alineado a palabra
                break
            fh.seek(size - 4 + (size & 1), 1)
        else:
            fh.seek(size + (size & 1), 1)

    raise ValueError("No se encontró el chunk 'phdr' en el SoundFont")
