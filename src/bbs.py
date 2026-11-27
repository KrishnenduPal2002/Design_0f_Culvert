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


def calculate_live_bbs(inputs, constants, flexural_rows, distribution_rows,
                       shear_strength, stirrup_rows, allowance_pct=5.0):
    """25 numbered BBS (2) rows, using source nominal length/count formulas.

    Source constants (61d, 300 mm haunch legs, 412/448 mm diagonals,
    10 mm corner/haunch bars) are detailing assumptions, not code-derived
    fabrication dimensions. Changed haunch geometry requires reviewed detail.
    Incompatible section layouts cannot be represented by the 25 source marks.
    """
    inputs.validate()
    if len(flexural_rows) != 16 or len(distribution_rows) != 10:
        raise ValueError('Live reinforcement tables are incomplete')
    if [r['Component'] for r in flexural_rows] != ['Top Slab']*3+['Bottom Slab']*3+['Side Wall']*10:
        raise ValueError('Unexpected flexural schedule order')
    def pair(i, face, slot):
        r=flexural_rows[i]
        return (float(r.get(f'{face} Bar-{slot} (mm)') or 0),
                float(r.get(f'{face} Spacing-{slot} (mm)') or 0))
    # Exact reference diameter/spacing links, with continuity checks.
    groups={
        'ts1':[(i,'Outer',1) for i in (0,1,2)]+[(i,'Outer',2) for i in (6,7,8,9)],
        'ts1a':[(i,'Outer',3) for i in (0,1)],
        'ts3':[(i,'Inner',1) for i in (0,1,2)], 'ts6':[(2,'Inner',2)],
        'bs1':[(i,'Inner',1) for i in (3,4,5)],
        'bs3':[(i,'Outer',1) for i in (3,4,5)]+[(i,'Outer',2) for i in (12,13,14,15)],
        'bs3a':[(i,'Outer',3) for i in (3,4)], 'bs5':[(5,'Inner',2)],
        'w1':[(i,'Outer',1) for i in range(6,16)]+[(i,'Outer',2) for i in (0,1,3,4)],
        'w2':[(i,'Inner',1) for i in range(6,16)],
    }
    specs={}
    issues={}
    def pending(mark, message):
        issues.setdefault(mark, []).append(message)
    for mark, links in groups.items():
        pairs=[pair(*link) for link in links]
        active={(d,s) for d,s in pairs if d>0}
        if len(active)>1 or (active and any(d==0 for d,s in pairs)):
            pending(mark,'Different section layouts; bar continuity not resolved')
        # First linked row is the actual workbook source, not an invented envelope.
        specs[mark]=pairs[0]
        if any(not math.isfinite(d) or d<0 or (d>0 and (not math.isfinite(s) or s<=0)) for d,s in pairs):
            raise ValueError(f'{mark}: invalid reinforcement data')
    if any(pair(i,'Outer',2)[0]>0 for i in (10,11)):
        pending('w1','w1a is active; 25-mark schedule cannot cover all steel')
    # Check for bars outside the reference mark mapping.
    for i in range(16):
        if i not in (2,5) and pair(i,'Inner',2)[0]>0:
            pending('ts3' if i<3 else 'bs1' if i<6 else 'w2','Additional inner steel requires a new mark')
        if i not in (0,1,3,4) and pair(i,'Outer',3)[0]>0:
            pending('ts1' if i<3 else 'bs3' if i<6 else 'w1','Additional outer steel requires a new mark')
        if i in (2,5) and pair(i,'Outer',2)[0]>0:
            pending('ts1' if i==2 else 'bs3','Additional slab outer steel requires a new mark')
    dist={(r['Member'],r['Surface'],r['Position']):r for r in distribution_rows}
    mapping={'ts2':('1. Top Slab','Top','Mid'), 'ts2a':('1. Top Slab','Top','Edge'),
             'ts4':('1. Top Slab','Bottom','Mid'),'ts4a':('1. Top Slab','Bottom','Edge'),
             'bs2':('2. Bottom Slab','Top','Mid'),'bs2a':('2. Bottom Slab','Bottom','Edge'),
             'bs4':('2. Bottom Slab','Bottom','Mid'),'bs4a':('2. Bottom Slab','Bottom','Edge'),
             'w3':('3. Side Wall','Earth',''),'w4':('3. Side Wall','Inner','')}
    for m,k in mapping.items():
        r=dist[k]
        specs[m]=(float(r['Diameter (mm)']),float(r['Spacing (mm)']))
        if r.get('Check')=='NOT OK': pending(m,'Distribution reinforcement check NOT OK')
    # The workbook links bs2a to the bottom edge (G99/H99), not top edge.
    # If the two faces diverge, its physical interpretation needs review.
    other=dist[('2. Bottom Slab','Top','Edge')]
    if specs['bs2a']!=(float(other['Diameter (mm)']),float(other['Spacing (mm)'])):
        pending('bs2a','Source edge-face ambiguity; top and bottom layouts differ')
    if not isinstance(shear_strength,dict) or len(shear_strength.get('rows',[]))!=16:
        pending('bs6','Live shear schedule unavailable')
    critical=[r for r in (shear_strength or {}).get('rows',[]) if r['Shear Reinf. Requirement']=='Require']
    stirrup_rows=stirrup_rows or []
    if len(critical)!=len(stirrup_rows): pending('bs6','Incomplete live stirrup schedule')
    # Retain the source nominal bs6 minimum layout (8 @ 225), even if no
    # calculated shear reinforcement is necessary. Only its original bottom
    # slab support zone is represented; no invented top/wall reinforcement.
    shear_specs=set()
    for r,s in zip(critical,stirrup_rows):
        if r['Component']!='Bottom Slab' or r['Section']!='End Supp.':
            pending('bs6','Shear outside original support zone needs additional marks')
        else:
            shear_specs.add((float(s['Diameter (mm)']),float(s['Spacing (mm)'])))
    # Different spacings at the two checked bottom-slab stations are not
    # necessarily different physical runs. Estimate a single nominal run at
    # the tighter spacing only when diameter is common and all inputs match.
    # The original support-zone extent/count is not validated by point checks.
    # Keep coverage incomplete so this estimate cannot feed a definitive BOQ.
    provisional_bs6 = False
    if any(not math.isfinite(d) or not math.isfinite(s) or d <= 0 or s <= 0
           for d,s in shear_specs):
        pending('bs6','Invalid live shear diameter or spacing')
    specs['bs6']=next(iter(shear_specs)) if len(shear_specs)==1 else (8.,225.)
    if len(shear_specs)>1:
        if 'bs6' not in issues and len({d for d,s in shear_specs})==1:
            specs['bs6']=(next(iter(shear_specs))[0],min(s for d,s in shear_specs))
            provisional_bs6=True
        else:
            pending('bs6','Different support shear layouts need reviewed detailing')
    L,W,H,t,b,w=(1000*float(x) for x in (inputs.length_m,inputs.clear_width_m,inputs.clear_height_m,inputs.top_slab_thickness_m,inputs.bottom_slab_thickness_m,inputs.wall_thickness_m))
    ci,to,bo,wi,wo=(float(constants[k]) for k in ('top_slab_bottom_cover_mm','top_slab_top_cover_mm','bottom_slab_cover_mm','side_wall_inner_cover_mm','side_wall_outer_cover_mm'))
    # Source end covers C14/C15 use non-earth/earth cover conventions.
    if any(not math.isfinite(x) or x<0 for x in (ci,to,bo,wi,wo)): raise ValueError('Invalid cover')
    a=max(.3*W,61*specs['w1'][0]); z=max(.3*H,61*specs['ts1'][0]); e=.15*W
    # If the primary bar layout is unresolved, the dependent anchorage
    # and shared count formulas cannot be treated as confirmed either.
    for source, affected in [('ts1',('ts1a','bs3','bs3a','h1','h2')),('w1',('ts1a',)),('bs1',('bs6',))]:
        if source in issues:
            for m in affected: pending(m,f'Depends on unresolved {source} layout')
    width=W+2*w; height=H+t+b; long=L-2*ci
    if min(long,L-2*wo,W-2*e,t-2*ci,b-bo-ci,w-wo-wi,width-2*wo,height-to-bo)<=0:
        raise ValueError('Invalid BBS dimensions after cover deductions')
    def sym(x,y): return (x,y,x)
    legs={
        'ts1':sym(z+t-ci,width-2*wo),'ts1a':(z+t-ci,a+w-wo,0),
        'ts3':sym(t-2*ci,width-2*wo),'ts5':sym(50,long),'ts6':sym(50,W-2*e),
        'bs1':sym(b-ci-bo,width-2*wo),'bs3':sym(z+b-bo,width-2*wo),
        'bs3a':(z+b-bo,e+w-wo,0),'bs5':sym(50,W-2*e),'bs6':sym(50,b-ci-bo),
        'w1':sym(a+w-wo,height-to-wi), 'w2':sym(w-wo-wi,height-to-wi),
        'w3':sym(math.ceil((w-wo-wi)/2),long),'w4':sym(math.ceil((w-wo-wi)/2),long),
        'w5':sym(300,long),'h1':sym(300,412),'h2':sym(300,448),
    }
    # Workbook J13 overall height minus earth and non-earth cover.
    for m in ('ts2','ts2a','ts4','ts4a'): legs[m]=sym(math.ceil((t-2*ci)/2),long)
    for m in ('bs2','bs2a'): legs[m]=sym(math.ceil((b-bo-ci)/2),long)
    for m in ('bs4','bs4a'): legs[m]=sym(math.ceil((b-2*ci)/2),long)
    specs['ts5']=(10.,None)  # workbook corner detail is a separate 10-mm bar
    specs['w5']=(10.,None)
    specs['h1']=(10.,specs['ts1'][1]); specs['h2']=specs['h1']
    if inputs.haunch_count and (not math.isclose(inputs.haunch_width_m,.15) or not math.isclose(inputs.haunch_height_m,.15)):
        for m in ('h1','h2'): pending(m,'Changed haunch geometry needs approved diagonal length')
    def up(x): return math.ceil(x-1e-10)
    def rnd(x): return math.floor(x+.5+1e-10)
    rows=[]
    for ref in reference_bars():
        m=ref['mark']
        if m=='w1a': continue
        d,s=specs[m]; dims=legs[m]
        if m in ('h1','h2') and not inputs.haunch_count:
            issues.pop(m,None)
            rows.append(dict(ref,diameter_mm=0.,spacing_mm=s or None,m1_mm=0.,m2_mm=0.,m3_mm=0.,cut_length_mm=0.,bar_count=0,source='Live nominal BBS'))
            continue
        if m in issues: continue
        if d<0 or not math.isfinite(d) or (d>0 and m not in ('ts5','w5') and (s is None or not math.isfinite(s) or s<=0)) or min(dims)<0:
            pending(m,'Invalid bar dimensions or spacing'); continue
        if d==0 and not s:
            n=0; s=None
        elif m in ('ts5','w5'): n=8
        elif m in ('h1','h2'): n=2*rnd((L-2*(ci if m=='h1' else wo))/s) if inputs.haunch_count else 0
        elif m in ('ts2','ts4','bs2','bs4'): n=up((W-2*e)/s)
        elif m in ('ts2a','ts4a','bs2a','bs4a'): n=(rnd if m=='ts2a' else up)(2*e/s)
        elif m in ('w3','w4'): n=2*up(H/s)
        elif m=='bs6':
            if specs['bs1'][1]<=0: pending(m,'Missing bottom slab main bar spacing'); continue
            n=2*up((L-2*bo)/specs['bs1'][1])*up((e+w-wo)/s)
        else: n=up((L-2*(ci if m in ('ts1','ts1a','ts3','ts6','w2') else bo if m in ('bs1','bs3','bs3a','bs5') else wo))/s)*(2 if m in ('w1','w2') else 1)
        rows.append(dict(ref,diameter_mm=d,spacing_mm=s,m1_mm=dims[0],m2_mm=dims[1],m3_mm=dims[2],cut_length_mm=sum(dims),bar_count=n,source='Live nominal BBS'))
    if provisional_bs6 and any(r['mark']=='bs6' for r in rows):
        pending('bs6','Nominal tighter-spacing estimate; verify support-zone extent, link shape and leg arrangement')
    out=calculate_bbs(rows,allowance_pct)
    # Match the workbook total length even for intentionally zero-diameter marks.
    for r in out['lines']:
        if r['diameter_mm']==0:
            r['length_total_m']=r['cut_length_mm']*r['bar_count']/1000
            r['status']='Inactive (zero diameter)'
        elif r['bar_count']==0: r['status']='Inactive'
        elif r['mark']=='bs6' and provisional_bs6:
            r['status']='Provisional: tighter spacing; support-zone detailing review required'
            r['source']='Live nominal shear estimate (not approved)'
        elif r['mark']=='bs6' and not critical: r['status']='Included (reference nominal links)'
    calculated={r['mark']:r for r in out['lines']}
    ordered=[]
    for ref in reference_bars():
        m=ref['mark']
        if m=='w1a': continue
        if m in calculated: ordered.append(calculated[m]); continue
        ordered.append(dict(id=ref['id'],mark=m,member=ref['member'],
            diameter_mm=None,spacing_mm=None,m1_mm=None,m2_mm=None,m3_mm=None,
            cut_length_mm=None,bar_count=None,length_total_m=None,unit_weight_kg_m=None,
            weight_kg=None,status='; '.join(issues.get(m,['Detailing unresolved'])),
            reference_weight_kg=ref['reference_weight_kg'],source='Live BBS pending'))
    out['lines']=ordered; out['bar_mark_count']=len(ordered)
    out['quantity_coverage_complete']=not issues
    out['approved']=False
    out['pending_marks']=list(issues)
    out['partial_net_steel_kg']=out['net_steel_kg']
    if issues:
        for key in ('net_steel_kg','allowance_kg','total_steel_kg','total_steel_t'): out[key]=None
    out.update(source='Live nominal BBS — workbook detailing conventions',
        status='Calculated nominal schedule; engineering detailing review required' if not issues else 'Incomplete nominal schedule',
        warnings=[f'{mark}: {message}' for mark,messages in issues.items() for message in messages])
    return out


# Reference sketch symbols only, not a fabrication shape code or bend detail.
def bbs_shape_name(mark):
    if mark in {'ts1','ts2','ts2a','ts4a','ts6','bs1','bs2','bs2a'}:
        return 'Inverted U'
    if mark in {'ts3','ts4','bs3','bs4','bs4a','bs5','w1','w2','w3','w4'}:
        return 'U'
    if mark in {'ts1a','bs3a'}:
        return 'L'
    if mark in {'ts5','w5'}:
        return 'Straight (reference sketch)'
    if mark in {'h1','h2'}:
        return 'Haunch (schematic)'
    return 'Unverified shape'
