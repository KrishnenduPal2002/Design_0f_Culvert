"""Input-driven, experimental RCC box section checks.

These helpers deliberately do not read historical design-force or reinforcement
quantities. Their results are PRELIMINARY and require an engineer-reviewed
load model, load-factor sets, boundary conditions, bar detail and design codes.
"""
from __future__ import annotations

import math
from dataclasses import replace

from .earth_pressure import TF_TO_KN
from .calculations import _ULS_FLEXURAL_LINKS, calculate_live_frame_flexural_demands
from .uls_design import flexural_check


def section_geometry_and_face(inputs, index: int, face: str, dia_mm: float, covers: dict) -> tuple[float,float,float]:
    """Return (overall depth mm, effective depth mm, clear cover mm).

    The wall-face section includes the *geometric* haunch size, unlike the
    historical effective-depth offset. Exact sloping-haunch faces need review.
    """
    member, location, *_ = _ULS_FLEXURAL_LINKS[index]
    if member == 'Top Slab':
        h = inputs.top_slab_thickness_m*1000
        if location == 'End Supp.':
            # Connection face is at the thick end of the triangular haunch.
            h += inputs.haunch_height_m*1000 if inputs.haunch_count else 0
        cover = covers['top_slab_top_cover_mm'] if face=='Outer' else covers['top_slab_bottom_cover_mm']
    elif member == 'Bottom Slab':
        h = inputs.bottom_slab_thickness_m*1000
        if location == 'End Supp.':
            h += inputs.haunch_height_m*1000 if inputs.haunch_count else 0
        cover = covers['bottom_slab_cover_mm']
    else:
        h = inputs.wall_thickness_m*1000
        if ('top supp.' in location.lower() or 'bottom supp.' in location.lower()) and 'haunch end' not in location.lower():
            h += inputs.haunch_width_m*1000 if inputs.haunch_count else 0
        cover = covers['side_wall_outer_cover_mm'] if face=='Outer' else covers['side_wall_inner_cover_mm']
    d = h-cover-dia_mm/2.0
    if d<=0:
        raise ValueError(f'{member} {location}: cover and bar diameter exceed member depth')
    return h,d,cover


def _model_sections(inputs):
    """FEM stations in 16 application section rows (two physical walls)."""
    span=inputs.clear_width_m+inputs.wall_thickness_m
    rise=inputs.clear_height_m+(inputs.top_slab_thickness_m+inputs.bottom_slab_thickness_m)/2
    slab_h=min(span/2,inputs.wall_thickness_m/2+inputs.haunch_width_m)
    top_h=min(rise/2,inputs.top_slab_thickness_m/2+inputs.haunch_height_m)
    bottom_h=min(rise/2,inputs.bottom_slab_thickness_m/2+inputs.haunch_height_m)
    targets=[
        [('Top slab',0.),('Top slab',span)],
        [('Top slab',slab_h),('Top slab',span-slab_h)],
        [('Top slab',span/2)],
        [('Bottom slab',0.),('Bottom slab',span)],
        [('Bottom slab',slab_h),('Bottom slab',span-slab_h)],
        [('Bottom slab',span/2)],
    ]
    for y in (rise,rise-top_h,rise/2,0.0,bottom_h):
        pair=[('Left wall',y),('Right wall',y)]
        targets.extend((pair,pair))
    return targets


