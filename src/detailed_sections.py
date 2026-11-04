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
    p.txt(x+18,y+h-33,f"HAUNCH {inp.haunch_width_m*1000:.0f}x{inp.haunch_height_m*1000:.0f} mm",size=6.8)
    p.txt(x-27,y+h*.35,"WEEP HOLES",size=6.8,align="right")
    p.txt((lx+x)/2,slope_bottom-20,f"1 : {detail.earth_fill_slope_h_to_v:g}",size=7,align="center")
    p.txt((rx+x+w)/2,slope_bottom-20,f"1 : {detail.earth_fill_slope_h_to_v:g}",size=7,align="center")
    p.txt((lx-85+lx-3)/2,y+48,"D/S CURTAIN",size=6.5,align="center")
    p.txt((rx+rx+82)/2,y+48,"U/S CURTAIN",size=6.5,align="center")
    p.txt((x0+x1)/2,y0+13,"SECTION B-B | PROPORTIONAL RCC BOX; APPROACH AND SITE DETAIL ARE REFERENCE SCHEMATICS",size=7,align="center",color=MID)


def _section_cc(p, inp, detail):
    x0,y0,x1,y1=76,238,1020,674
    p.rect(x0,y0,x1-x0,y1-y0,lw=.28,layer="PANELS")
    p.txt(100,657,"SECTION AT C-C",size=10,bold=True)
    p.txt(100,642,"EMBANKMENT / GEOTEXTILE / PCC BEDDING",size=7,color=MID)
    s=min(74,270/(inp.clear_width_m+2*inp.wall_thickness_m),268/(inp.clear_height_m+inp.top_slab_thickness_m+inp.bottom_slab_thickness_m))
    outw=(inp.clear_width_m+2*inp.wall_thickness_m)*s
    x=506-outw/2;y=297
    x,y,w,h,xx,yy,ww,hh=_box(p,inp,x,y,s)
    p.rect(x-12,y-9,w+24,9,fill=(.83,.86,.86),layer="PCC")
    p.path([(x-14,y-12),(x+w+14,y-12),(x+w+40,y-25),(x-40,y-25)],fill=(.96,.95,.94),closed=True,layer="FOUNDATION")
    p.txt(x+w+30,y-8,f"{detail.pcc_bedding_mm} mm PCC M10",size=7)
    road_y=y+h+11
    p.line(90,road_y,912,road_y,lw=2,layer="ROAD")
    p.line(90,road_y-7,912,road_y-7,lw=.5,layer="ROAD")
    # 1V:2H soil wedges to both sides of box; use panel-specific crop/scale
    apex_l=(x,road_y-7);left=(x-190,y+10)
    apex_r=(x+w,road_y-7);right=(x+w+190,y+10)
    polyl=[(x-238,road_y-7),apex_l,(x,y+9),left]
    polyr=[apex_r,(x+w+238,road_y-7),right,(x+w,y+9)]
    _polygon(p,polyl,fill=SOIL,layer="FILL",lw=.6,hatch=True)
    _polygon(p,polyr,fill=SOIL,layer="FILL",lw=.6,hatch=True)
    p.line(left[0],left[1],apex_l[0],apex_l[1],lw=1.1,layer="GEOTEXTILE")
    p.line(apex_r[0],apex_r[1],right[0],right[1],lw=1.1,layer="GEOTEXTILE")
    p.txt(146,460,f"GEO TEXTILE {detail.geo_textile_gsm} GSM",size=7)
    p.txt(146,449,"NON-WOVEN FABRIC",size=6.5)
    p.txt(853,460,f"GEO TEXTILE {detail.geo_textile_gsm} GSM",size=7,align="right")
    p.txt(853,449,"NON-WOVEN FABRIC",size=6.5,align="right")
    p.txt(140,390,"SELECTED SOIL BACKFILL",size=6.5)
    p.txt(140,377,"(REFERENCE)",size=6.5)
    p.txt(862,390,"SELECTED SOIL BACKFILL",size=6.5,align="right")
    p.txt(862,377,"(REFERENCE)",size=6.5,align="right")
    p.txt(506,road_y+14,"FRL / PAVEMENT AS PER TCS",size=7,align="center")
    p.line(114,y+76,889,y+76,lw=.4,color=MID,layer="GROUND_LEVEL",dash=(8,6))
    p.txt(111,y+82,"G.L.",size=7,align="right")
    p.txt(111,y+66,"I.L.",size=7,align="right")
    p.dim_h(xx,xx+ww,y+hh*.5,f"B {inp.clear_width_m:.3f} m",size=7.6)
    p.dim_v(yy,yy+hh,x+w+24,f"H {inp.clear_height_m:.3f} m",size=7.6)
    p.txt(x+21,yy+18,f"HAUNCH {inp.haunch_width_m*1000:.0f}x{inp.haunch_height_m*1000:.0f} mm",size=6.6)
    p.txt((x0+x1)/2,y0+13,"SECTION C-C | EMBANKMENT / GEOTEXTILE ARRANGEMENT BASED ON SOURCE PDF",size=7,align="center",color=MID)


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
