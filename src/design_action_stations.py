"""Stage 2: section-station recovery for signed frame actions.

No Streamlit changes. This module produces audited *diagnostic* results and
never substitutes unverified actions into the reference design schedules.

Solver element orientation is taken from coordinates, not guessed from names.
The same physical boundary is reported with the element on its requested side;
if a station lies at a joint, end actions are not casually averaged.
"""
from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Station:
    key: str
    member: str
    axis: str
    distance_m: float
    description: str


def reference_stations(inputs):
    """Geometric section centres corresponding to the original named locations.

    These are centreline projections of *faces*; translation to the actual
    concrete face or a haunch-dependent analysis section needs validation.
    """
    w=float(inputs.wall_thickness_m)
    span=float(inputs.clear_width_m)+w
    rise=float(inputs.clear_height_m)+0.5*(float(inputs.top_slab_thickness_m)+float(inputs.bottom_slab_thickness_m))
    hw=float(inputs.haunch_width_m) if inputs.haunch_count else 0.0
    hh=float(inputs.haunch_height_m) if inputs.haunch_count else 0.0
    lower=float(inputs.bottom_slab_thickness_m)/2
    upper=float(inputs.top_slab_thickness_m)/2
    result=[]
    for member in ('Top slab','Bottom slab'):
        for key,x,descr in (("wall_face",w/2,"End Supp. / Wall face"),
                            ("haunch_end",w/2+hw,"End Supp. / Haunch end"),
                            ("midspan",span/2,"Mid Span")):
            result.append(Station(f'{member.lower().replace(" ", "_")}.{key}',member,'x',x,descr))
    for name,side in (('Left wall','first'),('Right wall','second')):
        for key,y,desc in (("top_face",rise-upper,"Top Supp. / Wall face"),
                           ("top_haunch",rise-upper-hh,"Top Supp. / Haunch end"),
                           ("midspan",rise/2,"Mid Span"),
                           ("bottom_face",lower,"Bottom Supp. / Wall face"),
                           ("bottom_haunch",lower+hh,"Bottom Supp. / Haunch end")):
            result.append(Station(f'{side}_wall.{key}',name,'y',y,desc))
    return result


def section_action(start_shear_kn,start_moment_knm,length_m,distance_m,profile):
    """Signed local cross-section values in kN/m and kN.m/m.

    Uses the same section moment equation as the existing frame solver,
    including its bubble distribution and applied point moments. The shear is
    the derivative of moment between point loads. For a load exactly at the
    station, report right limit and allow explicit left limit separately.
    """
    L=float(length_m); x=float(distance_m)
    if not (math.isfinite(x) and math.isfinite(L) and L>0 and -1e-8<=x<=L+1e-8):
        raise ValueError('Station outside beam element')
    x=max(0.0,min(x,L))
    p0=float(profile['py_start_kn_per_m'])
    p1=float(profile['py_end_kn_per_m'])
    bubble=float(profile.get('py_bubble_kn_per_m',0.0))
    pts=profile.get('point_loads',[])
    V=float(start_shear_kn)+p0*x+(p1-p0)*x*x/(2*L)+bubble*(3*x*x/L-2*x**3/L**2)
    M=-float(start_moment_knm)+float(start_shear_kn)*x+p0*x*x/2+(p1-p0)*x**3/(6*L)+bubble*(x**3/L-x**4/(2*L**2))
    for point in pts:
        xp=float(point['fraction'])*L
        if x+1e-9>=xp:
            V+=float(point['py_kn'])
            M+=float(point['py_kn'])*(x-xp)-float(point['mz_knm'])
    if not (math.isfinite(V) and math.isfinite(M)):
        raise ValueError('Nonfinite section action')
    return M,V


def sample_solved_case(solved, stations, *, tol=1e-7):
    """Extract each requested physical station using matching FEM mesh edges.

    At a node, prefer an element that starts at the station, unless it is the
    member's last station. Boundary elements may disagree for shear due to
    nodal concentrated loads, so selection is explicit and repeatable.
    """
    nodes={int(n['node']):n for n in solved['nodes']}
    ends={(str(a['member']),int(a['element']),a['end']):a for a in solved['member_end_actions']}
    profiles={int(p['element']):p for p in solved['member_load_profiles']}
    elements=solved['elements']
    output=[]
    for station in stations:
        candidates=[]
        for e in elements:
            if e['member'] != station.member:
                continue
            a=nodes[int(e['node_start'])]; b=nodes[int(e['node_end'])]
            start=float(a[station.axis+'_m']);end=float(b[station.axis+'_m'])
            if min(start,end)-tol <= station.distance_m <= max(start,end)+tol:
                length=math.hypot(float(b['x_m'])-float(a['x_m']),float(b['y_m'])-float(a['y_m']))
                if not length>0: raise ValueError('Zero-length element')
                local=(station.distance_m-start)/(end-start)*length
                if local < -tol or local > length+tol: continue
                # At joints, prefer the following element's start (right limit).
                candidates.append((0 if abs(local)<tol else 1, e, min(length,max(0,local)),length))
        if not candidates:
            raise ValueError(f'No element covers {station.key} at {station.distance_m}m')
        _,element,x,L=min(candidates,key=lambda v:(v[0],int(v[1]['element'])))
        element_id=int(element['element'])
        start_action=ends[(station.member,element_id,'start')]
        profile=profiles[element_id]
        M,V=section_action(start_action['shear_kn'],start_action['moment_knm'],L,x,profile)
        output.append({'Station key':station.key,'Member':station.member,
                       'Description':station.description,'Axis':station.axis,
                       'Coordinate (m)':station.distance_m,'Element':element_id,
                       'Local coordinate (m)':x,
                       'Signed moment (kN.m/m)':M,'Signed shear (kN/m)':V})
    return output


def run_station_audit(solutions, inputs):
    """Six ULS workbook case results -> one signed section-action CSV ledger."""
    stations=reference_stations(inputs)
    output=[]
    for case in solutions:
        solved=case.get('raw_solver_result')
        if solved is None:
            raise ValueError('Stage 1 ledger omitted full raw_solver_result: run audit with retain_raw=True')
        rows=sample_solved_case(solved,stations)
        for row in rows:
            output.append({'Combination':case['combination'],**row})
    return output


def station_audit_csv(rows):
    if not rows: raise ValueError('No section actions')
    out=io.StringIO()
    writer=csv.DictWriter(out,fieldnames=list(rows[0].keys()))
    writer.writeheader();writer.writerows(rows)
    return out.getvalue()
