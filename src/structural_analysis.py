"""Stage 4: import, display and compare engineer-mapped frame-analysis outputs.

IMPORTANT: This module does NOT solve the structure and does NOT read raw STAAD
member-force exports directly. An engineer must transform the relevant STAAD
member-end / location / sign convention into the explicit normalized CSV schema.
Design-note rows below are transcribed *reference results*, not independently
calculated or certified results. No structural adequacy assessment is done.

Original design-note p. 4: signed MZ values in tf.m/m for ULS, rare SLS and
quasi-permanent; p.5: ULS shear magnitudes in tf/m.
"""
from __future__ import annotations

import csv
import io
import math
from typing import Any

COLUMNS = ("case", "member", "location", "action", "value", "unit")
CASES = ("ULS", "SLS_RARE", "SLS_QUASI")
MEMBERS = ("Top slab", "Bottom slab", "Left wall", "Right wall")
LOCATIONS = ("Wall face", "Haunch end", "Mid span", "Top support", "Bottom support")
ACTIONS = {"MZ": "tf.m/m", "V": "tf/m"}

# Source: Design Note of Box Culvert 1x3x3, page 4, table "Design moment
# due to different load combination" and page 5 "Total SF". Rounded values.
# Position / sign labels for top/bottom slabs are preserved; these rows are
# NOT a verified mapping to STAAD element/node identifiers.
_MOMENTS = [
    ("Top slab", "Wall face", 6.4, 4.8, 1.6),
    ("Top slab", "Haunch end", 4.7, 3.1, 0.8),
    ("Top slab", "Mid span", -6.8, -4.6, -1.5),
    ("Bottom slab", "Wall face", -7.4, -5.1, -1.8),
    ("Bottom slab", "Haunch end", -6.1, -4.1, -1.2),
    ("Bottom slab", "Mid span", 8.2, 5.7, 2.6),
]
_SHEARS = [
    ("Top slab", "Wall face", 4.9),
    ("Top slab", "Haunch end", 4.4),
    ("Top slab", "Mid span", 0.0),
    ("Bottom slab", "Wall face", 22.22),
    ("Bottom slab", "Haunch end", 18.6),
    ("Bottom slab", "Mid span", 7.9),
]


def reference_results() -> list[dict[str, Any]]:
    """Rounded design-note results, not analysis computed by Python."""
    rows: list[dict[str, Any]] = []
    for member, location, uls, rare, quasi in _MOMENTS:
        for case, value in zip(CASES, (uls, rare, quasi)):
            rows.append(dict(case=case, member=member, location=location,
                             action="MZ", value=value, unit="tf.m/m",
                             provenance="Design note p.4 (rounded)"))
    for member, location, value in _SHEARS:
        rows.append(dict(case="ULS", member=member, location=location,
                         action="V", value=value, unit="tf/m",
                         provenance="Design note p.5 (rounded)"))
    return rows


