"""Sheet 2 of 3: reference-based dimensioned RCC culvert detailed sections.

This creates an A2-style engineering REVIEW sheet based on the supplied
'General Arrangement Drawing' PDF page 2, including Sections B-B and C-C,
curtain-wall Types I/II, Shear Key X, and the U-type wing-wall reference.

Only the culvert box's actual geometry is parametric from CulvertInputs.
Curtain-wall sizes, apron/fill/slope and wing-wall detailing are *source sheet*
references, not recalculated structural designs. PDF/DXF output must not be
used as a construction/fabrication drawing without engineer validation.

DXF coordinates are A2 PAPER millimetres, not 1:1 world/site coordinates.
"""
from __future__ import annotations
from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path
from math import isfinite

from reportlab.pdfgen import canvas
from .inputs import CulvertInputs
from .general_arrangement import _Pen, PAGE_W, PAGE_H, _PAPER_TO_MM

# Palette is restrained to remain readable when printed grayscale.
DARK=(.12,.17,.22)
MID=(.38,.43,.48)
LIGHT=(.93,.95,.96)
SOIL=(.96,.96,.94)
BLUE=(.13,.34,.44)
ALERT=(.65,.20,.17)


@dataclass(frozen=True)
class Sheet2Inputs:
    chainage: str = "36+925"
    pcc_bedding_mm: int = 75
    earth_fill_slope_h_to_v: float = 2.0
    geo_textile_gsm: int = 150
    shear_key_gap_mm: int = 20
    shear_key_toe_mm: int = 300
    # source drawing reference values; not calculated from the box geometry
    curtain_i_height_mm: int = 2500
    curtain_i_base_mm: int = 1650
    curtain_i_pcc_width_mm: int = 1850
    curtain_ii_height_mm: int = 2000
    curtain_ii_base_mm: int = 1300
    curtain_ii_pcc_width_mm: int = 1500
    reference_status: str = "FOR ENGINEER REVIEW - NOT FOR CONSTRUCTION"

    def validate(self):
        if not self.chainage.strip() or len(self.chainage) > 30:
            raise ValueError("Drawing chainage must be nonblank, up to 30 characters")
        for attr, value in vars(self).items():
            if isinstance(value,(int,float)) and (not isfinite(value) or value <= 0):
                raise ValueError(f"{attr} must be positive and finite")
        if self.earth_fill_slope_h_to_v < 1:
            raise ValueError("Embankment horizontal:vertical ratio must be >= 1")
        return self


def _hatch(p, poly, step=16, kind="earth"):
    """Simple vector hatch clipped to polygon; intentionally schematic."""
    if not poly: return
    xs=[a for a,b in poly]; ys=[b for a,b in poly]
    x0,x1=min(xs),max(xs); y0,y1=min(ys),max(ys)
    def inside(x,y):
        c=False
        j=len(poly)-1
        for i in range(len(poly)):
            xi,yi=poly[i]; xj,yj=poly[j]
            if (yi>y)!=(yj>y) and x<(xj-xi)*(y-yi)/(yj-yi)+xi:
                c=not c
            j=i
        return c
    for yi in range(int(y0)+7,int(y1)-3,step):
        for xi in range(int(x0)+7,int(x1)-3,step):
            if inside(xi,yi) and inside(xi+4,yi+4):
                p.line(xi-3,yi-3,xi+4,yi+4,lw=.24,color=MID,layer="FILL_HATCH")
    

def _polygon(p, pts, fill=LIGHT, layer="STRUCTURE", lw=1.1, hatch=False):
    p.path(pts,closed=True,fill=fill,lw=lw,layer=layer)
    if hatch: _hatch(p,pts,step=14)


def _hole(p, x,y,w,h, hd_x,hd_y):
    # Opening corners cut by four triangular haunches.
    dx=max(0,min(w*.22,hd_x));dy=max(0,min(h*.22,hd_y))
    hole=[(x+dx,y),(x+w-dx,y),(x+w,y+dy),(x+w,y+h-dy),
          (x+w-dx,y+h),(x+dx,y+h),(x,y+h-dy),(x,y+dy)]
    _polygon(p,hole,fill=(1,1,1),layer="OPENING",lw=1.25)


def _box(p, inp, x,y,s=65):
    """Draw dimensionally responsive section; return outer rect and opening geometry."""
    ew=inp.clear_width_m+2*inp.wall_thickness_m
    eh=inp.clear_height_m+inp.top_slab_thickness_m+inp.bottom_slab_thickness_m
    w=ew*s; h=eh*s
    p.rect(x,y,w,h,fill=(.90,.93,.95),lw=1.3,layer="RCC_BOX")
    xx=x+inp.wall_thickness_m*s; yy=y+inp.bottom_slab_thickness_m*s
    ww=inp.clear_width_m*s; hh=inp.clear_height_m*s
    hw=(inp.haunch_width_m*s if inp.haunch_count==4 else 0)
    hd=(inp.haunch_height_m*s if inp.haunch_count==4 else 0)
    _hole(p,xx,yy,ww,hh,hw,hd)
    # centerline and weep-hole reference symbols
    p.line(x-22,y+h*.44,x+w+22,y+h*.44,lw=.4,color=MID,layer="CENTERLINE",dash=(5,4))
    p.circle(x-2,y+h*.53,2.8,lw=.5,layer="WEEP_HOLES")
    p.circle(x+w+2,y+h*.53,2.8,lw=.5,layer="WEEP_HOLES")
    return (x,y,w,h,xx,yy,ww,hh)