def calculate_live_shear_sections(inputs, frame_results: dict) -> list[dict]:
    """Recover |V| at 16 section stations from load-consistent FE end forces.

    Selects maximum absolute section shear of the active analysed model,
    allowing both sides of a concentrated force and adjoining elements.
    This is not a code-approved ULS shear envelope.
    """
    if not frame_results or not frame_results.get('converged'):
        raise ValueError('Converged live FEM solution required for shear')
    # The corresponding moment mapping checks the FE geometry and full
    # moment equilibrium, catching an incompatible/stale result.
    calculate_live_frame_flexural_demands(inputs,frame_results)
    profiles={(r['member'],r['element']):r for r in frame_results['member_load_profiles']}
    start_actions={(r['member'],r['element']):r for r in frame_results['member_end_actions'] if r['end']=='start'}
    segments={k:[] for k in ('Top slab','Bottom slab','Left wall','Right wall')}
    for (member,eid),profile in profiles.items():
        a=start_actions[(member,eid)]
        axis='x_m' if 'slab' in member else 'y_m'
        x0=a[axis]; L=profile['length_m']
        segments[member].append((x0,x0+L,float(a['shear_kn']),profile))
    targets=_model_sections(inputs)
    results=[]
    for i, locations in enumerate(targets):
        v_candidates=[]
        for member, station in locations:
            for x0,x1,v0,profile in segments[member]:
                if not x0-1e-8<=station<=x1+1e-8:
                    continue
                x=max(0.0,min(x1-x0,station-x0)); L=x1-x0
                p0=float(profile['py_start_kn_per_m']);p1=float(profile['py_end_kn_per_m'])
                v_base=v0+p0*x+(p1-p0)*x*x/(2*L)
                v_candidates.append((abs(v_base),member,station))
                for load in profile.get('point_loads',[]):
                    xp=float(load['fraction'])*L
                    if xp<x-1e-8:
                        v_base+=float(load['py_kn'])
                    elif abs(xp-x)<=1e-8:
                        v_candidates.append((abs(v_base+float(load['py_kn'])),member,station))
                        # Retain both one-sided values at a point load.
                v_candidates.append((abs(v_base),member,station))
        if not v_candidates:
            raise ValueError(f'No FEM shear section at row {i+1}')
        best=max(v_candidates,key=lambda v:v[0])
        component,section,face,*_= _ULS_FLEXURAL_LINKS[i]
        results.append({'Member':component,'Location':section,'Face':face,
                        'Total':best[0]/TF_TO_KN,'FEM member':best[1],
                        'Station (m)':best[2]})
    return results


def bar_layout_for_area(as_needed: float, diameter_candidates=(10.,12.,16.,20.,25.),
                        spacing_candidates=tuple(range(75,251,25))) -> tuple[float,float,float]:
    """Choose the least provided single-layer area meeting target demand.

    This is a trial section layout, not an anchorage, spacing, cover or
    longitudinal development-length approval.
    """
    candidates=[(math.pi*d*d/4*1000/s,d,float(s))
                for d in diameter_candidates for s in spacing_candidates]
    valid=[r for r in candidates if r[0]+1e-8 >= as_needed]
    if not valid:
        raise ValueError(f'Required steel {as_needed:.1f} mm²/m exceeds single-layer trial-layout range')
    area,d,s=min(valid,key=lambda r:(r[0],r[1],-r[2]))
    return d,s,area


def auto_flexural_bar_rows(inputs, moments: list[dict], covers: dict,
                           fck: float, fy: float, es: float, fctm: float) -> list[dict]:
    """Produce input-dependent, trial bar diameters and spacings at 16 sections.

    Only the *mapped tension face* uses the moment requirement. The other
    face gets an illustrative minimum-steel layout, NOT an independently
    computed opposite-face force envelope.
    """
    if len(moments)!=16:
        raise ValueError('Expected 16 live section bending demands')
    items=[]
    for i,demand in enumerate(moments):
        component,section,face,*_=_ULS_FLEXURAL_LINKS[i]
        selected='Inner' if (i in (2,5) or (i>=6 and i%2==1)) else 'Outer'
        row={'Component':component,'Section':section,'Position':face,
             'Reference Inner (mm²/m)':0., 'Reference Outer (mm²/m)':0.}
        for direction in ('Inner','Outer'):
            moment=float(demand['moment_tf_m_per_m']) if direction==selected else 0.
            if not math.isfinite(moment) or moment<0:raise ValueError('Invalid moment demand')
            # Try larger nominal bars for effective-depth-driven sizing.
            chosen=None
            for dia in (10.,12.,16.,20.,25.):
                try:
                    h,depth,cover=section_geometry_and_face(inputs,i,direction,dia,covers)
                    r=flexural_check(moment_tf_m_per_m=moment,
                                    effective_depth_mm=depth,
                                    provided_as_mm2_per_m=3000.,fck_mpa=fck,
                                    fy_mpa=fy,fctm_mpa=fctm,steel_modulus_gpa=es,
                                    tonne_force_to_kn=9.80665)
                    if r['as_required_mm2_per_m'] is None or r['depth_check']!='OK':
                        continue
                    target=max(r['as_min_mm2_per_m'],r['as_required_mm2_per_m'])
                    d,s,area=bar_layout_for_area(target,diameter_candidates=(dia,))
                    trial=flexural_check(moment_tf_m_per_m=moment,
                                         effective_depth_mm=depth,
                                         provided_as_mm2_per_m=area,fck_mpa=fck,
                                         fy_mpa=fy,fctm_mpa=fctm,steel_modulus_gpa=es,
                                         tonne_force_to_kn=9.80665)
                    if trial['reinforcement_check']=='OK' and trial['depth_check']=='OK':
                        chosen=(d,s,area);break
                except ValueError:
                    continue
            if chosen is None:
                raise ValueError(f'{component} {section} {direction}: no trial flexural layout passes; redesign section or change inputs')
            d,s,area=chosen
            for slot in range(1,4 if direction=='Outer' else 3):
                row[f'{direction} Bar-{slot} (mm)']=d if slot==1 else None
                row[f'{direction} Spacing-{slot} (mm)']=s if slot==1 else None
        items.append(row)
    return items


