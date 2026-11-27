"""Stage 8: review drawings traced from supplied reinforcement sheet + BBS(2).

Not for construction: reinforcement cover, locations, lap, anchorage, bend radii,
bar positions/counts and source inconsistencies require engineer sign-off.
Views are schematic and should never be used as dimensions by scaling.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from collections import Counter
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Polygon, Circle
from matplotlib.backends.backend_pdf import PdfPages

from .inputs import CulvertInputs
from .bbs import calculate_bbs

# Direct transcription of the bar mark / nominal diameter / spacing schedule on
# page 3 of the original reinforcement drawing (NOT automatically a checked BBS).
# Spacing as a string is intentional: notes like 2 x 4 NOS are not mm spacings.
PDF_SCHEDULE = (
    ('ts1', 8, '180', 'U', 'Top slab'),
    ('ts2', 8, '290', 'STRAIGHT', 'Top slab'),
    ('ts3', 10, '180', 'U', 'Top slab'),
    ('ts4', 8, '290', 'STRAIGHT', 'Top slab'),
    ('ts5', 10, '2 x 4 NOS', 'STRAIGHT', 'Top slab'),
    ('ts6', 10, '180', 'HOOKED', 'Top slab'),
    ('bs1', 12, '180', 'U', 'Bottom slab'),
    ('bs2', 8, '225', 'STRAIGHT', 'Bottom slab'),
    ('bs3', 10, '180', 'U', 'Bottom slab'),
    ('bs4', 8, '225', 'STRAIGHT', 'Bottom slab'),
    ('bs5', 10, '180', 'BENT', 'Bottom slab'),
    ('bs6', 8, 'LONG. 225 / TRANS. 180', 'LINK', 'Bottom slab'),
    ('w1', 12, '180', 'L', 'Side walls'),
    ('w2', 10, '180', 'L', 'Side walls'),
    ('w3', 8, '250', 'STRAIGHT', 'Side walls'),
    ('w4', 8, '300', 'STRAIGHT', 'Side walls'),
    ('w5', 10, '2 x 4 NOS', 'STRAIGHT', 'Side walls'),
    ('h1', 10, '180', 'HAUNCH', 'Haunches'),
    ('h2', 10, '180', 'HAUNCH', 'Haunches'),
    ('h3', 10, '1 x 4 NOS', 'STRAIGHT', 'Haunches'),
)
PDF_LAPS = {
    'favourable': {'<25%':36, '33%':42, '50%':51, '>50%':54},
    'unfavourable': {'<25%':52, '33%':60, '50%':73, '>50%':78},
}


def pdf_schedule_rows() -> list[dict]:
    return [{'mark':mark,'diameter_mm':dia,'spacing':spacing,'shape':shape,'zone':zone}
            for mark,dia,spacing,shape,zone in PDF_SCHEDULE]


def build_reinforcement_review(inputs: CulvertInputs, bbs_result: dict | None = None) -> dict:
    """Cross-reference drawing schedule and working BBS; don't infer missing bars."""
    inputs.validate()
    bbs=bbs_result or calculate_bbs()
    by_mark={str(r['mark']).lower(): r for r in bbs['lines']}
    matches=[]; issues=[]
    for record in pdf_schedule_rows():
        mark=record['mark']
        b=by_mark.get(mark)
        status='CHECK'
        if b is None:
            issues.append(f"{mark}: shown on reinforcement PDF, but absent from BBS (2)")
            status='PDF ONLY'
        else:
            dia=float(b['diameter_mm'])
            if not math.isclose(dia, record['diameter_mm'], rel_tol=0, abs_tol=0.01):
                issues.append(f"{mark}: PDF diameter {record['diameter_mm']} mm, working BBS {dia:g} mm")
                status='DIAMETER MISMATCH'
            else:
                try:
                    drawing_spacing=float(record['spacing'])
                    bbs_spacing=b['spacing_mm']
                    if bbs_spacing is None or not math.isclose(float(bbs_spacing), drawing_spacing,abs_tol=0.01):
                        issues.append(f"{mark}: drawing spacing {drawing_spacing:g} mm, BBS spacing {bbs_spacing}")
                        status='SPACING CHECK'
                    else: status='REFERENCE MATCH'
                except ValueError:
                    status='NOTE / COUNT CHECK'
        matches.append({**record,'bbs_count':b['bar_count'] if b else None,
                        'bbs_cut_mm':b['cut_length_mm'] if b else None,'status':status})
    pdf_marks={x[0] for x in PDF_SCHEDULE}
    extra=[x['mark'] for x in bbs['lines'] if x['mark'] not in pdf_marks]
    if extra: issues.append('BBS-specific variants not shown as individual PDF schedule marks: '+', '.join(extra))
    for x in bbs['lines']:
        if x['bar_count']>0 and x['diameter_mm']<=0:
            issues.append(f"{x['mark']}: missing BBS diameter — do not fabricate")
    if (not math.isclose(inputs.clear_width_m,3,abs_tol=1e-6) or
        not math.isclose(inputs.clear_height_m,3,abs_tol=1e-6) or
        not math.isclose(inputs.length_m,12,abs_tol=1e-6)):
        issues.append('Current geometry differs from 3 m x 3 m / 12 m reference; BBS cutting lengths and counts do not automatically update')
    if inputs.haunch_count==0:
        issues.append('Haunches are disabled: original reinforcement drawing includes haunch details')
    issues.append('PDF detail X/Y/Z and lap-zone sketches are reference schematics; anchorage, bends, laps, cover and member faces require checked CAD detailing')
    return {'source':'Reinforcement PDF sheet 3 and original BBS (2)', 'schedule':matches,
            'bbs_only_marks':extra,'issues':issues,
            'status':'REVIEW DRAWING — NOT FOR FABRICATION OR CONSTRUCTION',
            'reference_clear_width_m':3,'reference_clear_height_m':3,'reference_length_m':12,
            'pdf_notes':{'concrete':'M35','steel':'Fe500D',
                         'base_slab_cover_mm':75,'earth_face_cover_mm':75,'non_earth_face_cover_mm':50,
                         'L1_mm':900,'L2_mm':900,'L3_mm':450,
                         'lap_multipliers_by_bar_diameter':PDF_LAPS}}


def _fig(title:str, subtitle:str, size=(16,9)):
    fig=plt.figure(figsize=size,facecolor='#f8fafc',constrained_layout=False)
    fig.text(.045,.96,title,fontsize=19,fontweight='bold',color='#172b44')
    fig.text(.046,.93,subtitle,fontsize=9,color='#4b6077')
    fig.text(.045,.023,'REFERENCE DETAIL • SCHEMATIC • NOT TO SCALE • NOT FOR CONSTRUCTION',fontsize=8,color='#b45309',fontweight='bold')
    return fig


def _shape(ax,xy,wh,inputs:CulvertInputs):
    x,y=xy; W,H=wh
    ax.add_patch(Rectangle((x,y),W,H,facecolor='#bfc9d3',edgecolor='#25384c',lw=2))
    wi=inputs.wall_thickness_m; hb=inputs.bottom_slab_thickness_m; ht=inputs.top_slab_thickness_m
    ax.add_patch(Rectangle((x+wi,y+hb), inputs.clear_width_m,inputs.clear_height_m,
                           facecolor='#ffffff',edgecolor='#25384c',lw=1.3))
    if inputs.haunch_count:
        hw=inputs.haunch_width_m; hh=inputs.haunch_height_m
        xi=x+wi; xr=xi+inputs.clear_width_m; yi=y+hb; yt=yi+inputs.clear_height_m
        for triangle in (((xi,yi),(xi+hw,yi),(xi,yi+hh)),
                         ((xr,yi),(xr-hw,yi),(xr,yi+hh)),
                         ((xi,yt),(xi+hw,yt),(xi,yt-hh)),
                         ((xr,yt),(xr-hw,yt),(xr,yt-hh))):
            ax.add_patch(Polygon(triangle,closed=True,facecolor='#bfc9d3',edgecolor='#25384c',lw=1.2))


def _line(ax,x1,y1,x2,y2,color,lw=2.3,style='-'):
    ax.plot([x1,x2],[y1,y2],color=color,lw=lw,ls=style,solid_capstyle='round',zorder=4)


def _dots(ax,xs,ys,color,rad=.020):
    for x,y in zip(xs,ys): ax.add_patch(Circle((x,y),rad,color=color,zorder=8))


def draw_reinforcement_section(inputs:CulvertInputs, bbs_result:dict | None=None):
    """Top/bottom/wall/haunch typical bar face schematic, not bar shop details."""
    inputs.validate()
    bbs=bbs_result or calculate_bbs()
    mark={r['mark']:r for r in bbs['lines']}
    ow=inputs.clear_width_m+2*inputs.wall_thickness_m
    oh=inputs.clear_height_m+inputs.bottom_slab_thickness_m+inputs.top_slab_thickness_m
    fig=_fig('REINFORCEMENT • TYPICAL BOX SECTION',
             'Reference: reinforcement drawing sheet 3 • face identification and bar marks • dimensions from current inputs')
    ax=fig.add_axes([.065,.15,.57,.72]); ax.set_aspect('equal'); _shape(ax,(0,0),(ow,oh),inputs)
    z=min(.05,inputs.wall_thickness_m*.24,inputs.top_slab_thickness_m*.24,inputs.bottom_slab_thickness_m*.24)
    # Stylised locations of outer / inner face steel: cover reference in note, not bar centroid layout.
    for x1,x2,y,markname,c in [(.10,ow-.10,oh-z,'ts1','#b45309'),
                                (.10,ow-.10,oh-inputs.top_slab_thickness_m+z,'ts3','#0d9488'),
                                (.10,ow-.10,z,'bs3','#b45309'),
                                (.10,ow-.10,inputs.bottom_slab_thickness_m-z,'bs1','#0d9488')]:
        _line(ax,x1,y,x2,y,c,2)
        xs=[x1+(x2-x1)*i/14 for i in range(15)]
        _dots(ax,xs,[y]*len(xs),c,rad=.013)
    for x,c in ((z,'#b45309'),(inputs.wall_thickness_m-z,'#0d9488'),
                (ow-z,'#b45309'),(ow-inputs.wall_thickness_m+z,'#0d9488')):
        ys=[inputs.bottom_slab_thickness_m+.17+(.8*inputs.clear_height_m)*i/10 for i in range(11)]
        _line(ax,x,ys[0],x,ys[-1],c,2)
        _dots(ax,[x]*len(ys),ys,c,rad=.014)
    if inputs.haunch_count:
        hw=inputs.haunch_width_m; hh=inputs.haunch_height_m
        a=inputs.wall_thickness_m; b=inputs.bottom_slab_thickness_m
        for xx,yy,signx,signy in [(a,b,1,1),(ow-a,b,-1,1),
                                    (a,oh-inputs.top_slab_thickness_m,1,-1),
                                    (ow-a,oh-inputs.top_slab_thickness_m,-1,-1)]:
            _line(ax,xx+signx*.2*hw,yy+signy*.75*hh,
                  xx+signx*.75*hw,yy+signy*.2*hh,'#7c3aed',3)
    ax.annotate(f'Clear width {inputs.clear_width_m:.3f} m',
                xy=(inputs.wall_thickness_m,inputs.clear_height_m*.43),
                xytext=(ow/2,inputs.clear_height_m*.43),ha='center',fontsize=9,
                arrowprops={'arrowstyle':'-', 'lw':.3})
    ax.text(ow/2,oh+0.13,f'External width {ow:.3f} m',ha='center',fontsize=10,fontweight='bold')
    ax.text(-.18,oh/2,f'External height {oh:.3f} m',ha='right',rotation=90,va='center',fontsize=10)
    ax.set_xlim(-.55,ow+.3); ax.set_ylim(-.42,oh+.38); ax.axis('off')
    notes=fig.add_axes([.67,.16,.29,.72]); notes.axis('off')
    notes.text(0,1,'BAR FACES / MARKS',fontsize=13,fontweight='bold',color='#172b44',va='top')
    groups=[('TOP SLAB','ts1','ts3','ts2 / ts4'),('BOTTOM SLAB','bs3','bs1','bs2 / bs4'),
            ('SIDE WALL','w1','w2','w3 / w4'),('HAUNCH','h1','h2','h3 PDF-only')]
    yp=.92
    for name,o,inn,dist in groups:
        notes.text(0,yp,name,fontsize=11,fontweight='bold',color='#0f4f65',va='top');yp-=.037
        notes.text(.04,yp,f'Outer / earth face:  {o}',fontsize=9,color='#334155',va='top');yp-=.032
        notes.text(.04,yp,f'Inner / other face: {inn}',fontsize=9,color='#334155',va='top');yp-=.032
        notes.text(.04,yp,f'Distribution:       {dist}',fontsize=9,color='#334155',va='top');yp-=.079
    notes.text(0,yp,'REINFORCEMENT PDF NOTES',fontsize=11,fontweight='bold',color='#172b44');yp-=.045
    for line in ['Base slab cover: 75 mm','Earth faces: 75 mm','Non-earth faces: 50 mm',
                 'Haunch: 150 x 150 mm (reference)','M35 concrete / Fe500D steel']:
        notes.text(.01,yp,line,fontsize=9,color='#334155',va='top');yp-=.043
    notes.text(0,yp-.03,'Dots indicate bar-face locations only;\nnot actual counted bars or bend details.',fontsize=8.5,color='#a16207',va='top')
    return fig