def _section_bb(p, inp, detail):
    x0,y0,x1,y1=76,683,1020,1108
    p.rect(x0,y0,x1-x0,y1-y0,lw=.28,layer="PANELS")
    p.txt(100,1090,"SECTION AT B-B",size=10,bold=True)
    p.txt(100,1075,"ROAD EMBANKMENT / CURTAIN WALLS / CULVERT",size=7,color=MID)
    # proportional to outer dimensions but contained in view
    s=min(72,285/(inp.clear_width_m+2*inp.wall_thickness_m),245/(inp.clear_height_m+inp.top_slab_thickness_m+inp.bottom_slab_thickness_m))
    out_w=(inp.clear_width_m+2*inp.wall_thickness_m)*s
    out_h=(inp.clear_height_m+inp.top_slab_thickness_m+inp.bottom_slab_thickness_m)*s
    x=502-out_w/2;y=739
    x,y,w,h,xx,yy,ww,hh=_box(p,inp,x,y,s)
    p.rect(x-7,y-8,w+14,8,fill=(.83,.86,.86),layer="PCC")
    p.txt(x+w+23,y-10,f"{detail.pcc_bedding_mm} mm PCC M10",size=7,color=MID)
    # proposed road and railing above box
    road_y=y+h+12
    p.line(x-16,road_y,x+w+16,road_y,lw=1.4,layer="ROAD")
    p.line(x-14,road_y+5,x+w+14,road_y+5,lw=.6,layer="ROAD")
    for rx in (x+10,x+w-10):
        p.rect(rx-4,road_y+5,8,58,lw=.8,layer="RAILING")
    for j in range(5): p.line(x+12,road_y+13+8*j,x+w-12,road_y+13+8*j,lw=.4,layer="RAILING")
    # referential side apron/curtain-wall and approach embankment
    lx=x-140; rx=x+w+140
    slope_bottom=y+h*.59
    soil_left=[(lx,slope_bottom),(x,road_y),(x,road_y-8),(lx,slope_bottom-1)]
    soil_right=[(x+w,road_y),(rx,slope_bottom),(rx,slope_bottom-1),(x+w,road_y-8)]
    _polygon(p,soil_left,fill=SOIL,layer="EMBANKMENT",lw=.55)
    _polygon(p,soil_right,fill=SOIL,layer="EMBANKMENT",lw=.55)
    p.rect(lx-82,y+7,82,slope_bottom-(y+7),fill=(.95,.96,.96),layer="CURTAIN_WALL")
    p.rect(rx,y+7,82,slope_bottom-(y+7),fill=(.95,.96,.96),layer="CURTAIN_WALL")
    p.rect(lx-85,y-9,88,12,fill=(.90,.91,.91),layer="PCC")
    p.rect(rx-3,y-9,88,12,fill=(.90,.91,.91),layer="PCC")
    p.line(lx,slope_bottom,x-2,road_y,lw=.8,layer="EMBANKMENT")
    p.line(rx,slope_bottom,x+w+2,road_y,lw=.8,layer="EMBANKMENT")
    p.line(lx-85,slope_bottom-57,rx+85,slope_bottom-57,lw=.5,color=MID,layer="LEVELS",dash=(8,4))
    p.txt(lx-91,slope_bottom-53,"G.L.",size=7,bold=True,align="right")
    p.txt(lx-91,slope_bottom-64,"I.L.",size=7,bold=True,align="right")
    # apron between box and curtain wall
    p.rect(lx,y+2,x-lx,8,fill=(.94,.94,.94),layer="APRONS")
    p.rect(x+w,y+2,rx-x-w,8,fill=(.94,.94,.94),layer="APRONS")
    p.txt((x+lx)/2,y+16,"FLEXIBLE APRON",size=6.4,align="center")
    p.txt((rx+x+w)/2,y+16,"FLEXIBLE APRON",size=6.4,align="center")
    p.dim_h(xx,xx+ww,yy+hh*.49,f"CLEAR WIDTH {inp.clear_width_m:.3f} m",size=7.7)
    p.dim_v(yy,yy+hh,x+w+34,f"H {inp.clear_height_m:.3f} m",size=7.6)
    p.txt(x+18,y+h-33,(f"HAUNCH {inp.haunch_width_m*1000:.0f}x{inp.haunch_height_m*1000:.0f} mm" if inp.haunch_count else "HAUNCHES OFF"),size=6.8)
    p.txt(x-27,y+h*.35,"WEEP HOLES",size=6.8,align="right")
    p.txt((lx+x)/2,slope_bottom-20,f"1 : {detail.earth_fill_slope_h_to_v:g}",size=7,align="center")
    p.txt((rx+x+w)/2,slope_bottom-20,f"1 : {detail.earth_fill_slope_h_to_v:g}",size=7,align="center")
    p.txt((lx-85+lx-3)/2,y+48,"D/S CURTAIN",size=6.5,align="center")
    p.txt((rx+rx+82)/2,y+48,"U/S CURTAIN",size=6.5,align="center")
    p.txt((x0+x1)/2,y0+13,"SECTION B-B | PROPORTIONAL RCC BOX; APPROACH AND SITE DETAIL ARE REFERENCE SCHEMATICS",size=7,align="center",color=MID)