def auto_distribution_rows(flexural_rows: list[dict]) -> list[dict]:
    """10 dynamic trial transverse-steel checks using >=20% of provided main As."""
    from .calculations import default_distribution_reinforcement_rows, calculate_flexural_reinforcement
    main=calculate_flexural_reinforcement(flexural_rows)['rows']
    rows=[]
    for template in default_distribution_reinforcement_rows():
        row={k:template[k] for k in ('Member','Surface','Position')}
        name=row['Member'].lower()
        if 'top slab' in name:
            idx = 2 if row['Position'].lower()=='mid' else 0
            face='Outer' if row['Surface'].lower()=='top' else 'Inner'
        elif 'bottom slab' in name:
            idx = 5 if row['Position'].lower()=='mid' else 3
            face='Inner' if row['Surface'].lower()=='top' else 'Outer'
        else:
            idx=10
            face='Outer' if row['Surface'].lower()=='earth' else 'Inner'
        required=.20*main[idx][f'Calculated {face} (mm²/m)']
        d,s,_=bar_layout_for_area(required,diameter_candidates=(8.,10.,12.,16.),
                                   spacing_candidates=tuple(range(75,301,25)))
        row.update({'Required As (mm²/m)':required,'Diameter (mm)':d,'Spacing (mm)':s})
        rows.append(row)
    return rows


def sls_overrides_from_live(inputs, rare_moments, quasi_moments, bar_rows,
                            covers) -> dict[str,dict]:
    """Override *every* source-dependent SLS section field from current model.

    Uses active geometry, one actual trial bar layer and FE face moment.
    The service combinations and tension mapping still need engineer review.
    """
    if len(rare_moments)!=16 or len(quasi_moments)!=16 or len(bar_rows)!=16:
        raise ValueError('SLS requires 16 rare/quasi/live reinforcement rows')
    results={}
    for i,(rare,quasi,bar) in enumerate(zip(rare_moments,quasi_moments,bar_rows)):
        face='Inner' if i in (2,5) or (i>=6 and i%2==1) else 'Outer'
        dia=float(bar[f'{face} Bar-1 (mm)'])
        spacing=float(bar[f'{face} Spacing-1 (mm)'])
        _,d,cover=section_geometry_and_face(inputs,i,face,dia,covers)
        h,_,_=section_geometry_and_face(inputs,i,face,dia,covers)
        as_area=math.pi*dia*dia/4*1000/spacing
        results[f'S{i+1:02d}']={
            'rare_moment_tf_m_per_m':float(rare['moment_tf_m_per_m']),
            'quasi_moment_tf_m_per_m':float(quasi['moment_tf_m_per_m']),
            'section_depth_mm':h,'effective_depth_mm':d,
            'provided_as_mm2_per_m':as_area,
            'equivalent_bar_diameter_mm':dia,'cover_mm':cover,
        }
    return results


