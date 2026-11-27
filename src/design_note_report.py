"""PDF export of the current Streamlit calculation snapshot. No design solver."""
import io
import math
from xml.sax.saxutils import escape
from pathlib import Path
from numbers import Real
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, PageBreak, Image
from reportlab.pdfbase import pdfmetrics
from reportlab.graphics.shapes import Drawing, Rect, Polygon, String, Line
from reportlab.pdfbase.ttfonts import TTFont
from matplotlib import get_data_path


def build_design_note_pdf(ctx):
    """Build a fresh report from this rerun's actual calculated objects only."""
    i=ctx['inputs']; g=ctx['g']; loads=ctx['loads']
    linked=ctx['_uls_linked_inputs']
    for key in ('Design Moment','Shear Force','Flexural R/F','Design Constants'):
        if linked.get(key) is None: raise ValueError(f'Current {key} unavailable; PDF export blocked.')
    for name in ('dead_load','springs','pressures','braking_calcs','uniform_temperature_result','moment_result','shear_result','flexural_result','dc_result','uls_flexural_result','distribution_result','shear_strength','sls','rare_rows','quasi_rows'):
        if ctx.get(name) is None: raise ValueError(f'Current {name} unavailable; PDF export blocked.')
    for mode in ('rise','fall'):
        if mode not in ctx['temperature_panels']: raise ValueError(f'Temperature {mode} unavailable')
    for name,count in [('moment_result',26),('shear_result',16),('flexural_result',16),('uls_flexural_result',16),('distribution_result',10),('shear_strength',16)]:
        if len(ctx[name]['rows'])!=count: raise ValueError(f'Incomplete {name} table')
    if len(ctx['rare_rows'])!=16 or len(ctx['quasi_rows'])!=16: raise ValueError('Incomplete SLS tables')
    fontdir=Path(get_data_path())/'fonts'/'ttf'
    pdfmetrics.registerFont(TTFont('CulvertSans',str(fontdir/'DejaVuSans.ttf')))
    pdfmetrics.registerFont(TTFont('CulvertBold',str(fontdir/'DejaVuSans-Bold.ttf')))
    buf=io.BytesIO(); page=landscape(A4); margin=26; width=page[0]-2*margin-12
    doc=SimpleDocTemplate(buf,pagesize=page,leftMargin=margin,rightMargin=margin,topMargin=32,bottomMargin=32,title='Design Note of RCC Box Culvert',author='RCC Box Culvert calculation application')
    story=[]
    normal=ParagraphStyle('normal',fontName='CulvertSans',fontSize=9,leading=13,spaceAfter=5)
    heading=ParagraphStyle('heading',fontName='CulvertBold',fontSize=13,leading=17,spaceAfter=9,spaceBefore=5,keepWithNext=True)
    small=ParagraphStyle('small',fontName='CulvertSans',fontSize=8,leading=11,spaceAfter=5)
    def p(t,style=normal): return Paragraph(escape(str(t)).replace('\n','<br/>'),style)
    def text(t): story.append(p(t))
    def title(t,new=False):
        if new and story: story.append(PageBreak())
        story.append(p(t,heading))
    def val(v,fmt=None):
        if v is None: return '—'
        if isinstance(v,Real) and not isinstance(v,bool):
            if not math.isfinite(v): raise ValueError('Non-finite value in calculation report')
            return format(v,fmt or '.5g')
        return str(v)
    def table(rows,columns=None,formats=None,groups=None):
        rows=list(rows)
        if not rows: text('No applicable rows.'); return
        columns=columns or [(k,k) for k in rows[0]]; formats=formats or {}
        n=len(columns); fs=7.5 if n<=10 else 6.7
        cell=ParagraphStyle('cell',fontName='CulvertSans',fontSize=fs,leading=fs+2,wordWrap='CJK')
        head=ParagraphStyle('head',parent=cell,fontName='CulvertBold',alignment=1)
        # Give descriptive text columns room, reserve remaining width for numeric values.
        weights=[2.2 if k in ('Member','Component','Section','Location','Face','Face / location','Position','Node No.','Earth Pressure at','Particulars','Calculation','Parameter') else 1.2 if n<=8 else 1 for k,_ in columns]
        widths=[width*w/sum(weights) for w in weights]
        matrix=[]; spans=[]
        if groups:
            for label,lo,hi in groups:
                pass
            group_row=['']*n
            for label,lo,hi in groups:
                group_row[lo]=p(label,head)
                if hi>lo: spans.append(('SPAN',(lo,0),(hi,0)))
            matrix.append(group_row)
        matrix.append([p(label,head) for _,label in columns])
        for row in rows:
            matrix.append([p(val(row[k],formats.get(k)),cell) for k,_ in columns])
        headers=2 if groups else 1
        t=Table(matrix,colWidths=widths,repeatRows=headers,hAlign='LEFT')
        t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.35,colors.HexColor('#74808b')),('BACKGROUND',(0,0),(-1,headers-1),colors.HexColor('#e8edf1')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4),*spans]))
        story.extend([t,Spacer(1,9)])
    def kv(items): table([{'Parameter':a,'Value':b,'Unit':c} for a,b,c in items])
    def image(data):
        if data:
            im=Image(io.BytesIO(data)); scale=min(width/im.imageWidth,170/im.imageHeight); im.drawWidth=im.imageWidth*scale; im.drawHeight=im.imageHeight*scale; story.append(im)
    def cols(*keys): return [(k,k) for k in keys]
    title('Design Note of RCC Box Culvert')
    title('Geometric Parameters')
    kv([('Clear Width of cell',i.clear_width_m,'m'),('Clear Height of Cell',i.clear_height_m,'m'),('Total Length Of Structure',i.length_m,'m'),('C/C Height of structure',i.centreline_height_m,'m'),('Effective Single Span',ctx['effective_span_m'],'m'),('Side Wall Thickness',i.wall_thickness_m,'m'),('No. of cell',i.number_of_cells,''),('Total Width',i.total_width_m,'m'),('Top Slab Thickness',i.top_slab_thickness_m,'m'),('Bot. Slab Thickness',i.bottom_slab_thickness_m,'m'),('Height of Earth Cushion',i.earth_cushion_m,'m'),('Wearing Coat',ctx['wearing_coat_mm'],'mm'),('Haunch size',f"{i.haunch_width_m*1000:g} × {i.haunch_height_m*1000:g} ({i.haunch_count} active)",'mm'),('Int. wall Thickness',i.internal_wall_thickness_m,'m'),('MS Railing Width',i.ms_railing_width_m,'m')])
    title('Basic Parameters',True)
    kv([('Angle of internal friction of soil, φ',ctx['soil_friction_angle_deg'],'deg'),('Coefficient of Earth Pressure at Rest',ctx['k0'],''),('Density of Backfill',ctx['soil_density'],'t/m³'),('Density of wearing coarse',ctx['wearing_course_density'],'t/m³'),('Density of Concrete',ctx['concrete_density_tf_m3'],'t/m³'),('Live Load Surcharge',ctx['surcharge_height'],'m'),('Safe Bearing Capacity',ctx['safe_bearing_capacity'],'t/m²'),('Modulus of Subgrade Reaction',ctx['frame_k'],'t/m³'),('Grade of Concrete',i.concrete_grade,''),('Grade of Steel',i.steel_grade,''),('Modulus of Elasticity of steel, Es',ctx['steel_modulus_reference'],'source units'),('Modulus of Elasticity of concrete, Ec',ctx['concrete_modulus_reference'],'source units')])
    title('Geometry and quantity calculations')
    kv([('External width',g['external_width_m'],'m'),('External height',g['external_height_m'],'m'),('Top slab volume',g['top_slab_volume_m3'],'m³'),('Bottom slab volume',g['bottom_slab_volume_m3'],'m³'),('Side walls volume',g['walls_volume_m3'],'m³'),('Haunch volume',g['haunch_volume_m3'],'m³'),('Total concrete volume',g['concrete_volume_m3'],'m³'),('Self-weight',loads['concrete_self_weight_kn'],'kN')])
    title('Calculation of dead load and SIDL',True); text('Dead Load of Box per meter width (schedule for entered structure length)')
    d=ctx['dead_load']; dead=[]
    for j,r in enumerate(d['rows'],1):
        dead.append({**r,'Sl. No.':j,'Height (m)':('m²' if r['Method']=='Area' else r['Height (m)']),'Breadth (m)':r['Section area used (m²)'] if r['Method']=='Area' else r['Breadth (m)']})
    table(dead,[('Sl. No.','Sl. No.'),('Member','Member'),('Nos','Nos.'),*cols('Length (m)','Breadth (m)','Height (m)','Density (t/m³)','Weight (t)')],{'Weight (t)':'.2f'})
    kv([(label,d[key],unit) for label,key,unit in [('Total Weight on structure','total_weight_tf','t'),('Area of base slab','base_slab_area_m2','m²'),('DL Base Pressure','dead_load_base_pressure_tf_m2','t/m²'),('LL Base Pressure (estimated)','live_load_base_pressure_tf_m2','t/m²'),('Total Base Pressure','total_base_pressure_tf_m2','t/m²'),('Safe Bearing Capacity','safe_bearing_capacity_tf_m2','t/m²')]])

    kv([(f"{r['Member']} — adopted section area",r['Section area used (m²)'],'m²') for r in d['rows'] if r['Method']=='Area'])
    title('Idealised Structure for Python Analysis — schematic')
    dw=i.clear_width_m+2*i.wall_thickness_m; dh=i.clear_height_m+i.top_slab_thickness_m+i.bottom_slab_thickness_m
    scale=min(260/dw,145/dh); x=28; y=24
    W=dw*scale; H=dh*scale; w=i.wall_thickness_m*scale; bot=i.bottom_slab_thickness_m*scale; top=i.top_slab_thickness_m*scale
    schematic=Drawing(width,195)
    schematic.add(Rect(x,y,W,H,fillColor=colors.HexColor('#d9dee4'),strokeColor=colors.black))
    lx=x+w; rx=x+W-w; by=y+bot; ty=y+H-top
    schematic.add(Rect(lx,by,rx-lx,ty-by,fillColor=colors.white,strokeColor=colors.black))
    if i.haunch_count==4:
        hx=i.haunch_width_m*scale; hy=i.haunch_height_m*scale
        for a,b,sx,sy in [(lx,by,1,1),(rx,by,-1,1),(lx,ty,1,-1),(rx,ty,-1,-1)]:
            schematic.add(Polygon([a,b,a+sx*hx,b,a,b+sy*hy],fillColor=colors.HexColor('#d9dee4'),strokeColor=colors.black))
    schematic.add(String(x+W/2,y+H+8,f'Overall width: {dw:.3f} m',fontName='CulvertSans',fontSize=8,textAnchor='middle'))
    schematic.add(String(x+W/2,(by+ty)/2,f'{i.clear_width_m:.3f} × {i.clear_height_m:.3f} m',fontName='CulvertSans',fontSize=8,textAnchor='middle'))
    schematic.add(String(x+W+14,y+H-12,f'Overall height: {dh:.3f} m',fontName='CulvertSans',fontSize=8))
    schematic.add(String(x+W+14,y+H-27,f'Wall: {i.wall_thickness_m:.3f} m',fontName='CulvertSans',fontSize=8))
    schematic.add(String(x+W+14,y+H-42,f'Top slab: {i.top_slab_thickness_m:.3f} m',fontName='CulvertSans',fontSize=8))
    schematic.add(String(x+W+14,y+H-57,f'Bottom slab: {i.bottom_slab_thickness_m:.3f} m',fontName='CulvertSans',fontSize=8))
    story.append(schematic)
    title('Calculation of spring constant — representative nodes',True); table(ctx['springs']['rows'],cols('S.No.','Node No.','Eff. Width (m)','Spring constant (t/m)'),{'Eff. Width (m)':'.4f','Spring constant (t/m)':'.2f'})

    title('Earth Pressure per meter width'); pr=ctx['pressures']; table(pr['rows'],cols('S.No.','Earth Pressure at','H (m)','Earth Pressure (t/m²)'))
    kv([('LL Surcharge Pressure',pr['surcharge_pressure_tf_m2'],'t/m²'),('Horizontal Pressure on Side Wall Due to Earth Cushion',pr['cushion_pressure_tf_m2'],'t/m²')])
    image(ctx.get('_report_earth_png'))
    title('Braking Force',True)
    for r in ctx['braking_calcs']['cases']:
        title(r['Case']); kv([(r['Main force label'],r['Main force (t)'],'t'),('Force due to class A',r['Class A force (t)'],'t'),('Total horizontal force',r['Total horizontal force (t)'],'t'),('Braking force',r['Braking force (t/m)'],'t/m'),('Moment due to braking',r['Moment due to braking (t-m/m)'],'t-m/m')])
    title('Temperature Effect',True); text('Effect of temperature gradient — F = E α Δt A')
    kv([('Ec',ctx['temp_e'],'t/m²'),('α',ctx['temp_alpha'],'1/°C'),('Average thickness of deck slab',i.top_slab_thickness_m,'m')])
    for mode in ('rise','fall'):
        title('Temperature '+mode); panel=ctx['temperature_panels'][mode]
        table(panel['rows'],[('Zone','S.No.'),('ΔT (°C)','Δt'),('b (m)','b'),('t (m)','t'),('A=b×t (m²)','A=b×t'),('Force F (t)','Force, F (t)'),('Acting at (m)','Acting at (m)'),('Eccentricity e (m)','Eccentricity e (m)')])
        text(f"Σ F = {panel['total_force_t']:.2f} t; Σ F×e = {panel['net_moment_t_m']:.3f} t-m; Equivalent Temp. difference, ΔT = {panel['equivalent_delta_t_c']:.3f} °C")
        image(ctx.get('_report_temp_images',{}).get(mode))
    title('Effect of uniform temperature',True); u=ctx['uniform_temperature_result']
    kv([('Maximum shade air temperature',ctx['max_air'],'°C'),('Minimum shade air temperature',ctx['min_air'],'°C'),('Modulus of Elasticity of concrete considering creep',ctx['creep_e'],'t/m²'),('Difference between Maximum & Minimum Air temperature',u['shade_air_difference_c'],'°C'),('Bridge temperature at the time of restraint, Max',ctx['bridge_max'],'°C'),('Bridge temperature at the time of restraint, Min',ctx['bridge_min'],'°C'),('Load effect due to temperature rise',u['temperature_rise_c'],'°C'),('Load effect due to temperature fall',u['temperature_fall_c'],'°C'),('Temp. rise for applying in analysis',u['applied_rise_c'],'°C'),('Temp. fall for applying in analysis',u['applied_fall_c'],'°C')])

    title('Design moment due to different load combination',True); text('Sign convention: +ve MZ causes tension at top of member; -ve MZ at bottom.')
    mcols=[('Member','Location'),('Location','Section'),('Face','Face')]+[(f'{a} {b}',b) for a,bs in [('ULS',('Normal','LL','Factor load')),('Rare',('Normal','LL','Total')),('Quasi',('Normal','LL','Total'))] for b in bs]
    table(ctx['moment_result']['rows'],mcols,{k:'.4f' for k,_ in mcols[3:]},[('Location',0,2),('ULS — Total BM (t-m/m)',3,5),('Rare Combination — Total BM (t-m/m)',6,8),('Quasi-Permanent — Total BM (t-m/m)',9,11)])
    title('Design shear force',True); table(ctx['shear_result']['rows'],[('Member','Location'),('Location','Section'),('Face','Face'),*cols('Normal','LL','Total')],{k:'.4f' for k in ('Normal','LL','Total')},[('Location',0,2),('ULS — Total SF (t/m)',3,5)])
    title('Flexural R/F detail of RCC Box',True)
    rf=[('Component','Component'),('Section','Section'),('Position','Location'),('Calculated Inner (mm²/m)','Reinforcement Provided at inner face (mm²/m)'),('Calculated Outer (mm²/m)','Reinforcement Provided at outer face (mm²/m)')]
    for face,count in [('Inner',2),('Outer',3)]:
        for j in range(1,count+1): rf.extend([(f'{face} Bar-{j} (mm)',f'Bar-{j}'),(f'{face} Spacing-{j} (mm)','Spacing')])
    table(ctx['flexural_result']['rows'],rf,{'Calculated Inner (mm²/m)':'.2f','Calculated Outer (mm²/m)':'.2f'},[('',0,4),('Reinforcement Provided at inner face',5,8),('Reinforcement Provided at outer face',9,14)])
    title('Design Constants',True); dc=ctx['dc_result']; v=dc['values']
    kv([(label,v[key],unit) for label,key,unit in [('fy','fy_mpa','MPa'),('fck','fck_mpa','MPa'),('Es','es_gpa','GPa'),('Ecm','ecm_gpa','GPa'),('m','modular_ratio_reference',''),('fctm','fctm_mpa','MPa'),('Top Slab bottom','top_slab_bottom_cover_mm','mm'),('Top Slab','top_slab_top_cover_mm','mm'),('Bottom Slab','bottom_slab_cover_mm','mm'),('Side Slab Outer Face','side_wall_outer_cover_mm','mm'),('Side Slab Inner Face','side_wall_inner_cover_mm','mm')]])
    table(dc['rows'])
    kv([('Modular ratio (source)',v['modular_ratio_reference'],''),('Modular ratio (Es/Ecm)',v['es_gpa']/v['ecm_gpa'],'')])
    title('Check for strength (ULS : Basic Combination) (Max. Bending Moment)',True)
    table(ctx['uls_flexural_result']['rows'],[(k,{'Depth Check':'Check','Steel Check':'Check','d min (mm)':'dmin (mm)','d provided (mm)':'dprovided (mm)','Ast min (mm²/m)':'Astmin (mm²/m)'}.get(k,k)) for k in ['Component','Section','Face','Xu max/d','Xu max (m)','R lim (MPa)','d min (mm)','d provided (mm)','Depth Check','Design Moment (t-m)','Ast Reqd. (mm²/m)','Ast Lim (mm²/m)','Ast min (mm²/m)','Ast provided (mm²/m)','Steel Check']])
    title('Distribution Reinforcement',True); text('Transverse steel')
    table(ctx['distribution_result']['rows'],[('Member','S.No.'),('Surface','Surface'),('Position','Location'),('Required As (mm²/m)','Ast Reqd. (mm²/m)'),('Diameter (mm)','Dia'),('Spacing (mm)','Spacing'),('Provided As (mm²/m)','mm²/m'),('Check','Check')],groups=[('Transverse steel',0,3),('Reinf. Provided',4,6),('',7,7)])
    title('Check for strength (ULS : Basic Combination) (Max. Shear force)',True); text('Shear resistance without shear reinf.')
    sh=ctx['shear_strength']; table(sh['rows'],[(k,{'ρl = As/(bw d)':'ρl = Asl/bwd'}.get(k,k)) for k in sh['columns']])
    title('Shear reinforcement',True)
    if sh['required_count']:
        st=ctx.get('stirrup_table')
        if st is None: raise ValueError('Current stirrup check unavailable')
        table(st['rows'],[(k,k) for k in st['columns']])
        if st['fail_count']:
            text(f"Shear reinforcement checks not satisfied: {st['fail_count']}")
    else: text('No shear-critical sections in the current schedule.')
    title('Check for stress (SLS : Rare Combination)',True); text(f"For Short Term Effect — Modular Ratio, m = {ctx['sls']['materials']['es_gpa']/ctx['sls']['materials']['ecm_gpa']:.2f}")
    table(ctx['rare_rows'],[(k,lab) for k,lab in [('Component','Component'),('Section','Section'),('Face / location','Face / location'),('A_st provided (mm²/m)','Ast provided (mm²)'),('x_u (mm)','xu (in mm)'),('I_cr (m⁴)','2nd MOI, Icr (m⁴)'),('Moment (t·m/m)','Moment (t-m)'),('Concrete stress (t/m²)','Comp. Stress (t/m²)'),('Concrete limit (t/m²)','Permissible'),('Steel stress (t/m²)','Tensile Stress (t/m²)'),('Steel limit (t/m²)','Permissible'),('Check','Check')]])
    title('Check for crack width (SLS : Quasi-Permanent Combination)',True)
    table(ctx['quasi_rows'],[(k,lab) for k,lab in [('Component','Component'),('Section','Section'),('Face / location','Face / location'),('A_st (mm²/m)','Ast (mm²)'),('A_c,eff (mm²/m)','Ac,eff (mm²)'),('ρ_p,eff','ρp,eff'),('Φ_eq (mm)','Φeq'),('Clear cover (mm)','Clear Cover (mm)'),('5(c + Φ/2) (mm)','S<= 5 (c+Φ/2)'),('S_r,max (mm)','Sr max (mm)'),('Quasi moment (tf·m/m)','Quasi Permanent Moment (t-m)'),('σ_sc (MPa)','σsc (MPa)'),('ε_sm − ε_cm','εsm − εcm'),('Crack width (mm)','Crack width (mm)')]])
    def footer(c,d):
        c.setFont('CulvertSans',8); c.setFillColor(colors.HexColor('#475569')); c.drawString(margin,16,'RCC Box Culvert'); c.drawRightString(page[0]-margin,16,f'Page {d.page}')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return buf.getvalue()
