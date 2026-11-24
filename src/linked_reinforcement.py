"""Trial reinforcement and linked demand mappings. Not an approved design method."""
import math
import warnings

# Original Design Note, page 11: comparison assumption only.
SLS_CRACK_COVER_MM = 50.0
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
    """Generate a trial schedule, preserving passing source arrangements.

    Search first-bar spacing and then larger first-bar diameters. These are
    arithmetic candidates only; congestion, anchorage and detailing require review.
    """
    rows = default_flexural_reinforcement_rows()
    demands = linked_demands(moments, 'ULS Factor load')
    messages = []
    def checks(schedule):
        return calculate_linked_uls_flexural_strength(
            inputs, constants=constants, reinforcement_rows=schedule,
            live_demands=demands)['rows']
    def passes(check):
        return check['Depth Check'] == 'OK' and check['Steel Check'] == 'OK'
    for i, link in enumerate(_ULS_FLEXURAL_LINKS):
        face = link[6]
        physical = (link[5],) if i < 6 else (i-i%2, i-i%2+1)
        current = checks(rows)
        if passes(current[i]):
            continue
        if current[i]['Depth Check'] != 'OK':
            messages.append(f'Flexural section {i+1}: depth requires redesign')
            continue
        # Retain the other bar slots, and apply the same trial change on both walls.
        original_dia = float(rows[physical[0]][f'{face} Bar-1 (mm)'] or 10)
        original_spacing = float(rows[physical[0]][f'{face} Spacing-1 (mm)'] or 180)
        diameters = [original_dia] + [d for d in (8,10,12,16,20,25) if d > original_dia]
        accepted = False
        for dia in diameters:
            for spacing in range(5*math.floor(original_spacing/5), 74, -5):
                candidate = [dict(r) for r in rows]
                for j in physical:
                    candidate[j][f'{face} Bar-1 (mm)'] = float(dia)
                    candidate[j][f'{face} Spacing-1 (mm)'] = float(spacing)
                try:
                    verified = checks(candidate)
                except ValueError:
                    continue
                if passes(verified[i]) and all(
                    not passes(before) or passes(after)
                    for before, after in zip(current, verified)
                ):
                    rows = candidate
                    accepted = True
                    break
            if accepted:
                break
        if not accepted:
            messages.append(f'Flexural section {i+1} {face}: no passing trial layout; engineer redesign needed')
    for i, check in enumerate(checks(rows)):
        if not passes(check):
            messages.append(f'Final flexural section {i+1}: strength check NOT OK')
    return rows, messages


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


def service_overrides(moments, uls_rows, bars, constants, *, inputs=None):
    rare=linked_demands(moments,'Rare Total'); quasi=linked_demands(moments,'Quasi Total')
    if len(uls_rows)!=16 or len(bars)!=16: raise ValueError('Incomplete reinforcement schedule')
    out={}
    cover_mismatch = False
    for i,link in enumerate(_ULS_FLEXURAL_LINKS):
        component,_,_,_,depth_field,j,face,_=link
        row=bars[j]; u=uls_rows[i]
        dia=max(float(row.get(f'{face} Bar-{k} (mm)') or 0) for k in range(1,4 if face=='Outer' else 3))
        if component=='Top Slab': cover=constants['top_slab_top_cover_mm' if face=='Outer' else 'top_slab_bottom_cover_mm']
        elif component=='Bottom Slab': cover=constants['bottom_slab_cover_mm']
        else: cover=constants['side_wall_outer_cover_mm' if face=='Outer' else 'side_wall_inner_cover_mm']
        # Actual face cover still defines the physical section depth.
        # Only the crack-spacing equation uses the source comparison cover.
        cover_mismatch |= not math.isclose(float(cover), SLS_CRACK_COVER_MM)
        d=float(u['d provided (mm)'])
        h=d+cover+dia/2
        # S12: the workbook's non-earth midspan depth used outer cover.
        # For live SLS use the actual inner-face cover and current wall depth.
        # Single-layer bar-centre approximation; engineer must confirm detailing.
        if i == 11 and inputs is not None:
            if component != 'Side Wall' or face != 'Inner':
                raise ValueError('Unexpected S12 reinforcement mapping')
            h=float(inputs.wall_thickness_m)*1000.0
            d=h-float(cover)-dia/2
        if not (0 < d < h):
            raise ValueError(f'Invalid SLS section depth at S{i+1:02d}')
        out[f'S{i+1:02d}']={'rare_moment_tf_m_per_m':rare[i]['moment_tf_m_per_m'],
            'quasi_moment_tf_m_per_m':quasi[i]['moment_tf_m_per_m'],
            'effective_depth_mm':d,'section_depth_mm':h,
            'provided_as_mm2_per_m':u['Ast provided (mm²/m)'],
            'equivalent_bar_diameter_mm':equivalent_bar_diameter(row, face),'cover_mm':SLS_CRACK_COVER_MM}
    if cover_mismatch:
        warnings.warn(
            'SLS reference comparison uses 50 mm crack cover even where actual design cover differs. '
            'Crack widths may be underestimated; engineer confirmation required before design submission.',
            RuntimeWarning, stacklevel=2)
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
