from __future__ import annotations

import math
import re
import subprocess
import unicodedata
from collections import defaultdict
from pathlib import Path
from time import perf_counter

import fitz

from .models import (
    EquipmentCandidate,
    EquipmentType,
    LocalExtraction,
    ManeuverAction,
    Point,
    ProjectMetadata,
    Segment,
)

TYPE_ALIASES: dict[str, EquipmentType] = {
    "TR": EquipmentType.TR,
    "TRANSFORMADOR": EquipmentType.TR,
    "FU": EquipmentType.FU,
    "FUSIVEL": EquipmentType.FU,
    "CHAVE FUSIVEL": EquipmentType.FU,
    "FC": EquipmentType.FC,
    "FACA": EquipmentType.FC,
    "CHAVE FACA": EquipmentType.FC,
    "RL": EquipmentType.RL,
    "RELIGADOR": EquipmentType.RL,
    "RG": EquipmentType.RG,
    "REGULADOR": EquipmentType.RG,
    "OL": EquipmentType.OL,
    "OLEO": EquipmentType.OL,
    "SC": EquipmentType.SC,
    "SECCIONALIZADOR": EquipmentType.SC,
}

_TYPE_RE = re.compile(
    r"(?<![A-Z0-9])(TR|FU|FC|RL|RG|OL|SC|TRANSFORMADOR|RELIGADOR|REGULADOR|"
    r"SECCIONALIZADOR|CHAVE\s+FUSIVEL|CHAVE\s+FACA)(?![A-Z0-9])[^\d]{0,12}(\d{5,8})\b",
    re.I,
)
_NUMBER_RE = re.compile(r"\b\d{5,8}\b")


def normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return " ".join("".join(ch for ch in decomposed if not unicodedata.combining(ch)).upper().split())


def _one(text: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.M)
        if match:
            return " ".join(match.group(1).strip().split())
    return ""


TITLE_BLOCK_LABELS = {"MUNICIPIO", "OBRA", "DATA", "NOTA", "LEVANTADOR", "PROJETISTA"}


def _title_block_value(doc: fitz.Document, label: str) -> str:
    expected = normalize(label).rstrip(":")
    matches: list[tuple[fitz.Page, tuple]] = []
    for page in doc:
        for word in page.get_text("words", sort=False):
            if normalize(str(word[4])).rstrip(":") != expected:
                continue
            if word[1] / max(page.rect.height, 1) < 0.55:
                continue
            matches.append((page, word))
    if not matches:
        return ""

    page, marker = max(matches, key=lambda item: (item[1][1], item[1][0]))
    block, marker_line, marker_word = marker[5], marker[6], marker[7]
    values: list[tuple[int, int, str]] = []
    for word in page.get_text("words", sort=False):
        if word[5] != block:
            continue
        line, sequence = word[6], word[7]
        follows_on_line = line == marker_line and sequence > marker_word
        follows_below = line > marker_line and word[0] >= marker[0] - 2
        if not (follows_on_line or follows_below):
            continue
        token = normalize(str(word[4])).rstrip(":")
        if token in TITLE_BLOCK_LABELS:
            continue
        values.append((line, sequence, str(word[4])))
    return " ".join(value for _, _, value in sorted(values)).strip()


def _extract_metadata(doc: fitz.Document, text: str) -> ProjectMetadata:
    return ProjectMetadata(
        municipio=_title_block_value(doc, "Município")
        or _one(text, (r"Munic[ií]pio\s*:?\s*([^\n]+)", r"Cidade\s*:?\s*([^\n]+)")),
        obra=_title_block_value(doc, "Obra")
        or _one(text, (r"Obra\s*:?\s*([^\n]+)", r"Projeto\s*:?\s*([^\n]+)")),
        data_projeto=_title_block_value(doc, "Data")
        or _one(text, (r"Data\s*:?\s*(\d{2}/\d{2}/\d{4})",)),
        nota=_title_block_value(doc, "Nota") or _one(text, (r"Nota\s*:?\s*(\d{9,12})",)),
        levantador=_title_block_value(doc, "Levantador")
        or _one(text, (r"Levantador\s*:?\s*([^\n]+)", r"Projetista\s*:?\s*([^\n]+)")),
        departamento=_one(text, (r"Departamento\s*:?\s*([^\n]+)",)),
        pages=len(doc),
    )


