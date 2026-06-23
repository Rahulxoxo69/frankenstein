"""Inspector — multi-agent debate post-schematic critique.

Three 'experts' review the schematic independently:
  - electrical: voltage, current, logic levels, ERC-style concerns.
  - thermal/reliability: derating, hot spots, MTBF proxies.
  - manufacturing/yield: BOM completeness, sourcing risk, assembly traps.

Each emits a list of concerns. Aggregator produces a final critique the
Foreman uses to decide whether to reflexion-repair or finalise.

MVP: structural review of the schematic + the ERC violations + the manifests'
repair_notes. Real impl uses LLM debate; for now, deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from frankenstein.schematic import Schematic
from frankenstein.schema import ManifestBundle, PartStatus


@dataclass
class Concern:
    expert: str  # "electrical" | "thermal" | "manufacturing"
    severity: str  # "blocker" | "warning" | "nit"
    target: str  # refdes or "BOM" or "design"
    message: str


@dataclass
class InspectorReport:
    concerns: list[Concern] = field(default_factory=list)
    blockers: int = 0
    warnings: int = 0
    summary: str = ""

    def is_blocked(self) -> bool:
        return self.blockers > 0


def _review_electrical(schematic: Schematic, bundle: ManifestBundle, erc_violations: list) -> list[Concern]:
    out: list[Concern] = []
    # Forward any ERC errors as inspector concerns too — belt + suspenders
    for v in erc_violations:
        if v.severity != "error":
            continue
        out.append(Concern(
            expert="electrical",
            severity="blocker",
            target=v.refdes,
            message=f"ERC {v.rule}: {v.message}",
        ))

    # Repairable parts not addressed in design notes
    notes_text = "\n".join(schematic.notes).lower()
    for p in bundle.parts:
        if p.status != PartStatus.REPAIRABLE:
            continue
        part_id = p.part_id.lower()
        if part_id not in notes_text:
            out.append(Concern(
                expert="manufacturing",
                severity="warning",
                target=p.part_id,
                message=(
                    f"Repairable part {p.part_id} ({p.name}) is in the manifest but "
                    f"no design note acknowledges it. Either design around it, "
                    f"queue it for repair, or drop it explicitly."
                ),
            ))
    return out


def _review_thermal(schematic: Schematic, bundle: ManifestBundle) -> list[Concern]:
    out: list[Concern] = []
    parts_by_id = {p.part_id: p for p in bundle.parts}
    for c in schematic.components:
        if c.kind != "ic":
            continue
        spec = parts_by_id.get(c.part_id)
        if spec is None or spec.specs.current_ma is None:
            continue
        # Toy derating proxy: ICs drawing > 200mA without heatsink mention are flagged
        if spec.specs.current_ma > 200 and "heatsink" not in "\n".join(schematic.notes).lower():
            out.append(Concern(
                expert="thermal",
                severity="warning",
                target=c.refdes,
                message=(
                    f"IC {c.refdes} ({spec.name}) draws {spec.specs.current_ma}mA — "
                    f"derating concerns at >200mA without heatsink note."
                ),
            ))
    return out


def _review_manufacturing(schematic: Schematic, bundle: ManifestBundle) -> list[Concern]:
    out: list[Concern] = []
    # Every refdes in the schematic must reference a manifest part
    manifest_ids = {p.part_id for p in bundle.parts}
    for c in schematic.components:
        if c.part_id not in manifest_ids:
            out.append(Concern(
                expert="manufacturing",
                severity="blocker",
                target=c.refdes,
                message=f"Component {c.refdes} references part_id '{c.part_id}' which is not in the manifest bundle.",
            ))

    # Low-confidence parts that snuck into the design
    for c in schematic.components:
        spec = next((p for p in bundle.parts if p.part_id == c.part_id), None)
        if spec and spec.confidence < 0.65:
            out.append(Concern(
                expert="manufacturing",
                severity="warning",
                target=c.refdes,
                message=(
                    f"Component {c.refdes} ({spec.name}) has low detection confidence "
                    f"{spec.confidence:.2f} — Member 1's RAG fallback should have been "
                    f"triggered. Re-verify spec before assembly."
                ),
            ))
    return out


def inspect(
    schematic: Schematic,
    bundle: ManifestBundle,
    erc_violations: list,
) -> InspectorReport:
    concerns: list[Concern] = []
    concerns += _review_electrical(schematic, bundle, erc_violations)
    concerns += _review_thermal(schematic, bundle)
    concerns += _review_manufacturing(schematic, bundle)

    blockers = sum(1 for c in concerns if c.severity == "blocker")
    warnings = sum(1 for c in concerns if c.severity == "warning")

    summary = (
        f"Inspector: {blockers} blocker(s), {warnings} warning(s) "
        f"across {len(concerns)} concern(s)."
    )
    return InspectorReport(
        concerns=concerns,
        blockers=blockers,
        warnings=warnings,
        summary=summary,
    )