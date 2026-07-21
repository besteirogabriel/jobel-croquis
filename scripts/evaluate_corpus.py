from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.evaluation import compare_blind, run_local_blind, run_runtime_blind


def main() -> None:
    parser = argparse.ArgumentParser(description="Avaliação cega do corpus privado de croquis.")
    parser.add_argument("phase", choices=("blind", "compare"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--without-ocr", action="store_true")
    parser.add_argument("--engine", choices=("local", "runtime"), default="local")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--case", default=None)
    parser.add_argument("--without-references", action="store_true")
    args = parser.parse_args()
    if args.phase == "blind":
        if args.engine == "runtime":
            result = run_runtime_blind(
                args.output,
                workers=args.workers,
                case_id=args.case,
                references=not args.without_references,
            )
        else:
            result = run_local_blind(args.output, ocr=not args.without_ocr)
    else:
        result = compare_blind(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
