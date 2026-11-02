"""Explicit ULS-demand interface for engineer-mapped results.

Do not reinterpret raw STAAD force output: local axes, member stations, design
combinations, units, and face-sign mappings must be resolved by the engineer.
This CSV is a *ULS design-section mapping*, not raw STAAD output.
"""
from __future__ import annotations

import csv
import io
import math
from typing import Any

COLUMNS = ("row", "member", "location", "action", "value", "unit")
ACTION_FIELD = {"MZ": "moment_tf_m_per_m", "V": "design_shear_tf_per_m"}
UNITS = {"MZ": "tf.m/m", "V": "tf/m"}


def _expected_rows(reference: dict) -> dict[tuple[int, str], dict[str, Any]]:
    wanted: dict[tuple[int, str], dict[str, Any]] = {}
    for category, action in (("flexure", "MZ"), ("shear", "V")):
        for record in reference[category]:
            wanted[(int(record["row"]), action)] = record
    return wanted


def uls_demands_csv_template(reference: dict) -> bytes:
    """Create one BLANK force field per required ULS design section."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for (row, action), record in _expected_rows(reference).items():
        writer.writerow({"row": row, "member": record["member"],
                         "location": record["location"], "action": action,
                         "value": "", "unit": UNITS[action]})
    return out.getvalue().encode("utf-8-sig")


def parse_uls_demands_csv(data: bytes, reference: dict) -> dict[int, dict[str, float]]:
    """Require *complete* mapped design actions; refuse old/new mixed demand sets.

    Row IDs are internal section identifiers from the reference workbook.
    Values are NONNEGATIVE numerical magnitude demands, in tf.m/m or tf/m.
    The engineer is responsible for selecting the governing moment sign and face before mapping.
    """
    if len(data) > 500_000:
        raise ValueError("ULS demand CSV is too large (500 KB limit)")
    try:
        src = data.decode("utf-8-sig")
    except UnicodeDecodeError as err:
        raise ValueError("ULS demand CSV must be UTF-8") from err
    reader = csv.DictReader(io.StringIO(src))
    if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or set(COLUMNS) - set(reader.fieldnames):
        raise ValueError("ULS CSV requires unique columns: " + ", ".join(COLUMNS))
    expected = _expected_rows(reference)
    found: set[tuple[int, str]] = set()
    overrides: dict[int, dict[str, float]] = {}
    for lineno, raw in enumerate(reader, 2):
        if lineno > 1000:
            raise ValueError("Too many ULS rows")
        if None in raw:
            raise ValueError(f"Line {lineno}: unexpected extra CSV column")
        if all(not (x or "").strip() for x in raw.values()):
            continue
        try:
            row = int((raw["row"] or "").strip())
        except ValueError as err:
            raise ValueError(f"Line {lineno}: invalid section row ID") from err
        action = (raw["action"] or "").strip()
        key = (row, action)
        if key not in expected:
            raise ValueError(f"Line {lineno}: unexpected ULS section/action {key}")
        if key in found:
            raise ValueError(f"Line {lineno}: duplicated ULS section/action {key}")
        for field in ("member", "location"):
            if (raw[field] or "").strip() != str(expected[key][field]):
                raise ValueError(f"Line {lineno}: section '{field}' does not match the reference mapping")
        if (raw["unit"] or "").strip() != UNITS[action]:
            raise ValueError(f"Line {lineno}: unit for {action} must be {UNITS[action]}")
        try:
            value = float((raw["value"] or "").strip())
        except ValueError as err:
            raise ValueError(f"Line {lineno}: missing or invalid ULS force") from err
        if not math.isfinite(value):
            raise ValueError(f"Line {lineno}: demand must be finite")
        # The source ULS calculations use demand magnitudes. Reject signed
        # STAAD actions instead of silently taking abs and losing face data.
        if value < 0:
            raise ValueError(f"Line {lineno}: supply a nonnegative ULS demand magnitude "
                             "after the engineer maps the governing member face")
        overrides.setdefault(row, {})[ACTION_FIELD[action]] = value
        found.add(key)
    missing = expected.keys() - found
    if missing:
        head = ", ".join(f"{row}/{act}" for row, act in sorted(missing)[:6])
        raise ValueError(f"Incomplete ULS demand mapping: {len(missing)} section actions missing ({head} ...). "
                         "Provide all actions to avoid mixing new results with old Excel forces.")
    return overrides
