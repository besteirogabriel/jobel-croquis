#!/usr/bin/env bash
set -euo pipefail

training_dir="${TRAINING_DIR:-/data/training}"
if [[ -d "${CORPUS_PATH:-CROQUI IA}" && ! -f "$training_dir/manifest.json" ]]; then
  python -m backend.training.cli prepare
fi

if [[ "${AI_PROVIDER:-codex}" == "codex" ]]; then
  if ! codex --config 'cli_auth_credentials_store="file"' login status >/dev/null 2>&1; then
    echo "Aviso: análise automática ainda não autenticada. Execute: docker compose run --rm codex-login"
  fi
fi

exec uvicorn backend.main:app --host 0.0.0.0 --port 8080
