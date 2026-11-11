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


# ---------------------------------------------------------------------------
# Readable BOQ table corresponding to the supplied substructure/superstructure
# worksheet screenshot.  This does NOT replace the existing detailed BOQ data
# or the calculate_boq() API used by the other project modules.
# ---------------------------------------------------------------------------

def boq_excel_style_schedule(inputs: CulvertInputs) -> dict:
    """Return the screenshot-style main-box RCC BOQ with real input-derived amounts.

    Units are metres / cubic metres.  The separate retaining walls, aprons and
    curtain walls are NOT priced or calculated here; they require their own
    geometric designs.  Above-5m worksheet entries remain reference placeholders
    rather than fabricated calculations.
    """
    inputs.validate()
    geom = calculate_geometry(inputs).to_dict()
    length = inputs.length_m
    ext_width = geom['external_width_m']
    side_count = 2
    haunches_each_end = inputs.haunch_count // 2
    haunch_each_m3 = (inputs.haunch_width_m * inputs.haunch_height_m / 2) * length

    def row(desc, nos=0, row_length=None, width=None, depth=None, quantity=None, unit='Cum', **other):
        out = {'kind':'item', 'description':desc, 'unit':unit,
               'nos':nos, 'length_m':row_length, 'width_m':width,
               'depth_m':depth, 'area_m2':None, 'quantity_m3':quantity}
        out.update(other)
        return out

    bottom = row('Bottom Slab', 1, ext_width, length, inputs.bottom_slab_thickness_m,
                 geom['bottom_slab_volume_m3'])
    side = row('Side Wall', side_count, length, inputs.wall_thickness_m,
               inputs.clear_height_m, geom['walls_volume_m3'])
    middle = row('Middle Wall', 0, length, 0.300, inputs.clear_height_m, 0.0)
    bottom_haunch = row('Haunch at Bottom Slab', haunches_each_end, length,
                        inputs.haunch_width_m, inputs.haunch_height_m,
                        haunches_each_end * haunch_each_m3)
    top_haunch = row('Haunch at Top Slab', haunches_each_end, length,
                     inputs.haunch_width_m, inputs.haunch_height_m,
                     haunches_each_end * haunch_each_m3)
    substructure_total = sum(r['quantity_m3'] for r in (bottom, side, middle, bottom_haunch, top_haunch))
    top_slab = row('(i) Top Slab (Upto 5m height)', 1, ext_width, length,
                   inputs.top_slab_thickness_m, geom['top_slab_volume_m3'], unit='cum')
    top_slab_above = row('(ii) Top Slab (Above 5m height)', 1, 0.0, 0.0, 0.0, 0.0, unit='cum',
                         source='Worksheet reference placeholder')

    # These rows are zeros in the supplied screenshot.  We deliberately do not
    # infer >5m quantities from box height or site geometry (not enough inputs).
    reference_zero_rows = [
        row('Box Side Wall', 2, length, inputs.wall_thickness_m, 0.0, 0.0,
            source='Worksheet reference placeholder'),
        row('Box middle Wall', 0, length, 0.30, 0.0, 0.0,
            source='Worksheet reference placeholder'),
        row('Retaining wall Stem', 0, None, None, None, 0.0,
            source='Worksheet reference placeholder'),
        row('Haunch at Top Slab', haunches_each_end, length,
            inputs.haunch_width_m, inputs.haunch_height_m, 0.0,
            source='Worksheet reference placeholder'),
    ]

    rows = [
        {'kind':'section','description':'SUBSTRUCTURE'},
        {'kind':'item_heading', 'sl_no':'3', 'spec':'1500, 1700 & 2200',
         'description':'R.C.C. grade M 35 in Sub Structure complete as per Dwg & Tech Specification.'},
        {'kind':'subsection','description':'Upto 5m Height.'},
        bottom, side, middle, bottom_haunch, top_haunch,
        {'kind':'subtotal','description':'Sub-Total =', 'quantity_m3':substructure_total},
        {'kind':'subsection','description':'Above 5m Height.'},
        *reference_zero_rows,
        {'kind':'subtotal','description':'Sub-Total =', 'quantity_m3':0.0},
        {'kind':'section','description':'SUPERSTRUCTURE'},
        {'kind':'item_heading','sl_no':'8','spec':'1500 & 1600 in 1700',
         'description':('Providing and laying reinforced cement concrete in superstructure '
                        'complete as per drawing and technical specifications Clauses '
                        '800, 1205.4 and 1205.5. RCC M-35')},
        top_slab, top_slab_above,
        {'kind':'grand_total','description':'TOTAL RCC CONCRETE',
         'quantity_m3':substructure_total + geom['top_slab_volume_m3']},
    ]
    return {
        'rows':rows,
        'substructure_m3':substructure_total,
        'superstructure_m3':geom['top_slab_volume_m3'],
        'total_concrete_m3':substructure_total+geom['top_slab_volume_m3'],
        'above_5m_reference_only':True,
    }


