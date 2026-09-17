#!/bin/bash
# Caminhos compartilhados pelos scripts do pipeline. Importado com `source`.
PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${VOICE_STUDIO_HOME:-$(dirname "$PIPELINE_DIR")}"
APPLIO_DIR="${APPLIO_DIR:-$ROOT/Applio}"
DATA_DIR="${DATA_DIR:-$ROOT/data}"
REC_DIR="$ROOT/recordings"

# Commit do Applio usado nos treinos originais (set/2026)
APPLIO_REPO="https://github.com/IAHispano/Applio.git"
APPLIO_COMMIT="7b9f3fa0dde9f90946a5302b4ce4ab3410f12bb8"

activate_applio() {
  # shellcheck disable=SC1091
  source "$APPLIO_DIR/.venv/bin/activate"
}
