"""Stage 7 BBS quantity arithmetic from engineer's BBS (2) reference case.

DO NOT use this as a construction bar bending schedule. Cutting lengths,
anchorage, lap, bends and bar counts must come from approved reinforcement drawings.
Reference bars with zero/unknown diameter are EXCLUDED and flagged.
"""
from __future__ import annotations
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import json
import math

REFERENCE_PATH = Path(__file__).resolve().parents[1] / "data" / "bbs_reference.json"


@lru_cache(maxsize=1)
def load_bbs_reference() -> dict:
    with REFERENCE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def reference_bars() -> list[dict]:
    """Editable copies; modifying returned records never changes the source JSON."""
    return deepcopy(load_bbs_reference()["bars"])


def kg_per_m(diameter_mm: float) -> float:
    """Reference workbook mass formula (D² / 162.2) kg/m; not a material certificate."""
    dia = float(diameter_mm)
    if not math.isfinite(dia) or dia < 0:
        raise ValueError("Bar diameter must be finite and nonnegative")
    return dia * dia / 162.2


def calculate_bbs(bar_rows: list[dict] | None = None, allowance_pct: float = 5.0) -> dict:
    """Recompute schedule mass from editable diameter, cutting length and count.

    The workbook's nominal mark/spacing/shape values are reference data. This
    function NEVER infers bar count or bar length from culvert dimensions.
    """
    rows = reference_bars() if bar_rows is None else deepcopy(bar_rows)
    allowance = float(allowance_pct)
    if not math.isfinite(allowance) or not 0 <= allowance <= 100:
        raise ValueError("BBS allowance must be between 0 and 100 percent")
    lines=[]; warnings=[]; net=0.0
    seen=set()
    for r in rows:
        identifier = str(r.get('id', '')).strip()
        if not identifier or identifier in seen:
            raise ValueError("Each BBS row must have a distinct nonempty id")
        seen.add(identifier)
        dia=float(r.get('diameter_mm', 0) or 0)
        cut=float(r.get('cut_length_mm', 0) or 0)
        count=float(r.get('bar_count', 0) or 0)
        if any(not math.isfinite(v) for v in (dia,cut,count)):
            raise ValueError(f"BBS {identifier}: enter finite numeric values")
        if dia < 0 or cut < 0 or count < 0 or not count.is_integer():
            raise ValueError(f"BBS {identifier}: diameter/cut/count must be nonnegative; count integer")
        reason=''
        if count > 0 and dia <= 0:
            reason='Missing bar diameter — excluded; engineer must confirm'
        elif count > 0 and cut <= 0:
            reason='Missing cutting length — excluded; engineer must confirm'
        if reason:
            warnings.append(f"{r.get('mark',identifier)}: {reason}")
        active=(count > 0 and dia > 0 and cut > 0)
        total_m=(cut*count/1000.0) if active else 0.0
        unit_kgm=kg_per_m(dia) if active else 0.0
        total_kg=total_m*unit_kgm
        net+=total_kg
        lines.append({
            'id':identifier,'mark':str(r.get('mark','')),'member':str(r.get('member','')),
            'diameter_mm':dia,'spacing_mm':r.get('spacing_mm'),
            'm1_mm':r.get('m1_mm'),'m2_mm':r.get('m2_mm'),'m3_mm':r.get('m3_mm'),
            'cut_length_mm':cut, 'bar_count':int(count),
            'length_total_m':total_m,'unit_weight_kg_m':unit_kgm,
            'weight_kg':total_kg,'status':reason or ('Included' if active else 'Inactive'),
            'reference_weight_kg':r.get('reference_weight_kg',0.0),
            'source':r.get('source','manual'),
        })
    gross=net*(1+allowance/100.0)
    reference=load_bbs_reference()
    return {
        'source':'BBS (2) — original reference quantities; editable trial schedule',
        'lines':lines, 'bar_mark_count':len(lines),
        'net_steel_kg':net,'allowance_percent':allowance,
        'allowance_kg':gross-net,'total_steel_kg':gross,'total_steel_t':gross/1000,
        'reference_net_kg':reference['reference_totals']['steel_net_kg'],
        'reference_gross_kg':reference['reference_totals']['steel_with_allowance_kg'],
        'warnings':warnings+list(reference['source_issues']),
        'status':'REFERENCE/USER-EDITED QUANTITIES ONLY — NOT AN APPROVED BAR SCHEDULE',
    }
