#!/usr/bin/env bash
# Despliegue de Roboguitarra en la Raspberry Pi (ver tareas "Pi: ..." en VS Code).
#
# La Pi NO tiene internet en funcionamiento normal: las dependencias Python se
# descargan aquí como wheels (arm64/py311) y se copian con rsync; pip instala
# en la Pi con --no-index. Los paquetes del sistema (swh-plugins para el efecto
# Robot) solo se intentan instalar si faltan y la Pi tiene internet en ese
# momento; si no, se avisa y se continúa (el efecto queda inactivo).
#
#   ./deploy.sh panel   Copia el código del panel de control, instala
#                       dependencias y (re)arranca el servicio systemd.
#   ./deploy.sh sf2     Copia los soundfonts (rsync incremental, no borra los
#                       que solo existan en la Pi).
#   ./deploy.sh all     panel + sf2.
set -euo pipefail

PI="patch@192.168.0.20"
DIR="/home/patch/roboguitarra"
WHEELS=".deploy-wheels"
# Python de la Pi (bookworm) y plataforma de sus wheels compilados
PI_PY="311"
PI_PLAT="manylinux2014_aarch64"
cd "$(dirname "$0")"

deploy_panel() {
    echo "==> Descargando wheels para la Pi (arm64, py$PI_PY) en $WHEELS/"
    if ! python3 -m pip download -r requirements.txt -d "$WHEELS" \
        --python-version "$PI_PY" --platform "$PI_PLAT" --only-binary=:all: -q; then
        if [ -d "$WHEELS" ] && ls "$WHEELS"/*.whl >/dev/null 2>&1; then
            echo "AVISO: pip download falló (¿sin internet?); se usan los wheels ya descargados."
        else
            echo "ERROR: no hay wheels en $WHEELS/ y pip download falló." >&2
            exit 1
        fi
    fi

    echo "==> Copiando panel de control a $PI:$DIR"
    rsync -avz --delete \
        --exclude '.git' \
        --exclude '.venv' \
        --exclude '__pycache__' \
        --exclude 'soundfonts' \
        --exclude 'firmware' \
        --exclude '.vscode' \
        --exclude 'last_session.json' \
        --exclude '*.wav' \
        ./ "$PI:$DIR/"

    echo "==> Instalando dependencias y reiniciando servicio en la Pi"
    ssh "$PI" DIR="$DIR" 'bash -s' <<'REMOTE'
set -euo pipefail
cd "$DIR"

# Efecto Robot (LADSPA): solo si falta y hay internet; si no, aviso y seguimos
if ! dpkg -s swh-plugins >/dev/null 2>&1; then
    if getent hosts deb.debian.org >/dev/null 2>&1; then
        sudo apt-get update -qq && sudo apt-get install -y swh-plugins
    else
        echo "AVISO: falta swh-plugins y la Pi no tiene internet; el efecto Robot quedará inactivo."
    fi
fi

[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --quiet --no-index --find-links .deploy-wheels -r requirements.txt

sudo cp roboguitarra.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable roboguitarra >/dev/null 2>&1 || true
sudo systemctl restart roboguitarra
sleep 2
systemctl status roboguitarra --no-pager | head -5
REMOTE
}

deploy_sf2() {
    echo "==> Copiando soundfonts a $PI:$DIR/soundfonts"
    ssh "$PI" "mkdir -p '$DIR/soundfonts'"
    # Sin --delete (conserva sf2 que solo estén en la Pi) y sin -z (no comprimen)
    rsync -av --progress soundfonts/ "$PI:$DIR/soundfonts/"
}

case "${1:-}" in
    panel) deploy_panel ;;
    sf2)   deploy_sf2 ;;
    all)   deploy_panel; deploy_sf2 ;;
    *)     echo "Uso: $0 {panel|sf2|all}" >&2; exit 1 ;;
esac

echo "==> OK"