def _section_cc(p, inp, detail):
    """Reference-style C-C section; site levels and soil profiles are schematic."""
    p.rect(76,238,944,436,lw=.28,layer='PANELS')
    p.txt(548,253,'SECTION AT C-C',size=10,bold=True,align='center')
    p.txt(548,242,'REVIEW SECTION - SITE LEVELS REQUIRE VERIFICATION',size=6.5,align='center')
    W=inp.clear_width_m+2*inp.wall_thickness_m
    H=inp.clear_height_m+inp.top_slab_thickness_m+inp.bottom_slab_thickness_m
    # Use the existing slope input; do not silently substitute a reference angle.
    slope=detail.earth_fill_slope_h_to_v
    s=min(260/H, 800/(W+2*slope*H))
    x=548-W*s/2; y=325; w=W*s; h=H*s
    top=y+h; run=slope*h; xl=x-run; xr=x+w+run
    # Triangular backfill outside the walls, widening upwards.
    left=[(xl,top),(x,top),(x,y)]
    right=[(x+w,y),(x+w,top),(xr,top)]
    for poly in (left,right):
        _polygon(p,poly,fill=SOIL,layer='FILL',lw=.7)
        # Interlocking short diagonal strokes, clipped to the triangle.
        def inside(px,py):
            if not y+1<=py<=top-1: return False
            edge=slope*(py-y)
            return x-edge+1<=px<=x-1 if poly is left else x+w+1<=px<=x+w+edge-1
        for j,yy in enumerate(range(int(y)+4,int(top),13)):
            for i,xx in enumerate(range(int(min(a for a,b in poly))+3,int(max(a for a,b in poly)),13)):
                sign=1 if (i+j)%2==0 else -1
                for offset in (0,3):
                    a=(xx,yy+offset); b=(xx+7,yy+offset+sign*7)
                    if inside(*a) and inside(*b): p.line(*a,*b,lw=.32,layer='FILL_HATCH')
    # Draw the actual box, including live haunch corners.
    p.rect(x,y,w,h,lw=1.3,layer='RCC_BOX')
    sw=inp.wall_thickness_m*s; bs=inp.bottom_slab_thickness_m*s; ts=inp.top_slab_thickness_m*s
    xx=x+sw; yy=y+bs; cw=inp.clear_width_m*s; ch=inp.clear_height_m*s
    a=inp.haunch_width_m*s if inp.haunch_count else 0
    b=inp.haunch_height_m*s if inp.haunch_count else 0
    _hole(p,xx,yy,cw,ch,a,b)
    # Pavement is schematic; wearing-coat thickness is input-linked.
    coat=inp.wearing_coat_thickness_m*s
    p.rect(xl-12,top,xr-xl+24,max(coat,0.6),lw=.8,layer='ROAD')
    p.line(xl-12,top+coat+5,xr+12,top+coat+5,lw=1.2,layer='ROAD')
    p.txt(548,top+coat+12,'FRL / PAVEMENT AS PER TCS',size=7,align='center')
    p.path([(x-9,y),(x-9,top),(x-4,top)],lw=.7,layer='GEOTEXTILE',dash=(4,3))
    p.path([(x+w+9,y),(x+w+9,top),(x+w+4,top)],lw=.7,layer='GEOTEXTILE',dash=(4,3))
    # Weep-hole symbols only; actual bore, gradient and locations not supplied.
    for side,sgn in ((x,1),(x+w,-1)):
        for q in (.32,.68):
            cy=y+h*q
            p.line(side,cy,side+sgn*sw,cy-2,lw=.65,layer='WEEP_HOLES')
    p.txt(xx+8,yy+ch*.69,'WEEP HOLES (TYP.)',size=6.4)
    p.line(xx+6,yy+ch*.67,x+sw,y+h*.68-2,lw=.4,layer='TEXT')
    # PCC bedding thickness is drawn at the same scale as the box.
    pc=detail.pcc_bedding_mm*s/1000
    p.rect(x-4,y-pc,w+8,pc,lw=.8,layer='PCC')
    p.rect(x-6,y-pc-10,w+12,10,lw=.4,layer='FOUNDATION')
    for i in range(int(x-5),int(x+w+3),6):
        p.line(i,y-pc-10,min(i+7,x+w+6),y-pc-3,lw=.3,layer='FILL_HATCH')
    p.txt(x+w+14,y-pc-8,f'{detail.pcc_bedding_mm} mm PCC (M10)',size=6.5)
    p.txt(548,y-pc-23,'COMPACTED SOIL (REFERENCE)',size=6.5,align='center')
    # Level symbols are indicative; no numerical site elevation inferred.
    for lab,px,py in [('G.L.',xl+10,y+30),('I.L.',xl+10,y+10)]:
        p.line(px,py,px+42,py,lw=.5,layer='LEVELS',dash=(5,3))
        p.path([(px+8,py),(px+4,py+6),(px+12,py+6)],closed=True,layer='LEVELS')
        p.txt(px+15,py+3,lab,size=6.5)
    p.dim_h(xx,xx+cw,yy+ch*.48,f'B = {inp.clear_width_m*1000:g}',size=7)
    p.dim_v(yy,yy+ch,xx+cw*.70,f'H = {inp.clear_height_m*1000:g}',size=7)
    p.dim_v(top-ts,top,x+w+20,f'TS {inp.top_slab_thickness_m*1000:g}',size=6.2)
    p.dim_v(y,y+bs,x+w+20,f'BS {inp.bottom_slab_thickness_m*1000:g}',size=6.2)
    p.dim_h(x,xx,y+h*.82,f'SW {inp.wall_thickness_m*1000:g}',size=6.0)
    if inp.haunch_count:
        p.txt(xx+8,yy+ch*.18,f'HAUNCH {inp.haunch_width_m*1000:g} x {inp.haunch_height_m*1000:g}',size=6.2)
        p.line(xx+8,yy+ch*.15,xx+a/2,yy+b/2,lw=.45,layer='TEXT')
    for px,align in ((xl+12,'left'),(xr-12,'right')):
        p.txt(px,top+25,f'GEO TEXTILE {detail.geo_textile_gsm} GSM / NON-WOVEN',size=6.2,align=align)
    p.txt(xl+5,top-45,'SELECTED SOIL BACKFILL',size=6.4)
    p.txt(xr-5,top-45,'SELECTED SOIL BACKFILL',size=6.4,align='right')
    p.txt(xl+5,top-58,f'SLOPE {slope:g}H : 1V',size=6.4)
    p.txt(xr-5,top-58,f'SLOPE {slope:g}H : 1V',size=6.4,align='right')


