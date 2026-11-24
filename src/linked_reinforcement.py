"""Trial reinforcement and linked demand mappings. Not an approved design method."""
import math
from .calculations import (default_flexural_reinforcement_rows, calculate_flexural_reinforcement,
    calculate_uls_flexural_strength, calculate_uls_stirrup_design_table, calculate_uls_shear_strength_table,
    default_uls_stirrup_inputs, _ULS_FLEXURAL_LINKS, calculate_design_constants)
from .uls_design import shear_without_stirrups, flexural_check


def linked_demands(rows, column):
    by_id = {int(r['Excel row']): r for r in rows}
    if len(rows) != 26 or len(by_id) != 26:
        raise ValueError('Expected all 26 unique Design Moment rows')
    out=[]
    for component, section, face, _, _, _, _, ids in _ULS_FLEXURAL_LINKS:
        vals=[float(by_id[i][column]) for i in ids]
        if not all(math.isfinite(x) for x in vals):
            raise ValueError('Nonfinite linked moment')
        signed=max(vals,key=abs)
        out.append({'moment_tf_m_per_m':abs(signed),'signed_moment_tf_m_per_m':signed,
                    'mapped_member':component,'station_m':None})
    return out


def calculate_linked_uls_flexural_strength(inputs, *, constants, reinforcement_rows, live_demands):
    """Current linked actions, with Design!I57/M57 workbook unit convention.

    The source uses 10 kN/t (rounded engineering convention), not the exact
    SI tonne-force conversion. Preserve live depths and current bar areas;
    the rounded convention is used only for these workbook-style flexural
    checks. This is a comparison/design convention, not engineering approval.
    """
    result = calculate_uls_flexural_strength(
        inputs, constants=constants, reinforcement_rows=reinforcement_rows,
        live_demands=live_demands,
    )
    materials = calculate_design_constants(inputs, constants)['values']
    for row in result['rows']:
        checked = flexural_check(
            moment_tf_m_per_m=row['Design Moment (t-m)'],
            effective_depth_mm=row['d provided (mm)'],
            provided_as_mm2_per_m=row['Ast provided (mm²/m)'],
            fck_mpa=materials['fck_mpa'], fy_mpa=materials['fy_mpa'],
            fctm_mpa=materials['fctm_mpa'], steel_modulus_gpa=materials['es_gpa'],
            tonne_force_to_kn=10.0,
        )
        row.update({
            'd min (mm)': checked['d_min_mm'],
            'Ast Reqd. (mm²/m)': checked['as_required_mm2_per_m'],
            'Depth Check': checked['depth_check'],
            'Steel Check': ('OK' if checked['reinforcement_check']=='OK'
                            and checked['reinforcement_type']=='Under-reinforced' else 'NOT OK'),
        })
    result['depth_ok_count'] = sum(r['Depth Check']=='OK' for r in result['rows'])
    result['steel_ok_count'] = sum(r['Steel Check']=='OK' for r in result['rows'])
    result['scope_note'] += ' Workbook-style flexural equations use rounded 10 kN/t; engineer review required.'
    return result


def trial_main_steel(inputs, constants, moments):
    """Retain source bar sizes; tighten spacing only if needed. Both walls use
    their common governing face demand. Infeasible sections remain REVIEW."""
    rows=default_flexural_reinforcement_rows()
    demands=linked_demands(moments,'ULS Factor load')
    # Every pair of physical wall locations shares outer/inner governing demands.
    targets=[(i, (i,)) for i in range(6)] + [(i,(i-i%2,i-i%2+1)) for i in range(6,16)]
    warnings=[]
    for i, physical in targets:
        face=_ULS_FLEXURAL_LINKS[i][6]
        checks=calculate_linked_uls_flexural_strength(inputs,constants=constants,reinforcement_rows=rows,live_demands=demands)['rows']
        check=checks[i]
        needed=check['Ast Reqd. (mm²/m)']
        if needed is None or check['Depth Check']!='OK':
            warnings.append(f'Flexural section {i+1}: depth or moment domain requires redesign'); continue
        needed=max(needed,check['Ast min (mm²/m)'])
        for j in physical:
            row=rows[j]
            area=calculate_flexural_reinforcement([row])['rows'][0][f'Calculated {face} (mm²/m)']
            if area >= needed: continue
            d=float(row[f'{face} Bar-1 (mm)'] or 10)
            s=float(row[f'{face} Spacing-1 (mm)'] or 180)
            other=area-math.pi*d*d/4*1000/s
            target=needed-max(0,other)
            proposed=min(s,5*math.floor((math.pi*d*d/4*1000/target)/5))
            if proposed < 75:
                warnings.append(f'Flexural row {j+1} {face}: trial spacing below 75 mm; engineer redesign needed'); continue
            row[f'{face} Spacing-1 (mm)']=proposed
    return rows,warnings