def _page_words(page: fitz.Page) -> list[tuple[float, float, float, float, str]]:
    return [(w[0], w[1], w[2], w[3], str(w[4])) for w in page.get_text("words", sort=True)]


def _nearby(words: list[tuple[float, float, float, float, str]], index: int, radius: int = 8) -> str:
    start = max(0, index - radius)
    stop = min(len(words), index + radius + 1)
    return normalize(" ".join(word[4] for word in words[start:stop]))


def _word_position(word: tuple[float, float, float, float, str], page: fitz.Page) -> Point:
    x = ((word[0] + word[2]) / 2) / max(page.rect.width, 1)
    y = ((word[1] + word[3]) / 2) / max(page.rect.height, 1)
    return Point(x=min(max(x, 0), 1), y=min(max(y, 0), 1))


def _candidate_score(context: str, equipment_type: EquipmentType) -> tuple[float, list[str]]:
    score = 0.26
    evidence: list[str] = []
    aliases = [key for key, value in TYPE_ALIASES.items() if value == equipment_type]
    if any(re.search(rf"\b{re.escape(alias)}\b", context) for alias in aliases):
        score += 0.48
        evidence.append(f"tipo {equipment_type} junto ao identificador")
    if equipment_type is EquipmentType.TR and re.search(r"\b\d+(?:[.,]\d+)?\s*KVA\b", context):
        score += 0.24
        evidence.append("potência em kVA junto ao identificador")
    if "ISOL" in context or "BYPASS" in context:
        score += 0.09
        evidence.append("contexto de isolamento")
    if "EXIST" in context or "INSTAL" in context or "RETIR" in context:
        score += 0.05
        evidence.append("contexto de intervenção")
    if "REFERENCIA" in context:
        score = min(score, 0.58)
        evidence.append("equipamento de referência não define automaticamente o isolamento")
    return min(score, 1), evidence


def _extract_candidates(doc: fitz.Document, text: str) -> list[EquipmentCandidate]:
    aggregated: dict[tuple[EquipmentType, str], dict] = defaultdict(
        lambda: {"score": 0.0, "evidence": set(), "position": None}
    )
    normalized_text = normalize(text)

    for match in _TYPE_RE.finditer(normalized_text):
        label, number = normalize(match.group(1)), match.group(2)
        equipment_type = TYPE_ALIASES[label]
        item = aggregated[(equipment_type, number)]
        context = normalized_text[max(0, match.start() - 55) : min(len(normalized_text), match.end() + 55)]
        if "REFERENCIA" in context:
            item["score"] = max(item["score"], 0.58)
            item["evidence"].add("marcado como equipamento de referência, não como isolamento")
        else:
            item["score"] = max(item["score"], 0.88)
            item["evidence"].add("tipo e número explicitamente associados no texto")

    for page in doc:
        words = _page_words(page)
        for index, word in enumerate(words):
            number = word[4].strip(".,;:()[]")
            if not _NUMBER_RE.fullmatch(number):
                continue
            context = _nearby(words, index)
            found_types: set[EquipmentType] = set()
            for alias, equipment_type in TYPE_ALIASES.items():
                if re.search(rf"\b{re.escape(alias)}\b", context):
                    found_types.add(equipment_type)
            if re.search(r"\b\d+(?:[.,]\d+)?\s*KVA\b", context):
                found_types.add(EquipmentType.TR)
            for equipment_type in found_types:
                score, evidence = _candidate_score(context, equipment_type)
                item = aggregated[(equipment_type, number)]
                if score > item["score"]:
                    item["score"] = score
                    item["position"] = _word_position(word, page)
                item["evidence"].update(evidence)

    candidates = [
        EquipmentCandidate(
            equipment_type=equipment_type,
            number=number,
            score=values["score"],
            evidence=sorted(values["evidence"]),
            position=values["position"],
        )
        for (equipment_type, number), values in aggregated.items()
    ]
    return sorted(candidates, key=lambda candidate: (-candidate.score, candidate.number))


