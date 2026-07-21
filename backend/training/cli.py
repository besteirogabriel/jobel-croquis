from __future__ import annotations

import argparse
import json
from pathlib import Path

from .dataset import build_manifest, load_manifest
from .fine_tuning import build_fine_tuning_files, retrieve_job, submit_job
from .labeling import draft_label, review_label


def main() -> None:
    parser = argparse.ArgumentParser(description="Corpus, avaliação e fine-tuning do Jobel Croquis.")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--corpus", default=None)
    prepare.add_argument("--output", default=None)
    prepare.add_argument("--seed", default=None)
    commands.add_parser("summary")
    label = commands.add_parser("label")
    label.add_argument("--case", required=True)
    review = commands.add_parser("review")
    review.add_argument("--case", required=True)
    review.add_argument("--status", required=True, choices=("draft", "approved", "rejected"))
    review.add_argument("--reviewer", required=True)
    review.add_argument("--notes", default="")
    review.add_argument("--allow-warnings", action="store_true")
    build = commands.add_parser("build")
    build.add_argument("--output-dir", type=Path, default=None)
    submit = commands.add_parser("submit")
    submit.add_argument("--train", type=Path, required=True)
    submit.add_argument("--validation", type=Path, default=None)
    status = commands.add_parser("status")
    status.add_argument("--job", required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = build_manifest(args.corpus, output=args.output, seed=args.seed)
        _print_manifest(result)
        return
    elif args.command == "summary":
        result = load_manifest()
        _print_manifest(result)
        return
    elif args.command == "label":
        result = draft_label(args.case)
    elif args.command == "review":
        result = review_label(
            args.case,
            status=args.status,
            reviewer=args.reviewer,
            notes=args.notes,
            allow_warnings=args.allow_warnings,
        )
    elif args.command == "build":
        result = build_fine_tuning_files(args.output_dir)
    elif args.command == "submit":
        result = submit_job(args.train, args.validation)
    else:
        result = retrieve_job(args.job)
    payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _print_manifest(manifest) -> None:
    print(f"Fingerprint: {manifest.corpus_fingerprint}")
    print(f"Casos: {len(manifest.cases)}")
    print(f"Partições: {manifest.counts}")
    print(f"Equipamentos: {manifest.equipment_type_counts}")
    print(f"Por partição/tipo: {manifest.split_equipment_counts}")
    print(f"Avisos: {len(manifest.warnings)}")


if __name__ == "__main__":
    main()