def boq_sketch_parameters(inputs: CulvertInputs) -> dict:
    """Read the clearly labelled dimensions shown in the supplied BOQ sketch.

    Box geometry uses the live sidebar inputs.  Other site/work dimensions are
    static *reference data* from the screenshot, NOT derived design quantities.
    """
    inputs.validate()
    g=calculate_geometry(inputs).to_dict()
    box_rows = [
        ('Span arrangement', f'1 × {inputs.clear_width_m:.3f}', ''),
        ('Total length / outside width of bridge', g['external_width_m'], 'm'),
        ('Clear length / opening width', inputs.clear_width_m, 'm'),
        ('Clear opening height', inputs.clear_height_m, 'm'),
        ('Number of side walls', 2, 'Nos'),
        ('Thickness of side wall', inputs.wall_thickness_m, 'm'),
        ('Number of middle walls', 0, 'Nos'),
        ('Thickness of middle wall (reference only)', 0.300, 'm'),
        ('Thickness of top slab', inputs.top_slab_thickness_m, 'm'),
        ('Thickness of bottom slab', inputs.bottom_slab_thickness_m, 'm'),
        ('Haunch horizontal size', inputs.haunch_width_m if inputs.haunch_count else 0.0, 'm'),
        ('Haunch vertical size', inputs.haunch_height_m if inputs.haunch_count else 0.0, 'm'),
        ('Deck width / barrel length', inputs.length_m, 'm'),
    ]
    # Name-value pairs below are source-screenshot data, not active calculation
    # inputs.  They are not overwritten by edits to the main box dimensions.
    site_rows = [
        ('Shear key depth', 1.200, 'm'),
        ('Retaining wall thickness (top)', 0.300, 'm'),
        ('Retaining wall thickness (bottom)', 0.800, 'm'),
        ('Retaining wall height', 4.698, 'm'),
        ('Retaining wall length', 4.247, 'm'),
        ('Retaining wall foundation width', 4.400, 'm'),
        ('Retaining wall foundation depth (uniform)', 0.300, 'm'),
        ('Retaining wall foundation depth (sloping)', 0.300, 'm'),
        ('Approach slab thickness', 0.300, 'm'),
        ('Approach slab length', 3.500, 'm'),
        ('Rigid apron length (front side)', 7.747, 'm'),
        ('Rigid apron length (back side)', 3.000, 'm'),
        ('Rigid apron width', 4.875, 'm'),
        ('Curtain wall length', 7.747, 'm'),
        ('Curtain wall depth (D/S)', 2.500, 'm'),
        ('Curtain wall depth (U/S)', 2.000, 'm'),
        ('Curtain wall width (D/S)', 1.850, 'm'),
        ('Curtain wall width (U/S)', 1.500, 'm'),
        ('Floor apron length', 7.747, 'm'),
        ('Floor apron width (D/S)', 6.000, 'm'),
        ('Floor apron width (U/S)', 3.000, 'm'),
    ]
    return {'box_geometry':[{'Parameter':n, 'Value':v,'Unit':u} for n,v,u in box_rows],
            'other_works_reference':[{'Parameter':n, 'Value':v,'Unit':u} for n,v,u in site_rows]}