def _type_i(p,d):
    x0,y0,x1,y1=1030,838,1308,1108
    p.rect(x0,y0,x1-x0,y1-y0,lw=.28,layer="PANELS")
    p.txt((x0+x1)/2,y1-19,"CURTAIN WALL TYPE I (D/S)",size=8.1,bold=True,align="center")
    p.txt((x0+x1)/2,y1-34,"CROSS SECTION - SOURCE PROFILE",size=6.4,align="center")
    cx=(x0+x1)/2; floor=888
    sc=lambda k:floor+k*.75
    # staged stem/footing in relative reference proportions
    contour=[(cx-68,floor),(cx+68,floor),(cx+68,sc(45)),(cx+39,sc(45)),
             (cx+39,sc(108)),(cx+23,sc(108)),(cx+23,sc(161)),
             (cx+9,sc(161)),(cx+9,sc(217)),(cx-9,sc(217)),
             (cx-9,sc(161)),(cx-23,sc(161)),(cx-23,sc(108)),
             (cx-39,sc(108)),(cx-39,sc(45)),(cx-68,sc(45))]
    _polygon(p,contour,fill=LIGHT,layer="CURTAIN_WALL",lw=1)
    p.rect(cx-76,floor-7,152,7,fill=(.87,.87,.86),layer="PCC")
    # fill apron stone side
    apron=[(cx-125,sc(167)),(cx-11,sc(167)),(cx-11,sc(210)),(cx-125,sc(210))]
    _polygon(p,apron,fill=SOIL,layer="FLEXIBLE_APRON",lw=.4,hatch=True)
    p.txt(x0+10,sc(177),"FLEXIBLE",size=6.1)
    p.txt(x0+10,sc(166),"APRON",size=6.1)
    p.dim_h(cx-76,cx+76,floor-23,f"{d.curtain_i_pcc_width_mm} mm PCC",size=6.4)
    p.dim_h(cx-68,cx+68,sc(27),f"{d.curtain_i_base_mm} mm",size=6.4)
    p.dim_v(floor,sc(217),cx+88,f"{d.curtain_i_height_mm} mm",size=6.4)
    p.txt(cx-36,sc(225),"I.L.",size=6.2,align="center")
    p.txt(cx,floor-33,"75 mm THK. PCC (M10)",size=6.4,align="center")
    p.txt((x0+x1)/2,y0+7,"SOURCE FOOTING / NOT RE-DESIGNED",size=6.0,align="center",color=ALERT)


def _type_ii(p,d):
    x0,y0,x1,y1=1320,838,1607,1108
    p.rect(x0,y0,x1-x0,y1-y0,lw=.28,layer="PANELS")
    p.txt((x0+x1)/2,y1-19,"CURTAIN WALL TYPE II (U/S)",size=8.1,bold=True,align="center")
    p.txt((x0+x1)/2,y1-34,"CROSS SECTION - SOURCE PROFILE",size=6.4,align="center")
    cx=(x0+x1)/2;floor=892
    sc=lambda k:floor+k*.77
    # source 1300 base, 450 stem; 800+750+450 height annotations
    shape=[(cx-61,floor),(cx+61,floor),(cx+61,sc(64)),(cx+20,sc(64)),
           (cx+20,sc(145)),(cx+10,sc(145)),(cx+10,sc(205)),
           (cx-10,sc(205)),(cx-10,sc(145)),(cx-20,sc(145)),
           (cx-20,sc(64)),(cx-61,sc(64))]
    _polygon(p,shape,fill=LIGHT,layer="CURTAIN_WALL",lw=1)
    p.rect(cx-70,floor-7,140,7,fill=(.86,.86,.86),layer="PCC")
    apron=[(cx+10,sc(138)),(cx+105,sc(138)),(cx+105,sc(194)),(cx+10,sc(194))]
    _polygon(p,apron,fill=SOIL,layer="FLEXIBLE_APRON",lw=.45,hatch=True)
    p.txt(cx+48,sc(153),"FLEXIBLE",size=6.2,align="center")
    p.txt(cx+48,sc(143),"APRON",size=6.2,align="center")
    p.dim_h(cx-70,cx+70,floor-21,f"{d.curtain_ii_pcc_width_mm} mm",size=6.4)
    p.dim_h(cx-61,cx+61,sc(28),f"{d.curtain_ii_base_mm} mm",size=6.3)
    p.dim_v(floor,sc(205),cx-88,f"{d.curtain_ii_height_mm} mm",size=6.4)
    p.txt(cx-35,sc(210),"I.L.",size=6.2,align="center")
    p.txt(cx,floor-31,"75 mm THK. PCC (M10)",size=6.4,align="center")
    p.txt((x0+x1)/2,y0+7,"SOURCE FOOTING / NOT RE-DESIGNED",size=6.0,align="center",color=ALERT)


def _key_x(p,inp,d):
    x0,y0,x1,y1=1030,556,1308,828
    p.rect(x0,y0,x1-x0,y1-y0,lw=.28,layer="PANELS")
    p.txt((x0+x1)/2,y1-19,"SHEAR KEY DETAIL - X",size=8.7,bold=True,align="center")
    p.txt((x0+x1)/2,y1-34,"REFERENCE DETAIL / 1:1 INCLINE",size=6.5,align="center")
    # source slab and key sketch
    xa=x0+56; yf=y0+66
    p.path([(xa,yf+145),(xa+16,yf+145),(xa+16,yf+39),(xa+37,yf+39),
            (xa+135,yf+124),(xa+195,yf+124),(xa+195,yf+142)],lw=1.2,layer="SHEAR_KEY")
    p.path([(xa,yf+132),(xa+3,yf+132),(xa+3,yf+24),(xa+43,yf+24),
            (xa+140,yf+110),(xa+195,yf+110)],lw=.7,layer="SHEAR_KEY")
    p.rect(xa-2,yf+2,34,8,fill=LIGHT,layer="PCC")
    p.txt(xa+119,yf+153,"BOTTOM SLAB",size=7.2,align="center")
    p.txt(xa+142,yf+90,f"{d.pcc_bedding_mm} mm PCC",size=6.8)
    p.txt(xa+117,yf+79,"M10 BELOW SLAB",size=6.4)
    p.dim_h(xa,xa+32,yf-12,f"{d.shear_key_toe_mm} mm",size=6.5)
    p.dim_h(xa,xa+16,yf+159,f"{d.shear_key_gap_mm} mm GAP",size=6.1)
    p.txt(xa+125,yf+58,"1:1",size=7.1)
    p.txt((x0+x1)/2,y0+8,"KEY DEPTH DK VARIES - NOT PROVIDED",size=6.4,align="center",color=ALERT)