def _draw_plan(ax,inputs,main_mark,secondary_mark,name,bbs):
    L=inputs.length_m; W=inputs.clear_width_m+2*inputs.wall_thickness_m
    ax.add_patch(Rectangle((0,0),L,W,facecolor='#edf2f7',edgecolor='#22374a',lw=1.7))
    ax.set_xlim(-.25,L+.25);ax.set_ylim(-.25,W+.28)
    ax.set_aspect('auto'); ax.set_title(name,fontsize=12,fontweight='bold',color='#172b44')
    mark_by={r['mark']:r for r in bbs['lines']}
    a=mark_by.get(main_mark,{}); b=mark_by.get(secondary_mark,{})
    main_spacing=(a.get('spacing_mm') or 180)/1000
    transverse_start=.075; transverse_end=max(.076,L-.075)
    if main_spacing>0:
        n=min(350,int((transverse_end-transverse_start)/main_spacing)+1)
        pos=[transverse_start+i*main_spacing for i in range(n)]
        for x in pos:_line(ax,x,.075,x,W-.075,'#b45309',.72)
    nlong=int(b.get('bar_count',5))
    for y in [W*(j+1)/(min(21,max(2,nlong))+1) for j in range(min(21,max(2,nlong)))]:
        _line(ax,.075,y,L-.075,y,'#0d9488',1.2)
    ax.set_xticks([0,L/4,L/2,L*3/4,L]);ax.set_xticklabels([f'{x:.1f}' for x in [0,L/4,L/2,L*3/4,L]])
    ax.set_yticks([0,W/2,W]);ax.set_yticklabels([f'{y:.2f}' for y in [0,W/2,W]])
    ax.set_xlabel('Barrel length (m)',fontsize=8);ax.set_ylabel('Box width (m)',fontsize=8)
    ax.grid(alpha=.12)
    ax.text(.01,-.23,f'{main_mark}: transverse / {a.get("diameter_mm",0):g} mm @ {a.get("spacing_mm",0):g} mm   |   '
            f'{secondary_mark}: longitudinal / {b.get("diameter_mm",0):g} mm @ {b.get("spacing_mm",0):g} mm',
            fontsize=9,transform=ax.transAxes,color='#30455b')


def draw_reinforcement_plans(inputs:CulvertInputs,bbs_result:dict|None=None):
    bbs=bbs_result or calculate_bbs()
    fig=_fig('REINFORCEMENT • TOP AND BOTTOM SLAB PLANS',
             'Plan interpretation of PDF sheet 3 and BBS (2) • nominal spacings • actual site placement must be checked',
             size=(16,10))
    ax1=fig.add_axes([.08,.60,.85,.25]);_draw_plan(ax1,inputs,'ts1','ts2','TOP SLAB / reference top face',bbs)
    ax2=fig.add_axes([.08,.22,.85,.25]);_draw_plan(ax2,inputs,'bs1','bs2','BOTTOM SLAB / reference upper face',bbs)
    fig.text(.08,.105,'Orange = transverse bars, teal = longitudinal bars. Additional faces/extra bars (ts3–ts6, bs3–bs6) remain in the bar schedule.',fontsize=10,color='#30455b')
    fig.text(.08,.08,'Reference BBS counts/cutting lengths DO NOT update automatically when dimensions change. Bars shown by nominal spacing, not an issued placing plan.',fontsize=9,color='#9a3412')
    return fig


def draw_side_wall_elevation(inputs:CulvertInputs,bbs_result:dict|None=None):
    """Developed 12m x 3m face elevation (both wall faces, schematic)."""
    bbs=bbs_result or calculate_bbs()
    marks={r['mark']:r for r in bbs['lines']}
    fig=_fig('REINFORCEMENT • SIDE WALL ELEVATIONS',
             'Reference wall marks w1–w5 • barrel-length orientation • layout is schematic, not a placing drawing',
             size=(16,10))
    length=inputs.length_m; H=inputs.clear_height_m
    for j,(face,main,dist) in enumerate((('EARTH / OUTER FACE','w1','w3'),('NON-EARTH / INNER FACE','w2','w4'))):
        ax=fig.add_axes([.075,.60-j*.39,.84,.25]);ax.set_title(face,fontsize=12,fontweight='bold',color='#172b44')
        ax.add_patch(Rectangle((0,0),length,H,facecolor='#e8eef4',edgecolor='#22374a',lw=1.7))
        bar=marks[main]; dbar=marks[dist]
        spacing=max(.05,float(bar['spacing_mm'] or 180)/1000)
        positions=[.075+i*spacing for i in range(min(350,int((length-.15)/spacing)+1))]
        for x in positions:_line(ax,x,.10,x,H-.10,'#b45309',.8)
        count=min(20,max(3,int(dbar['bar_count']/2)))
        for i in range(count):
            y=H*(i+1)/(count+1)
            _line(ax,.07,y,length-.07,y,'#0d9488',1.15)
        ax.set_xlim(-.12,length+.12);ax.set_ylim(-.13,H+.17)
        ax.set_xticks([0,length/4,length/2,3*length/4,length]);ax.set_yticks([0,H/2,H])
        ax.set_xlabel('Culvert length (m)',fontsize=8);ax.set_ylabel('Clear height (m)',fontsize=8)
        ax.grid(alpha=.11)
        fig.text(.081,.54-j*.39,f"{main}: Ø{bar['diameter_mm']:g} @ {bar['spacing_mm']:g} mm main vertical  |  "
                 f"{dist}: Ø{dbar['diameter_mm']:g} @ {dbar['spacing_mm']:g} mm horizontal (reference)",
                 fontsize=10,color='#334155')
    fig.text(.08,.095,'w5: reference corner extra bars. Two walls shown by face; splice/lap positions and true reinforcement lengths require engineer detailing.',
             fontsize=9,color='#a16207')
    return fig


def _bar_path(shape):
    return {
        'STRAIGHT':[(0,.5),(1,.5)],
        'U':[(0,.88),(0,.2),(1,.2),(1,.88)],
        'HOOKED':[(.05,.67),(.2,.46),(.8,.46),(.95,.67)],
        'BENT':[(.05,.4),(.2,.65),(.8,.65),(.95,.4)],
        'L':[(.18,.90),(.18,.24),(.85,.24)],
        'LINK':[(.15,.25),(.15,.82),(.85,.82),(.85,.25),(.65,.25)],
        'HAUNCH':[(.08,.15),(.40,.65),(.82,.65)],
    }[shape]


def draw_bar_shape_schedule(bbs_result:dict|None=None):
    bbs=bbs_result or calculate_bbs()
    by={x['mark']:x for x in bbs['lines']}
    fig=_fig('REINFORCEMENT • BAR SHAPE AND MARK SCHEDULE',
             'Bar shapes from reinforcement PDF; weights, counts and nominal cuts from BBS (2) • review only',size=(16,11))
    rows=pdf_schedule_rows()
    ax=fig.add_axes([.035,.11,.93,.78]);ax.set_xlim(0,4);ax.set_ylim(0,5);ax.axis('off')
    for i,row in enumerate(rows):
        r=i//4; c=i%4; x=c; y=4-r
        ax.add_patch(Rectangle((x+.02,y+.04),.95,.89,facecolor='white',edgecolor='#c6d3df',lw=.9))
        b=by.get(row['mark'])
        invalid=b is None or float(b.get('diameter_mm',0))<=0
        color='#bd512a' if invalid else '#0d6c78'
        ax.text(x+.08,y+.81,row['mark'],fontweight='bold',fontsize=12,color='#172b44')
        ax.text(x+.92,y+.81,f"Ø{row['diameter_mm']} | {row['spacing']}",fontsize=8,ha='right',color='#49647c')
        p=_bar_path(row['shape']);xx=[x+.14+px*.70 for px,py in p];yy=[y+.21+py*.45 for px,py in p]
        ax.plot(xx,yy,color=color,lw=2.8,solid_capstyle='round')
        if b:
            desc=f"BBS: {int(b['bar_count'])} nos | cut {b['cut_length_mm']:.0f} mm"
        else: desc='PDF-only mark: no BBS cutting length'
        ax.text(x+.08,y+.14,desc,fontsize=7.2,color=color)
    fig.text(.046,.063,'Shapes are symbols only. Bend radii / hooks / laps / deductions are NOT defined by these icons. Ø and spacing from PDF.',fontsize=9,color='#a16207')
    return fig