def derived_dead_load_rows(inputs):
    """Compute only quantities supported by actual box geometry/input fields."""
    w=inputs.clear_width_m+2*inputs.wall_thickness_m
    l=inputs.length_m
    c=inputs.concrete_density_tf_m3
    return [
      {'Member':'Top Slab','Nos':1,'Length (m)':l,'Breadth (m)':w,
       'Height (m)':inputs.top_slab_thickness_m,'Density (t/m³)':c,'Method':'Rectangle','Area (m²)':0.},
      {'Member':'Bottom Slab','Nos':1,'Length (m)':l,'Breadth (m)':w,
       'Height (m)':inputs.bottom_slab_thickness_m,'Density (t/m³)':c,'Method':'Rectangle','Area (m²)':0.},
      {'Member':'Side Walls','Nos':2,'Length (m)':l,'Breadth (m)':inputs.wall_thickness_m,
       'Height (m)':inputs.clear_height_m,'Density (t/m³)':c,'Method':'Rectangle','Area (m²)':0.},
      {'Member':'Wearing Course','Nos':1,'Length (m)':l,'Breadth (m)':w,
       'Height (m)':inputs.wearing_coat_thickness_m,'Density (t/m³)':inputs.wearing_course_density_tf_m3,
       'Method':'Rectangle','Area (m²)':0.},
      {'Member':'Earth Fill Above Roof','Nos':1,'Length (m)':l,'Breadth (m)':w,
       'Height (m)':inputs.earth_cushion_m,'Density (t/m³)':inputs.backfill_density_tf_m3,
       'Method':'Rectangle','Area (m²)':0.},
      {'Member':'Internal Haunches','Nos':inputs.haunch_count,'Length (m)':l,
       'Breadth (m)':inputs.haunch_width_m,'Height (m)':inputs.haunch_height_m,
       'Density (t/m³)':c,'Method':'Area',
       'Area (m²)':inputs.haunch_width_m*inputs.haunch_height_m/2.0},
    ]


def live_spring_rows(inputs, options):
    """Bottom-slab Winkler-spring mesh tributary widths used by current FEM."""
    span=inputs.clear_width_m+inputs.wall_thickness_m
    n=int(options.mesh_divisions_per_member)
    step=span/n
    k=options.foundation_modulus_tf_m3
    return [{'Node':i+1,'x (m)':i*step,
             'Tributary width (m)':step*(.5 if i in (0,n) else 1.),
             'Spring constant (tf/m)':k*step*(.5 if i in (0,n) else 1.)}
            for i in range(n+1)]


def auto_temperature_rows(mode,inputs):
    """Scale active thermal-profile zones to present slab thickness.

    The temperature increments are illustrative user-editable environment
    parameters, not automatically derived from location/climate conditions.
    """
    from .calculations import default_temperature_gradient_rows
    rows=default_temperature_gradient_rows(mode)
    base_depth=.25
    factor=inputs.top_slab_thickness_m/base_depth
    for row in rows:
        row['t (m)']*=factor
    return rows


def auto_stirrup_rows(shear_strength: dict, *, fck_mpa: float, fy_mpa: float) -> list[dict]:
    """Choose a preliminary *passing arithmetic* stirrup layout per needed row.

    No anchorage/bend detailing, fatigue, minimum absolute bar spacing,
    construction constraints, or verified IRC combination is asserted.
    If no layout works, raise instead of silently displaying a failing design.
    """
    from .uls_design import stirrup_check
    rows=[]
    for index,section in enumerate(shear_strength['rows']):
        if section['Shear Reinf. Requirement']!='Require':
            continue
        depth=float(section['Effective Depth Provided (mm)'])
        vu=float(section['Design Shear (t/m)'])
        options=[]
        for legs in (2,3,4,5,6):
            for dia in (8.,10.,12.,16.):
                for spacing in (75.,100.,125.,150.,175.,200.,225.,250.):
                    result=stirrup_check(design_shear_tf_per_m=vu,
                        effective_depth_mm=depth,legs=legs,diameter_mm=dia,
                        spacing_mm=spacing,theta_deg=21.8,
                        fck_mpa=fck_mpa,fy_mpa=fy_mpa)
                    if result['status']=='PASS (arithmetic only)':
                        # Minimize bar area per m length, then prefer fewer legs.
                        score=(result['asw_mm2']/spacing,legs,dia)
                        options.append((score,legs,dia,spacing))
        if not options:
            raise ValueError(f"{section['Component']} {section['Section']}: shear demand cannot be met with available trial stirrups; structural redesign needed")
        _,legs,dia,spacing=min(options,key=lambda x:x[0])
        rows.append({
            'Section':f"{index+1}. {section['Component']} / {section['Section']} / {section['Face']}",
            'Stirrup legs':legs,'Diameter (mm)':dia,'Spacing (mm)':spacing,'θ (degree)':21.8,
        })
    return rows
