"""Stage 7 BOQ quantities: geometry-linked core items and reference-only BOQ lines.

Do not represent the general BOQ schedule as automatically recalculated: many
works (foundations, retaining walls, aprons, barriers and drainage) have separate
geometries and source formulas not reproduced by this module.
"""
from __future__ import annotations
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import json, math
from .inputs import CulvertInputs
from .geometry import calculate_geometry

REFERENCE_PATH = Path(__file__).resolve().parents[1] / 'data' / 'boq_reference.json'

@lru_cache(maxsize=1)
def load_boq_reference() -> dict:
    with REFERENCE_PATH.open(encoding='utf-8') as handle:
        return json.load(handle)


def reference_boq_items() -> list[dict]:
    return deepcopy(load_boq_reference()['items'])


def calculate_boq(inputs: CulvertInputs, bbs_results: dict | None = None,
                  quantity_overrides: dict[str,float] | None = None) -> dict:
    inputs.validate()
    geom=calculate_geometry(inputs).to_dict()
    ref=load_boq_reference()
    quantity_overrides=quantity_overrides or {}
    ids={r['id'] for r in ref['items']}
    extra=set(quantity_overrides)-ids
    if extra:
        raise ValueError('Unknown BOQ reference item id(s): '+', '.join(sorted(extra)))
    items=[]
    for r in ref['items']:
        row=dict(r)
        original=float(row['reference_quantity'])
        if r['id'] in quantity_overrides:
            q=float(quantity_overrides[r['id']])
            if not math.isfinite(q) or q < 0:
                raise ValueError(f"Invalid quantity for {r['id']}")
            row['quantity']=q;row['quantity_source']='Manually entered — not recomputed from geometry'
        else:
            row['quantity']=original;row['quantity_source']='Original BOQ cached reference, not updated for geometry'
        items.append(row)
    core=[
       {'description':'Top slab concrete','unit':'m³','quantity':geom['top_slab_volume_m3']},
       {'description':'Bottom slab concrete','unit':'m³','quantity':geom['bottom_slab_volume_m3']},
       {'description':'Side wall concrete','unit':'m³','quantity':geom['walls_volume_m3']},
       {'description':'Four internal triangular haunches concrete','unit':'m³','quantity':geom['haunch_volume_m3']},
    ]
    core.append({'description':'TOTAL single-cell culvert box concrete', 'unit':'m³','quantity':geom['concrete_volume_m3']})
    if bbs_results is not None:
        core.append({'description':'Bar-by-bar scheduled reinforcement incl. allowance',
                     'unit':'t','quantity':bbs_results['total_steel_t']})
    orig=ref['reference_quantities']
    ref_boq_rebar_t=orig['estimated_substructure_reinforcement_t']+orig['estimated_top_slab_reinforcement_t']
    flags=[]
    if bbs_results is not None:
        flags.append('BOQ reinforcement estimator (80 kg/m³ for specified concrete categories) differs from the BBS bar-by-bar schedule. Confirm with sir.')
    geometry_is_reference=(
      math.isclose(inputs.clear_width_m,3.0,abs_tol=1e-9) and
      math.isclose(inputs.clear_height_m,3.0,abs_tol=1e-9) and
      math.isclose(inputs.length_m,12.0,abs_tol=1e-9) and
      math.isclose(inputs.wall_thickness_m,0.25,abs_tol=1e-9) and
      math.isclose(inputs.top_slab_thickness_m,0.25,abs_tol=1e-9) and
      math.isclose(inputs.bottom_slab_thickness_m,0.275,abs_tol=1e-9) and
      math.isclose(inputs.haunch_width_m,0.15,abs_tol=1e-9) and
      math.isclose(inputs.haunch_height_m,0.15,abs_tol=1e-9) and
      inputs.haunch_count==4
    )
    if not geometry_is_reference:
        flags.append('Input geometry differs from reference: only core concrete volumes update dynamically. Other BOQ rows are cached or manual.')
    return {
      'status':'CORE GEOMETRY DYNAMIC; GENERAL BOQ REFERENCE/MANUAL ONLY',
      'core_quantities':core, 'reference_items':items,
      'reference_item_count':len(items),
      'source_reference_geometry_matches':geometry_is_reference,
      'reference_box_concrete_m3':orig['total_culvert_concrete_m3'],
      'boq_estimated_rebar_t':ref_boq_rebar_t,
      'bbs_scheduled_rebar_t':(bbs_results or {}).get('total_steel_t'),
      'boq_vs_bbs_rebar_difference_t':(ref_boq_rebar_t-bbs_results['total_steel_t'] if bbs_results is not None else None),
      'warnings':flags+list(ref['limitations'])
    }
