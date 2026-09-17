#!/bin/bash
# Abre o Voice Studio usando o venv do Applio.
# Variaveis opcionais: VOICE_STUDIO_HOME (raiz com Applio/ e recordings/), APPLIO_DIR.
set -e
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
export VOICE_STUDIO_HOME="${VOICE_STUDIO_HOME:-$(dirname "$APP_DIR")}"
APPLIO_DIR="${APPLIO_DIR:-$VOICE_STUDIO_HOME/Applio}"
source "$APPLIO_DIR/.venv/bin/activate"
python "$APP_DIR/voice_studio.py"
