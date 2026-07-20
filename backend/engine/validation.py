from __future__ import annotations

from .models import CroquiPlan, LocalExtraction, ValidationIssue, ValidationResult


def _largest_topology_component(plan: CroquiPlan) -> float:
    if not plan.segments:
        return 0
    network_segments = [segment for segment in plan.segments if segment.style != "projected"]
    if not network_segments:
        network_segments = plan.segments
    style_counts: dict[str, int] = {}
    for segment in network_segments:
        style_counts[segment.style] = style_counts.get(segment.style, 0) + 1
    dominant_style = max(style_counts, key=style_counts.get)
    adjacency: dict[tuple[int, int], set[tuple[int, int]]] = {}
    for segment in network_segments:
        if segment.style != dominant_style:
            continue
        start = (round(segment.start.x * 40), round(segment.start.y * 40))
        end = (round(segment.end.x * 40), round(segment.end.y * 40))
        adjacency.setdefault(start, set()).add(end)
        adjacency.setdefault(end, set()).add(start)
    visited: set[tuple[int, int]] = set()
    largest = 0
    for node in adjacency:
        if node in visited:
            continue
        stack = [node]
        visited.add(node)
        size = 0
        while stack:
            current = stack.pop()
            size += 1
            for neighbor in adjacency[current]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        largest = max(largest, size)
    return largest / max(len(adjacency), 1)


def validate_plan(
    plan: CroquiPlan,
    extraction: LocalExtraction,
    *,
    automatic_threshold: float,
    allow_manual_number: bool = False,
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    main = plan.main_equipment
    if main is None:
        issues.append(
            ValidationIssue(code="MAIN_EQUIPMENT_MISSING", message="equipamento principal não identificado")
        )
    else:
        known_identifiers = set(extraction.identifiers) | set(extraction.registry_identifiers)
        if not allow_manual_number and main.number not in known_identifiers:
            issues.append(
                ValidationIssue(
                    code="MAIN_EQUIPMENT_NOT_IN_PROJECT",
                    message="o identificador principal não foi localizado no projeto",
                )
            )
        matching_main = [
            item
            for item in plan.equipment
            if item.main and item.number == main.number and item.equipment_type == main.equipment_type
        ]
        if len(matching_main) != 1:
            issues.append(
                ValidationIssue(
                    code="MAIN_EQUIPMENT_INCONSISTENT",
                    message="o equipamento principal deve aparecer uma única vez no plano",
                )
            )
    if plan.source not in {"manual"} and plan.confidence < automatic_threshold:
        issues.append(
            ValidationIssue(
                code="LOW_CONFIDENCE",
                message=f"confiança {plan.confidence:.2f} abaixo do mínimo {automatic_threshold:.2f}",
            )
        )
    if len(plan.segments) < 2:
        issues.append(
            ValidationIssue(
                code="TOPOLOGY_MISSING",
                message="não foi possível reconstruir uma topologia mínima da rede",
            )
        )
    elif len(plan.segments) >= 6 and _largest_topology_component(plan) < 0.42:
        issues.append(
            ValidationIssue(
                code="TOPOLOGY_FRAGMENTED",
                message="a rede extraída contém fragmentos demais para gerar um croqui confiável",
            )
        )
    seen: dict[str, str] = {}
    for item in plan.equipment:
        previous = seen.setdefault(item.number, str(item.equipment_type))
        if previous != str(item.equipment_type):
            issues.append(
                ValidationIssue(
                    code="EQUIPMENT_TYPE_CONFLICT",
                    message=f"o número {item.number} recebeu tipos incompatíveis",
                )
            )
    return ValidationResult(accepted=not any(issue.blocking for issue in issues), issues=issues)