def _wing_wall(p,inp,d):
    x0,y0,x1,y1=1320,434,1607,828
    p.rect(x0,y0,x1-x0,y1-y0,lw=.28,layer="PANELS")
    p.txt((x0+x1)/2,y1-20,"U-TYPE WING WALL",size=9.3,bold=True,align="center")
    p.txt((x0+x1)/2,y1-34,"REFER SEPARATE WING WALL DESIGN",size=6.5,align="center",color=ALERT)
    cx=(x0+x1)/2; left=cx-81;right=cx+81
    base=y0+64; top=y0+280
    # U plan cross-section matching source on sheet 2. Wing heights are not assumed design values.
    p.rect(left-8,base-7,right-left+16,7,fill=LIGHT,layer="PCC")
    p.line(left,base,left,top,lw=1.2,layer="WING_WALL")
    p.line(right,base,right,top,lw=1.2,layer="WING_WALL")
    p.line(left+8,base+17,left+8,top-28,lw=1.1,layer="WING_WALL")
    p.line(right-8,base+17,right-8,top-28,lw=1.1,layer="WING_WALL")
    p.line(left+8,base+17,left+22,base+36,lw=1.1,layer="HAUNCH")
    p.line(right-8,base+17,right-22,base+36,lw=1.1,layer="HAUNCH")
    p.line(left+22,base+36,right-22,base+36,lw=.45,color=MID,layer="BED_LEVEL")
    p.txt(cx,base+49,"BED LEVEL",size=6.6,align="center")
    p.txt(cx,base+77,"NON EARTH FACE",size=7.0,align="center")
    p.txt(left-11,base+110,"EARTH FACE",size=6.9,angle=90,align="center")
    p.txt(right+15,base+110,"EARTH FACE",size=6.9,angle=90,align="center")
    p.line(left+8,top-28,right-8,top-28,lw=.4,color=MID,layer="EMBANKMENT",dash=(5,4))
    p.txt(cx,top-16,"WIDTH VARIES / WB-II",size=6.9,align="center")
    p.txt(cx,base+15,f"{d.pcc_bedding_mm} mm PCC M10",size=6.8,align="center")
    p.txt(cx,base+30,f"HAUNCH {inp.haunch_width_m*1000:.0f}x{inp.haunch_height_m*1000:.0f} mm (TYP.)",size=6.4,align="center")
    p.txt((x0+x1)/2,y0+14,"WING WALL DETAILS REQUIRE SEPARATE DESIGN",size=6.4,color=ALERT,align="center")


def _bottom_notes_and_title(p, d):
    p.rect(1030,238,577,184,lw=.3,layer="PANELS")
    p.txt(1043,401,"REFERENCE NOTES",size=8.6,bold=True)
    notes=[
        "1. DIMENSIONS SHOWN ARE mm UNLESS STATED OTHERWISE.",
        "2. RECONSTRUCTED FROM SOURCE PDF - SHEET 2 OF 3.",
        "3. SITE LEVELS / CURTAIN WALLS MUST BE CHECKED.",
        "4. PCC THICKNESS 75 mm (M10) PER SOURCE DETAIL.",
        "5. WING WALL: AS PER SEPARATE DRAWING.",
        "6. GEOTEXTILE: 150 GSM NON-WOVEN (SOURCE).",
        "7. DETAIL X: 20 mm GAP / 300 mm TOE (SOURCE).",
        "8. NOT A STRUCTURALLY VERIFIED CAD MODEL.",
    ]
    for i,t in enumerate(notes):
        p.txt(1043,385-i*17,t,size=6.5,layer="NOTES")
    p.txt(1043,246,"REFERENCE DRAWINGS: GAD 01 / REINF. 01 (SOURCE PDF)",size=6.4,color=MID)
    # title block mirrors lower portion of original, but no originals' logos/signatures
    yb=54;yt=220;xa=76;xb=1607
    p.rect(xa,yb,xb-xa,yt-yb,lw=1.0,layer="TITLE_BLOCK")
    cuts=[xa+215,xa+460,xa+780,xa+1120,xa+1365]
    for xx in cuts:p.line(xx,yb,xx,yt,lw=.6,layer="TITLE_BLOCK")
    p.txt(xa+10,yt-19,"REVISION",size=8,bold=True)
    p.txt(xa+10,yt-41,"SOURCE: R0 / 17-05-2025",size=7)
    p.txt(xa+10,yt-61,"SOURCE STATUS: FOR APPROVAL",size=7)
    p.txt(xa+10,yt-83,"PYTHON OUTPUT: REVIEW ONLY",size=7,bold=True,color=ALERT)
    p.txt(xa+10,yb+20,"NOT A SIGNED ENGINEERING SHEET",size=6.7,color=ALERT)
    p.txt(xa+225,yt-19,"CLIENT",size=8,bold=True)
    p.txt(xa+225,yt-43,"NHIDCL",size=10,bold=True)
    p.txt(xa+225,yt-60,"National Highways &",size=7)
    p.txt(xa+225,yt-75,"Infrastructure Dev. Corp. Ltd.",size=7)
    p.txt(xa+470,yt-19,"EPC CONTRACTOR",size=8,bold=True)
    p.txt(xa+470,yt-43,"RANI CONSTRUCTIONS PVT. LTD.",size=8)
    p.txt(xa+790,yt-19,"DESIGN CONSULTANT (SOURCE PDF)",size=8,bold=True)
    p.txt(xa+790,yt-43,"Shashwat Structural Solutions Pvt. Ltd.",size=7.8)
    p.txt(xa+790,yt-68,"Original approvals not reproduced",size=7.4)
    p.txt(xa+1130,yt-19,"PROJECT",size=8,bold=True)
    p.txt(xa+1130,yt-44,"NH-208 / PACKAGE III",size=8)
    p.txt(xa+1130,yt-61,"TRIPURA",size=7.8)
    p.txt(xa+1374,yt-19,"DRAWING TITLE",size=8,bold=True)
    p.txt(xa+1374,yt-40,"DETAILED SECTIONS",size=9,bold=True)
    p.txt(xa+1374,yt-56,"GAD - SHEET 2 OF 3",size=8,bold=True)
    p.txt(xa+1374,yt-73,f"CH. {d.chainage}",size=7.5)
    p.txt(xa+1374,yt-89,"A2 LANDSCAPE",size=7.0)
    p.txt(xa+1374,yb+17,"REVIEW RECONSTRUCTION",size=6.7,color=ALERT)


