"""Stage 6: numeric verification of the existing solver against workbook envelopes.

Nothing here replaces or modifies the Streamlit Design Moment / Shear tables.
The caller must not use output values as inputs for the FEM calculation.
"""
from __future__ import annotations
import csv
import io
import json
import math
from pathlib import Path
from .design_action_stage5 import solve_independent_cases
from .earth_pressure import TF_TO_KN

SOURCE_GROUPS=(('Bottom slab',range(101,115)),('Top slab',range(201,216)),
               ('Left wall',range(1001,1007)),('Right wall',range(2001,2007)))
END_KEYS=('shear_kn','moment_knm')
MEASURES=(('Fy_max_tf','shear_kn','max'),('Fy_min_tf','shear_kn','min'),
          ('Mz_max_tfm','moment_knm','max'),('Mz_min_tfm','moment_knm','min'))
CASE_RANGES={'ULS':range(1001,1031),'Rare':range(1031,1061),'Quasi':range(1061,1091)}


def read_fixture(path):
    data=json.loads(Path(path).read_text(encoding='utf8'))
    if set(data['outputs']) != set(CASE_RANGES):
        raise ValueError('Expected ULS, Rare and Quasi reference outputs')
    for name, rows in data['outputs'].items():
        if len(rows)!=82:raise ValueError(f'{name}: source must contain 82 member ends')
    if len(data['cases'])!=90:raise ValueError('Missing original source combinations')
    return data


def source_beam_mapping(solution):
    """Source 1x3x3 mesh has exactly 14 bottom, 15 top and 6 per side."""
    by_group={}
    for e in solution['elements']:
        by_group.setdefault(e['member'],[]).append(int(e['element']))
    mapping={}
    for name,source_beams in SOURCE_GROUPS:
        ids=by_group.get(name,[])
        if len(ids)!=len(source_beams):
            raise ValueError(f'Cannot map {name}: expected {len(source_beams)} elements, got {len(ids)}')
        for beam, element in zip(source_beams,ids):
            for end in ('start','end'):
                mapping[(beam,end)]=(name,element,end)
    if len(mapping)!=82:raise AssertionError('Invalid primary beam mapping')
    return mapping


def _action_index(solved):
    return {(row['member'],int(row['element']),row['end']):row
            for row in solved['member_end_actions']}


def predict_envelopes(independent, reference):
    """Superpose seven UNFACTORED solved load cases, then envelope each family.

    For the workbook 1x3x3 only: recorded source temperature cases 508-511
    were zero. This method must be replaced with independently solved thermal
    cases if those loads become nonzero for other geometry or future design.
    Values retain sign; end member actions reverse to the STAAD end convention.
    """
    if set(independent)!=set(range(501,508)):
        raise ValueError('Need independent 501-507 actions')
    if not reference['notes']['thermal_508_511_are_zero_in_supplied_reference']:
        raise ValueError('Nonzero temperature load requires real thermal FEM cases')
    template=independent[501]
    mapping=source_beam_mapping(template)
    indexes={num:_action_index(sol) for num,sol in independent.items()}
    result={}
    for family,case_numbers in CASE_RANGES.items():
        out=[]
        for refrow in reference['outputs'][family]:
            beam,end=refrow['beam'],refrow['end']
            key=mapping[(beam,end)]
            if key not in indexes[501]:raise ValueError(f'Missing model end: {key}')
            # Member end actions are forces applied to the member;
            # the reference uses section actions (end sign reversed).
            orientation=1.0 if end=='start' else -1.0
            values={name:[] for name in END_KEYS}
            governors={name:[] for name in END_KEYS}
            for number in case_numbers:
                factors=reference['cases'][str(number)]
                for load,factor in factors.items():
                    if int(load) in (508,509,510,511) and float(factor)!=0.0:
                        # Only valid because those original thermal actions are zero.
                        continue
                    if int(load) not in indexes:
                        raise ValueError(f'Unknown load {load} in case {number}')
                for action in END_KEYS:
                    amount=sum(float(factor)*float(indexes[int(load)][key][action])
                               for load,factor in factors.items() if 501<=int(load)<=507)
                    value=orientation*amount/TF_TO_KN
                    if not math.isfinite(value):raise ValueError('Nonfinite action')
                    values[action].append(value)
                    governors[action].append(number)
            out.append({'beam':beam,'end':end,'node':refrow['node'],
                        'Fy_max_tf':max(0.0,max(values['shear_kn'])),'Fy_min_tf':min(0.0,min(values['shear_kn'])),
                        'Mz_max_tfm':max(0.0,max(values['moment_knm'])),'Mz_min_tfm':min(0.0,min(values['moment_knm'])),
                        'Fy_case_max':governors['shear_kn'][values['shear_kn'].index(max(values['shear_kn']))],
                        'Fy_case_min':governors['shear_kn'][values['shear_kn'].index(min(values['shear_kn']))],
                        'Mz_case_max':governors['moment_knm'][values['moment_knm'].index(max(values['moment_knm']))],
                        'Mz_case_min':governors['moment_knm'][values['moment_knm'].index(min(values['moment_knm']))]})
        result[family]=out
    return result


def compare_to_reference(predictions,reference,*,tol=0.01):
    if not (math.isfinite(tol) and tol>0):raise ValueError('Tolerance must be positive')
    rows=[]
    for family,expected in reference['outputs'].items():
        actual=predictions.get(family)
        if actual is None or len(actual)!=len(expected):raise ValueError(f'Missing {family} outputs')
        for source,computed in zip(expected,actual):
            if (source['beam'],source['node'],source['end']) != (computed['beam'],computed['node'],computed['end']):
                raise ValueError(f'{family}: source and model row mapping mismatch')
            for column,_,extreme in MEASURES:
                ref=float(source[column]);model=float(computed[column]);error=model-ref
                key='Fy_case_'+extreme if column.startswith('Fy') else 'Mz_case_'+extreme
                rows.append({'Family':family,'Beam':source['beam'],'Node':source['node'],
                             'End':source['end'],'Quantity':column,
                             'Reference':ref,'Calculated':model,'Difference':error,
                             'Abs difference':abs(error),'Tolerance':tol,
                             'Governing case (FEM)':computed[key],
                             'Source output row':source['sheet_row_max' if extreme=='max' else 'sheet_row_min'],
                             'Status':'PASS' if abs(error)<=tol else 'MISMATCH'})
    return rows


def summarize(rows):
    if not rows:raise ValueError('Empty numeric audit')
    ans={}
    for family in CASE_RANGES:
        matching=[r for r in rows if r['Family']==family]
        worst=max(matching,key=lambda x:x['Abs difference'])
        ans[family]={'checks':len(matching),'passed':sum(r['Status']=='PASS' for r in matching),
                     'mismatch':sum(r['Status']=='MISMATCH' for r in matching),
                     'max_abs_error':worst['Abs difference'],
                     'worst_location':f"beam {worst['Beam']} / node {worst['Node']} / {worst['Quantity']}"}
    return ans


def to_csv(rows):
    out=io.StringIO(newline='');writer=csv.DictWriter(out,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    return out.getvalue()
