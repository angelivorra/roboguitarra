"""Gestión de entradas MIDI vía ALSA secuenciador (aconnect).

FluidSynth arranca con `midi.driver=alsa_seq`, lo que crea un puerto de entrada
ALSA. Aquí listamos las fuentes MIDI disponibles (teclados USB, hardware
roboguitarra, etc.) y las conectamos a ese puerto con `aconnect`.

Las direcciones numéricas (p.ej. "16:0") cambian al reconectar dispositivos, así
que para persistir usamos una "clave" estable = "<nombre cliente>/<nombre puerto>"
y la resolvemos a la dirección actual al conectar.
"""
import os
import re
import subprocess

FLUID_HINT = "fluid"  # para localizar el puerto de FluidSynth en los destinos

_CLIENT_RE = re.compile(r"^client (\d+): '(.+?)'")
_PORT_RE = re.compile(r"^\s+(\d+) '(.*)'")


def _run(args):
    env = dict(os.environ, LC_ALL="C", LANG="C")  # salida en inglés, parseable
    return subprocess.run(
        ["aconnect", *args], capture_output=True, text=True, env=env
    )


def _parse(flag):
    """Devuelve [{client, client_name, port, port_name}] de `aconnect <flag>`."""
    res = _run([flag])
    ports = []
    current = None
    for line in res.stdout.splitlines():
        m = _CLIENT_RE.match(line)
        if m:
            current = {"client": int(m.group(1)), "client_name": m.group(2).strip()}
            continue
        m = _PORT_RE.match(line)
        if m and current:
            ports.append(
                {
                    "client": current["client"],
                    "client_name": current["client_name"],
                    "port": int(m.group(1)),
                    "port_name": m.group(2).strip(),
                }
            )
    return ports


def list_sources():
    """Fuentes MIDI de entrada (dispositivos que pueden enviarnos notas)."""
    sources = []
    for p in _parse("-i"):
        if p["client"] == 0:  # System (Timer/Announce), no sirve
            continue
        if FLUID_HINT in p["client_name"].lower():  # nuestro propio synth
            continue
        sources.append(
            {
                "addr": f"{p['client']}:{p['port']}",
                "key": f"{p['client_name']}/{p['port_name']}",
                "label": f"{p['client_name']} — {p['port_name']}",
            }
        )
    return sources


def fluid_port():
    """Dirección 'client:port' del puerto de entrada de NUESTRO FluidSynth.

    Puede haber varias instancias "FLUID Synth" (procesos viejos). FluidSynth
    nombra su cliente "FLUID Synth (<pid>)", así que damos prioridad al que
    lleva el PID de este proceso; si no, caemos en la primera coincidencia.
    """
    mypid = str(os.getpid())
    fallback = None
    for p in _parse("-o"):
        if FLUID_HINT not in p["client_name"].lower():
            continue
        addr = f"{p['client']}:{p['port']}"
        if mypid in p["client_name"]:
            return addr
        if fallback is None:
            fallback = addr
    return fallback


def addr_for_key(key):
    for s in list_sources():
        if s["key"] == key:
            return s["addr"]
    return None


def disconnect_all():
    """Desconecta cualquier fuente conectada al puerto de FluidSynth."""
    fp = fluid_port()
    if not fp:
        return
    for s in list_sources():
        _run(["-d", s["addr"], fp])


def connect(addr):
    """Conecta una fuente (dirección 'client:port') a FluidSynth.

    Deja una sola conexión activa: limpia las anteriores antes.
    """
    fp = fluid_port()
    if not fp:
        raise RuntimeError(
            "No se encontró el puerto de FluidSynth "
            "(¿arrancó con midi.driver=alsa_seq?)"
        )
    disconnect_all()
    res = _run([addr, fp])
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or "aconnect falló")
