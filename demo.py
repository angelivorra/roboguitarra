"""Genera un WAV de demostración de los controles del panel:

  1. Referencia limpia (sin efectos)
  2. Pitch bend  (±2 semitonos)
  3. Robot ARRIBA: frequency shifter (timbre metálico)
  4. Robot ABAJO: decimator / bitcrush (robot de 8 bits)

Render offline (driver 'file' de FluidSynth) -> determinista y sin ruido.
Uso:  ./.venv/bin/python demo.py [salida.wav]
"""
import sys
import time

import fluidsynth

from ladspa import RobotFx

SF = "soundfonts/2_solS.sf2"
PRESET = 0
OUT = sys.argv[1] if len(sys.argv) > 1 else "demo_roboguitarra.wav"

fs = fluidsynth.Synth(gain=0.7, samplerate=44100, **{"synth.ladspa.active": 1})
fs.setting("audio.file.name", OUT)
fs.setting("audio.file.type", "wav")
robot = RobotFx(fs.synth)
robot.load()
fs.start(driver="file")
sfid = fs.sfload(SF)
fs.program_select(0, sfid, 0, PRESET)

CHORD = [60, 64, 67]          # Do mayor
ARP = [60, 64, 67, 72]
_clock = [0.0]                # tiempo acumulado para la cronología
marks = []


def wait(t):
    time.sleep(t)
    _clock[0] += t


def mark(label):
    marks.append((_clock[0], label))
    print(f"  [{_clock[0]:5.1f}s] {label}")


def on(notes, vel=95):
    for n in notes:
        fs.noteon(0, n, vel)


def off(notes):
    for n in notes:
        fs.noteoff(0, n)


def sweep(setter, frm, to, dur, steps=48):
    for i in range(steps + 1):
        setter(frm + (to - frm) * i / steps)
        wait(dur / steps)


# 1) LIMPIO -----------------------------------------------------------------
mark("1) Referencia LIMPIA (arpegio + acorde)")
robot.set_robot(0.0)
fs.pitch_bend(0, 8192)
for n in ARP:
    on([n]); wait(0.35)
wait(1.0)
off(ARP); wait(0.8)

# 2) PITCH BEND -------------------------------------------------------------
mark("2) PITCH BEND: sube +2st, baja -2st")
on(CHORD)
sweep(lambda v: fs.pitch_bend(0, int(v)), 8192, 16383, 1.2)  # +2 st
sweep(lambda v: fs.pitch_bend(0, int(v)), 16383, 8192, 0.8)  # vuelve
sweep(lambda v: fs.pitch_bend(0, int(v)), 8192, 0, 1.2)      # -2 st
sweep(lambda v: fs.pitch_bend(0, int(v)), 0, 8192, 0.8)      # vuelve
off(CHORD); fs.pitch_bend(0, 8192); wait(0.9)

# 3) ROBOT ARRIBA: frequency shifter ---------------------------------------
mark("3) ROBOT arriba: FREQUENCY SHIFTER (metalico)")
on(CHORD)
sweep(robot.set_robot, 0.0, 1.0, 3.2)   # limpio -> shift máximo
wait(0.6)
sweep(robot.set_robot, 1.0, 0.0, 1.0)   # vuelve a limpio
off(CHORD); robot.set_robot(0.0); wait(0.9)

# 4) ROBOT ABAJO: bitcrush --------------------------------------------------
mark("4) ROBOT abajo: BITCRUSH 8-bit (lo-fi)")
on(CHORD)
sweep(robot.set_robot, 0.0, -1.0, 3.2)  # limpio -> crush máximo
wait(0.6)
sweep(robot.set_robot, -1.0, 0.0, 1.0)  # vuelve a limpio
off(CHORD); robot.set_robot(0.0); wait(0.8)

fs.delete()

print(f"\nWAV: {OUT}  ({_clock[0]:.1f}s)")
print("Cronología:")
for t, label in marks:
    print(f"  {t:5.1f}s  {label}")
