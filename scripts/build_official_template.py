from __future__ import annotations

import argparse
from pathlib import Path

from backend.engine.excel_native import create_official_template


def main() -> None:
    parser = argparse.ArgumentParser(description="Sanitiza um croqui oficial para uso interno no engine")
    parser.add_argument("source", type=Path, help="croqui oficial convertido para .xlsx")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        default=Path("backend/assets/modelo_croqui_oficial.xlsx"),
    )
    args = parser.parse_args()
    create_official_template(args.source, args.output)
    print(args.output)


if __name__ == "__main__":
    main()