def draw_corner_and_lap_details(inputs:CulvertInputs,bbs_result:dict|None=None):
    fig=_fig('REINFORCEMENT • DETAILS X, Y, Z AND LAP ZONE',
             'Reference drawing sheet 3 • schematic enlargements • written dimensions control',size=(16,10))
    boxes=[(.06,.52,.40,.35,'DETAIL X • TOP CORNER'),(.55,.52,.40,.35,'DETAIL Y • BOTTOM CORNER'),
           (.06,.13,.40,.32,'DETAIL Z • BOTTOM SHEAR LINK'),(.55,.13,.40,.32,'LAP ZONE • REFERENCE ONLY')]
    for k,(xx,yy,ww,hh,title) in enumerate(boxes):
        ax=fig.add_axes([xx,yy,ww,hh]);ax.set_facecolor('white')
        ax.set_xlim(0,10);ax.set_ylim(0,7);ax.set_aspect('equal');ax.axis('off')
        ax.set_title(title,fontsize=11,fontweight='bold',color='#172b44')
        if k<=1:
            pts=[(1,1),(1,6),(9,6),(9,5),(4,5),(2,3),(2,1)] if k==0 else [(1,1),(9,1),(9,2),(4,2),(2,4),(2,6),(1,6)]
            ax.add_patch(Polygon(pts,closed=True,facecolor='#d8e0e7',edgecolor='#405364',lw=1.5))
            if k==0:
                ax.plot([1.4,1.4,3.1,8.5],[2,5.55,5.55,5.55],color='#0d6c78',lw=2)
                ax.plot([1.7,4,8.5],[2.7,5.3,5.3],color='#b45309',lw=2.2)
                ax.text(5.2,3.3,'ts1 / w1 / h1',ha='center',fontsize=11,color='#334155')
            else:
                ax.plot([1.4,1.4,3.1,8.5],[5,1.45,1.45,1.45],color='#0d6c78',lw=2)
                ax.plot([1.7,4,8.5],[4.3,1.7,1.7],color='#b45309',lw=2.2)
                ax.text(5.2,4,'bs1 / bs3 / h2',ha='center',fontsize=11,color='#334155')
            ax.text(5,.46,'Reference haunch 150 x 150 mm',ha='center',fontsize=9,color='#596e82')
        elif k==2:
            ax.add_patch(Rectangle((1,1),8,5,facecolor='#d8e0e7',edgecolor='#405364',lw=1.5))
            ax.plot([2,2,6,6,4],[1.7,5.2,5.2,1.7,1.7],color='#a16207',lw=2.6)
            ax.text(5.2,3.4,'bs6 / shear link',ha='center',fontsize=10,color='#334155')
            ax.text(5.1,2.5,'Ø8 reference',ha='center',fontsize=9,color='#334155')
        else:
            ax.plot([.8,8.8],[4,4],lw=2.5,color='#0d6c78')
            ax.plot([2.2,9.2],[3.4,3.4],lw=2.5,color='#b45309')
            ax.annotate('',(2.2,2.6),(8.8,2.6),arrowprops={'arrowstyle':'<->','lw':1.5})
            ax.text(5.5,2.2,'Lp = X × smaller bar diameter',ha='center',fontsize=10,color='#334155')
            ax.text(5.2,1.25,'M35 reference: X depends on bond & lapped %',ha='center',fontsize=9,color='#596e82')
    fig.text(.06,.064,'PDF nominal lengths: L1 = 900 mm, L2 = 900 mm, L3 = 450 mm. Reference anchorage: 36D favourable / 52D unfavourable; laps vary with percentage.',fontsize=9,color='#445c72')
    return fig


def figures_for_review(inputs:CulvertInputs,bbs_result:dict|None=None)->dict:
    bbs=bbs_result or calculate_bbs()
    return {'Box reinforcement section':draw_reinforcement_section(inputs,bbs),
            'Top and bottom slab plans':draw_reinforcement_plans(inputs,bbs),
            'Side wall elevations':draw_side_wall_elevation(inputs,bbs),
            'Bar mark and shape schedule':draw_bar_shape_schedule(bbs),
            'Corner and lap reference details':draw_corner_and_lap_details(inputs,bbs)}


def figure_png_bytes(fig,dpi:int=165)->bytes:
    out=BytesIO(); fig.savefig(out,format='png',dpi=dpi,facecolor=fig.get_facecolor(),bbox_inches='tight')
    return out.getvalue()


def make_review_pdf(inputs:CulvertInputs,bbs_result:dict|None=None)->bytes:
    figures=figures_for_review(inputs,bbs_result)
    buffer=BytesIO()
    try:
        with PdfPages(buffer,metadata={'Title':'RCC Culvert — Reinforcement drawing review package',
                                      'Subject':'Unapproved reference drawings based on PDF + BBS(2)'}) as pdf:
            for fig in figures.values(): pdf.savefig(fig,facecolor=fig.get_facecolor())
    finally:
        for fig in figures.values():plt.close(fig)
    return buffer.getvalue()


def export_reinforcement_dxf(inputs:CulvertInputs, bbs_result:dict|None, path: str|Path)->Path:
    """CAD-friendly reference geometry and bar-face locations. NO fabrication polylines."""
    import ezdxf
    inputs.validate()
    doc=ezdxf.new('R2010');msp=doc.modelspace()
    for layer,c in [('CONCRETE',7),('STEEL_MAIN',1),('STEEL_DIST',4),('TEXT',3),('HAUNCH',5)]:
        doc.layers.new(layer,dxfattribs={'color':c})
    W=inputs.clear_width_m+2*inputs.wall_thickness_m
    H=inputs.clear_height_m+inputs.bottom_slab_thickness_m+inputs.top_slab_thickness_m
    t=inputs.wall_thickness_m; b=inputs.bottom_slab_thickness_m; top=inputs.top_slab_thickness_m
    def closed(points,layer):msp.add_lwpolyline(points,close=True,dxfattribs={'layer':layer})
    closed([(0,0),(W,0),(W,H),(0,H)],'CONCRETE')
    closed([(t,b),(W-t,b),(W-t,b+inputs.clear_height_m),(t,b+inputs.clear_height_m)],'CONCRETE')
    if inputs.haunch_count:
        hw=inputs.haunch_width_m;hh=inputs.haunch_height_m
        for tri in ([ (t,b),(t+hw,b),(t,b+hh)],[(W-t,b),(W-t-hw,b),(W-t,b+hh)],
                    [(t,H-top),(t+hw,H-top),(t,H-top-hh)],[(W-t,H-top),(W-t-hw,H-top),(W-t,H-top-hh)]):closed(tri,'HAUNCH')
    c=min(.05,t*.25,top*.25,b*.25)
    for y in (c,b-c,b+inputs.clear_height_m+c,H-c):
        msp.add_line((.1,y),(W-.1,y),dxfattribs={'layer':'STEEL_MAIN'})
    for x in (c,t-c,W-t+c,W-c):
        msp.add_line((x,b+.15),(x,b+inputs.clear_height_m-.15),dxfattribs={'layer':'STEEL_MAIN'})
    notes=['STAGE 8 — REVIEW-ONLY REINFORCEMENT',
           f'CLEAR {inputs.clear_width_m:.3f} x {inputs.clear_height_m:.3f} m; LENGTH {inputs.length_m:.3f} m',
           'REFERENCE PDF: SHEET 3 / BBS (2); NOT FOR FABRICATION',
           'REFERENCE: 75mm EARTH/BASE FACES, 50mm NON-EARTH FACES',
           'MARKS: ts1/ts3 TOP, bs1/bs3 BOTTOM, w1/w2 WALL, h1/h2 HAUNCH',
           'BEND / LAP / ANCHORAGE / MISSING BAR DIAMETERS REQUIRE ENGINEER REVIEW']
    for i,note in enumerate(notes):msp.add_text(note,dxfattribs={'height':.095,'layer':'TEXT','insert':(W+.5,H-.13-i*.25)})
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);doc.saveas(p)
    return p


# Stage 6: live schedule adapter. No reference fallback in live mode.
def live_drawing_data(inputs, live, constants):
    inputs.validate()
    if not isinstance(live, dict) or not live.get('lines'):
        raise ValueError('Live BBS unavailable')
    mapping = {'Bar mark':'mark','Member':'member','Dia (mm)':'diameter_mm',
               'Spacing (mm)':'spacing_mm','Bar count':'bar_count',
               'Cut length (mm)':'cut_length_mm','Weight (kg)':'weight_kg',
               'M1 (mm)':'m1_mm','M2 (mm)':'m2_mm','M3 (mm)':'m3_mm','Status':'status'}
    rows = [{k:r.get(v) for k,v in mapping.items()} for r in live['lines']]
    if len({r['Bar mark'] for r in rows}) != len(rows):
        raise ValueError('Duplicate live bar marks')
    return {'rows': rows, 'pending': list(live.get('pending_marks', [])),
            'covers': dict(constants),
            'quantity_coverage_complete': bool(live.get('quantity_coverage_complete')), 'approved': False}


def _live_label(r):
    def fmt(v): return 'pending' if v is None else f'{v:g}'
    return f"{r['Bar mark']}: dia {fmt(r['Dia (mm)'])} mm @ {fmt(r['Spacing (mm)'])} mm"


def _live_face_paths(inputs, data):
    w=inputs.clear_width_m+2*inputs.wall_thickness_m
    h=inputs.clear_height_m+inputs.bottom_slab_thickness_m+inputs.top_slab_thickness_m
    t=inputs.wall_thickness_m; b=inputs.bottom_slab_thickness_m; top=inputs.top_slab_thickness_m
    rows={r['Bar mark']:r for r in data['rows']}; c=data['covers']
    paths=[]
    for mark,key,thick,kind in [('ts1','top_slab_top_cover_mm',top,'top_outer'),('ts3','top_slab_bottom_cover_mm',top,'top_inner'),('bs3','bottom_slab_cover_mm',b,'bottom_outer'),('bs1','bottom_slab_cover_mm',b,'bottom_inner'),('w1','side_wall_outer_cover_mm',t,'wall_outer'),('w2','side_wall_inner_cover_mm',t,'wall_inner')]:
        r=rows.get(mark,{}); d=r.get('Dia (mm)')
        if d is None or d<=0 or r.get('Weight (kg)') is None: continue
        z=(float(c[key])+d/2)/1000
        if not 0<z<thick: raise ValueError(f'{mark}: cover and bar do not fit member')
        if kind.startswith('top'): y=h-z if kind=='top_outer' else h-top+z
        elif kind.startswith('bottom'): y=z if kind=='bottom_outer' else b-z
        else:
            x=z if kind=='wall_outer' else t-z
            for xx in (x,w-x): paths.append((mark,[(xx,b),(xx,h-top)]))
            continue
        paths.append((mark,[(t,y),(w-t,y)]))
    return paths