def _draw(p, inp, detail):
    inp.validate();detail.validate()
    p.rect(40,42,PAGE_W-80,PAGE_H-83,lw=1.1,layer="BORDER")
    p.rect(49,51,PAGE_W-98,PAGE_H-101,lw=.45,layer="BORDER")
    p.txt(76,1130,"RCC BOX CULVERT - DETAIL SECTIONS / GAD SHEET 2",size=11.5,bold=True)
    p.txt(PAGE_W-76,1130,detail.reference_status,size=8.5,bold=True,align="right",color=ALERT)
    _section_bb(p,inp,detail)
    _section_cc(p,inp,detail)
    _type_i(p,detail)
    _type_ii(p,detail)
    _key_x(p,inp,detail)
    _wing_wall(p,inp,detail)
    _bottom_notes_and_title(p,detail)
    p.txt(PAGE_W/2,21,"REFERENCE RECONSTRUCTION: NOT THE ORIGINAL CAD / NOT FOR CONSTRUCTION OR FABRICATION",size=8,align="center",bold=True,color=ALERT)


def make_sheet2_pdf(inputs: CulvertInputs, detail: Sheet2Inputs | None=None) -> bytes:
    detail=detail or Sheet2Inputs()
    b=BytesIO();c=canvas.Canvas(b,pagesize=(PAGE_W,PAGE_H),pageCompression=1)
    c.setTitle("RCC Box Culvert Detailed Sections - Sheet 2 - Review")
    c.setAuthor("RCC Culvert Python Review Tool")
    _draw(_Pen(c),inputs,detail); c.showPage();c.save();return b.getvalue()


def sheet2_preview_png(inputs: CulvertInputs, detail: Sheet2Inputs | None=None, dpi: int=120)->bytes:
    if not 60 <= dpi <= 240: raise ValueError("DPI must be between 60 and 240")
    import fitz
    with fitz.open(stream=make_sheet2_pdf(inputs,detail),filetype="pdf") as doc:
        return doc[0].get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),alpha=False).tobytes("png")


def export_sheet2_dxf(inputs:CulvertInputs, detail: Sheet2Inputs|None=None)->bytes:
    import ezdxf
    p=_Pen(None,record=True)
    _draw(p,inputs,detail or Sheet2Inputs())
    doc=ezdxf.new("R2010")
    doc.header["$INSUNITS"]=4
    layers={"BORDER":7,"PANELS":8,"STRUCTURE":7,"RCC_BOX":7,"OPENING":7,
            "WEEP_HOLES":7,"CENTERLINE":8,"PCC":8,"FOUNDATION":8,
            "SHEAR_KEY":7,"ROAD":7,"RAILING":7,"EMBANKMENT":8,"FILL":8,
            "FILL_HATCH":9,"GEOTEXTILE":3,"CURTAIN_WALL":7,"WING_WALL":7,
            "HAUNCH":7,"SHEAR_KEY":7,"FLEXIBLE_APRON":8,"APRONS":8,
            "GROUND_LEVEL":8,"LEVELS":8,"DIMENSIONS":5,"TEXT":7,"TITLE_BLOCK":7,
            "NOTES":7,"BED_LEVEL":8}
    for layer,color in layers.items():doc.layers.new(layer,dxfattribs={"color":color})
    ms=doc.modelspace();f=_PAPER_TO_MM
    for kind,layer,a in p.items:
        if kind=="line":
            xx,yy,xx2,yy2=a;ms.add_line((xx*f,yy*f),(xx2*f,yy2*f),dxfattribs={"layer":layer})
        elif kind=="path":
            pts,closed=a;ms.add_lwpolyline([(x*f,y*f) for x,y in pts],close=closed,dxfattribs={"layer":layer})
        elif kind=="circle":
            x,y,r=a;ms.add_circle((x*f,y*f),r*f,dxfattribs={"layer":layer})
        elif kind=="text":
            x,y,txt,size,rot=a; ms.add_text(txt,dxfattribs={"layer":layer,
              "height":max(size*f,.6),"rotation":rot,"insert":(x*f,y*f)})
    out=StringIO();doc.write(out);return out.getvalue().encode("utf-8")


def write_sheet2_files(inputs:CulvertInputs,output_dir:str|Path,detail:Sheet2Inputs|None=None):
    folder=Path(output_dir);folder.mkdir(parents=True,exist_ok=True)
    artifacts={"pdf":folder/"GAD_Sheet2_Review.pdf",
               "png":folder/"GAD_Sheet2_Review.png",
               "dxf":folder/"GAD_Sheet2_Review.dxf"}
    artifacts["pdf"].write_bytes(make_sheet2_pdf(inputs,detail))
    artifacts["png"].write_bytes(sheet2_preview_png(inputs,detail))
    artifacts["dxf"].write_bytes(export_sheet2_dxf(inputs,detail))
    return artifacts

