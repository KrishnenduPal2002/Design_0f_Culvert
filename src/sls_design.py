"""Stage 6 SLS — transparent translation of the supplied Design-sheet reference.

Rare-combination short-term cracked-section stresses, and quasi-permanent
crack-width equations only. No long-term, deflection, fatigue, structural
model/force-envelope or approved crack-width limit is assumed.

The workbook uses t-force metres and its own 100 t/m2-to-MPa convention.
This is an explicit *reference arithmetic* translation, not certified IRC
compliance. Engineering review must confirm forces, limits and sign/face maps.
"""
from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
from typing import Any

REFERENCE_FILE = Path(__file__).resolve().parents[1] / 'data' / 'sls_design_reference.json'


def reference_table() -> dict:
    with REFERENCE_FILE.open(encoding='utf-8') as stream:
        return json.load(stream)


def _positive(name: str, val: Any, *, allow_zero: bool = False) -> float:
    num = float(val)
    if not math.isfinite(num) or (num < 0 if allow_zero else num <= 0):
        raise ValueError(f'{name} must be finite and {"nonnegative" if allow_zero else "positive"}')
    return num


def cracked_section(
    *, moment_tf_m_per_m: float, effective_depth_mm: float,
    as_mm2_per_m: float, fck_mpa: float, fy_mpa: float,
    es_gpa: float, ecm_gpa: float,
) -> dict:
    """Compute neutral axis, cracked inertia and rare steel/concrete stress.

    Mirrors the supplied workbook's 1 m rectangular strip, not a general
    cracked-section model (e.g., axial loading, T-sections or transformed faces).
    """
    m_tf = float(moment_tf_m_per_m)
    if not math.isfinite(m_tf):
        raise ValueError('Rare moment must be finite')
    d = _positive('effective depth', effective_depth_mm)
    area = _positive('As', as_mm2_per_m)
    fck = _positive('fck', fck_mpa)
    fy = _positive('fy', fy_mpa)
    es = _positive('Es', es_gpa)
    ec = _positive('Ecm', ecm_gpa)
    ratio = es / ec
    # Design-sheet neutral-axis formula in mm, 1-m effective width.
    x = (-ratio * area + math.sqrt((ratio*area)**2 + 2*1000*ratio*d*area)) / 1000
    inertia = (1000*x**3 / 3 + ratio*area*(d-x)**2) / 1e12
    if x <= 0 or x >= d or inertia <= 0:
        raise ValueError('Invalid cracked-section properties for supplied inputs')
    # The original spreadsheet uses 100 t/m2 per MPa.
    concrete_mpa = abs(m_tf) * x / inertia / 1000 / 100
    steel_mpa = ratio * abs(m_tf) * (d-x) / 1000 / inertia / 100
    concrete_limit = 0.48 * fck
    steel_limit = min(0.8*fy, 300.0)
    return {
        'modular_ratio': ratio,
        'neutral_axis_mm': x,
        'cracked_inertia_m4': inertia,
        'concrete_stress_mpa': concrete_mpa,
        'steel_stress_mpa': steel_mpa,
        'concrete_limit_mpa': concrete_limit,
        'steel_limit_mpa': steel_limit,
        'concrete_status': 'PASS' if concrete_mpa < concrete_limit else 'REVIEW',
        'steel_status': 'PASS' if steel_mpa < steel_limit else 'REVIEW',
        'stress_status': 'PASS' if (concrete_mpa < concrete_limit and steel_mpa < steel_limit) else 'REVIEW',
    }