def example_template_csv() -> bytes:
    """A blank template: genuine STAAD-derived mapped results must be supplied."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(COLUMNS)
    return output.getvalue().encode("utf-8-sig")


def reference_csv() -> bytes:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=(*COLUMNS, "provenance"))
    writer.writeheader()
    writer.writerows(reference_results())
    return output.getvalue().encode("utf-8-sig")


def _key(r: dict[str, Any]) -> tuple[str, str, str, str]:
    return (r["case"], r["member"], r["location"], r["action"])


def parse_mapped_analysis_csv(data: bytes, *, max_rows: int = 20000) -> list[dict[str, Any]]:
    """Validate UTF-8 CSV mapped to the app's required locations and units.

    Reject unknown locations/units rather than silently converting raw STAAD
    values, where element axes, dimensions and sign convention may differ.
    """
    if len(data) > 2_000_000:
        raise ValueError("CSV too large (maximum 2 MB)")
    try:
        body = data.decode("utf-8-sig")
    except UnicodeDecodeError as err:
        raise ValueError("Use UTF-8 encoded CSV") from err
    reader = csv.DictReader(io.StringIO(body))
    if not reader.fieldnames or not set(COLUMNS).issubset(reader.fieldnames):
        raise ValueError("Missing CSV columns. Required: " + ", ".join(COLUMNS))
    if len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("CSV contains duplicate column names")
    records: list[dict[str, Any]] = []
    seen = set()
    for lineno, raw in enumerate(reader, start=2):
        if lineno > max_rows + 1:
            raise ValueError(f"More than {max_rows} records")
        if None in raw:
            raise ValueError(f"Line {lineno}: too many comma-separated fields")
        row = {k: (raw[k] or "").strip() for k in COLUMNS}
        if not any(row.values()):
            continue
        if row["case"] not in CASES:
            raise ValueError(f"Line {lineno}: case must be one of {', '.join(CASES)}")
        if row["member"] not in MEMBERS:
            raise ValueError(f"Line {lineno}: unknown member '{row['member']}'")
        if row["location"] not in LOCATIONS:
            raise ValueError(f"Line {lineno}: unknown location '{row['location']}'")
        if row["action"] not in ACTIONS:
            raise ValueError(f"Line {lineno}: action must be MZ or V")
        if row["unit"] != ACTIONS[row["action"]]:
            raise ValueError(f"Line {lineno}: {row['action']} must use {ACTIONS[row['action']]} (no implicit conversion)")
        try:
            num = float(row["value"])
        except ValueError as err:
            raise ValueError(f"Line {lineno}: value must be numeric") from err
        if not math.isfinite(num):
            raise ValueError(f"Line {lineno}: value must be finite")
        row["value"] = num
        row["provenance"] = "User-uploaded mapped analysis CSV (not independently verified)"
        k = _key(row)
        if k in seen:
            raise ValueError(f"Line {lineno}: duplicate case/member/location/action: {k}")
        seen.add(k)
        records.append(row)
    if not records:
        raise ValueError("CSV contains no data rows")
    return records


def is_original_geometry(inputs: Any) -> bool:
    """Return True only for the original workbook geometry / fill reference."""
    required = dict(clear_width_m=3., clear_height_m=3., length_m=12.,
                    wall_thickness_m=.25, top_slab_thickness_m=.25,
                    bottom_slab_thickness_m=.275, earth_cushion_m=.3,
                    haunch_width_m=.15, haunch_height_m=.15,
                    concrete_unit_weight_kn_m3=25.)
    return (all(math.isclose(getattr(inputs, name), value, rel_tol=0, abs_tol=1e-9)
                for name, value in required.items())
            and inputs.haunch_count == 4
            and inputs.concrete_grade == "M35"
            and inputs.steel_grade == "Fe500")


def build_structural_analysis_snapshot(
    uploaded_records: list[dict[str, Any]] | None = None,
    *,
    reference_geometry_matches: bool = True,
) -> dict[str, Any]:
    """Preserve imported action values and differences from rounded note values."""
    refs = reference_results()
    comparison: list[dict[str, Any]] = []
    imported = uploaded_records or []
    ref_by_key = {_key(row): row for row in refs}
    for row in imported:
        target = ref_by_key.get(_key(row))
        diff = row["value"] - target["value"] if target else None
        # This is a NUMERICAL DIFFERENCE only, *not* an engineering pass/fail.
        comparison.append({
            "case": row["case"], "member": row["member"],
            "location": row["location"], "action": row["action"],
            "imported_value": row["value"], "reference_value": target["value"] if target else None,
            "difference": round(diff, 8) if diff is not None else None,
            "unit": row["unit"],
            "reference_available": target is not None,
            "reference_geometry_matches": reference_geometry_matches,
        })
    return {
        "mode": "Imported mapped results only — NO Python frame solver",
        "has_imported_results": bool(imported),
        "imported_count": len(imported),
        "reference_count": len(refs),
        "reference_geometry_matches": reference_geometry_matches,
        "reference": refs,
        "imported": imported,
        "comparison": comparison,
        "mapping_requirements": (
            "Engineer must map STAAD member IDs, sections, local sign convention and force units "
            "to case/member/location/action. Values refer to per-metre culvert strip."
        ),
        "status": (
            "External solver results imported for review, NOT independently analyzed or safety-checked"
            if imported else "Awaiting engineer-mapped STAAD results; only design-note references are shown"
        ),
    }