# ---------------------------------------------------------------------------
# Step 9 — simple, editable, auto-calculated BOQ tables.
# Public functions below intentionally do not change calculate_boq() or the
# older JSON reference schedule, preserving all existing reporting features.
# ---------------------------------------------------------------------------

BOQ_GROUPS = (
    ('Substructure', 'Up to 5m'),
    ('Substructure', 'Above 5m'),
    ('Superstructure', 'Top slab'),
)


def editable_boq_template(inputs: CulvertInputs) -> list[dict]:
    """Make data-editor-friendly rows from the approved screenshot-style example.

    Formula inputs are in metres and the calculated quantity is cubic metres.
    This is a single-cell box bill; other site-work entries are reference-only.
    """
    source = boq_excel_style_schedule(inputs)['rows']
    section = subsection = ''
    rows = []
    for record in source:
        kind = record['kind']
        if kind == 'section':
            section = record['description'].title()
            subsection = ''
        elif kind == 'subsection':
            subsection = ('Above 5m' if record['description'].strip().lower().startswith('above')
                          else 'Up to 5m')
        elif kind == 'item':
            group = 'Top slab' if section == 'Superstructure' else subsection
            rows.append({
                'key': f'boq_{len(rows):02}',
                'section': section,
                'group': group,
                'item': record['description'],
                'nos': (0 if group == 'Above 5m' and
                        'haunch' in record['description'].lower()
                        else int(record['nos'])),
                'length_m': float(record['length_m'] or 0),
                'width_m': float(record['width_m'] or 0),
                'depth_m': float(record['depth_m'] or 0),
                'shape_factor': 0.5 if 'haunch' in record['description'].lower() else 1.0,
                'unit': 'm³',
            })
    return rows


def calculate_editable_boq(rows: list[dict]) -> dict:
    """Calculate quantities from editable BOQ rows (never from static images).

    Each row has `nos * length * width * depth * shape_factor` where
    shape_factor is 1/2 for triangular haunches. Rejects negative, missing,
    nonfinite data rather than silently substituting misleading quantities.
    """
    if not isinstance(rows, list):
        raise ValueError('BOQ rows must be a list')
    computed = []
    groups = {pair: 0.0 for pair in BOQ_GROUPS}
    seen = set()
    for row in rows:
        key = row.get('key')
        if not key or key in seen:
            raise ValueError('BOQ row keys must be unique')
        seen.add(key)
        group = (row.get('section'), row.get('group'))
        if group not in groups:
            raise ValueError(f'Unknown BOQ section: {group}')
        values = {}
        for field in ('nos', 'length_m', 'width_m', 'depth_m', 'shape_factor'):
            try:
                val = float(row[field])
            except (TypeError, ValueError, KeyError):
                raise ValueError(f'{row.get("item", key)}: invalid {field}') from None
            if not math.isfinite(val) or val < 0:
                raise ValueError(f'{row.get("item", key)}: {field} must be nonnegative and finite')
            values[field] = val
        if not values['nos'].is_integer():
            raise ValueError(f'{row.get("item", key)}: Nos must be a whole number')
        quantity = (values['nos'] * values['length_m'] * values['width_m']
                    * values['depth_m'] * values['shape_factor'])
        data = dict(row)
        data['quantity_m3'] = quantity
        computed.append(data)
        groups[group] += quantity
    substructure = groups[('Substructure', 'Up to 5m')] + groups[('Substructure', 'Above 5m')]
    superstructure = groups[('Superstructure', 'Top slab')]
    return {
        'rows': computed,
        'groups': groups,
        'substructure_m3': substructure,
        'superstructure_m3': superstructure,
        'total_concrete_m3': substructure + superstructure,
    }
