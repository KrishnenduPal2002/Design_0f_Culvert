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
