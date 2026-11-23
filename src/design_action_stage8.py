"""Stage 8 — vehicle LL provenance audit and *unverified* independent FEM envelope.

No modification to Streamlit tables or static Stage 7 components. Reference
workbook vehicle outputs are targets only: they never enter the FEM solver.
For 1x3x3 single-cell topology with bilateral (linear) foundation springs only.
"""
from __future__ import annotations
from dataclasses import replace
import csv
import io
import math
import json
from pathlib import Path

from .design_action_stage6 import source_beam_mapping
from .earth_pressure import TF_TO_KN
from .frame_solver import run_frame_analysis, FrameOptions
from .load_combinations import CombinationInputs
from .vehicle_loading import vehicle_preset

VEHICLE_IDS=('70rw','40t','70rt','sv385')
SCHEMA_COLUMNS={'moment':('ULS LL','Rare LL','Quasi LL'),'shear':('LL',)}
REFERENCE_ROWS={'moment':(215,216,217,220,221,222,*range(225,245)),
                'shear':(259,260,261,264,265,266,*range(269,279))}


def load_rules(path):
    doc=json.loads(Path(path).read_text(encoding='utf8'))
    if doc.get('fixture_type')!='vehicle-ll-formula-metadata-only':
        raise ValueError('Wrong rules fixture type')
    for kind in ('moment','shear'):
        records=doc.get(kind)
        if not isinstance(records,list) or tuple(r['excel_row'] for r in records)!=REFERENCE_ROWS[kind]:
            raise ValueError(f'Invalid {kind} row order')
        for row in records:
            if set(row['rules'])!=set(SCHEMA_COLUMNS[kind]):
                raise ValueError(f'Missing original columns for {kind}, row {row["excel_row"]}')
            for col, rule in row['rules'].items():
                if rule.get('operation') not in ('min','max','literal_zero'):
                    raise ValueError(f'Bad operator for {col}')
                if rule['operation']=='literal_zero' and rule.get('terms'):
                    raise ValueError('Literal zero cannot have terms')
                if rule['operation']!='literal_zero' and not rule.get('terms'):
                    raise ValueError('Required source factors are missing')
                for term in rule.get('terms',[]):
                    if term['vehicle'] not in VEHICLE_IDS or not isinstance(term['cell'],str):
                        raise ValueError('Invalid vehicle source key')
                    if not math.isfinite(float(term['factor'])) or float(term['factor'])<0:
                        raise ValueError('Invalid source factor')
    return doc


def evaluate_worksheet_ll(rulebook, vehicle_summary_values):
    """Rebuild LL *column formulas* given supplied vehicle summary cells.

    When supplied source-output cached values this checks Excel formula lineage,
    NOT independent live-load/FEM prediction. Future independently computed
    vehicle summary cells can use this exact same routine.
    """
    computed={'moment':[],'shear':[]}
    for kind in computed:
        for source in rulebook[kind]:
            row={'Excel row':int(source['excel_row'])}
            for col,rule in source['rules'].items():
                if rule['operation']=='literal_zero':
                    row[col]=0.0
                    continue
                candidates=[]
                for term in rule['terms']:
                    source_cell=vehicle_summary_values[term['vehicle']][term['cell']]
                    if source_cell is None or not isinstance(source_cell,(float,int)):
                        raise ValueError(f"Missing numeric vehicle result: {term['vehicle']}!{term['cell']}")
                    number=float(source_cell)
                    if not math.isfinite(number):raise ValueError('Nonfinite vehicle result')
                    if term['abs']:number=abs(number)
                    candidates.append(float(term['factor'])*number)
                selected=max(candidates) if rule['operation']=='max' else min(candidates)
                row[col]=float(rule['outer_factor'])*selected
            computed[kind].append(row)
    return computed


def compare_to_workbook(computed,targets,*,tolerance=1e-8):
    if not math.isfinite(tolerance) or tolerance<0:raise ValueError('Invalid tolerance')
    rows=[]
    for kind in ('moment','shear'):
        if len(computed[kind])!=len(targets[kind]):raise ValueError('Reference length mismatch')
        for observed,expected in zip(computed[kind],targets[kind]):
            if observed['Excel row']!=expected['Excel row']:raise ValueError('Reference row mismatch')
            for col in SCHEMA_COLUMNS[kind]:
                n=float(observed[col]);ref=float(expected[col]);delta=n-ref
                rows.append({'Kind':kind,'Excel row':observed['Excel row'],'Column':col,
                             'Calculated from source vehicle outputs':n,'Source load-table target':ref,
                             'Difference':delta,'Status':'PASS' if abs(delta)<=tolerance else 'MISMATCH'})
    return rows


def csv_text(rows):
    if not rows:return ''
    output=io.StringIO(newline='')
    writer=csv.DictWriter(output,fieldnames=list(rows[0]))
    writer.writeheader();writer.writerows(rows)
    return output.getvalue()


def _vehicle_only_combination():
    return CombinationInputs(name='Vehicle only — unfactored experimental FEM',
        concrete_dead_factor=0.,additional_sidl_factor=0.,wearing_course_factor=0.,
        soil_factor=0.,cushion_factor=0.,surcharge_factor=0.,surcharge_side='none',
        vehicle_factor=1.,braking_factor=0.)