def crack_width(
    *, moment_tf_m_per_m: float, section_depth_mm: float,
    effective_depth_mm: float, as_mm2_per_m: float,
    equivalent_diameter_mm: float, cover_mm: float,
    neutral_axis_mm: float, cracked_inertia_m4: float,
    modular_ratio: float, es_gpa: float, fctm_mpa: float,
    kt: float = 0.5, k1: float = 0.8, k2: float = 0.5,
    permissible_width_mm: float | None = None,
) -> dict:
    """Workbook quasi-permanent short-term crack-width arithmetic.

    No crack-width PASS/FAIL is issued without an engineer-confirmed limit.
    """
    moment = _positive('quasi moment magnitude', abs(moment_tf_m_per_m), allow_zero=True)
    h = _positive('section depth', section_depth_mm)
    d = _positive('effective depth', effective_depth_mm)
    if d >= h:
        raise ValueError('effective depth must be smaller than section depth')
    area = _positive('As', as_mm2_per_m)
    phi = _positive('equivalent diameter', equivalent_diameter_mm)
    cover = _positive('clear cover', cover_mm)
    x = _positive('neutral axis', neutral_axis_mm)
    inertia = _positive('cracked inertia', cracked_inertia_m4)
    ratio = _positive('modular ratio', modular_ratio)
    es = _positive('Es', es_gpa)
    fctm = _positive('mean concrete tensile strength', fctm_mpa)
    kt = _positive('kt', kt, allow_zero=True)
    k1 = _positive('k1', k1)
    k2 = _positive('k2', k2)
    ac_eff = min(2.5*(h-d), (h-x)/3, h/2)*1000  # mm² / m
    if ac_eff <= 0:
        raise ValueError('Effective concrete tension area is not positive')
    rho = area / ac_eff
    s_r_max = 3.4*cover + (0.425*k1*k2*phi) / rho
    steel_stress = ratio*moment*(d-x)/1000/inertia/100  # MPa
    strain = max(
        (steel_stress-kt*fctm/rho*(1+ratio*rho))/(es*1000),
        0.6*steel_stress/(es*1000),
    )
    w = s_r_max * strain
    if permissible_width_mm is None:
        status = 'LIMIT NOT SET'
    else:
        limit = _positive('permissible crack width', permissible_width_mm)
        status = 'PASS' if w <= limit else 'REVIEW'
    return {
        'effective_tension_area_mm2_per_m': ac_eff,
        'effective_reinforcement_ratio': rho,
        'maximum_crack_spacing_mm': s_r_max,
        'quasi_steel_stress_mpa': steel_stress,
        'mean_strain_difference': strain,
        'crack_width_mm': w,
        'permissible_crack_width_mm': permissible_width_mm,
        'crack_status': status,
    }


def calculate_sls_design(
    inputs: Any,
    overrides: dict[str, dict] | None = None,
    *, demand_source: str = 'Original Excel reference (SLS demands; not live model)',
    es_gpa: float = 200.0,
    ecm_gpa: float | None = None,
    k_t: float = 0.5,
    k_1: float = 0.8,
    k_2: float = 0.5,
    permissible_crack_width_mm: float | None = None,
) -> dict:
    reference = reference_table()
    original = reference['material_reference']
    fck = float(inputs.concrete_grade.removeprefix('M'))
    fy = float(inputs.steel_grade.removeprefix('Fe'))
    ec = _positive('Ecm', ecm_gpa if ecm_gpa is not None else original['ecm_gpa'])
    # Formula fctm is from the original design sheet and must be checked by the engineer.
    fctm = 0.259*fck**(2/3)
    from .structural_analysis import is_original_geometry
    matches = is_original_geometry(inputs)
    overrides = overrides or {}
    known_ids = {sec['section_id'] for sec in reference['sections']}
    unknown = set(overrides)-known_ids
    if unknown:
        raise ValueError(f'Unknown SLS section IDs: {sorted(unknown)}')
    sections = []
    for source in reference['sections']:
        section = dict(source)
        changed = overrides.get(section['section_id'], {})
        allowed = {
            'section_depth_mm', 'effective_depth_mm', 'provided_as_mm2_per_m',
            'equivalent_bar_diameter_mm', 'cover_mm',
            'rare_moment_tf_m_per_m', 'quasi_moment_tf_m_per_m'
        }
        invalid = set(changed)-allowed
        if invalid:
            raise ValueError(f'Unsupported SLS inputs for {section["section_id"]}: {sorted(invalid)}')
        section.update(changed)
        stress = cracked_section(
            moment_tf_m_per_m=section['rare_moment_tf_m_per_m'],
            effective_depth_mm=section['effective_depth_mm'],
            as_mm2_per_m=section['provided_as_mm2_per_m'],
            fck_mpa=fck, fy_mpa=fy, es_gpa=es_gpa, ecm_gpa=ec,
        )
        crack = crack_width(
            moment_tf_m_per_m=section['quasi_moment_tf_m_per_m'],
            section_depth_mm=section['section_depth_mm'],
            effective_depth_mm=section['effective_depth_mm'],
            as_mm2_per_m=section['provided_as_mm2_per_m'],
            equivalent_diameter_mm=section['equivalent_bar_diameter_mm'],
            cover_mm=section['cover_mm'],
            neutral_axis_mm=stress['neutral_axis_mm'],
            cracked_inertia_m4=stress['cracked_inertia_m4'],
            modular_ratio=stress['modular_ratio'], es_gpa=es_gpa,
            fctm_mpa=fctm, kt=k_t, k1=k_1, k2=k_2,
            permissible_width_mm=permissible_crack_width_mm,
        )
        section.update(stress)
        section.update(crack)
        sections.append(section)
    rare_review = sum(s['stress_status'] != 'PASS' for s in sections)
    crack_review = sum(s['crack_status'] == 'REVIEW' for s in sections)
    limitations = [
        'SLS rare and quasi demands use the selected source, not ULS ultimate moments.',
        'Original Excel section properties and steel are retained as defaults; changing geometry alone does not redesign sections.',
        'Service-load combinations, structural demand envelopes, tension-face mapping, and permissible crack width require engineer confirmation.',
        'Short-term rare stresses and quasi-permanent crack widths only: no long-term effects or deflection checks.',
    ]
    if not matches:
        limitations.insert(0, 'Current geometry differs from original Excel. Re-enter engineer-verified SLS section data.')
    return {
        'source': reference['source'],
        'demand_source': demand_source,
        'reference_geometry_matches': matches,
        'materials': {
            'fck_mpa': fck, 'fy_mpa': fy, 'es_gpa': es_gpa, 'ecm_gpa': ec,
            'fctm_mpa': fctm, 'kt': k_t, 'k1': k_1, 'k2': k_2,
        },
        'permissible_crack_width_mm': permissible_crack_width_mm,
        'sections': sections,
        'rare_review_count': rare_review,
        'crack_review_count': crack_review,
        'limitations': limitations,
    }


