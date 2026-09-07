"""Catálogo fijo de presets de la roboguitarra.

Solo se puede circular por esta lista (anterior/siguiente o índice).
Cada entrada apunta a un .sf2 concreto y a un bank:preset de ese archivo.
"""
from pathlib import Path

import config

# El sample de 2_solS se usa como nota base de la 3ª cuerda (sol / MIDI 55),
# igual que Heavy Metal (overridingRootKey 55). El WAV suena ~G#3, pero el
# mástil mapea el aire de esa cuerda a 55: misma transposición que el resto.
SOL_ROOT_KEY = 55  # G3
SOL_PITCH_CENTS = 0

CATALOG = [
    {
        "name": "Sol S",
        "filename": "2_solS.sf2",
        "bank": 0,
        "preset": 0,
        "effect_up": "phaser",
        "effect_down": "bitcrush",
    },
    {
        "name": "gtr Texture",
        "filename": "gtr Texture.sf2",
        "bank": 0,
        "preset": 0,
        "effect_up": "phaser",
        "effect_down": "bitcrush",
    },
    {
        "name": "Heavy Rhythm",
        "filename": "Guitar Set Pasi's Heavy And Acoustic (3,745KB).sf2",
        "bank": 0,
        "preset": 0,
        "effect_up": "phaser",
        "effect_down": "bitcrush",
    },
    {
        "name": "Pop Slide",
        "filename": "Guitar Pop Slide (22KB).sf2",
        "bank": 0,
        "preset": 0,
        "effect_up": "phaser",
        "effect_down": "bitcrush",
    },
    {
        "name": "Heavy Chords",
        "filename": "Guitar Heavy Metal (1,1476KB).SF2",
        "bank": 0,
        "preset": 0,
        "effect_up": "phaser",
        "effect_down": "bitcrush",
    },
]


def catalog_filenames():
    """Nombres de .sf2 que hay que conservar."""
    return {item["filename"] for item in CATALOG}


def catalog_item(index):
    if not CATALOG:
        raise IndexError("Catálogo de presets vacío")
    n = len(CATALOG)
    return CATALOG[index % n], index % n


def resolve_catalog_path(filename):
    path = config.SOUNDFONT_DIR / Path(filename).name
    if not path.exists() or path.suffix.lower() != ".sf2":
        raise FileNotFoundError(f"SoundFont no encontrado: {filename}")
    return path