def _positions(span,spacing,step):
    if not (math.isfinite(step) and .05<=step<=2):raise ValueError('Scan step outside [.05,2] m')
    max_pos=span+sum(spacing)
    n=int(max_pos/step)
    places=[round(i*step,10) for i in range(n+1)]
    if abs(places[-1]-max_pos)>1e-9:places.append(max_pos)
    if len(places)>400:raise ValueError('Too many scan positions: increase step')
    return places


def predict_independent_vehicle_end_envelopes(inputs, *, options=None, step_m=.25,
                                               vehicle_ids=VEHICLE_IDS, solver=run_frame_analysis,
                                               vehicle_overrides=None):
    """Experimental independent 4-vehicle FEM; NO workbook output cell is used.

    Scans lead-axle positions for all four input patterns. The scan is an
    approximation, not a continuous analytic moving-load envelope.
    """
    inputs.validate()
    options=options or FrameOptions(mesh_divisions_per_member=8,
                                   compression_only_springs=False,
                                   sidl_centre_member_repeat_count=2)
    if options.compression_only_springs:
        raise ValueError('Workbook baseline requires bilateral springs')
    if len(set(vehicle_ids))!=len(vehicle_ids) or set(vehicle_ids)-set(VEHICLE_IDS):
        raise ValueError('Invalid vehicle ids')
    result={};meta={};combo=_vehicle_only_combination()
    vehicle_overrides = vehicle_overrides or {}
    if set(vehicle_overrides) - set(vehicle_ids):
        raise ValueError("Vehicle override is not included in the scan")
    for vehicle_id in vehicle_ids:
        vehicle=vehicle_overrides.get(vehicle_id) or vehicle_preset(vehicle_id)
        span=inputs.clear_width_m+inputs.wall_thickness_m
        places=_positions(span,vehicle.axle_spacings_m,step_m)
        extremes=None; mapping=None
        for lead_pos in places:
            candidate=replace(vehicle,lead_axle_position_m=lead_pos)
            sol=solver(inputs,None,None,candidate,combo,options)
            if not sol.get('converged'):
                raise ValueError(f'FEM not converged for {vehicle_id}, lead {lead_pos}m')
            if mapping is None:
                mapping=source_beam_mapping(sol)
                extremes={key:{'beam':key[0],'end':key[1],
                    'Fy_max_tf':0.,'Fy_min_tf':0.,'Mz_max_tfm':0.,'Mz_min_tfm':0.,
                    'position_of_Fy_max_m':None,'position_of_Fy_min_m':None,
                    'position_of_Mz_max_m':None,'position_of_Mz_min_m':None}
                    for key in mapping}
            idx={(a['member'],int(a['element']),a['end']):a
                 for a in sol['member_end_actions']}
            for key, local in mapping.items():
                force=idx[local]
                # STAAD source reports section actions at the element end.
                orientation=1. if key[1]=='start' else -1.
                for field,action in [('Fy','shear_kn'),('Mz','moment_knm')]:
                    value=orientation*float(force[action])/TF_TO_KN
                    if not math.isfinite(value):raise ValueError('Nonfinite FEM vehicle action')
                    for bound,comparison in (('max',lambda x,y:x>y),('min',lambda x,y:x<y)):
                        result_key=f'{field}_{bound}_tf'+('m' if field=='Mz' else '')
                        if comparison(value,extremes[key][result_key]):
                            extremes[key][result_key]=value
                            extremes[key][f'position_of_{field}_{bound}_m']=lead_pos
        result[vehicle_id]=extremes
        meta[vehicle_id]={'sample_count':len(places),'step_m':step_m,
                          'scan_start_m':places[0],'scan_end_m':places[-1],
                          'vehicle_preset':vehicle.case_name,
                          'status':'Unverified independent FEM sweep; not safe for design tables'}
    return {'envelopes':result,'scan_info':meta}


def compare_vehicle_member_ends(candidate_envelopes, reference_vehicle_ends, *, tolerance=.025):
    """Reference member end output is comparison-only (never a solver input)."""
    output=[]
    for vehicle in VEHICLE_IDS:
        predicted=candidate_envelopes['envelopes'][vehicle]
        reference=reference_vehicle_ends[vehicle]
        if len(reference)!=82 or len(predicted)!=82:
            raise ValueError(f'Incorrect vehicle member end count: {vehicle}')
        for source in reference:
            key=(source['beam'],source['end'])
            if key not in predicted:raise ValueError(f'Missing FEM source member {key}')
            row=predicted[key]
            for quantity in ('Fy_max_tf','Fy_min_tf','Mz_max_tfm','Mz_min_tfm'):
                ref=float(source[quantity]);got=float(row[quantity]);err=got-ref
                output.append({'Vehicle':vehicle,'Beam':key[0],'End':key[1],
                               'Quantity':quantity,'Workbook':ref,'Experimental FEM':got,
                               'Difference':err,'Abs difference':abs(err),
                               'Status':'PASS' if abs(err)<=tolerance else 'MISMATCH'})
    return output


def validation_counts(rows):
    if not rows:return {'checks':0,'passes':0,'mismatches':0,'max_error':None}
    return {'checks':len(rows),'passes':sum(x['Status']=='PASS' for x in rows),
            'mismatches':sum(x['Status']!='PASS' for x in rows),
            'max_error':max(abs(float(x['Difference'])) for x in rows)}