def live_figures(inputs,data):
    rows=data['rows']; w=inputs.clear_width_m+2*inputs.wall_thickness_m
    h=inputs.clear_height_m+inputs.bottom_slab_thickness_m+inputs.top_slab_thickness_m
    fig=_fig('REINFORCEMENT • TYPICAL BOX SECTION','Live BBS • nominal face indicators only; no fabrication paths')
    ax=fig.add_axes([.05,.15,.52,.72]); _shape(ax,(0,0),(w,h),inputs); ax.set_aspect('equal');ax.set_xlim(-.2,w+.2);ax.set_ylim(-.2,h+.2);ax.axis('off')
    for mark,pts in _live_face_paths(inputs,data):
        ax.plot(*zip(*pts),lw=2,color='#0d9488')
    notes=fig.add_axes([.60,.13,.37,.75]);notes.axis('off')
    main=[r for r in rows if r['Bar mark'] in ('ts1','ts3','bs1','bs3','w1','w2')]
    lines=[f'Clear opening: {inputs.clear_width_m:g} × {inputs.clear_height_m:g} m',f'Length: {inputs.length_m:g} m']+[_live_label(r) for r in main]+[f'{k}: {v:g} mm' for k,v in data['covers'].items() if k.endswith('cover_mm')]
    notes.text(0,1,'\n'.join(lines),va='top',fontsize=10,linespacing=1.7)
    figs={'Box reinforcement section':fig}
    for name,title,prefixes in [('Top and bottom slab plans','SLAB REINFORCEMENT • FACE SCHEDULE',('ts','bs')),('Side wall elevations','WALL REINFORCEMENT • FACE SCHEDULE',('w',)),('Bar mark and shape schedule','LIVE BAR MARK SCHEDULE',('',)),('Corner and lap reference details','CORNER / HAUNCH / SHEAR DETAILING',('h','ts5','w5','bs6'))]:
        chosen=[r for r in rows if r['Bar mark'].startswith(prefixes)]
        f=_fig(title,'Live reinforcement schedule • placement, bends and laps require engineer review',size=(16,11))
        a=f.add_axes([.04,.12,.92,.76]);a.axis('off')
        cells=[[r['Bar mark'],r['Member'],*['—' if r[k] is None else f'{r[k]:g}' for k in ('Dia (mm)','Spacing (mm)','Bar count','Cut length (mm)')], 'Pending' if r['Weight (kg)'] is None else 'Nominal'] for r in chosen]
        if cells:
            tab=a.table(cellText=cells,colLabels=['Mark','Member','Dia mm','Spacing mm','Count','Cut mm','Status'],cellLoc='left',loc='upper center',colWidths=[.09,.29,.10,.12,.10,.12,.12]);tab.auto_set_font_size(False);tab.set_fontsize(9);tab.scale(1, min(1.6,30/max(len(cells),1)))
        else:a.text(.1,.8,'No linked marks')
        figs[name]=f
    return figs


def live_review_pdf(inputs,data):
    figures=live_figures(inputs,data); buf=BytesIO()
    try:
        with PdfPages(buf) as pdf:
            for fig in figures.values():pdf.savefig(fig)
    finally:
        for fig in figures.values():plt.close(fig)
    return buf.getvalue()


def live_reinforcement_dxf(inputs,data,path):
    import ezdxf
    doc=ezdxf.new('R2010'); doc.units=6; m=doc.modelspace()
    w=inputs.clear_width_m+2*inputs.wall_thickness_m; h=inputs.clear_height_m+inputs.bottom_slab_thickness_m+inputs.top_slab_thickness_m
    t=inputs.wall_thickness_m;b=inputs.bottom_slab_thickness_m
    m.add_lwpolyline([(0,0),(w,0),(w,h),(0,h)],close=True)
    m.add_lwpolyline([(t,b),(w-t,b),(w-t,b+inputs.clear_height_m),(t,b+inputs.clear_height_m)],close=True)
    if inputs.haunch_count:
        for x,y,sx,sy in [(t,b,1,1),(w-t,b,-1,1),(t,b+inputs.clear_height_m,1,-1),(w-t,b+inputs.clear_height_m,-1,-1)]:
            m.add_lwpolyline([(x,y),(x+sx*inputs.haunch_width_m,y),(x,y+sy*inputs.haunch_height_m)],close=True)
    doc.layers.new('FACE_INDICATORS',dxfattribs={'color':3})
    for mark,pts in _live_face_paths(inputs,data):m.add_lwpolyline(pts,dxfattribs={'layer':'FACE_INDICATORS'})
    notes=['LIVE BBS REVIEW - NOT FOR CONSTRUCTION', 'BBS quantity coverage: '+('complete nominal' if data['quantity_coverage_complete'] else 'partial / provisional')]+[_live_label(r)+(' | quantity pending' if r['Weight (kg)'] is None else f" | count {r['Bar count']:g} | cut {r['Cut length (mm)']:g} mm") for r in data['rows']]
    for i,n in enumerate(notes):m.add_text(n,dxfattribs={'height':.09,'insert':(w+.4,h-i*.18)})
    doc.saveas(str(path));return Path(path)

# Stage 6B: graphical review views, using the same live rows as the exports.
def _active_row(row):
    return row is not None and row.get('Weight (kg)') is not None and (row.get('Dia (mm)') or 0)>0 and (row.get('Bar count') or 0)>0


def _nominal_grid(ax, length, height, main, dist, end_cover, face_cover):
    ax.add_patch(Rectangle((0,0),length,height,facecolor='#edf2f7',edgecolor='#22374a'))
    for row,vertical,cover,color in [(main,True,end_cover,'#b45309'),(dist,False,face_cover,'#0d9488')]:
        if not _active_row(row): continue
        spacing=row.get('Spacing (mm)')
        if spacing is None or not math.isfinite(spacing) or spacing<=0: continue
        span=length if vertical else height
        offset=(cover+row['Dia (mm)']/2)/1000
        if 2*offset>=span: continue
        n=math.floor((span-2*offset)/(spacing/1000))+1
        # Display a representative subset for extremely dense schedules.
        stride=max(1,math.ceil(n/350))
        for i in range(0,n,stride):
            p=offset+i*spacing/1000
            if vertical: _line(ax,p,0,p,height,color,.7)
            else: _line(ax,0,p,length,p,color,.9)
    ax.set_xlim(-.02*length,1.02*length);ax.set_ylim(-.03*height,1.03*height)
    ax.set_xlabel('Barrel length (m)',fontsize=8);ax.set_ylabel('Face dimension (m)',fontsize=8)


def _row_note(row,mark):
    if row is None: return f'{mark}: not mapped'
    return _live_label(row)+(' — quantity pending' if row['Weight (kg)'] is None else ' — inactive' if not _active_row(row) else ' — detailing review' if mark=='bs6' else '')


