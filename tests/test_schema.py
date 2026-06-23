"""Schema tests — prove the contract is enforced."""

import pytest
from pydantic import ValidationError

from frankenstein.schema import (
    ManifestBundle,
    PartSource,
    PartSpecs,
    PartsManifest,
    PartStatus,
    SpecSource,
    is_compatible,
)


def _specs(**overrides) -> PartSpecs:
    base = dict(source=SpecSource.INFERRED)
    base.update(overrides)
    return PartSpecs(**base)


def test_functional_part_parses():
    p = PartsManifest(
        part_id="x", name="X", status=PartStatus.FUNCTIONAL,
        confidence=0.9, source=PartSource.VAULT, specs=_specs(),
    )
    assert p.status == PartStatus.FUNCTIONAL
    assert p.repair_note is None


def test_repairable_requires_repair_note():
    with pytest.raises(ValidationError, match="repair_note is required"):
        PartsManifest(
            part_id="x", name="X", status=PartStatus.REPAIRABLE,
            confidence=0.9, source=PartSource.PHOTO, specs=_specs(),
        )


def test_functional_rejects_repair_note():
    with pytest.raises(ValidationError, match="only allowed when status == repairable"):
        PartsManifest(
            part_id="x", name="X", status=PartStatus.FUNCTIONAL,
            confidence=0.9, source=PartSource.PHOTO,
            repair_note="not needed", specs=_specs(),
        )


def test_unsafe_requires_disposal_reason():
    with pytest.raises(ValidationError, match="disposal_reason is required"):
        PartsManifest(
            part_id="x", name="X", status=PartStatus.UNSAFE,
            confidence=0.9, source=PartSource.PHOTO, specs=_specs(),
        )


def test_confidence_bounded():
    with pytest.raises(ValidationError):
        PartsManifest(
            part_id="x", name="X", status=PartStatus.FUNCTIONAL,
            confidence=1.5, source=PartSource.PHOTO, specs=_specs(),
        )


def test_extra_fields_rejected():
    with pytest.raises(ValidationError, match="Extra inputs"):
        PartsManifest(
            part_id="x", name="X", status=PartStatus.FUNCTIONAL,
            confidence=0.9, source=PartSource.PHOTO,
            specs=_specs(),
            surprise_field="should fail",
        )


def test_bundle_filters_unsafe():
    bundle = ManifestBundle(
        bundle_id="t", parts=[
            PartsManifest(part_id="a", name="A", status=PartStatus.FUNCTIONAL,
                          confidence=0.9, source=PartSource.PHOTO, specs=_specs()),
            PartsManifest(part_id="b", name="B", status=PartStatus.UNSAFE,
                          confidence=0.9, source=PartSource.PHOTO,
                          disposal_reason="cracked", specs=_specs()),
            PartsManifest(part_id="c", name="C", status=PartStatus.REPAIRABLE,
                          confidence=0.9, source=PartSource.PHOTO,
                          repair_note="fix it", specs=_specs()),
        ]
    )
    usable = bundle.usable()
    assert [p.part_id for p in usable] == ["a", "c"]
    assert [p.part_id for p in bundle.repairable()] == ["c"]


def test_schema_version_pinned():
    """If Member 1 bumps the schema, Member 2 fails fast here."""
    assert is_compatible("0.1.0")
    assert is_compatible("0.0.5")
    assert not is_compatible("0.2.0")