def _extract_actions(text: str) -> list[ManeuverAction]:
    actions: list[ManeuverAction] = []
    seen: set[tuple[str, str, str]] = set()
    pattern = re.compile(
        r"(?<![A-Z0-9])(ABRIR|FECHAR)(?![A-Z0-9])[^A-Z]{0,12}"
        r"(TRANSFORMADOR|RELIGADOR|REGULADOR|SECCIONALIZADOR|"
        r"CHAVE(?:\s+FUSIVEL|\s+FACA)?|FUSIVEL)(?![A-Z0-9])[^\d]{0,12}(\d{5,8})",
        re.I,
    )
    for match in pattern.finditer(normalize(text)):
        key = (match.group(1).upper(), normalize(match.group(2)), match.group(3))
        if key in seen:
            continue
        seen.add(key)
        actions.append(
            ManeuverAction(action=key[0].title(), equipment_type=key[1].title(), number=key[2])
        )
    return actions


def _action_type(label: str) -> EquipmentType | None:
    normalized = normalize(label)
    if normalized in TYPE_ALIASES:
        return TYPE_ALIASES[normalized]
    if normalized == "CHAVE FUSIVEL":
        return EquipmentType.FU
    if normalized == "CHAVE FACA":
        return EquipmentType.FC
    return None


def _identifier_position(doc: fitz.Document, number: str) -> Point | None:
    positions: list[tuple[float, Point]] = []
    for page in doc:
        for word in _page_words(page):
            if word[4].strip(".,;:()[]") != number:
                continue
            position = _word_position(word, page)
            penalty = 1 if position.y > 0.78 else 0
            positions.append((penalty + abs(position.y - 0.5), position))
    return min(positions, key=lambda item: item[0])[1] if positions else None


def _action_equipment_is_work_target(text: str, equipment_type: EquipmentType) -> bool:
    if equipment_type is not EquipmentType.TR:
        return False
    return bool(
        re.search(
            r"\b(?:SUBSTITUIR|SUBST(?:ITUIR)?|RETIRAR|REMOVER)\s+(?:O\s+)?(?:TR|TRANSFORMADOR)\b",
            normalize(text),
        )
    )


def _apply_action_evidence(
    doc: fitz.Document,
    text: str,
    candidates: list[EquipmentCandidate],
    actions: list[ManeuverAction],
) -> list[EquipmentCandidate]:
    by_key = {(item.equipment_type, item.number): item for item in candidates}
    for action in actions:
        equipment_type = _action_type(action.equipment_type)
        if equipment_type is None:
            continue
        key = (equipment_type, action.number)
        candidate = by_key.get(key)
        position = candidate.position if candidate is not None else None
        position = position or _identifier_position(doc, action.number)
        evidence = set(candidate.evidence if candidate is not None else [])
        if _action_equipment_is_work_target(text, equipment_type):
            score = min(candidate.score if candidate is not None else 0.64, 0.64)
            evidence.add("equipamento da manobra é também alvo da obra; não define o isolamento")
        else:
            score = max(candidate.score if candidate is not None else 0, 0.99)
            evidence.add("equipamento identificado na tabela de manobras")
        by_key[key] = EquipmentCandidate(
            equipment_type=equipment_type,
            number=action.number,
            score=score,
            evidence=sorted(evidence),
            position=position,
        )
    return sorted(by_key.values(), key=lambda candidate: (-candidate.score, candidate.number))


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _extract_segments(doc: fitz.Document) -> list[Segment]:
    """Extrai uma espinha vetorial conservadora do projeto.

    Segmentos muito curtos, molduras, carimbos e linhas de grade são descartados.
    A etapa de placement ainda simplifica e normaliza a topologia antes do Excel.
    """

    result: list[Segment] = []
    for page in doc:
        width, height = max(page.rect.width, 1), max(page.rect.height, 1)
        for drawing in page.get_drawings():
            color = drawing.get("color") or (0.0, 0.0, 0.0)
            line_width = float(drawing.get("width") or 0)
            red, green, blue = color
            if line_width < 0.9:
                continue
            if blue > 0.55 and blue > red + 0.2 and blue > green + 0.15:
                style = "primary"
            elif green > 0.45 and green > red + 0.15 and green > blue + 0.15:
                style = "secondary"
            elif red > 0.65 and green < 0.35 and blue < 0.35:
                style = "projected"
            else:
                continue
            for item in drawing.get("items", []):
                if not item or item[0] != "l":
                    continue
                start_raw, end_raw = item[1], item[2]
                start = Point(x=max(0, min(1, start_raw.x / width)), y=max(0, min(1, start_raw.y / height)))
                end = Point(x=max(0, min(1, end_raw.x / width)), y=max(0, min(1, end_raw.y / height)))
                length = _distance(start, end)
                if length < 0.008 or length > 0.72:
                    continue
                if min(start.y, end.y) > 0.92 or max(start.x, end.x) < 0.03:
                    continue
                result.append(Segment(start=start, end=end, style=style))
    return result[:400]


