"""Stage 5 — ULS checks translated from workbook Design!52:166.

IMPORTANT: These are transparent engineering *reference checks*, not approved
member design. The default demand (Mu/Vu) comes from cached outputs in the
original Excel, NOT from the experimental Python frame model. Row-to-member
mapping, load factors, ULS envelopes and IRC design assumptions must be
reviewed by the responsible structural engineer before any structural use.

The single-cell culvert has no interior wall. Invalid/blank cells in the
interior-wall and non-applicable worksheet rows are NOT considered passing.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

REFERENCE_FILE = Path(__file__).resolve().parents[1] / "data" / "uls_design_reference.json"
REFERENCE_GEOMETRY = (3.0, 3.0, 12.0, 0.25, 0.25, 0.275, 0.3, 0.15, 0.15)


def reference_table() -> dict:
    with REFERENCE_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def _require_positive(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return value


def _reference_geometry_matches(inputs: Any) -> bool:
    from .structural_analysis import is_original_geometry
    return is_original_geometry(inputs)


def flexural_check(
    *, moment_tf_m_per_m: float, effective_depth_mm: float,
    provided_as_mm2_per_m: float, fck_mpa: float = 35,
    fy_mpa: float = 500, fctm_mpa: float | None = None,
    steel_modulus_gpa: float = 200,
) -> dict:
    """Reproduce workbook's rectangular 1m strip ULS flexural arithmetic.

    Workbooks store moments as tf·m / m and depth in mm. This deliberately
    follows Excel's 100 conversion factor, rather than silently substituting
    another t-to-kN conversion convention. Review against IRC 112 with engineer.
    """
    M = abs(float(moment_tf_m_per_m))
    if not math.isfinite(M):
        raise ValueError("moment must be finite")
    d = _require_positive("effective depth (mm)", effective_depth_mm)
    As = float(provided_as_mm2_per_m)
    if not math.isfinite(As) or As < 0:
        raise ValueError("provided As must be non-negative and finite")
    fck = _require_positive("fck", fck_mpa)
    fy = _require_positive("fy", fy_mpa)
    Es = _require_positive("steel modulus", steel_modulus_gpa)
    fctm = fctm_mpa if fctm_mpa is not None else 0.259 * fck ** (2 / 3)
    fctm = _require_positive("fctm", fctm)
    xu_ratio = .0035 / (.0035 + .87 * fy / (Es * 1000))
    r_lim = .36 * fck * xu_ratio * (1 - .416 * xu_ratio)
    d_min = 1000 * math.sqrt(M / (100 * r_lim)) if M else 0.0
    discriminant = 1 - 4.6 * M / (fck * 100 * (d / 1000)**2)
    if discriminant < 0:
        ast_required = None
        reason = "Moment exceeds the domain of the Excel singly-reinforced equation"
    else:
        ast_required = .5 * fck / fy * (1 - math.sqrt(max(discriminant, 0))) * (d / 1000) * 1e6
        reason = ""
    xu_max_mm = xu_ratio * d
    as_limit = .36 * fck * 1000 * xu_max_mm / (.87 * fy)
    # Explicitly follow source MIN(), even though minimum-steel interpretation
    # and governing code clause should be reviewed by engineer.
    as_min = min(.26 * fctm / fy * 1000 * d, .0013 * 1000 * d)
    depth_ok = d + 1e-8 >= d_min
    steel_ok = ast_required is not None and As + 1e-8 >= max(ast_required, as_min)
    under_reinforced = As < as_limit
    checks_pass = depth_ok and steel_ok and under_reinforced
    return {
        "moment_tf_m_per_m": M, "effective_depth_mm": d,
        "provided_as_mm2_per_m": As,
        "xu_max_over_d": xu_ratio, "xu_max_mm": xu_max_mm,
        "r_lim_mpa": r_lim, "d_min_mm": d_min,
        "as_required_mm2_per_m": ast_required,
        "as_min_mm2_per_m": as_min, "as_limit_mm2_per_m": as_limit,
        "depth_check": "OK" if depth_ok else "NOT OK",
        "reinforcement_check": "OK" if steel_ok else "NOT OK",
        "reinforcement_type": "Under-reinforced" if under_reinforced else "Over-reinforced / limit reached",
        "status": "PASS (arithmetic only)" if checks_pass else "REVIEW REQUIRED",
        "note": reason,
    }


def distribution_check(*, required_as_mm2_per_m: float, diameter_mm: float, spacing_mm: float) -> dict:
    required = float(required_as_mm2_per_m)
    if not math.isfinite(required) or required < 0:
        raise ValueError("distribution As required must be non-negative and finite")
    dia = _require_positive("distribution bar diameter", diameter_mm)
    spacing = _require_positive("distribution bar spacing", spacing_mm)
    provided = math.pi * dia**2 / 4 * 1000 / spacing
    return {"required_as_mm2_per_m": required, "provided_as_mm2_per_m": provided,
            "diameter_mm": dia, "spacing_mm": spacing,
            "status": "PASS (arithmetic only)" if provided + 1e-8 >= required else "REVIEW REQUIRED"}


def shear_without_stirrups(
    *, design_shear_tf_per_m: float, provided_as_mm2_per_m: float,
    effective_depth_mm: float, fck_mpa: float = 35,
    sigma_cp_mpa: float = 0.0,
) -> dict:
    V = abs(float(design_shear_tf_per_m))
    As = float(provided_as_mm2_per_m)
    if not math.isfinite(V) or not math.isfinite(As) or As < 0:
        raise ValueError("shear and longitudinal As must be finite; As nonnegative")
    d = _require_positive("effective depth", effective_depth_mm)
    fck = _require_positive("fck", fck_mpa)
    cp = float(sigma_cp_mpa)
    if not math.isfinite(cp) or cp < 0:
        raise ValueError("sigma_cp must be a finite nonnegative compression stress")
    rho = As / (1000 * d)
    k = min(2.0, 1 + math.sqrt(200 / d))
    v_min = .031 * k**1.5 * math.sqrt(fck) * (1000 * d) / 10000
    v_rdc = (.12 * k * (80 * rho * fck)**.33 + .15 * cp) * (1000 * d) / 10000
    resistance = max(v_min, v_rdc)
    needs = V >= resistance  # workbook uses strictly greater capacity to waive stirrups
    return {
        "design_shear_tf_per_m": V, "provided_as_mm2_per_m": As,
        "effective_depth_mm": d, "longitudinal_rho": rho,
        "k": k, "v_min_tf_per_m": v_min, "v_rdc_tf_per_m": v_rdc,
        "resistance_tf_per_m": resistance,
        "stirrups_required": needs,
        "status": "STIRRUPS REQUIRED" if needs else "NO STIRRUPS PER WORKBOOK EQUATION",
    }


def stirrup_check(
    *, design_shear_tf_per_m: float, effective_depth_mm: float,
    legs: int, diameter_mm: float, spacing_mm: float, theta_deg: float = 21.8,
    fck_mpa: float = 35, fy_mpa: float = 500,
) -> dict:
    V = abs(float(design_shear_tf_per_m))
    if not math.isfinite(V):
        raise ValueError("design shear must be finite")
    d = _require_positive("effective depth", effective_depth_mm)
    if type(legs) is not int or legs <= 0:
        raise ValueError("stirrup legs must be a positive integer")
    dia = _require_positive("stirrup bar diameter", diameter_mm)
    spacing = _require_positive("stirrup spacing", spacing_mm)
    theta = _require_positive("theta", theta_deg)
    if not 1 < theta < 89:
        raise ValueError("theta must be between 1 and 89 degrees")
    fck = _require_positive("fck", fck_mpa)
    fy = _require_positive("fy", fy_mpa)
    asw = legs * math.pi * dia**2 / 4
    nu1 = .6 * (1 - fck / 310)
    if nu1 <= 0:
        raise ValueError("fck outside of workbook's stirrup equation domain")
    # Follow rows 145–164 of the workbook; units tf/m per 1 m design strip.
    vrd_max = .225 * nu1 * fck * 100 * .9 * (d / 1000)
    vrd_min = .155 * nu1 * fck * 100 * .9 * (d / 1000)
    vrd_s = (asw / 1e6) / (spacing / 1000) * (.8 * fy * 100) * \
        (1 / math.tan(math.radians(theta))) * .9 * (d / 1000)
    rho_w = asw / (spacing * 1000)
    rho_min = .072 * math.sqrt(fck) / fy
    max_spacing = min(.75 * d, 600)
    strength_ok = V <= min(vrd_s, vrd_max) + 1e-8
    min_reinf_ok = rho_w + 1e-10 >= rho_min
    spacing_ok = spacing <= max_spacing + 1e-8
    ok = strength_ok and min_reinf_ok and spacing_ok
    return {
        "design_shear_tf_per_m": V, "effective_depth_mm": d,
        "legs": legs, "bar_dia_mm": dia, "spacing_mm": spacing,
        "theta_deg": theta, "asw_mm2": asw, "nu1": nu1,
        "vrd_max_tf_per_m": vrd_max, "vrd_min_tf_per_m": vrd_min,
        "vrd_s_tf_per_m": vrd_s, "rho_w": rho_w, "rho_w_min": rho_min,
        "max_spacing_mm": max_spacing,
        "shear_strength_check": "OK" if strength_ok else "NOT OK",
        "minimum_shear_reinforcement_check": "OK" if min_reinf_ok else "NOT OK",
        "stirrup_spacing_check": "OK" if spacing_ok else "NOT OK",
        "status": "PASS (arithmetic only)" if ok else "REVIEW REQUIRED",
    }


def calculate_uls_design(inputs: Any, overrides: dict[int, dict[str, Any]] | None = None) -> dict:
    """Calculate the valid single-cell rows of Excel Design!52:166.

    ``overrides`` maps Excel row numbers to editable input fields, e.g.
      {64: {'moment_tf_m_per_m': 8.5, 'provided_as_mm2_per_m': 1100}}
      {122: {'design_shear_tf_per_m': 22.5}}
      {150: {'spacing_mm': 150}}

    The imported ULS actions are frozen workbook cached values and must NOT be
    confused with forces from the experimental FEM solver.
    """
    ref = reference_table()
    overrides = overrides or {}
    fck = float(inputs.concrete_grade.removeprefix('M'))
    fy = float(inputs.steel_grade.removeprefix('Fe'))
    reference_geometry = _reference_geometry_matches(inputs)
    flex_rows, dist_rows, shear_rows, stirrup_rows = [], [], [], []
    for src in ref['flexure']:
        row = dict(src)
        row.update(overrides.get(src['row'], {}))
        calc = flexural_check(
            moment_tf_m_per_m=row['moment_tf_m_per_m'],
            effective_depth_mm=row['effective_depth_mm'],
            provided_as_mm2_per_m=row['provided_as_mm2_per_m'],
            fck_mpa=fck, fy_mpa=fy,
        )
        result = {**row, **calc}
        result['excel_as_required_delta_mm2_per_m'] = (
            None if result['as_required_mm2_per_m'] is None else
            result['as_required_mm2_per_m'] - src['excel_as_required_mm2_per_m']
        )
        flex_rows.append(result)
    for src in ref['distribution']:
        row = dict(src); row.update(overrides.get(src['row'], {}))
        dist_rows.append({**row, **distribution_check(
            required_as_mm2_per_m=row['required_as_mm2_per_m'],
            diameter_mm=row['diameter_mm'], spacing_mm=row['spacing_mm'],
        )})
    for src in ref['shear']:
        row = dict(src); row.update(overrides.get(src['row'], {}))
        shear_rows.append({**row, **shear_without_stirrups(
            design_shear_tf_per_m=row['design_shear_tf_per_m'],
            provided_as_mm2_per_m=row['provided_as_mm2_per_m'],
            effective_depth_mm=row['effective_depth_mm'], fck_mpa=fck,
        )})
    by_shear = {r['row']:r for r in shear_rows}
    for src in ref['stirrups']:
        row = dict(src); row.update(overrides.get(src['row'], {}))
        corresponding = by_shear[row['shear_row']]
        stirrup_rows.append({**row, **stirrup_check(
            design_shear_tf_per_m=corresponding['design_shear_tf_per_m'],
            effective_depth_mm=corresponding['effective_depth_mm'],
            legs=int(row['legs']), diameter_mm=row['bar_dia_mm'],
            spacing_mm=row['spacing_mm'], theta_deg=row['theta_deg'],
            fck_mpa=fck, fy_mpa=fy,
        )})
    all_results = flex_rows + dist_rows + stirrup_rows
    review_count = sum(x['status'] == 'REVIEW REQUIRED' for x in all_results)
    return {
        'source': 'Excel Design!52:166 cached reference values (original case)',
        'demand_source': 'SOURCE WORKBOOK CACHED VALUES — NOT LIVE FRAME SOLVER',
        'reference_geometry_matches': reference_geometry,
        'fck_mpa': fck, 'fy_mpa': fy,
        'flexure': flex_rows, 'distribution': dist_rows,
        'shear_without_stirrups': shear_rows, 'stirrups': stirrup_rows,
        'review_count': review_count,
        'skipped_nonapplicable_rows': ref['not_applicable'],
        'reference_warnings': ref['warnings'],
        'status': (
            'REFERENCE-CASE CALCULATIONS ONLY — ENGINEER VERIFICATION REQUIRED'
            if reference_geometry else
            'REFERENCE INPUTS DO NOT MATCH CURRENT GEOMETRY — NOT A DESIGN CHECK'
        ),
        'limitations': [
            'ULS moment/shear demands are copied from the ORIGINAL workbook, not computed from the current load patterns or the experimental FEM.',
            'Only single-cell applicable/valid rows are evaluated; blank and Excel error rows are skipped, not passed.',
            'No verified force envelope, axial–bending interaction, torsion, punching, anchorage, detailing or construction approval.',
            'Workbook has a stirrup spacing inconsistency: 225mm is larger than its own s_max values even where the source says OK.',
            'Changing geometry/materials invalidates the original workbook demand and effective depth mapping until engineer updates inputs.',
            'Formula factors and tf conversion conventions are preserved from the workbook for audit, not independently certified as current IRC-compliant.',
        ],
    }