def _graphic_faces(inputs,data,wall=False):
    by={r['Bar mark']:r for r in data['rows']};c=data['covers']
    if wall:
        specs=[('Earth face','w1','w3','side_wall_outer_cover_mm'),('Non-earth face','w2','w4','side_wall_inner_cover_mm')]; height=inputs.clear_height_m
    else:
        specs=[('Top slab outer','ts1','ts2','top_slab_top_cover_mm'),('Top slab inner','ts3','ts4','top_slab_bottom_cover_mm'),('Bottom slab inner','bs1','bs2','bottom_slab_cover_mm'),('Bottom slab outer','bs3','bs4','bottom_slab_cover_mm')];height=inputs.clear_width_m+2*inputs.wall_thickness_m
    fig=_fig('REINFORCEMENT • '+('SIDE WALL ELEVATIONS' if wall else 'SLAB PLANS'),'Nominal spacing illustrations • not counted placing plans • edge and extra bars listed separately',size=(16,11))
    for i,(name,m,d,k) in enumerate(specs):
        if wall: x,y=.08,.56-i*.39; width=.84; ht=.25
        else: x,y=.07+(i%2)*.48,.56-(i//2)*.37; width=.40; ht=.25
        a=fig.add_axes([x,y,width,ht]);a.set_title(name,fontsize=12)
        # End-cover is not supplied by the Stage 6 adapter. This is a face-cover
        # offset for the illustration only, not a confirmed end placement.
        _nominal_grid(a,inputs.length_m,height,by.get(m),by.get(d),float(c[k]),float(c[k]))
        fig.text(x,y-.065,_row_note(by.get(m),m)+'\n'+_row_note(by.get(d),d),fontsize=8)
    extra=[r for r in data['rows'] if r['Bar mark'] not in {v for _,m,d,_ in specs for v in (m,d)} and r['Bar mark'].startswith(('w',) if wall else ('ts','bs'))]
    fig.text(.07,.065,'Other marks: '+', '.join(r['Bar mark'] for r in extra)+' (see bar schedule).',fontsize=9)
    return fig


def _graphic_shapes(data):
    shapes={m:s for m,_,_,s,_ in PDF_SCHEDULE};shapes.update({'ts1a':'L','bs3a':'L','w1a':'STRAIGHT','ts2a':'STRAIGHT','ts4a':'STRAIGHT','bs2a':'STRAIGHT','bs4a':'STRAIGHT'})
    rows=data['rows'];ncols=4;nrows=math.ceil(len(rows)/ncols)
    fig=_fig('REINFORCEMENT • BAR MARK AND SHAPE SCHEDULE','Live quantities • reference shape symbols, not verified bend geometry',size=(16,max(11,nrows*1.55)))
    ax=fig.add_axes([.035,.10,.93,.80]);ax.set_xlim(0,ncols);ax.set_ylim(0,nrows);ax.axis('off')
    for i,r in enumerate(rows):
        mark=r['Bar mark'];x=i%ncols;y=nrows-1-i//ncols
        ax.add_patch(Rectangle((x+.02,y+.04),.95,.89,facecolor='white',edgecolor='#c6d3df'))
        ax.text(x+.07,y+.82,mark,fontsize=11,fontweight='bold')
        ax.text(x+.07,y+.67,_live_label(r).split(': ',1)[1],fontsize=8)
        shape=shapes.get(mark)
        if _active_row(r) and shape:
            p=_bar_path(shape);ax.plot([x+.15+a*.65 for a,b in p],[y+.25+b*.34 for a,b in p],color='#0d6c78',lw=2)
        elif _active_row(r):ax.text(x+.07,y+.43,'Shape not confirmed',fontsize=8)
        else:ax.text(x+.07,y+.43,'Pending' if r['Weight (kg)'] is None else 'Inactive',fontsize=8)
        count=r['Bar count'];cut=r['Cut length (mm)']
        ax.text(x+.07,y+.13, f'Count: {count if count is not None else "—"} | Cut: {cut if cut is not None else "—"} mm',fontsize=7.5)
        if mark=='bs6': ax.text(x+.07,y+.04,'Shear arrangement: review',fontsize=7)
    return fig


def _graphic_corners(inputs,data):
    by={r['Bar mark']:r for r in data['rows']}
    fig=_fig('REINFORCEMENT • CORNER AND SHEAR DETAILS','Live mark annotations • schematic geometry only; no inferred anchorage or shear-zone placement',size=(16,10))
    w=inputs.clear_width_m+2*inputs.wall_thickness_m;h=inputs.clear_height_m+inputs.bottom_slab_thickness_m+inputs.top_slab_thickness_m
    for j,(title,marks) in enumerate([('Top corner',('ts1','ts3','w1','w2','h1')),('Bottom corner',('bs1','bs3','w1','w2','h2'))]):
        ax=fig.add_axes([.06+j*.48,.46,.40,.38]);_shape(ax,(0,0),(w,h),inputs)
        for mark,pts in _live_face_paths(inputs,data):
            if mark in marks:ax.plot(*zip(*pts),color='#0d9488',lw=2)
        extent=min(w*.45,h*.45);ax.set_xlim(-.03,extent);ax.set_ylim((h-extent,h+.03) if j==0 else (-.03,extent));ax.set_aspect('equal');ax.axis('off');ax.set_title(title)
        fig.text(.06+j*.48,.42,'\n'.join(_row_note(by.get(m),m) for m in marks if not m.startswith('h') or inputs.haunch_count),fontsize=9,va='top')
    shear=[r for r in data['rows'] if r['Bar mark'].startswith('bs6')]
    fig.text(.06,.20,'SHEAR LINKS / LAP DETAILS',fontsize=12,fontweight='bold')
    fig.text(.06,.17,'\n'.join(_row_note(r,r['Bar mark']) for r in shear[:4])+'\nShear placement, bends and laps require approved detailing.',fontsize=9,va='top')
    return fig


_live_figures_stage6 = live_figures

def live_figures(inputs,data):
    figures=_live_figures_stage6(inputs,data)
    replacements={'Top and bottom slab plans':lambda:_graphic_faces(inputs,data), 'Side wall elevations':lambda:_graphic_faces(inputs,data,True),'Bar mark and shape schedule':lambda:_graphic_shapes(data),'Corner and lap reference details':lambda:_graphic_corners(inputs,data)}
    for name,fn in replacements.items():
        plt.close(figures[name]);figures[name]=fn()
    for fig in figures.values():
        fig.text(.98,.02,'SCHEMATIC REVIEW — NOT FOR CONSTRUCTION' + (' | BBS PARTIAL / PROVISIONAL' if not data['quantity_coverage_complete'] else ''),ha='right',fontsize=8,color='#9b341c')
    return figures


def live_reinforcement_dxf(inputs, data, path):
    """Five native model-space review panels; mm units, no invented placing detail."""
    import ezdxf
    from ezdxf import zoom
    inputs.validate()
    doc = ezdxf.new('R2010'); doc.units = 4
    m = doc.modelspace()
    for name, color in [('CONCRETE',7),('MAIN',3),('DISTRIBUTION',4),('DIMENSIONS',2),('TEXT',7),('REVIEW',7),('FRAME',8)]:
        doc.layers.new(name,dxfattribs={'color':color})
    W=(inputs.clear_width_m+2*inputs.wall_thickness_m)*1000
    H=(inputs.clear_height_m+inputs.top_slab_thickness_m+inputs.bottom_slab_thickness_m)*1000
    L=inputs.length_m*1000; t=inputs.wall_thickness_m*1000; b=inputs.bottom_slab_thickness_m*1000
    top=inputs.top_slab_thickness_m*1000; hw=inputs.haunch_width_m*1000 if inputs.haunch_count else 0
    hh=inputs.haunch_height_m*1000 if inputs.haunch_count else 0
    rows=data['rows']; by={r['Bar mark']:r for r in rows}
    def poly(p,layer='CONCRETE',close=False):
        m.add_lwpolyline(p,close=close,dxfattribs={'layer':layer})
    def line(a,b,layer='DIMENSIONS'): m.add_line(a,b,dxfattribs={'layer':layer})
    def rect(x,y,w,h,layer='FRAME'): poly([(x,y),(x+w,y),(x+w,y+h),(x,y+h)],layer,True)
    def text(x,y,s,h=110,layer='TEXT'):
        m.add_text(str(s).replace('—','-').replace('×','x'),dxfattribs={'insert':(x,y),'height':h,'layer':layer})
    def fmt(v): return '-' if v is None else f'{v:g}'
    def label(mark):
        r=by.get(mark)
        if not r:return mark+' | not mapped'
        return mark+' | '+('PENDING' if r['Weight (kg)'] is None else 'INACTIVE' if not _active_row(r) else f"D{fmt(r['Dia (mm)'])} @ {fmt(r['Spacing (mm)'])}")
    def dim(x,y,a,b,vertical=False):
        if vertical:
            line((x,y),(x,y+b)); line((x-70,y),(x+70,y));line((x-70,y+b),(x+70,y+b));text(x+100,y+b/2,a,95,'DIMENSIONS')
        else:
            line((x,y),(x+b,y));line((x,y-70),(x,y+70));line((x+b,y-70),(x+b,y+70));text(x+b/2-200,y+100,a,95,'DIMENSIONS')
    def section(x,y,scale=1):
        def p(x0,y0):return (x+x0*scale,y+y0*scale)
        poly([p(0,0),p(W,0),p(W,H),p(0,H)],close=True)
        z=H-top
        poly([p(t+hw,b),p(W-t-hw,b),p(W-t,b+hh),p(W-t,z-hh),p(W-t-hw,z),p(t+hw,z),p(t,z-hh),p(t,b+hh)],close=True)
        for mark,pts in _live_face_paths(inputs,data):poly([p(a*1000,c*1000) for a,c in pts],'MAIN')
    panelw=max(L+2400, W*2+4000, 16000); panelh=max(2*W+3600,2*H+3600,13000)
    def panel(x,y,title):
        rect(x,y,panelw,panelh);text(x+300,y+panelh-350,title,210)
        text(x+300,y+180,'REVIEW ONLY - NOT FOR CONSTRUCTION | Dimensions in mm; nominal layouts',110,'REVIEW')
    # Panel 1: cross-section, dimension strings and live marks.
    panel(0,0,'01  TYPICAL BOX SECTION')
    x=1100;y=1800;section(x,y)
    dim(x,y-400,f'{W:g}',W);dim(x+t,y+b+inputs.clear_height_m*400,f'{inputs.clear_width_m*1000:g}',inputs.clear_width_m*1000)
    dim(x-450,y,f'{H:g}',H,True);dim(x+W+400,y+b,f'{inputs.clear_height_m*1000:g}',inputs.clear_height_m*1000,True)
    text(x,y+H+500,f'Wall {t:g} | Top {top:g} | Bottom {b:g} | Haunch {hw:g} x {hh:g}',125)
    for j,mark in enumerate(['ts1','ts3','bs1','bs3','w1','w2','h1','h2','bs6']):text(x+W+1500,y+H-j*260,label(mark),130,'REVIEW' if 'PENDING' in label(mark) else 'TEXT')
    # Plans use nominal spacing indicators, not actual counted placement.
    def grid(x,y,height,main,dist,cover):
        rect(x,y,L,height,'CONCRETE')
        for mark,vertical in [(main,True),(dist,False)]:
            r=by.get(mark)
            if not _active_row(r) or not (r.get('Spacing (mm)') or 0)>0:continue
            span=L if vertical else height; off=cover+r['Dia (mm)']/2; s=r['Spacing (mm)']
            n=max(0,math.floor((span-2*off)/s)+1)
            for i in range(0,n,max(1,math.ceil(n/350))):
                pos=off+i*s
                if vertical:line((x+pos,y),(x+pos,y+height),'MAIN')
                else:line((x,y+pos),(x+L,y+pos),'DISTRIBUTION')
        dim(x,y-250,f'{L:g}',L);dim(x-260,y,f'{height:g}',height,True)
        text(x,y+height+160,label(main)+'   /   '+label(dist),120)
    # Four slab faces, two per panel row, arranged vertically in two adjacent panels.
    for j,(title, pairs,height) in enumerate([
        ('02  TOP SLAB PLANS',[('ts1','ts2','top_slab_top_cover_mm'),('ts3','ts4','top_slab_bottom_cover_mm')],W),
        ('03  BOTTOM SLAB PLANS',[('bs1','bs2','bottom_slab_cover_mm'),('bs3','bs4','bottom_slab_cover_mm')],W),
        ('04  SIDE WALL ELEVATIONS',[('w1','w3','side_wall_outer_cover_mm'),('w2','w4','side_wall_inner_cover_mm')],inputs.clear_height_m*1000)]):
        px=(j+1)* (panelw+600);panel(px,0,title)
        for k,(a,d,c) in enumerate(pairs):grid(px+1000,1100+k*(height+1000),height,a,d,float(data['covers'][c]))
        text(px+400,panelh-720,'Nominal face grids only; edge, extra and support-zone bars refer to schedule.',120,'REVIEW')
    # Schedule panel: one row per live bar mark; shape symbols are schematic.
    px=0;py=panelh+600;panel(px,py,'05  LIVE BAR SCHEDULE AND CORNER REVIEW')
    shapes={k:s for k,_,_,s,_ in PDF_SCHEDULE};shapes.update({'ts1a':'L','bs3a':'L','ts2a':'STRAIGHT','ts4a':'STRAIGHT','bs2a':'STRAIGHT','bs4a':'STRAIGHT'})
    cols=[300,1200,3400,4300,5400,6500,7600,8800,10100,11400,12700,14000]
    heads=['Mark','Shape','Dia','Spacing','M1','M2','M3','Cut','Count','Weight kg','Status']
    sy=py+panelh-1050; rh=min(320,(panelh-1700)/(len(rows)+1))
    for j,hdr in enumerate(heads):text(px+cols[j]+70,sy,hdr,110)
    for i,r in enumerate(rows):
        yy=sy-(i+1)*rh;mark=r['Bar mark'];line((px+cols[0],yy-55),(px+cols[-1],yy-55),'FRAME');text(px+cols[0]+70,yy,mark,105)
        if _active_row(r) and mark in shapes:poly([(px+cols[1]+100+a*1600,yy-35+c*180) for a,c in _bar_path(shapes[mark])],'MAIN')
        vals=[fmt(r.get(k)) for k in ('Dia (mm)','Spacing (mm)','M1 (mm)','M2 (mm)','M3 (mm)','Cut length (mm)','Bar count','Weight (kg)')]
        for j,v in enumerate(vals,2):text(px+cols[j]+70,yy,v,105)
        status='PENDING' if r['Weight (kg)'] is None else 'INACTIVE' if not _active_row(r) else 'REVIEW' if mark=='bs6' else 'Nominal'
        text(px+cols[10]+70,yy,status,100,'REVIEW' if status in ('PENDING','REVIEW') else 'TEXT')
    px=panelw+600;panel(px,py,'06  CORNER AND SHEAR DETAIL REVIEW')
    section(px+1000,py+1400)
    for i,mark in enumerate(['ts1','ts3','w1','w2','h1','bs1','bs3','h2','bs6']):text(px+W+1800,py+H+1400-i*250,label(mark),120)
    text(px+600,py+panelh-900,'Shear-zone extent, hooks, anchorage, lap locations and h3 require approved detailing.',125,'REVIEW')
    text(px+600,py+panelh-1250,'Quantity coverage: '+('complete nominal' if data['quantity_coverage_complete'] else 'partial / provisional'),130,'REVIEW')
    zoom.extents(m)
    doc.saveas(str(path));return Path(path)


def live_reinforcement_dxf(inputs, data, path, *, presentation=False):
    """Full-size review model and separate A2 layout. No reference image entities."""
    import ezdxf
    from ezdxf import bbox
    from ezdxf.enums import TextEntityAlignment
    inputs.validate()
    if not inputs.supports_single_cell_model:
        raise ValueError('Single-cell model required')
    doc=ezdxf.new('R2010'); doc.units=4
    m=doc.modelspace()
    for name,color in [('CONCRETE',7),('MAIN',3),('DISTRIBUTION',4),('DIMENSIONS',2),('TEXT',7),('REVIEW',7),('SHEET',7)]:
        doc.layers.new(name,dxfattribs={'color':color})
    W=1000*(inputs.clear_width_m+2*inputs.wall_thickness_m); H=1000*(inputs.clear_height_m+inputs.top_slab_thickness_m+inputs.bottom_slab_thickness_m)
    L=1000*inputs.length_m; w=1000*inputs.wall_thickness_m; b=1000*inputs.bottom_slab_thickness_m; t=1000*inputs.top_slab_thickness_m
    hw=1000*inputs.haunch_width_m if inputs.haunch_count else 0; hh=1000*inputs.haunch_height_m if inputs.haunch_count else 0
    rows=data['rows']; by={r['Bar mark']:r for r in rows}; covers=data['covers']
    def poly(p,pts,layer='CONCRETE',close=False): return p.add_lwpolyline(pts,close=close,dxfattribs={'layer':layer})
    def rect(p,x,y,a,c,layer='CONCRETE'): return poly(p,[(x,y),(x+a,y),(x+a,y+c),(x,y+c)],layer,True)
    def line(p,a,z,layer='TEXT'): return p.add_line(a,z,dxfattribs={'layer':layer})
    def text(p,x,y,s,h=100,layer='TEXT',center=False):
        e=p.add_text(str(s),dxfattribs={'height':h,'layer':layer}); e.set_placement((x,y),align=TextEntityAlignment.MIDDLE_CENTER if center else TextEntityAlignment.LEFT);return e
    def dim(a,z,base,angle=0):
        q=m.add_linear_dim(base=base,p1=a,p2=z,angle=angle,dimstyle='Standard',override={'dimtxt':90,'dimasz':55,'dimdec':0},dxfattribs={'layer':'DIMENSIONS'});q.render()
    def fmt(v): return '-' if v is None else f'{v:g}'
    def label(mark):
        r=by.get(mark,{});return mark+' '+(f"D{fmt(r.get('Dia (mm)'))} @ {fmt(r.get('Spacing (mm)'))}" if _active_row(r) else 'PENDING / INACTIVE')
    paths=_live_face_paths(inputs,data)
    panels=[]
    column = max(W + 2600, 6500)
    plan_y = -2*W - 5200
    corner_x = max(column + L, 2*L + 2600) + 4200
    def capture(start,center,size,title):
        entities=list(m)[start:]
        ext=bbox.extents(entities)
        prefix=f'V{len(panels)+1:02d}_'
        layers=set()
        for ent in entities:
            old=ent.dxf.layer; new=prefix+old
            if new not in doc.layers:
                original=doc.layers.get(old)
                doc.layers.new(new,dxfattribs={'color':original.dxf.color,'linetype':original.dxf.linetype})
            ent.dxf.layer=new; layers.add(new)
        panels.append((ext,center,size,title,layers))
    # Reference sheet 3: separate bar paths, section indicators and callouts.
    # Sharp vertices represent nominal topology, not approved bend radii.
    start=len(m);rect(m,0,0,W,H)
    poly(m,[(w+hw,b),(W-w-hw,b),(W-w,b+hh),(W-w,H-t-hh),(W-w-hw,H-t),(w+hw,H-t),(w,H-t-hh),(w,b+hh)],close=True)
    def offset(mark,key):
        return float(covers[key])+(by.get(mark,{}).get('Dia (mm)') or 0)/2
    def active(mark):return _active_row(by.get(mark))
    targets={}
    section_runs=[]
    def run(mark,pts):
        if not active(mark):return
        poly(m,pts,'MAIN');section_runs.append((mark,pts));targets.setdefault(mark,pts[len(pts)//2])
    def leg(mark,key):return float(by.get(mark,{}).get(key) or 0)
    wo=offset('w1','side_wall_outer_cover_mm');wi=offset('w2','side_wall_inner_cover_mm')
    for mark,key,y,sign in [('ts1','top_slab_top_cover_mm',H,-1),('bs3','bottom_slab_cover_mm',0,1)]:
        c=offset(mark,key); yy=y+sign*c; x=offset(mark,'side_wall_outer_cover_mm')
        # U-shaped end legs drawn only when nominal length fits the wall envelope.
        l1,l3=leg(mark,'M1 (mm)'),leg(mark,'M3 (mm)')
        if 0<x<w and 0<l1<H-c and 0<l3<H-c:
            run(mark,[(x,yy+sign*l1),(x,yy),(W-x,yy),(W-x,yy+sign*l3)])
        else:run(mark,[(w,yy),(W-w,yy)])
    for mark,key,y,sign in [('ts3','top_slab_bottom_cover_mm',H-t,1),('bs1','bottom_slab_cover_mm',b,-1)]:
        c=offset(mark,key);yy=y+sign*c
        run(mark,[(w+hw,yy),(W-w-hw,yy)])
    for mark,x in [('w1',wo),('w2',w-wi)]:
        if 0<x<w:
            for xx in (x,W-x):run(mark,[(xx,b),(xx,H-t)])
    for mark,y in [('ts6',H-t+offset('ts6','top_slab_bottom_cover_mm')),('bs5',b-offset('bs5','bottom_slab_cover_mm'))]:
        cut=leg(mark,'M2 (mm)')
        if 0<cut<=W-2*w:run(mark,[(W/2-cut/2,y),(W/2+cut/2,y)])
    # Representative diagonal haunch bars: offset from the concrete face;
    # legs and bend radii are deliberately not inferred from the schedule.
    if hw>0 and hh>0:
        for mk,base,sgn in [('h1',H-t,-1),('h2',b,1)]:
            if active(mk):
                for side in (1,-1):
                    xx=w if side==1 else W-w
                    c=min(offset(mk,'side_wall_inner_cover_mm'),w*.45,(t if mk=='h1' else b)*.45)
                    run(mk,[(xx-side*c,base+sgn*hh),(xx+side*hw,base-sgn*c)])
    # Distribution bars are seen in cross-section as circles. Symbol positions
    # are representative, not a verified set-out or count.
    def dots(mark,a,z,n=5):
        if not active(mark):return
        for j in range(n):
            f=(j+.5)/n;pt=(a[0]+f*(z[0]-a[0]),a[1]+f*(z[1]-a[1]))
            m.add_circle(pt,max(12,by[mark]['Dia (mm)']/2),dxfattribs={'layer':'DISTRIBUTION'})
        targets[mark]=((a[0]+z[0])/2,(a[1]+z[1])/2)
    for mark,key,y,sign in [('ts2','top_slab_top_cover_mm',H,-1),('ts4','top_slab_bottom_cover_mm',H-t,1),('bs4','bottom_slab_cover_mm',0,1),('bs2','bottom_slab_cover_mm',b,-1)]:
        yy=y+sign*offset(mark,key);dots(mark,(w+hw,yy),(W-w-hw,yy))
    for mark,key,inner in [('w3','side_wall_outer_cover_mm',False),('w4','side_wall_inner_cover_mm',True)]:
        c=offset(mark,key);xx=w-c if inner else c
        for x in (xx,W-xx):dots(mark,(x,b+hh),(x,H-t-hh))
    # Corner detail identifiers and A-A cutting-line symbols.
    for yy,name in [(H-t-hh,'X'),(b+hh,'Y')]:
        line(m,(w,yy),(w-300,yy),'TEXT');text(m,w-440,yy,name,85)
    for yy,sgn in [(H+170,1),(-170,-1)]:
        line(m,(W*.5,yy),(W*.5,yy+sgn*160),'TEXT');text(m,W*.5+100,yy+sgn*160,'A',100)
    # Keep labels in external lanes, rather than across the opening.
    left=('ts1','ts2','ts4','w1','w3','bs4','bs3')
    right=('ts3','ts6','w2','w4','bs1','bs2','bs5')
    for marks,side in [(left,-1),(right,1)]:
        for j,mark in enumerate(marks):
            yy=H-150-j*(H-300)/6;xx=-1050 if side<0 else W+220
            pt=targets.get(mark)
            if pt:
                # For paired wall marks choose the nearest wall face.
                candidates=[q for mk,ps in section_runs if mk==mark for q in ps]
                if candidates: pt=min(candidates,key=lambda q: abs(q[0]-(0 if side<0 else W))+.12*abs(q[1]-yy))
                elif side>0 and mark.startswith('w'):pt=(W-pt[0],pt[1])
                end=(-120 if side<0 else W+120,yy)
                line(m,pt,end);line(m,end,(xx+800 if side<0 else xx,yy))
            text(m,xx,yy+40,label(mark),70,'TEXT')
    dim((0,0),(W,0),(0,-600));dim((0,0),(0,H),(-1350,0),90)
    dim((w,b),(W-w,b),(w,b+0.42*(H-b-t)));dim((W-w,b),(W-w,H-t),(W*.64,b),90)
    text(m,W/2,H+520,'REINFORCEMENT DETAILS OF BOX',110,center=True)
    # removed review sentence
    capture(start,(96,303),(165,170),'REINFORCEMENT DETAILS OF BOX')
    # Section A-A: representative longitudinal runs, not a counted grid.
    start=len(m); x=column
    full_L=L
    if presentation: L=min(L,H*1.10)
    if 'SECTION_INNER_DASH' not in doc.linetypes:
        doc.linetypes.new('SECTION_INNER_DASH',dxfattribs={'description':'Non-earth face','pattern':[300,200,-100]})
    doc.layers.new('SECTION_INNER',dxfattribs={'color':7,'linetype':'SECTION_INNER_DASH'})
    rect(m,x,0,L,H)
    for yy in (b,H-t): line(m,(x,yy),(x+L,yy),'CONCRETE')
    # The longitudinal view shows representative lines and transverse symbols.
    # No physical bar count, lap position or construction joint is inferred.
    def sec_run(mark, a, end, inner=False):
        if not active(mark): return
        layer='SECTION_INNER' if inner else 'MAIN'
        line(m,(x+a[0],a[1]),(x+end[0],end[1]),layer)
    for mark,yy,inner in [('ts2',H-offset('ts2','top_slab_top_cover_mm'),False),('ts4',H-t+offset('ts4','top_slab_bottom_cover_mm'),True),('bs4',offset('bs4','bottom_slab_cover_mm'),False),('bs2',b-offset('bs2','bottom_slab_cover_mm'),True)]:
        c=offset(mark,'side_wall_outer_cover_mm');sec_run(mark,(c,yy),(L-c,yy),inner)
        text(m,x+L*.72,yy+95,label(mark),80,'TEXT')
    for mark,inner,fracs in [('w3',False,(.16,.36,.60,.84)),('w4',True,(.25,.48,.72))]:
        c=offset(mark,'side_wall_inner_cover_mm' if inner else 'side_wall_outer_cover_mm')
        for f in fracs:
            yy=b+(H-b-t)*f;sec_run(mark,(c,yy),(L-c,yy),inner)
    for mark,inner,frac in [('w1',False,.36),('w2',True,.58)]:
        c=offset(mark,'side_wall_inner_cover_mm' if inner else 'side_wall_outer_cover_mm'); xx=L*frac
        sec_run(mark,(xx,b+c),(xx,H-t-c),inner)
        yy=H*.53 if inner else H*.34
        if active(mark):
            m.add_circle((x+xx,yy),36,dxfattribs={'layer':'TEXT'});line(m,(x+xx,yy),(x+xx+250,yy+160))
        text(m,x+xx+270,yy+170,label(mark),85,'TEXT')
    for mark,frac,yy in [('ts1',.23,H-100),('ts3',.47,H-t+65),('bs3',.28,100),('bs1',.67,b-65)]:
        if active(mark):
            m.add_circle((x+L*frac,yy),max(12,by[mark]['Dia (mm)']/2),dxfattribs={'layer':'DISTRIBUTION'})
        ey=H+440 if mark.startswith('ts') else -430
        line(m,(x+L*frac,yy),(x+L*frac,ey-45 if ey>0 else ey+100))
        text(m,x+L*frac+60,ey,label(mark),80,'TEXT')
    # Detail Z reference callout; exact extent and joint position unverified.
    zz=min(L*.12,H*.3);rect(m,x-100,-100,zz+100,b+hh+220,'REVIEW')
    text(m,x-100,-260,'Z - reference detail',85,'REVIEW')
    if not presentation: dim((x,0),(x+L,0),(x,-750))
    else: text(m,x+L/2,-680,f"L = {full_L:g} mm; abbreviated view / NTS",110,center=True)
    dim((x,0),(x,H),(x-420,0),90)
    text(m,x+L/2,H+780,'SECTION A-A',150,center=True)
    # removed review sentence
    capture(start,(267,303),(170,170),'SECTION A-A')
    L=full_L
    if presentation: L=min(L,W*1.48)
    # Reference Sheet 3 uses a combined face plan, not separate dense grids.
    # Lines below identify representative runs, not a fabrication set-out.
    if 'FACE_DASH' not in doc.linetypes:
        doc.linetypes.new('FACE_DASH', dxfattribs={'description':'Inner face review','pattern':[150,100,-50]})
    for layer, colour, lt in [('SLAB_EARTH',3,'Continuous'),('SLAB_INNER',4,'FACE_DASH'),('SLAB_HAUNCH',8,'FACE_DASH')]:
        doc.layers.new(layer,dxfattribs={'color':colour,'linetype':lt})
    def slab_plan(x,y,upper):
        rect(m,x,y,L,W)
        prefix='ts' if upper else 'bs'
        earth='SLAB_EARTH'; inner='SLAB_INNER'
        cearth=float(covers['top_slab_top_cover_mm' if upper else 'bottom_slab_cover_mm'])
        cinn=float(covers['top_slab_bottom_cover_mm' if upper else 'bottom_slab_cover_mm'])
        # L3 is the source BBS edge-zone rule. L1 is derived from live w1 only.
        l3=.15*(W-2*w)
        wr=by.get('w1',{}); l1=max(.3*(W-2*w),61*wr['Dia (mm)']) if _active_row(wr) else None
        for yy in (w,W-w):line(m,(x,y+yy),(x+L,y+yy),'SLAB_HAUNCH')
        if inputs.haunch_count:
            for yy in (w+hw,W-w-hw):line(m,(x,y+yy),(x+L,y+yy),'SLAB_HAUNCH')
        # Representative positions chosen for legibility, like the reference plan.
        # No spacing/count is inferred from these display positions.
        def run(mark, a, z, layer, cover):
            r=by.get(mark,{})
            if not _active_row(r):return None
            # Coordinates already refer to nominal covered face envelope.
            if min(a[0],z[0])<0 or max(a[0],z[0])>L or min(a[1],z[1])<0 or max(a[1],z[1])>W:return None
            return line(m,(x+a[0],y+a[1]),(x+z[0],y+z[1]),layer)
        def off(mark,cover):return cover+(by.get(mark,{}).get('Dia (mm)') or 0)/2
        main=[('ts1',earth,cearth,.36),('ts3',inner,cinn,.57),('ts6',inner,cinn,.66)] if upper else [('bs3',earth,cearth,.36),('bs1',inner,cinn,.57),('bs5',inner,cinn,.66)]
        for mark,layer,c,frac in main:
            z=off(mark,c); short=mark in ('ts6','bs5'); lo=w+l3 if short else z; hi=W-lo
            if hi>lo:run(mark,(L*frac,lo),(L*frac,hi),layer,c)
        for mark,layer,c,frac in ([(prefix+'2',earth,cearth,.40),(prefix+'4',inner,cinn,.53),(prefix+'2a',earth,cearth,.10),(prefix+'4a',inner,cinn,.90)] if upper else [('bs2',inner,cinn,.40),('bs4',earth,cearth,.53),('bs2a',inner,cinn,.10),('bs4a',earth,cearth,.90)]):
            z=off(mark,c);run(mark,(z,W*frac),(L-z,W*frac),layer,c)
        # Reference corner bars and wall extensions: representative only.
        corner='ts5' if upper else 'w5'; z=off(corner,cearth)
        for yy in (z,W-z):run(corner,(z,yy),(L-z,yy),earth,cearth)
        if l1 is not None:
            z=off('w1',cearth); xx=L*.22
            if z+l1<W/2:
                run('w1',(xx,z),(xx,z+l1),earth,cearth);run('w1',(xx,W-z),(xx,W-z-l1),earth,cearth)
                dim((x+xx,y+z),(x+xx,y+z+l1),(x+xx+250,y+z),90)
        if W>2*(w+l3):dim((x+L*.80,y+w),(x+L*.80,y+w+l3),(x+L*.80+250,y+w),90)
        if not presentation: dim((x,y),(x+L,y),(x,y-350))
        else: text(m,x+L/2,y-350,f"L = {full_L:g} mm; abbreviated / NTS",100,center=True)
        dim((x,y),(x,y+W),(x-350,y),90)
        title='TOP SLAB' if upper else 'BOTTOM SLAB'
        text(m,x,y+W+1050,title+' - COMBINED FACE PLAN / REPRESENTATIVE RUNS',140)
        text(m,x,y+W+800,'SOLID: EARTH FACE   DASHED: NON-EARTH FACE   DIMS IN mm',100)
        # Label each representative line locally; do not imply physical set-out.
        # Separate callouts by face and route outside the outline where possible.
        def call(mark, tx, ty, ex, ey):
            if _active_row(by.get(mark)):
                line(m,(x+tx,y+ty),(x+ex,y+ty),'TEXT')
                line(m,(x+ex,y+ty),(x+ex,y+ey-35),'TEXT')
            text(m,x+ex+45,y+ey,label(mark),95,'TEXT')
        for j,(mark,_,_,frac) in enumerate(main):
            call(mark,L*frac,W*.70,L*frac,W+180+190*(j%2))
        dist_marks=(('ts2','ts4','ts2a','ts4a') if upper else ('bs2','bs4','bs2a','bs4a'))
        for mark,frac in zip(dist_marks,(.40,.53,.10,.90)):
            tx=L*.08; yy=W*frac
            if _active_row(by.get(mark)):
                line(m,(x+tx,y+yy),(x+tx,y+yy+100),'TEXT')
            text(m,x+tx+40,y+yy+110,label(mark),95,'TEXT')
        text(m,x+L*.77,y+W+180,label(corner),95)
        text(m,x+L*.22,y+W+180,label('w1'),95)
        extras=('ts1a','h1') if upper else ('bs3a','bs6','h2')
        for j,mark in enumerate(extras):
            if _active_row(by.get(mark)): text(m,x+L*.32*j,y-650,label(mark),100,'TEXT')
        # removed review sentence
    for i,upper in enumerate((True,False)):
        start=len(m);x=i*(L+2600);slab_plan(x,plan_y+W/2,upper)
        capture(start,(96+171*i,139),(165,132),('TOP' if upper else 'BOTTOM')+' SLAB REINFORCEMENT PLAN')
    L=full_L
    # Full-size corner envelope; nominal paths are clipped from the live section.
    # Reference shape diagrams are kept separate from physical bar placement.
    for i,upper in enumerate((True,False)):
        start=len(m); x=corner_x; slab=t if upper else b
        run=min(W/2,w+max(hw,600)); rise=min(H/2,slab+max(hh,600))
        y=H+1800-i*(H+2400)
        pts=[(0,0),(run,0),(run,slab),(w+hw,slab),(w,slab+hh),(w,rise),(0,rise)]
        if upper:pts=[(a,rise-z) for a,z in pts]
        poly(m,[(x+a,y+z) for a,z in pts],close=True)
        title='X' if upper else 'Y'
        text(m,x,y+rise+300,f'DETAIL {title} - NOMINAL CORNER REVIEW',100)
        ymin=H-rise if upper else 0; ymax=ymin+rise
        clipped=[]
        for mark, (aa,zz) in [(mk,(p0,p1)) for mk,pts0 in section_runs for p0,p1 in zip(pts0,pts0[1:])]:
            # Clip arbitrary segments, including diagonal haunch runs, to detail envelope.
            dx,dy=zz[0]-aa[0],zz[1]-aa[1]; lo,hi=0.,1.
            for u,v in ((-dx,aa[0]),(dx,run-aa[0]),(-dy,aa[1]-ymin),(dy,ymax-aa[1])):
                if abs(u)<1e-9:
                    if v<0:lo,hi=1.,0.;break
                elif u<0:lo=max(lo,v/u)
                else:hi=min(hi,v/u)
            if hi<=lo:continue
            pp=[(x+aa[0]+f*dx,y+aa[1]+f*dy-ymin) for f in (lo,hi)]
            poly(m,pp,'MAIN');clipped.append((mark,pp))
        # One leader per mark, to the nominal path nearest the corner.
        unique={}
        for mk,pp in clipped: unique.setdefault(mk,pp)
        for j,(mark,pp) in enumerate(sorted(unique.items(),key=lambda item:item[1][-1][1],reverse=True)):
            end=(x+run+450,y+rise-160*j)
            line(m,pp[-1],(end[0]-140,end[1]));line(m,(end[0]-140,end[1]),end)
            text(m,end[0]+40,end[1],label(mark),80)
        dim((x,y),(x+w,y),(x,y-220))
        a=y+rise-slab if upper else y
        dim((x+run,a),(x+run,a+slab),(x+run+180,a),90)
        # Distribution symbols in the two slab faces and wall faces, as in X/Y.
        for mk,xx,yy in ([('ts2',run*.70,rise-offset('ts2','top_slab_top_cover_mm')),('ts4',run*.70,rise-t+offset('ts4','top_slab_bottom_cover_mm'))] if upper else [('bs2',run*.70,b-offset('bs2','bottom_slab_cover_mm')),('bs4',run*.70,offset('bs4','bottom_slab_cover_mm'))]):
            if active(mk) and 0<yy<rise:
                m.add_circle((x+xx,y+yy),max(12,by[mk]['Dia (mm)']/2),dxfattribs={'layer':'DISTRIBUTION'})
                text(m,x+run+450,y+yy-65,mk,85)
        for mk,xx in [('w3',offset('w3','side_wall_outer_cover_mm')),('w4',w-offset('w4','side_wall_inner_cover_mm'))]:
            if active(mk) and 0<xx<w:
                for yy in (rise*.28,rise*.48):
                    m.add_circle((x+xx,y+yy),max(12,by[mk]['Dia (mm)']/2),dxfattribs={'layer':'DISTRIBUTION'})
        # removed review sentence
        capture(start,(397,343-100*i),(88,86),f'DETAIL {title}')
    # Z and lap zone: source topology only, deliberately symbolic coordinates.
    start=len(m); zx=corner_x; zy=-H-3300
    text(m,zx,zy+1250,'DETAIL Z - NTS',105)
    rect(m,zx,zy,2200,950)
    for yy in (150,800):
        line(m,(zx+100,zy+yy),(zx+2100,zy+yy),'MAIN')
        for xx in (200,600,1000,1400,1800):
            m.add_circle((zx+xx,zy+yy),30,dxfattribs={'layer':'DISTRIBUTION'})
    for a,z in [((150,150),(950,800)),((150,800),(950,150)),((150,150),(150,800))]:
        line(m,(zx+a[0],zy+a[1]),(zx+z[0],zy+z[1]),'MAIN')
    line(m,(zx+600,zy+650),(zx+1050,zy+1100));text(m,zx+1100,zy+1100,'D10 (10 nos.) - REF',85)
    line(m,(zx+800,zy+150),(zx+1100,zy-160));text(m,zx+1150,zy-160,'D10 @ 200 - REF',85)
    # removed review sentence
    capture(start,(399,218),(86,45),'DETAIL Z - NTS')
    start=len(m); ly=zy-2400
    text(m,zx,ly+650,'TRANSVERSE REINFORCEMENT IN LAP ZONE - NTS',105)
    # Source notation: three equal overlap zones, closer outer-third spacing.
    for j,zone in enumerate(('TENSION','COMPRESSION')):
        yy=ly-j*1450
        rect(m,zx,yy,2600,65,'MAIN');rect(m,zx+650,yy+170,2600,65,'MAIN')
        bounds=[650,1300,1950,2600]
        for xx in bounds:line(m,(zx+xx,yy-210),(zx+xx,yy+320),'DIMENSIONS')
        for k in range(3):
            a,z=bounds[k:k+2];mid=(a+z)/2
            text(m,zx+mid,yy+370,'St' if k==1 else '2St/3',85,center=True)
            line(m,(zx+a,yy-150),(zx+z,yy-150),'DIMENSIONS')
            lab='Lp/3' if j==0 or k==1 else 'Lp/3+4D'
            text(m,zx+mid,yy-310,lab,85,center=True)
        text(m,zx+1625,yy-520,zone+' ZONE',100,center=True)
    # removed review sentence
    capture(start,(399,116),(86,105),'LAP ZONE - NTS')
    # Model-space schedule: editable table, nominal shape symbols (not fabrication paths).
    start=len(m); sx=corner_x+max(W,4500)+2800; sy=H+2600; rh=330
    widths=[950,1600,800,1050,1000,1000,1000,1200,950,1200,1500]
    cx=[sx]
    for width in widths: cx.append(cx[-1]+width)
    text(m,sx,sy+350,'LIVE BBS SCHEDULE',150)
    headers=['Mark','Shape','Dia','Spacing','M1','M2','M3','Cut mm','Count','kg','Status']
    shapes={k:s for k,_,_,s,_ in PDF_SCHEDULE}; shapes.update({'ts1a':'L','bs3a':'L','ts2a':'STRAIGHT','ts4a':'STRAIGHT','bs2a':'STRAIGHT','bs4a':'STRAIGHT'})
    for j,hdr in enumerate(headers): text(m,cx[j]+55,sy-220,hdr,95)
    for i,r in enumerate(rows):
        yy=sy-(i+1)*rh; mark=r['Bar mark']; text(m,sx+55,yy-220,mark,95)
        if _active_row(r) and mark in shapes:
            poly(m,[(cx[1]+160+u*1100,yy-280+v*210) for u,v in _bar_path(shapes[mark])],'MAIN')
        vals=[fmt(r.get(key)) for key in ('Dia (mm)','Spacing (mm)','M1 (mm)','M2 (mm)','M3 (mm)','Cut length (mm)','Bar count','Weight (kg)')]
        for j,val in enumerate(vals,2):text(m,cx[j]+55,yy-220,val,90)
        text(m,cx[10]+55,yy-220,'PENDING' if r.get('Weight (kg)') is None else 'INACTIVE' if not _active_row(r) else 'OK',85,'TEXT')
    for xx in cx:line(m,(xx,sy),(xx,sy-(len(rows)+1)*rh),'SHEET')
    for i in range(len(rows)+2):line(m,(sx,sy-i*rh),(cx[-1],sy-i*rh),'SHEET')
    capture(start,(0,0),(1,1),'MODEL SCHEDULE')
    sheet=doc.layouts.new('REINFORCEMENT A2');sheet.page_setup(size=(594,420),margins=(0,0,0,0),units='mm')
    rect(sheet,8,8,578,404,'SHEET')
    # Paper-space layout follows source sheet 3; viewports are independently
    # fitted and isolated. Model-space coordinates are never scaled.
    layout={
        'REINFORCEMENT DETAILS OF BOX':(10,202,176,191),
        'SECTION A-A':(187,202,176,191),
        'TOP SLAB REINFORCEMENT PLAN':(10,58,176,142),
        'BOTTOM SLAB REINFORCEMENT PLAN':(187,58,176,142),
        'DETAIL X':(364,300,87,93),
        'DETAIL Y':(364,205,87,93),
        'DETAIL Z - NTS':(364,150,87,53),
        'LAP ZONE - NTS':(364,57,87,91),
    }
    all_layers=set().union(*(p[4] for p in panels))
    for ext,_,_,title,layers in panels:
        if title not in layout: continue
        x,y,wid,hei=layout[title]
        sw,sh=wid-3,hei-9; center=(x+wid/2,y+hei/2+1)
        mid=ext.center; vh=max(ext.size.y,ext.size.x*sh/sw,1)*1.035
        vp=sheet.add_viewport(center=center,size=(sw,sh),view_center_point=(mid.x,mid.y),view_height=vh,dxfattribs={'layer':'SHEET','flags':16384})
        vp.frozen_layers=sorted(all_layers-layers)
        actual_h=ext.size.y*sh/vh
        text(sheet,x+wid/2,max(y+3,center[1]-actual_h/2-4),title,2.5,center=True)
    text(sheet,14,400,'REINFORCEMENT DRAWING OF RCC BOX CULVERT',3.8)
    text(sheet,454,400,'LIVE BBS SCHEDULE',3)
    # Reference-style reinforcement schedule; retain all live marks and status.
    xedges=[453,470,498,515,544,565,584]
    heads=['Mark','Shape','Dia','Spacing','Nos','State']
    top=392; rh=7.1
    for j,hdr in enumerate(heads):text(sheet,xedges[j]+1,top-4.6,hdr,2.3)
    for i,r in enumerate(rows):
        yy=top-(i+1)*rh; mark=r['Bar mark']
        vals=[mark,'',fmt(r.get('Dia (mm)')),fmt(r.get('Spacing (mm)')),fmt(r.get('Bar count')),'PEND' if r.get('Weight (kg)') is None else 'INACT' if not _active_row(r) else 'REV']
        for j,v in enumerate(vals):
            if v:text(sheet,xedges[j]+1,yy-4.8,v,2.25)
        if mark in shapes:
            poly(sheet,[(xedges[1]+3+u*20,yy-6.1+v*5) for u,v in _bar_path(shapes[mark])],'TEXT')
    bottom=top-(len(rows)+1)*rh
    for xx in xedges:line(sheet,(xx,top),(xx,bottom),'SHEET')
    for i in range(len(rows)+2):line(sheet,(xedges[0],top-i*rh),(xedges[-1],top-i*rh),'SHEET')
    notes=['DRAWING NOTES','Solid: earth face; dashed: inner face.','Nominal shapes and representative runs.','Bends, laps and link positions unverified.','PEND = missing quantity; INACT = inactive.','REV = nominal detail for review.','h3 requires separate verification.','Use full-size export for measurements.']
    for j,note in enumerate(notes):text(sheet,454,bottom-9-j*6,note,2.3 if j==0 else 2.1,'TEXT')
    # removed review note
    rect(sheet,8,8,578,46,'SHEET')
    for xx in (112,280,454):line(sheet,(xx,8),(xx,54),'SHEET')
    text(sheet,14,44,'REVISION',2.8);text(sheet,14,33,'Generated CAD',2.6)
    text(sheet,118,44,'PROJECT / CULVERT',2.8);text(sheet,118,33,f'Clear opening: {inputs.clear_width_m:g} x {inputs.clear_height_m:g} m',2.8);text(sheet,118,19,f'Length: {inputs.length_m:g} m',2.8)
    text(sheet,286,44,'REINFORCEMENT DETAILS',3.2)
    text(sheet,460,44,'SHEET 3 OF 3',3.2);text(sheet,460,33,'A2 LANDSCAPE',2.7);text(sheet,460,19,'SEE FULL-SIZE MODEL',2.7)
    doc.layouts.set_active_layout(sheet.name)
    ext=bbox.extents(m);doc.set_modelspace_vport(height=max(ext.size.y,ext.size.x/1.34)*1.15,center=(ext.center.x,ext.center.y))
    doc.header["$EXTMIN"] = ext.extmin
    doc.header["$EXTMAX"] = ext.extmax
    m.dxf.extmin = ext.extmin
    m.dxf.extmax = ext.extmax
    doc.saveas(str(path));return Path(path)

# Preserve the full-size export, and expose the A2 review sheet in model space.
live_reinforcement_measured_dxf = live_reinforcement_dxf

def live_reinforcement_dxf(inputs, data, path):
    import ezdxf
    from ezdxf import bbox
    from tempfile import TemporaryDirectory
    from ezdxf.math import Matrix44
    with TemporaryDirectory() as tmp:
        source=Path(tmp)/'measured.dxf'
        live_reinforcement_measured_dxf(inputs,data,source,presentation=True)
        doc=ezdxf.readfile(source)
    model=doc.modelspace(); sheet=doc.layouts.get('REINFORCEMENT A2')
    original=list(model); copies=[]
    for vp in sheet.query('VIEWPORT'):
        if vp.dxf.id==1: continue
        frozen=set(vp.frozen_layers); matrix=vp.get_transformation_matrix()
        for ent in original:
            if ent.dxf.layer in frozen: continue
            # Dimensions are converted to their rendered editable primitives;
            # retain displayed full-size dimensions, not paper-scale lengths.
            parts=list(ent.virtual_entities()) if ent.dxftype()=='DIMENSION' else [ent]
            for part in parts:
                e=part.copy(); e.transform(matrix)
                if e.dxf.layer=='0': e.dxf.layer=ent.dxf.layer
                if e.dxf.is_supported('ltscale'): e.dxf.ltscale *= vp.get_scale()
                copies.append(e)
    for ent in sheet:
        if ent.dxftype()!='VIEWPORT': copies.append(ent.copy())
    for ent in original: model.delete_entity(ent)
    for ent in copies: model.add_entity(ent)
    # removed review footer
    # Remove obsolete viewports, which would refer to relocated geometry.
    doc.layouts.delete('REINFORCEMENT A2')
    ext=bbox.extents(model);doc.set_modelspace_vport(height=max(ext.size.y,ext.size.x/1.35)*1.1,center=(ext.center.x,ext.center.y))
    doc.header['$EXTMIN']=ext.extmin;doc.header['$EXTMAX']=ext.extmax
    model.dxf.extmin=ext.extmin;model.dxf.extmax=ext.extmax
    doc.saveas(str(path));return Path(path)