def sls_demands_csv_template() -> bytes:
    result = io.StringIO()
    writer = csv.writer(result)
    writer.writerow(['section_id', 'member', 'location', 'rare_moment_tf_m_per_m', 'quasi_moment_tf_m_per_m'])
    for sec in reference_table()['sections']:
        writer.writerow([sec['section_id'], sec['member'], sec['location'], '', ''])
    return result.getvalue().encode('utf-8-sig')


def parse_sls_demands_csv(data: bytes | str) -> dict[str, dict]:
    content = data.decode('utf-8-sig') if isinstance(data, bytes) else data
    reader = csv.DictReader(io.StringIO(content))
    expected = {'section_id', 'rare_moment_tf_m_per_m', 'quasi_moment_tf_m_per_m'}
    if not reader.fieldnames or not expected.issubset(set(reader.fieldnames)):
        raise ValueError('SLS CSV must include section ID, rare moment and quasi moment columns')
    valid = {sec['section_id'] for sec in reference_table()['sections']}
    rows = {}
    for row in reader:
        key = (row.get('section_id') or '').strip()
        if key not in valid or key in rows:
            raise ValueError(f'Unknown/duplicate SLS section ID: {key}')
        values = {}
        for field in ('rare_moment_tf_m_per_m', 'quasi_moment_tf_m_per_m'):
            val = row.get(field, '')
            if not val or not val.strip():
                raise ValueError(f'Missing {field} at section {key}')
            number = float(val)
            if not math.isfinite(number):
                raise ValueError(f'Non-finite {field} at section {key}')
            values[field] = number
        rows[key] = values
    if set(rows) != valid:
        raise ValueError('Provide all SLS sections; partial files cannot be mixed with old reference demands')
    return rows


def steel_details_from_uls(uls_result: dict) -> dict[str, dict]:
    """Optional reviewed transfer of section d/As; no transfer of ULS moments.

    The identifiers correspond to the verified single-cell section pairs in the
    workbook, but the engineer must confirm the same tension face is used.
    """
    mapping = {
        'S01': 57, 'S02': 58, 'S03': 59,
        'S04': 62, 'S05': 63, 'S06': 64,
        'S07': 67, 'S08': 68, 'S09': 69, 'S10': 70,
        'S11': 71, 'S12': 72, 'S13': 73, 'S14': 74, 'S15': 75, 'S16': 76,
    }
    flex = {int(x['row']): x for x in uls_result['flexure']}
    if not all(row in flex for row in mapping.values()):
        raise ValueError('Missing matching ULS reinforcement sections')
    return {
        sid: {
            'effective_depth_mm': float(flex[row]['effective_depth_mm']),
            'provided_as_mm2_per_m': float(flex[row]['provided_as_mm2_per_m']),
        }
        for sid, row in mapping.items()
    }