def equivalent_bar_diameter(row, face):
    """Number-weighted equivalent diameter for the current reinforcement face.

    The workbook uses sum(n*phi**2)/sum(n*phi); n = 1000/spacing
    for a one-metre strip. Absent bars contribute nothing. This does not
    verify multilayer detailing or the applicability of the crack-spacing rule.
    """
    numerator = denominator = 0.0
    for k in range(1, 4 if face == 'Outer' else 3):
        dia = float(row.get(f'{face} Bar-{k} (mm)') or 0)
        if dia == 0:
            continue
        spacing = float(row.get(f'{face} Spacing-{k} (mm)') or 0)
        if not math.isfinite(dia) or dia < 0 or not math.isfinite(spacing) or spacing <= 0:
            raise ValueError('Invalid bar diameter or spacing for SLS')
        count = 1000.0 / spacing
        numerator += count * dia**2
        denominator += count * dia
    if denominator <= 0:
        raise ValueError('No tension reinforcement for SLS')
    return numerator / denominator


def service_overrides(moments, uls_rows, bars, constants):
    rare=linked_demands(moments,'Rare Total'); quasi=linked_demands(moments,'Quasi Total')
    if len(uls_rows)!=16 or len(bars)!=16: raise ValueError('Incomplete reinforcement schedule')
    out={}
    for i,link in enumerate(_ULS_FLEXURAL_LINKS):
        component,_,_,_,depth_field,j,face,_=link
        row=bars[j]; u=uls_rows[i]
        dia=max(float(row.get(f'{face} Bar-{k} (mm)') or 0) for k in range(1,4 if face=='Outer' else 3))
        if component=='Top Slab': cover=constants['top_slab_top_cover_mm' if face=='Outer' else 'top_slab_bottom_cover_mm']
        elif component=='Bottom Slab': cover=constants['bottom_slab_cover_mm']
        else: cover=constants['side_wall_outer_cover_mm' if face=='Outer' else 'side_wall_inner_cover_mm']
        d=float(u['d provided (mm)'])
        out[f'S{i+1:02d}']={'rare_moment_tf_m_per_m':rare[i]['moment_tf_m_per_m'],
            'quasi_moment_tf_m_per_m':quasi[i]['moment_tf_m_per_m'],
            'effective_depth_mm':d,'section_depth_mm':d+cover+dia/2,
            'provided_as_mm2_per_m':u['Ast provided (mm²/m)'],
            'equivalent_bar_diameter_mm':equivalent_bar_diameter(row, face),'cover_mm':cover}
    return out


def trial_stirrups(strength, fck, fy):
    defaults=default_uls_stirrup_inputs(strength)
    if not defaults:return defaults
    # Try each spacing across all shear-critical sections, retain first passing
    # layout individually. Failed layouts remain visibly failed.
    chosen=[dict(x) for x in defaults]
    pending=set(range(len(chosen)))
    for spacing in range(225,74,-5):
        candidate=[{**r,'Spacing (mm)':float(spacing)} if i in pending else r for i,r in enumerate(chosen)]
        result=calculate_uls_stirrup_design_table(strength,stirrup_inputs=candidate,fck_mpa=fck,fy_mpa=fy)
        for i in list(pending):
            chosen[i]=candidate[i]
            if result['checks'][i]['Overall']=='PASS (arithmetic only)':pending.remove(i)
        if not pending:break
    return chosen


def calculate_linked_uls_shear_strength(inputs, *, live_shear_rows, reinforcement_rows, constants):
    """Workbook Design!F117:F136 caps rho_l at 0.02; retain zero axial credit.

    Preserve actual steel area in the schedule. Only the resistance equation
    receives the capped area. No unverified axial compression credit is added.
    """
    result = calculate_uls_shear_strength_table(inputs, live_shear_rows=live_shear_rows,
        reinforcement_rows=reinforcement_rows, constants=constants)
    fck = calculate_design_constants(inputs, constants)['values']['fck_mpa']
    for row in result['rows']:
        d = float(row['Effective Depth Provided (mm)'])
        area = float(row['Reinforcement (mm²/m)'])
        strength = shear_without_stirrups(design_shear_tf_per_m=row['Design Shear (t/m)'],
            provided_as_mm2_per_m=min(area, 0.02 * 1000 * d),
            effective_depth_mm=d, fck_mpa=fck)
        row.update({'ρl = As/(bw d)': strength['longitudinal_rho'], 'k': strength['k'],
            'Vmin (t/m)': strength['v_min_tf_per_m'], 'VRdc (t/m)': strength['v_rdc_tf_per_m'],
            'VRd w/o shear reinf. (t/m)': strength['resistance_tf_per_m'],
            'Shear Reinf. Requirement': 'Require' if strength['stirrups_required'] else 'Not Require'})
    result['required_count'] = sum(r['Shear Reinf. Requirement']=='Require' for r in result['rows'])
    return result
