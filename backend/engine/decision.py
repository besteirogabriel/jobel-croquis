from __future__ import annotations

from .models import CroquiPlan, EquipmentPlacement, LocalExtraction, Point, Segment, WorkZone


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def _transform_point(point: Point, crop: tuple[float, float, float, float]) -> Point:
    left, top, right, bottom = crop
    x = 0.07 + ((point.x - left) / max(right - left, 0.01)) * 0.86
    y = 0.13 + ((point.y - top) / max(bottom - top, 0.01)) * 0.68
    return Point(x=_clamp(x, 0.05, 0.95), y=_clamp(y, 0.11, 0.84))


def _network_crop(extraction: LocalExtraction, focus: Point) -> tuple[float, float, float, float]:
    nearby = []
    for segment in extraction.segments:
        midpoint = Point(
            x=(segment.start.x + segment.end.x) / 2,
            y=(segment.start.y + segment.end.y) / 2,
        )
        if abs(midpoint.x - focus.x) < 0.34 and abs(midpoint.y - focus.y) < 0.31:
            nearby.append(segment)
    if not nearby:
        return (
            _clamp(focus.x - 0.25, 0, 0.5),
            _clamp(focus.y - 0.22, 0, 0.55),
            _clamp(focus.x + 0.25, 0.5, 1),
            _clamp(focus.y + 0.22, 0.45, 0.9),
        )
    xs = [point.x for segment in nearby for point in (segment.start, segment.end)]
    ys = [point.y for segment in nearby for point in (segment.start, segment.end)]
    padding_x, padding_y = 0.025, 0.025
    return (
        _clamp(min(xs) - padding_x, 0, 0.75),
        _clamp(min(ys) - padding_y, 0, 0.75),
        _clamp(max(xs) + padding_x, 0.25, 1),
        _clamp(max(ys) + padding_y, 0.25, 0.9),
    )


def _normalized_segments(
    extraction: LocalExtraction, crop: tuple[float, float, float, float]
) -> list[Segment]:
    left, top, right, bottom = crop
    unique: dict[tuple[int, int, int, int, str], Segment] = {}
    for segment in extraction.segments:
        points = (segment.start, segment.end)
        if not all(left <= point.x <= right and top <= point.y <= bottom for point in points):
            continue
        start, end = (_transform_point(point, crop) for point in points)
        dx, dy = abs(start.x - end.x), abs(start.y - end.y)
        if dx < 0.007 and dy < 0.007:
            continue
        # Croquis oficiais privilegiam uma espinha legível; pequenos artefatos
        # diagonais de texto e carimbo não entram no desenho.
        if dx > 0 and dy > 0 and min(dx, dy) / max(dx, dy) > 0.32:
            continue
        key = (
            round(start.x * 100),
            round(start.y * 100),
            round(end.x * 100),
            round(end.y * 100),
            segment.style,
        )
        reverse = (key[2], key[3], key[0], key[1], key[4])
        if reverse not in unique:
            unique[key] = Segment(start=start, end=end, style=segment.style)
        if len(unique) >= 180:
            break
    return list(unique.values())


def _infer_poles(segments: list[Segment]) -> list[Point]:
    network = [segment for segment in segments if segment.style != "projected"]
    if not network:
        return []
    style_counts: dict[str, int] = {}
    for segment in network:
        style_counts[segment.style] = style_counts.get(segment.style, 0) + 1
    dominant_style = max(style_counts, key=style_counts.get)
    buckets: dict[tuple[int, int], list[Point]] = {}
    for segment in network:
        if segment.style != dominant_style:
            continue
        for point in (segment.start, segment.end):
            buckets.setdefault((round(point.x * 50), round(point.y * 50)), []).append(point)
    poles = [
        Point(
            x=sum(point.x for point in points) / len(points),
            y=sum(point.y for point in points) / len(points),
        )
        for points in buckets.values()
    ]
    return poles[:60]


def decide_local(
    extraction: LocalExtraction,
    *,
    threshold: float,
    minimum_gap: float,
) -> CroquiPlan:
    candidates = extraction.candidates
    if not candidates:
        return CroquiPlan(rationale=["nenhum equipamento de isolamento identificado localmente"])

    best = candidates[0]
    runner_up = candidates[1].score if len(candidates) > 1 else 0
    gap = best.score - runner_up
    position = best.position or Point(x=0.5, y=0.48)
    crop = _network_crop(extraction, position)
    transformed_position = _transform_point(position, crop)
    segments = _normalized_segments(extraction, crop)
    poles = _infer_poles(segments)

    accepted_confidence = (
        best.score if best.score >= threshold and gap >= minimum_gap else min(best.score, 0.79)
    )
    rationale = list(best.evidence)
    rationale.append(f"diferença para o segundo candidato: {gap:.2f}")
    if best.score < threshold:
        rationale.append("pontuação local abaixo do limiar automático")
    if gap < minimum_gap:
        rationale.append("candidatos locais ambíguos")

    main = EquipmentPlacement(
        equipment_type=best.equipment_type,
        number=best.number,
        position=transformed_position,
        label=f"{best.equipment_type} {best.number}",
        main=True,
    )
    equipment = [main]
    for candidate in candidates[1:]:
        if candidate.position is None or candidate.score < 0.68:
            continue
        if not (crop[0] <= candidate.position.x <= crop[2] and crop[1] <= candidate.position.y <= crop[3]):
            continue
        equipment.append(
            EquipmentPlacement(
                equipment_type=candidate.equipment_type,
                number=candidate.number,
                position=_transform_point(candidate.position, crop),
                label=f"{candidate.equipment_type} {candidate.number}",
            )
        )
        if len(equipment) >= 16:
            break

    zone_half_width, zone_half_height = 0.10, 0.12
    work_zone = WorkZone(
        top_left=Point(
            x=_clamp(transformed_position.x - zone_half_width, 0.04, 0.78),
            y=_clamp(transformed_position.y - zone_half_height, 0.11, 0.70),
        ),
        bottom_right=Point(
            x=_clamp(transformed_position.x + zone_half_width, 0.22, 0.96),
            y=_clamp(transformed_position.y + zone_half_height, 0.28, 0.86),
        ),
    )
    return CroquiPlan(
        main_equipment=main,
        equipment=equipment,
        poles=poles,
        segments=segments,
        work_zones=[work_zone],
        confidence=accepted_confidence,
        source="local",
        rationale=rationale,
    )
