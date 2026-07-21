#!/usr/bin/env bash
set -euo pipefail

training_dir="${TRAINING_DIR:-/data/training}"
if [[ -d "${CORPUS_PATH:-CROQUI IA}" && ! -f "$training_dir/manifest.json" ]]; then
  python -m backend.training.cli prepare
fi

exec uvicorn backend.main:app --host 0.0.0.0 --port 8080