def _run_tesseract(
    image: bytes,
    *,
    tesseract_bin: str,
    language: str,
    psm: int,
) -> str:
    languages = (language, "eng") if "+" in language else (language,)
    for selected_language in languages:
        try:
            process = subprocess.run(
                [tesseract_bin, "stdin", "stdout", "-l", selected_language, "--psm", str(psm)],
                input=image,
                capture_output=True,
                timeout=35,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""
        if process.returncode == 0:
            return process.stdout.decode("utf-8", errors="replace")
    return ""


def _targeted_ocr(
    doc: fitz.Document,
    *,
    tesseract_bin: str,
    language: str,
    dpi: int,
    telemetry=None,
) -> str:
    """Lê em alta resolução a tabela de manobras no rodapé.

    Diversos PDFs da concessionária guardam essa tabela apenas como vetores;
    portanto o texto normal do PDF não contém o número que está visível.
    """

    texts: list[str] = []
    for page_number, page in enumerate(list(doc)[:2], start=1):
        started = perf_counter()
        rect = page.rect
        broad = page.get_pixmap(
            dpi=dpi,
            clip=fitz.Rect(
                rect.width * 0.025,
                rect.height * 0.74,
                rect.width * 0.38,
                rect.height * 0.995,
            ),
            alpha=False,
        )
        table = page.get_pixmap(
            dpi=dpi,
            clip=fitz.Rect(
                rect.width * 0.035,
                rect.height * 0.82,
                rect.width * 0.30,
                rect.height * 0.995,
            ),
            alpha=False,
        )
        texts.extend(
            text
            for text in (
                _run_tesseract(
                    broad.tobytes("png"),
                    tesseract_bin=tesseract_bin,
                    language=language,
                    psm=6,
                ),
                _run_tesseract(
                    table.tobytes("png"),
                    tesseract_bin=tesseract_bin,
                    language=language,
                    psm=4,
                ),
            )
            if text
        )
        if telemetry is not None:
            telemetry(f"ocr_pagina_{page_number}", perf_counter() - started)
    return "\n".join(texts)


def extract_project(
    pdf_path: Path,
    *,
    ocr_enabled: bool = True,
    tesseract_bin: str = "tesseract",
    ocr_language: str = "por+eng",
    ocr_dpi: int = 600,
    telemetry=None,
) -> LocalExtraction:
    with fitz.open(pdf_path) as doc:
        started = perf_counter()
        text = "\n".join(page.get_text("text", sort=True) for page in doc)
        if telemetry is not None:
            telemetry("extracao_pdf_nativa", perf_counter() - started)
        if ocr_enabled:
            text = f"{text}\n{_targeted_ocr(doc, tesseract_bin=tesseract_bin, language=ocr_language, dpi=ocr_dpi, telemetry=telemetry)}"
        started = perf_counter()
        normalized = normalize(text)
        metadata = _extract_metadata(doc, text)
        actions = _extract_actions(text)
        candidates = _apply_action_evidence(doc, text, _extract_candidates(doc, text), actions)
        identifiers = sorted(set(_NUMBER_RE.findall(normalized)))
        segments = _extract_segments(doc)
        if telemetry is not None:
            telemetry("analise_local_pdf", perf_counter() - started)
        return LocalExtraction(
            metadata=metadata,
            text=text,
            identifiers=identifiers,
            actions=actions,
            candidates=candidates,
            segments=segments,
        )