# Native full-size CAD exporter. Existing PDF/PNG API remains unchanged.
def export_sheet2_dxf(inputs: CulvertInputs, detail: Sheet2Inputs | None = None) -> bytes:
    """Generate measured model geometry and an A2 review layout, no raster underlays."""
    import ezdxf
    from ezdxf.enums import TextEntityAlignment
    from ezdxf import bbox
    from io import StringIO
    d=detail or Sheet2Inputs(); d.validate(); inputs.validate()
    if not inputs.supports_single_cell_model:
        raise ValueError('Only single-cell geometry is supported')
    W,H,w,t,b=[1000*getattr(inputs,k) for k in ('clear_width_m','clear_height_m','wall_thickness_m','top_slab_thickness_m','bottom_slab_thickness_m')]
    X=W+2*w; Y=H+t+b; pcc=d.pcc_bedding_mm
    hw=1000*inputs.haunch_width_m if inputs.haunch_count else 0
    hh=1000*inputs.haunch_height_m if inputs.haunch_count else 0
    if 2*hw>=W or 2*hh>=H: raise ValueError('Haunches overlap')
    doc=ezdxf.new('R2010'); doc.units=4
    for n,c in [('CONCRETE',7),('PCC',8),('DIMENSIONS',2),('ANNOTATION',7),('SITE_REFERENCE',8),('GEOTEXTILE',3),('HATCH',8),('SHEET',7)]:
        doc.layers.new(n,dxfattribs={'color':c})
    m=doc.modelspace(); panels=[]
    def poly(p,pts,layer='CONCRETE',closed=True): return p.add_lwpolyline(pts,close=closed,dxfattribs={'layer':layer})
    def rect(p,x,y,a,z,layer='CONCRETE'): return poly(p,[(x,y),(x+a,y),(x+a,y+z),(x,y+z)],layer)
    def line(p,a,z,layer='ANNOTATION'): return p.add_line(a,z,dxfattribs={'layer':layer})
    def txt(p,x,y,s,h=100,center=False,layer='ANNOTATION'):
        e=p.add_text(str(s),dxfattribs={'height':h,'layer':layer}); e.set_placement((x,y),align=TextEntityAlignment.MIDDLE_CENTER if center else TextEntityAlignment.LEFT); return e
    def dim(a,z,base,angle=0):
        q=m.add_linear_dim(base=base,p1=a,p2=z,angle=angle,dimstyle='Standard',override={'dimtxt':100,'dimasz':65,'dimdec':0},dxfattribs={'layer':'DIMENSIONS'});q.render()
    def hatch(pts):
        h=m.add_hatch(dxfattribs={'layer':'HATCH'});h.set_pattern_fill('ANSI31',scale=20);h.paths.add_polyline_path(pts,is_closed=True)
    def panel(start,center,size,title):
        entities=list(m)[start:]; ext=bbox.extents(entities); panels.append((ext,center,size,title))
    def box(x,y):
        rect(m,x,y,X,Y); a=x+w; c=y+b
        poly(m,[(a+hw,c),(a+W-hw,c),(a+W,c+hh),(a+W,c+H-hh),(a+W-hw,c+H),(a+hw,c+H),(a,c+H-hh),(a,c+hh)] if hw and hh else [(a,c),(a+W,c),(a+W,c+H),(a,c+H)])
        rect(m,x,y-pcc,X,pcc,'PCC')
        dim((a,c+H/2),(a+W,c+H/2),(a,c+H/2))
        dim((a+W*.65,c),(a+W*.65,c+H),(a+W*.65,c),90)
        dim((x,y),(x+X,y),(x,y-pcc-300))
        dim((x,y),(x,y+Y),(x-400,y),90)
        txt(m,x+X/2,y+Y+220,f'TS {t:g} / BS {b:g} / SW {w:g} mm',110,True)
        txt(m,x+X/2,y-pcc-600,f'{pcc:g} mm PCC (M10)',95,True)
        if hw and hh: txt(m,a+180,c+H-400,f'HAUNCH {hw:g} x {hh:g}',85)
    # Independent views at non-overlapping model coordinates; sheet via viewports.
    gap=max(X,Y,8000)*2.5
    start=len(m); box(0,0)
    txt(m,X/2,Y+650,'SECTION B-B - BOX CORE',150,True)
    txt(m,X/2,-1100,'Wing-wall, railing and curtain set-out pending',100,True)
    panel(start,(174,308),(310,155),'SECTION B-B')
    start=len(m); x=X+max(2500,d.earth_fill_slope_h_to_v*(Y+inputs.earth_cushion_m*1000)+1800); box(x,0)
    fill=inputs.earth_cushion_m*1000; wear=inputs.wearing_coat_thickness_m*1000; run=d.earth_fill_slope_h_to_v*(Y+fill)
    for side in (-1,1):
        e=x if side<0 else x+X
        pts=[(e,0),(e,Y+fill),(e+side*run,Y+fill)]
        poly(m,pts,'SITE_REFERENCE');hatch(pts);line(m,(e,0),(e,Y),'GEOTEXTILE')
    if wear>0: rect(m,x-run,Y+fill,X+2*run,wear,'SITE_REFERENCE')
    txt(m,x+X/2,Y+fill+wear+350,'PAVEMENT / FRL - SITE LEVEL PENDING',120,True)
    txt(m,x-run*.65,Y*.6,f'GEOTEXTILE {d.geo_textile_gsm:g} GSM',100,True)
    txt(m,x+X+run*.6,Y*.6,'SELECTED BACKFILL',100,True)
    txt(m,x+X/2,-1050,f'Fill slope 1V:{d.earth_fill_slope_h_to_v:g}H - review assumption',100,True)
    panel(start,(174,144),(310,145),'SECTION C-C')
    # Source profiles: step heights and widths from PDF sheet 2. Only the
    # original dimensions are supported; changed totals need an approved profile.
    def curtain(x, widths, heights, base,pccw,total,title,ref):
        start=len(m)
        if abs(total-sum(heights))>1e-6 or base!=widths[0] or pccw!=ref:
            txt(m,x,0,'Changed curtain dimensions: approved stepped profile required',110)
        else:
            y=0; right=[]
            for width,height in zip(widths,heights):
                right.extend([(x+width/2,y),(x+width/2,y+height)]);y+=height
            pts=right+[(2*x-a,z) for a,z in reversed(right)]
            poly(m,pts);rect(m,x-pccw/2,-pcc,pccw,pcc,'PCC')
            dim((x-base/2,0),(x+base/2,0),(x,-350))
            dim((x-pccw/2,-pcc),(x+pccw/2,-pcc),(x,-650))
            dim((x+base/2,0),(x+base/2,total),(x+base/2+300,0),90)
            for i,width in enumerate(widths): txt(m,x,sum(heights[:i])+heights[i]/2,f'{width:g}',95,True)
            txt(m,x,total+300,'PCC (M15) - SOURCE PROFILE',100,True)
            txt(m,x,-950,'Foundation design / set-out pending',90,True)
        return start
    start=curtain(-6500,[1650,750,450,200],[550,750,750,450],d.curtain_i_base_mm,d.curtain_i_pcc_width_mm,d.curtain_i_height_mm,'I',1850)
    panel(start,(407,319),(132,133),'CURTAIN WALL TYPE I (D/S)')
    start=curtain(-10500,[1300,450,200],[800,750,450],d.curtain_ii_base_mm,d.curtain_ii_pcc_width_mm,d.curtain_ii_height_mm,'II',1500)
    panel(start,(541,319),(96,133),'CURTAIN WALL TYPE II (U/S)')
    # Key and wing wall geometry cannot be established from box inputs alone.
    # Keep their reference positions on the sheet but do not invent dimensions.
    sheet=doc.layouts.new('DETAILS A2');sheet.page_setup(size=(594,420),margins=(0,0,0,0),units='mm')
    rect(sheet,8,8,578,404,'SHEET')
    for ext,center,size,title in panels:
        if not ext.has_data: continue
        cx,cy=center; sw,sh=size; ex=ext.size; mid=ext.center
        vh=max(ex.y,ex.x*sh/sw,1)*1.12
        sheet.add_viewport(center=center,size=size,view_center_point=(mid.x,mid.y),view_height=vh,dxfattribs={'layer':'SHEET','flags':16384})
        txt(sheet,cx,cy-sh/2-4,title,2.8,True)
    txt(sheet,342,235,'SHEAR KEY DETAIL X',3.2)
    txt(sheet,342,226,f'Gap: {d.shear_key_gap_mm:g} mm; toe: {d.shear_key_toe_mm:g} mm',2.6)
    txt(sheet,342,218,'DK and verified profile required.',2.6)
    txt(sheet,342,190,'U-TYPE WING WALL',3.2)
    txt(sheet,342,181,'Separate wing-wall design required.',2.6)
    txt(sheet,342,173,'Width, height and foundation not inferred.',2.6)
    txt(sheet,342,135,'NOTES',3.2)
    for i,s in enumerate(['All model dimensions in mm.','Box geometry from live design inputs.','Curtain profiles: reference geometry only.','Site levels / tie-ins require verification.','Engineering review - not for construction.']): txt(sheet,342,126-i*7,s,2.6)
    rect(sheet,8,8,578,44,'SHEET');line(sheet,(400,8),(400,52),'SHEET')
    txt(sheet,14,37,'GENERAL ARRANGEMENT - DETAILED SECTIONS',4)
    txt(sheet,14,24,f'CH. {d.chainage} | SHEET 2 OF 3 | A2 | ENGINEERING REVIEW',3)
    txt(sheet,405,37,'MODEL: 1:1 mm',3);txt(sheet,405,24,'Approval: pending',3)
    ext=bbox.extents(m);doc.set_modelspace_vport(height=max(ext.size.y,ext.size.x/1.8)*1.15,center=(ext.center.x,ext.center.y))
    _restore_detailed_paper_layout(doc, inputs, d)
    out=StringIO();doc.write(out);return out.getvalue().encode('utf-8')


