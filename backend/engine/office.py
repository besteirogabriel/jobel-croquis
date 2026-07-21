from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import fitz


class OfficeError(RuntimeError):
    pass


def _run_libreoffice(source: Path, output_dir: Path, convert_to: str, binary: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="jobel-lo-profile-"))
    extension = convert_to.split(":", 1)[0]
    expected = output_dir / f"{source.stem}.{extension}"
    expected.unlink(missing_ok=True)
    try:
        command = [
            binary,
            f"-env:UserInstallation={profile.as_uri()}",
            "--headless",
            "--convert-to",
            convert_to,
            "--outdir",
            str(output_dir),
            str(source),
        ]
        process = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
        if process.returncode != 0:
            raise OfficeError(f"LibreOffice falhou: {process.stderr.strip() or process.stdout.strip()}")
        if not expected.exists() or expected.stat().st_size == 0:
            raise OfficeError(f"LibreOffice não produziu {expected.name}: {process.stdout.strip()}")
        return expected
    except FileNotFoundError as exc:
        raise OfficeError(f"LibreOffice não encontrado em {binary}") from exc
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def to_xlsx(source: Path, output_dir: Path, binary: str) -> Path:
    if source.suffix.lower() == ".xlsx":
        target = output_dir / source.name
        output_dir.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return target
    if source.suffix.lower() != ".xls":
        raise OfficeError("o modelo deve ser .xls ou .xlsx")
    return _run_libreoffice(source, output_dir, "xlsx:Calc MS Excel 2007 XML", binary)


def to_xls(source: Path, output_dir: Path, binary: str) -> Path:
    return _run_libreoffice(source, output_dir, "xls:MS Excel 97", binary)


def _single_sheet_copy(source: Path, target: Path) -> None:
    workbook_name = "xl/workbook.xml"
    with ZipFile(source, "r") as archive, ZipFile(target, "w", ZIP_DEFLATED) as output:
        for item in archive.infolist():
            payload = archive.read(item.filename)
            if item.filename == workbook_name:
                from xml.etree import ElementTree as ET

                ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                root = ET.fromstring(payload)
                sheets = root.find("m:sheets", ns)
                if sheets is not None:
                    for index, sheet in enumerate(list(sheets)):
                        if index:
                            sheet.set("state", "hidden")
                        else:
                            sheet.set("state", "visible")
                payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            output.writestr(item, payload)


def to_pdf(source: Path, output_dir: Path, binary: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output_dir / f".{source.stem}-print.xlsx"
    _single_sheet_copy(source, temporary)
    try:
        generated = _run_libreoffice(temporary, output_dir, "pdf:calc_pdf_Export", binary)
        target = output_dir / f"{source.stem}.pdf"
        if generated != target:
            if target.exists():
                target.unlink()
            generated.replace(target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def render_preview(pdf: Path, output: Path) -> Path:
    with fitz.open(pdf) as document:
        if len(document) < 1:
            raise OfficeError("o PDF gerado não possui páginas")
        page = document[0]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False)
        pixmap.save(output)
    if output.stat().st_size < 5_000:
        raise OfficeError("a prévia do PDF gerado parece vazia")
    return output