def _restore_detailed_paper_layout(doc, inputs, detail):
    """Regenerate the earlier detailed sheet as native paper-space linework.

    This is a diagrammatic layout, separate from the measured model views.
    Site shapes are not certified full-size geometry. No image is embedded.
    """
    from ezdxf.enums import TextEntityAlignment
    class SheetPen(_Pen):
        def __init__(self):
            super().__init__(None, record=True)
        def txt(self,x,y,text,size=8,bold=False,align="left",color=(.15,.19,.23),angle=0,layer="TEXT"):
            self.items.append(("text_aligned",layer,(x,y,str(text),size,angle,align)))
    pen=SheetPen()
    _draw(pen,inputs,detail)
    layout=doc.layouts.new('DETAILED A2')
    layout.page_setup(size=(594,420),margins=(0,0,0,0),units='mm')
    f=_PAPER_TO_MM
    # No model viewport in this layout: it is an independently regenerated
    # paper-space schematic, not a measured projection of the model.
    for v in list(layout.query('VIEWPORT')):
        if v.dxf.id != 1: layout.delete_entity(v)
    for kind,layer,args in pen.items:
        name='PAPER_'+layer
        if name not in doc.layers:
            doc.layers.new(name,dxfattribs={'color':3 if layer=='GEOTEXTILE' else 7 if layer!='DIMENSIONS' else 2})
        attr={'layer':name}
        if kind=='line':
            x,y,u,v=args;layout.add_line((x*f,y*f),(u*f,v*f),dxfattribs=attr)
        elif kind=='path':
            pts,closed=args;layout.add_lwpolyline([(x*f,y*f) for x,y in pts],close=closed,dxfattribs=attr)
        elif kind=='circle':
            x,y,r=args;layout.add_circle((x*f,y*f),r*f,dxfattribs=attr)
        elif kind=='text_aligned':
            x,y,t,size,angle,align=args
            e=layout.add_text(t,dxfattribs={**attr,'height':size*f,'rotation':angle})
            e.set_placement((x*f,y*f),align={'left':TextEntityAlignment.LEFT,'center':TextEntityAlignment.CENTER,'right':TextEntityAlignment.RIGHT}[align])
    layout.add_text('PAPER-SCALE REVIEW SHEET - USE SEPARATE FULL-SIZE DXF FOR MEASUREMENTS',dxfattribs={'height':2.6,'insert':(26,3),'layer':'PAPER_TEXT'})
    doc.layouts.set_active_layout(layout.name)


# Default download is the detailed sheet in Model Space for viewers that hide
# named layouts. The full-size geometry remains available as a separate export.
export_sheet2_measured_dxf = export_sheet2_dxf

def export_sheet2_dxf(inputs: CulvertInputs, detail: Sheet2Inputs | None = None) -> bytes:
    import ezdxf
    from ezdxf import bbox
    detail = detail or Sheet2Inputs()
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    _restore_detailed_paper_layout(doc, inputs, detail)
    layout = doc.layouts.get("DETAILED A2")
    model = doc.modelspace()
    for entity in layout:
        if entity.dxftype() != "VIEWPORT":
            model.add_entity(entity.copy())
    ext = bbox.extents(model)
    doc.set_modelspace_vport(height=max(ext.size.y, ext.size.x / 1.35)*1.10, center=(ext.center.x,ext.center.y))
    out = StringIO()
    doc.write(out)
    return out.getvalue().encode("utf-8")
