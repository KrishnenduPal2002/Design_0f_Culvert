"""Reference-based reconstruction of Sheet 1 (GAD) for the RCC 1x3x3 box culvert.

This is a distinct sheet-layout generator, not the original CAD geometry and not
an engineering-approved construction drawing. Cross-sectional geometry and
length use the application's current CulvertInputs. Site-specific wing-wall,
level, apron and road values are explicit GADInputs, initially transcribed from
Sheet 1 of the supplied drawing. No change to geometry silently redesigns the
wing walls, flood levels, pavement or foundation.

Outputs: vector A2 landscape PDF, PNG preview and a paper-layout DXF.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import math
from pathlib import Path

from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas
from .inputs import CulvertInputs

PAGE_W, PAGE_H = 1684.0, 1191.0  # A2 landscape in PDF points
# Sheet drawing core scale 1:75 (37.795 pt/m); site wing walls are diagrammatic.
PT_PER_M_AT_75 = 72.0 / 25.4 * 1000.0 / 75.0
_PAPER_TO_MM = 25.4 / 72.0

@dataclass(frozen=True)
class GADInputs:
    chainage: str = "36+925"
    flow_direction: str = "R-L"
    left_apron_m: float = 6.0
    right_apron_m: float = 3.0
    railing_pedestal_width_m: float = 0.5
    wingwall_left_length_m: float = 4.924
    wingwall_right_length_m: float = 4.900
    wingwall_left_height_m: float = 3.762
    wingwall_right_height_m: float = 3.750
    wingwall_left_breadth_m: float = 8.686
    wingwall_right_breadth_m: float = 8.658
    road_camper_left_percent: float = -2.5
    road_camper_right_percent: float = -2.5
    frl1_m: float = 32.214
    frl2_m: float = 32.351
    frl3_m: float = 32.214
    ground_level_m: float = 29.219
    invert_level_left_m: float = 28.790
    invert_level_right_m: float = 28.802
    fill_left_m: float = 0.150
    fill_crown_m: float = 0.288
    fill_right_m: float = 0.150
    base_pressure_t_m2: float = 3.920
    pcc_bedding_mm: int = 75
    issue_note: str = "RECONSTRUCTED FOR ENGINEERING REVIEW"

    def validate(self):
        if not self.chainage.strip() or len(self.chainage) > 35:
            raise ValueError("Chainage must be non-empty and at most 35 characters")
        for name, val in vars(self).items():
            if isinstance(val, (int, float)):
                if not math.isfinite(val):
                    raise ValueError(f"{name} must be finite")
                if any(key in name for key in ("apron_m", "width_m", "length_m", "height_m", "breadth_m")) and val <= 0:
                    raise ValueError(f"{name} must be positive")
                if name.startswith('fill_') and val < 0:
                    raise ValueError(f"{name} must not be negative")
        if self.pcc_bedding_mm <= 0:
            raise ValueError("PCC bedding thickness must be positive")
        return self


class _Pen:
    """Vector primitives shared between PDF output and an optional DXF recorder."""

    def __init__(self, pdf: canvas.Canvas | None, record: bool = False):
        self.c = pdf
        self.record = record
        self.items = []

    def line(self, x1, y1, x2, y2, lw=.6, color=(.16,.20,.24), layer="GEOMETRY", dash=None):
        if self.c:
            self.c.saveState()
            self.c.setStrokeColor(Color(*color))
            self.c.setLineWidth(lw)
            if dash: self.c.setDash(*dash)
            self.c.line(x1,y1,x2,y2)
            self.c.restoreState()
        if self.record:
            self.items.append(("line",layer,(x1,y1,x2,y2)))

    def path(self, points, lw=.65, closed=False, fill=None, stroke=(.16,.20,.24), layer="GEOMETRY", dash=None):
        if not points:
            return
        if self.c:
            self.c.saveState()
            self.c.setStrokeColor(Color(*stroke))
            self.c.setLineWidth(lw)
            if dash: self.c.setDash(*dash)
            p = self.c.beginPath()
            p.moveTo(*points[0])
            for pt in points[1:]: p.lineTo(*pt)
            if closed: p.close()
            if fill is not None:
                self.c.setFillColor(Color(*fill))
            self.c.drawPath(p, stroke=1, fill=int(fill is not None))
            self.c.restoreState()
        if self.record:
            self.items.append(("path",layer,(list(points),closed)))

    def rect(self,x,y,w,h,lw=.6,fill=None,layer="GEOMETRY"):
        self.path([(x,y),(x+w,y),(x+w,y+h),(x,y+h)],lw=lw,closed=True,fill=fill,layer=layer)

    def circle(self,x,y,r,lw=.5,fill=None,layer="GEOMETRY"):
        if self.c:
            self.c.saveState()
            self.c.setStrokeColor(Color(.18,.23,.27))
            self.c.setLineWidth(lw)
            if fill is not None:
                self.c.setFillColor(Color(*fill))
            self.c.circle(x,y,r,stroke=1,fill=int(fill is not None))
            self.c.restoreState()
        if self.record: self.items.append(("circle",layer,(x,y,r)))

    def txt(self,x,y,text,size=8,bold=False,align="left",color=(.15,.19,.23),angle=0,layer="TEXT"):
        text = str(text)
        if self.c:
            self.c.saveState()
            self.c.translate(x,y)
            if angle: self.c.rotate(angle)
            self.c.setFont("Helvetica-Bold" if bold else "Helvetica",size)
            self.c.setFillColor(Color(*color))
            if align=="center": self.c.drawCentredString(0,0,text)
            elif align=="right": self.c.drawRightString(0,0,text)
            else: self.c.drawString(0,0,text)
            self.c.restoreState()
        if self.record: self.items.append(("text",layer,(x,y,text,size,angle)))

    def arrow(self,x1,y1,x2,y2, size=5, lw=.6,layer="DIMENSIONS"):
        self.line(x1,y1,x2,y2,lw=lw,layer=layer)
        a=math.atan2(y2-y1,x2-x1)
        for side in [-.48,.48]:
            dx=size*math.cos(a+math.pi+side)
            dy=size*math.sin(a+math.pi+side)
            self.line(x2,y2,x2+dx,y2+dy,lw=lw,layer=layer)

    def dim_h(self,x1,x2,y,lab,source_y=None,size=6.8):
        if source_y is not None:
            self.line(x1,source_y,x1,y+8,layer="DIMENSIONS")
            self.line(x2,source_y,x2,y+8,layer="DIMENSIONS")
        self.line(x1,y,x2,y,layer="DIMENSIONS")
        for x,sgn in [(x1,1),(x2,-1)]:
            self.path([(x,y),(x+sgn*6,y+2),(x+sgn*6,y-2)],closed=True,fill=(.14,.19,.25),layer="DIMENSIONS")
        self.txt((x1+x2)/2,y+4,lab,size=size,align="center",layer="DIMENSIONS")

    def dim_v(self,y1,y2,x,lab,source_x=None,size=7):
        if source_x is not None:
            self.line(source_x,y1,x-8,y1,layer="DIMENSIONS")
            self.line(source_x,y2,x-8,y2,layer="DIMENSIONS")
        self.line(x,y1,x,y2,layer="DIMENSIONS")
        for y,sgn in [(y1,1),(y2,-1)]:
            self.path([(x,y),(x+2,y+sgn*6),(x-2,y+sgn*6)],closed=True,fill=(.14,.19,.25),layer="DIMENSIONS")
        self.txt(x+4,(y1+y2)/2,lab,size=size,angle=90,align="center",layer="DIMENSIONS")


def _stone_fill(p, x0, y0, x1, y1, stride=16):
    """Sparse crushed-stone hatch pattern for the flexible apron (diagrammatic)."""
    for row in range(int((y1-y0)//stride)):
        y=y0+8+row*stride
        shift=(row%2)*8
        for col in range(int((x1-x0)//stride)):
            x=x0+7+shift+col*stride
            if x>x1-5: continue
            # varied small block outlines, vector and deterministic
            p.path([(x-4,y-3),(x+1,y-5),(x+5,y-1),(x+3,y+4),(x-3,y+4)],lw=.28,closed=True,layer="HATCH")


def _sheet_border(p, gad):
    p.rect(39,42,PAGE_W-78,PAGE_H-82,lw=1.2,layer="BORDER")
    p.rect(48,51,PAGE_W-96,PAGE_H-100,lw=.55,layer="BORDER")
    p.txt(62,1129,"GENERAL ARRANGEMENT DRAWING OF BOX CULVERT",size=11.5,bold=True)
    p.txt(62,1114,f"AT CH. {gad.chainage} | SINGLE-CELL RCC BOX",size=7.6)
    p.txt(PAGE_W-64,1128,"REFERENCE RECONSTRUCTION - NOT FOR CONSTRUCTION",size=9,bold=True,align="right",color=(.63,.18,.13))


def _section_aa(p, inp, gad):
    # section left part mirrors original document's longitudinal elevation layout
    x0,y0,x1,y1 = 75,862,1154,1105
    p.rect(x0,y0,x1-x0,y1-y0,lw=.28,layer="PANELS")
    p.txt((x0+x1)/2,865,"SECTION AT A-A",size=9.2,bold=True,align="center")
    p.txt(x0+10,y0+9,"LONGITUDINAL ELEVATION (REFERENCE RECONSTRUCTION)",size=6.3)
    s=min(PT_PER_M_AT_75, 530/max(inp.length_m,1))
    xa=390; xb=xa+s*inp.length_m
    yc=933; top=yc+24; bottom=yc-38
    # apron and gl line
    l_apron = max(65,min(230,s*gad.left_apron_m)); r_apron = max(65,min(160,s*gad.right_apron_m))
    p.line(120,yc-12,1038,yc-12,lw=.9,layer="TERRAIN")
    # stone apron outside structure
    p.rect(xa-l_apron,yc-12,l_apron,15,fill=(.95,.96,.96),lw=.7,layer="APRONS")
    p.rect(xb,yc-12,r_apron,15,fill=(.95,.96,.96),lw=.7,layer="APRONS")
    _stone_fill(p,xa-l_apron+5,yc-9,xa-5,yc+1,stride=15)
    _stone_fill(p,xb+5,yc-9,xb+r_apron-5,yc+1,stride=15)
    # floor PCC/bottom; top deck and void shown lengthwise
    p.rect(xa,bottom,xb-xa,7,fill=(.88,.88,.88),lw=.9,layer="STRUCTURE")
    p.rect(xa,bottom-7,xb-xa,5,fill=(.94,.94,.94),lw=.45,layer="PCC")
    p.rect(xa,top,xb-xa,9,fill=(.81,.84,.86),lw=1,layer="STRUCTURE")
    p.rect(xa,yc-32,6,(top-yc+32),fill=(.85,.85,.85),lw=.8,layer="STRUCTURE")
    p.rect(xb-6,yc-32,6,(top-yc+32),fill=(.85,.85,.85),lw=.8,layer="STRUCTURE")
    # side elevation haunch indication
    for x,sgn in ((xa,1),(xb,-1)):
        p.path([(x+sgn*6,top),(x+sgn*14,top),(x+sgn*6,top-8)],lw=.5,closed=True,fill=(.68,.72,.75),layer="STRUCTURE")
    # bedding and embankment side slopes
    p.path([(xa-125,yc-10),(xa-112,yc+13),(xa-35,top+3),(xa,top+8)],lw=.7,layer="EMBANKMENT")
    p.path([(xb,top+8),(xb+33,top+3),(xb+102,yc+12),(xb+115,yc-10)],lw=.7,layer="EMBANKMENT")
    # two lanes crowned center and wearing/earth cushion
    mid=(xa+xb)/2
    p.path([(xa,top+15),(mid,top+24),(xb,top+15)],lw=1.2,layer="ROAD")
    p.path([(xa,top+12),(mid,top+20),(xb,top+12)],lw=.7,layer="ROAD")
    # railing pedestals
    for xx in (xa+11,xb-11):
        p.rect(xx-6,top+11,12,33,lw=.75,fill=(.90,.90,.90),layer="RAILING")
        p.line(xx-7,top+47,xx+7,top+47,lw=1.1,layer="RAILING")
        p.txt(xx,top+52,"MS",size=6.8,align="center")
        p.txt(xx,top+59,"RAILING",size=6.0,align="center")
    # curtain walls / foundation pads at both ends of approaches
    for xx,label in [(xa-108,"CURTAIN WALL-I"),(xb+96,"CURTAIN WALL-II")]:
        p.rect(xx-6,yc-33,12,36,lw=.6,fill=(.88,.88,.88),layer="FOUNDATION")
        p.rect(xx-24,yc-42,48,9,lw=.6,fill=(.86,.86,.86),layer="FOUNDATION")
        p.txt(xx,yc-53,label,size=6.6,align="center")
    # ground, invert and road reference labels
    for x, label in [(xa+20,f"FRL1 {gad.frl1_m:.3f}"),(mid,f"FRL2 {gad.frl2_m:.3f}"),(xb-20,f"FRL3 {gad.frl3_m:.3f}")]:
        p.txt(x,top+36,label,size=7.7,align="center",bold=True)
    p.txt(145,yc+3,"G.L.",size=8,bold=True)
    p.txt(xb+r_apron+20,yc+3,"G.L.",size=8,bold=True)
    p.txt(xa+10,bottom+14,f"IL-1  {gad.invert_level_left_m:.3f}",size=6.8)
    p.txt(xb-10,bottom+14,f"IL-2  {gad.invert_level_right_m:.3f}",size=6.8,align="right")
    p.txt(mid,top-1,(f"HAUNCH {inp.haunch_width_m*1000:.0f}x{inp.haunch_height_m*1000:.0f} mm (TYP.)"
                           if inp.haunch_count else "HAUNCHES OFF"),size=7,align="center")
    p.txt(xa+22,bottom-17,f"{gad.pcc_bedding_mm} mm PCC BEDDING (REF.)",size=6.2)
    p.txt(xa-62,yc+14,"D/S",size=7,bold=True,align="center")
    p.txt(xb+65,yc+14,"U/S",size=7,bold=True,align="center")
    # dimensions across the top
    p.dim_h(xa,xb,1092,f"OVERALL BOX LENGTH L = {inp.length_m:.3f} m",source_y=top+10,size=7.7)
    p.dim_h(xa-l_apron,xa,1065,f"{gad.left_apron_m:.1f} m APRON",source_y=yc+3,size=6.8)
    p.dim_h(xb,xb+r_apron,1065,f"{gad.right_apron_m:.1f} m APRON",source_y=yc+3,size=6.8)
    p.dim_h(xa+gad.railing_pedestal_width_m*s,mid,1049,"L1",size=7.0)
    p.dim_h(mid,xb-gad.railing_pedestal_width_m*s,1049,"L2",size=7.0)
    p.txt(mid+78,top+12,f"CAMBER {gad.road_camper_left_percent:+.1f}% / {gad.road_camper_right_percent:+.1f}%",size=6.6)


def _plan(p, inp, gad):
    x0,y0,x1,y1=75,324,1154,847
    p.rect(x0,y0,x1-x0,y1-y0,lw=.30,layer="PANELS")
    p.txt((x0+x1)/2,332,"PLAN",size=9.5,bold=True,align="center")
    p.txt((x0+x1)/2,320,"(REFERENCE ARRANGEMENT - WING WALL FOOTPRINTS DIAGRAMMATIC)",size=6.3,align="center")
    # central box in plan - longitudinal dimension across the sheet
    s=min(PT_PER_M_AT_75, 500/max(inp.length_m,1), 125/(inp.clear_width_m+2*inp.wall_thickness_m))
    xa=385; xb=xa+s*inp.length_m
    centre=585; opening=inp.clear_width_m*s; total=(inp.clear_width_m+2*inp.wall_thickness_m)*s
    ya=centre-total/2; yb=centre+total/2
    left_extent=min(245,max(60, s*gad.left_apron_m, s*gad.wingwall_left_length_m*.85+25))
    right_extent=min(218,max(60, s*gad.right_apron_m, s*gad.wingwall_right_length_m*.85+25))
    xa0=max(130,xa-left_extent); xb0=min(1080,xb+right_extent)
    # road surrounding background on center and embankment
    p.rect(xa,387,xb-xa,425,lw=.5,fill=(.988,.988,.988),layer="PAVEMENT")
    # field riprap at approaches
    for xlo,xhi in [(xa0,xa),(xb,xb0)]:
        p.rect(xlo,398,xhi-xlo,407,lw=.45,fill=(.977,.977,.977),layer="EMBANKMENT")
        _stone_fill(p,xlo+10,410,xhi-10,500,stride=22)
        _stone_fill(p,xlo+10,675,xhi-10,794,stride=22)
    # upper and lower road margins and pavement center line
    p.line(xa,396,xa,810,lw=1.1,layer="BOUNDARY")
    p.line(xb,396,xb,810,lw=1.1,layer="BOUNDARY")
    p.line((xa+xb)/2,396,(xa+xb)/2,810,lw=.55,dash=(6,4),layer="CENTERLINE")
    p.txt((xa+xb)/2+11,765,"C/L OF CARRIAGEWAY",size=7,angle=90)
    # cross-sectional box strip in plan
    p.rect(xa,ya,xb-xa,total,lw=1.1,fill=(.975,.975,.975),layer="STRUCTURE")
    p.line(xa,ya+inp.wall_thickness_m*s,xb,ya+inp.wall_thickness_m*s,lw=.6,layer="STRUCTURE")
    p.line(xa,yb-inp.wall_thickness_m*s,xb,yb-inp.wall_thickness_m*s,lw=.6,layer="STRUCTURE")
    p.line(xa,centre,xb,centre,lw=.5,dash=(8,6),layer="CENTERLINE")
    # railings are longitudinal at both edges of 12m box, reference diagram uses side lines
    for x in (xa+4,xb-4):
        p.line(x,ya,x,yb,lw=1.5,layer="RAILING")
        for y in (ya+8,centre,yb-8): p.circle(x,y,2.0,lw=.35,layer="RAILING")
    # wing walls flare to aprons, four corners
    extL=min(s*gad.wingwall_left_length_m*.85,xa-xa0-17)
    extR=min(s*gad.wingwall_right_length_m*.85,xb0-xb-17)
    flareL=min(110,max(32,(gad.wingwall_left_breadth_m-total/s)*s/2))
    flareR=min(110,max(32,(gad.wingwall_right_breadth_m-total/s)*s/2))
    quad=[
        [(xa,ya),(xa-extL,ya-flareL),(xa-extL-13,ya-flareL-5),(xa,ya-13)],
        [(xa,yb),(xa-extL,yb+flareL),(xa-extL-13,yb+flareL+5),(xa,yb+13)],
        [(xb,ya),(xb+extR,ya-flareR),(xb+extR+13,ya-flareR-5),(xb,ya-13)],
        [(xb,yb),(xb+extR,yb+flareR),(xb+extR+13,yb+flareR+5),(xb,yb+13)],
    ]
    for wing in quad:
        p.path(wing,lw=1.2,closed=True,fill=(.94,.94,.94),layer="WING_WALLS")
        p.line(wing[0][0],wing[0][1],wing[1][0],wing[1][1],lw=1.6,layer="WING_WALLS")
    for x,label in ((xa-extL-18,"CURTAIN WALL-I"),(xb+extR+18,"CURTAIN WALL-II")):
        p.line(x,ya-70,x,yb+70,lw=1,layer="CURTAIN_WALL")
        p.txt(x,centre+91,label,size=6.7,align="center",angle=90)
    # flow/axis callouts
    p.txt(xa-60,centre,"D/S",size=8,bold=True,align="center")
    p.txt(xb+60,centre,"U/S",size=8,bold=True,align="center")
    p.txt((xa+xb)/2,centre-2,f"BOX OPENING B = {inp.clear_width_m:.3f} m",size=7.8,align="center")
    p.txt((xa+xb)/2,centre+14,"HAUNCH (TYP.) / REFERENCE",size=7.0,align="center")
    # section identifiers and leaders
    p.line(xa0+8,centre,xb0-8,centre,lw=.5,dash=(7,5),layer="CENTERLINE")
    for x,label in [(xa0+20,"A"),(xb0-12,"A"),(xb+83,"B")]:
        p.txt(x,centre+6,label,size=9,bold=True,align="center")
    for x in ((xa+xb)/2,):
        p.txt(x+11,ya-18,"C",size=8,bold=True)
        p.txt(x+11,yb+8,"C",size=8,bold=True)
        p.line(x,ya-22,x,yb+22,lw=.4,dash=(2,3),layer="CENTERLINE")
    # reference arrows to project directions (not GIS bearings)
    p.arrow(110,650,110,720,size=7,lw=1.6)
    p.txt(116,730,"SABROOM",size=8,bold=True)
    p.arrow(110,515,110,446,size=7,lw=1.6)
    p.txt(120,425,"TO TELIAMURA",size=8,bold=True)
    # lengths and cross dimensions
    p.dim_h(xa,xb,811,f"L = {inp.length_m:.3f} m",source_y=yb,size=7.8)
    p.dim_h(xa-extL-14,xa,793,f"WING WALL-I / {gad.wingwall_left_length_m:.3f} m",size=6.3)
    p.dim_h(xb,xb+extR+14,793,f"WING WALL-II / {gad.wingwall_right_length_m:.3f} m",size=6.3)
    p.dim_v(ya,yb,xb+27,f"B = {inp.clear_width_m+2*inp.wall_thickness_m:.3f} m",source_x=xb,size=6.9)
    p.txt(xa-85,736,"FLEXIBLE APRON",size=7,align="center")
    p.txt(xb+82,736,"FLEXIBLE APRON",size=7,align="center")
    p.txt((xa+xb)/2,414,"ROAD AXIS / TWO-LANE PAVED SHOULDER",size=7,align="center")


def _key_plan(p,gad):
    x0,y0,w,h=1170,847,463,258
    p.rect(x0,y0,w,h,lw=.7,layer="KEY_PLAN")
    p.txt(x0+10,y0+h-20,"TO TELIAMURA",size=7.5,bold=True)
    p.txt(x0+w-12,y0+h-20,"SABROOM",size=7.5,bold=True,align="right")
    p.arrow(x0+50,y0+h-26,x0+15,y0+h-26,size=6)
    p.arrow(x0+w-52,y0+h-26,x0+w-14,y0+h-26,size=6)
    p.txt(x0+w/2,y0+h-20,"KEY PLAN",size=10,bold=True,align="center")
    yc=y0+104
    p.path([(x0+9,yc-13),(x0+w-12,yc-17)],lw=.9,layer="ROAD")
    p.path([(x0+9,yc+13),(x0+w-12,yc+17)],lw=.9,layer="ROAD")
    p.line(x0+9,yc,x0+w-12,yc,lw=.45,dash=(7,4),layer="ROAD")
    for j,chain in enumerate(("36+800","36+900",gad.chainage,"37+000")):
        xx=x0+73+j*102
        p.line(xx,yc-34,xx,yc+44,lw=.55,layer="ROAD")
        p.txt(xx+7,yc+49,f"CH {chain}",size=6.5,angle=90)
    p.txt(x0+w/2,y0+30,"NH-208 / CHAINAGE REFERENCE (NOT GEODETIC)",size=6.7,align="center")
    # north sign
    cx,cy=x0+w-32,y0+h-63
    p.circle(cx,cy,18,lw=.5,layer="NORTH")
    p.arrow(cx,cy-10,cx,cy+14,size=6,lw=1.2)
    p.txt(cx,cy+20,"N",size=7,bold=True,align="center")


def _notes(p,inp,gad):
    x0,y0,w,h=1170,300,463,538
    p.rect(x0,y0,w,h,lw=.65,layer="NOTES")
    p.txt(x0+8,y0+h-20,"GENERAL NOTES / REFERENCE ASSUMPTIONS",size=10,bold=True)
    notes=[
       "01. Dimensions shown in metres unless specified in mm.",
       "02. This is a vector RECONSTRUCTION of supplied Sheet 1.",
       "03. Original sheet provides plan, Section A-A and site levels.",
       "04. Box, slabs, wall, haunch and length follow current inputs.",
       "05. Wing walls, aprons and road geometry: reference diagram.",
       "06. Reference wing-wall dimensions are NOT redesigned",
       "    when box geometry changes; engineer must verify tie-ins.",
       "07. Do not infer construction set-out from this diagram.",
       "08. Reinforcement details are on source Sheet 3.",
       "09. Hydraulic/site design, PCC and wing-wall checks pending.",
       "10. Compare with signed drawing before issuing CAD.",
    ]
    yy=y0+h-44
    for note in notes:
        p.txt(x0+10,yy,note,size=8.0)
        yy-=19
    p.line(x0+8,yy-6,x0+w-8,yy-6,lw=.5,layer="NOTES")
    p.txt(x0+10,yy-28,"KEY DIMENSIONS / MATERIALS",size=9,bold=True)
    t=[
      ("Clear opening",f"{inp.clear_width_m:.3f} x {inp.clear_height_m:.3f} m"),
      ("Box length",f"{inp.length_m:.3f} m"),
      ("Side / top / bottom",f"{inp.wall_thickness_m:.3f} / {inp.top_slab_thickness_m:.3f} / {inp.bottom_slab_thickness_m:.3f} m"),
      ("Haunch",f"{inp.haunch_width_m*1000:.0f} x {inp.haunch_height_m*1000:.0f} mm" if inp.haunch_count else "OFF"),
      ("Earth cushion",f"{inp.earth_cushion_m:.3f} m (application input)"),
      ("Concrete / steel",f"{inp.concrete_grade} / {inp.steel_grade}"),
      ("WG left / right",f"{gad.wingwall_left_length_m:.3f} / {gad.wingwall_right_length_m:.3f} m (reference)"),
    ]
    yy-=46
    for k,v in t:
        p.txt(x0+10,yy,k,size=7.8,bold=True)
        p.txt(x0+177,yy,v,size=7.8)
        yy-=24
    yy-=5
    p.line(x0+8,yy+9,x0+w-8,yy+9,lw=.5,layer="NOTES")
    p.txt(x0+10,yy-11,"REFERENCE DRAWINGS",size=9,bold=True)
    p.txt(x0+10,yy-29,"Source GAD / DIM & REINF PDF: SHEET 1 OF 3",size=7.5)
    p.txt(x0+10,yy-45,"Remaining sections/details: source Sheets 2 and 3.",size=7.5)
    p.txt(x0+10,y0+12,"FOR DESIGN REVIEW ONLY - NOT A SIGNED OR APPROVED DRAWING",size=8,bold=True,color=(.63,.15,.13))


def _table_and_title(p,inp,gad):
    x0=61; x1=1633
    # reference design data table (not fully linked to hydraulic design)
    y0=190; top=283
    p.rect(x0,y0,x1-x0,top-y0,lw=.75,layer="TABLE")
    p.txt(x0+6,top-16,"DESIGN DATA (ORIGINAL GAD REFERENCE AND CURRENT BOX INPUTS)",size=8.5,bold=True)
    columns=[
      ("CHAINAGE",gad.chainage),("SPANS","1"),("B CLEAR",f"{inp.clear_width_m:.3f}"),
      ("H CLEAR",f"{inp.clear_height_m:.3f}"),("FLOW",gad.flow_direction),
      ("e1 %",f"{gad.road_camper_left_percent:.2f}"),("e2 %",f"{gad.road_camper_right_percent:.2f}"),
      ("FRL1",f"{gad.frl1_m:.3f}"),("FRL2",f"{gad.frl2_m:.3f}"),("FRL3",f"{gad.frl3_m:.3f}"),
      ("G.L.",f"{gad.ground_level_m:.3f}"),("IL1",f"{gad.invert_level_left_m:.3f}"),
      ("IL2",f"{gad.invert_level_right_m:.3f}"),("f1",f"{gad.fill_left_m:.3f}"),
      ("f2",f"{gad.fill_crown_m:.3f}"),("f3",f"{gad.fill_right_m:.3f}"),
      ("T.S.",f"{inp.top_slab_thickness_m:.3f}"),("S.W.",f"{inp.wall_thickness_m:.3f}"),
      ("B.S.",f"{inp.bottom_slab_thickness_m:.3f}"),("L",f"{inp.length_m:.3f}"),
      ("L1/L2", (f"{(inp.length_m-2*gad.railing_pedestal_width_m)/2:.2f}/"
                 f"{(inp.length_m-2*gad.railing_pedestal_width_m)/2:.2f}")
                 if inp.length_m>2*gad.railing_pedestal_width_m else "REVIEW"),
      ("WH-I",f"{gad.wingwall_left_height_m:.3f}"),("WH-II",f"{gad.wingwall_right_height_m:.3f}"),
      ("WL-I",f"{gad.wingwall_left_length_m:.3f}"),("WL-II",f"{gad.wingwall_right_length_m:.3f}"),
      ("WB-I",f"{gad.wingwall_left_breadth_m:.3f}"),("WB-II",f"{gad.wingwall_right_breadth_m:.3f}"),
      ("Pbase",f"{gad.base_pressure_t_m2:.3f}"),
    ]
    cw=(x1-x0)/len(columns)
    p.line(x0,213,x1,213,lw=.45,layer="TABLE")
    for i,(name,value) in enumerate(columns):
        xx=x0+i*cw
        if i:p.line(xx,y0,xx,258,lw=.42,layer="TABLE")
        p.txt(xx+cw/2,223,name,size=6.4,bold=True,align="center")
        p.txt(xx+cw/2,201,value,size=6.4,align="center")
    # status band
    p.txt(x0+5,top-32,"FRLs / wing-wall sizes / apron / fills / base pressure are fixed site references unless separately edited.",size=6.8)
    p.txt(x0+5,top-46,"They do not automatically follow changes to the structural box and require independent verification.",size=6.8)
    # bottom title block
    yb=57; yt=184
    p.rect(x0,yb,x1-x0,yt-yb,lw=.9,layer="TITLE_BLOCK")
    stops=[x0,x0+210,x0+465,x0+720,x0+988,x0+1245,x1]
    for xx in stops[1:-1]:p.line(xx,yb,xx,yt,lw=.5,layer="TITLE_BLOCK")
    # revisions and client
    p.txt(x0+9,yt-16,"REVISION RECORD",size=8,bold=True)
    p.line(x0,yt-28,x0+210,yt-28,lw=.5,layer="TITLE_BLOCK")
    p.txt(x0+10,yt-46,"REV  R0",size=7)
    p.txt(x0+10,yt-61,"DATE  17-05-2025",size=7)
    p.txt(x0+10,yt-76,"STATUS  ORIGINAL: FOR APPROVAL",size=7)
    p.txt(x0+10,yb+13,"THIS OUTPUT: UNSIGNED REVIEW",size=7,bold=True,color=(.64,.16,.13))
    p.txt(x0+220,yt-16,"CLIENT",size=8,bold=True)
    p.txt(x0+220,yt-37,"NHIDCL",size=9,bold=True)
    p.txt(x0+220,yt-55,"National Highways &",size=7.6)
    p.txt(x0+220,yt-68,"Infrastructure Development Corp.",size=7.3)
    p.txt(x0+475,yt-16,"EPC CONTRACTOR",size=8,bold=True)
    p.txt(x0+475,yt-38,"RANI CONSTRUCTIONS PVT. LTD.",size=8,bold=True)
    p.txt(x0+730,yt-16,"DESIGN CONSULTANT",size=8,bold=True)
    p.txt(x0+730,yt-39,"Shashwat Structural Solutions Pvt. Ltd.",size=7.6)
    p.txt(x0+998,yt-16,"PROJECT",size=8,bold=True)
    project=("Improvement / widening of NH-208", "Teliamura to Harina Section", "Package III, Tripura (source PDF)")
    for k,line in enumerate(project):p.txt(x0+998,yt-39-k*15,line,size=7)
    p.txt(stops[-2]+9,yt-16,"DRAWING TITLE",size=8,bold=True)
    p.txt(stops[-2]+9,yt-36,"GENERAL ARRANGEMENT",size=9,bold=True)
    p.txt(stops[-2]+9,yt-49,f"BOX CULVERT AT CH {gad.chainage}",size=8)
    p.txt(stops[-2]+9,yt-66,gad.issue_note,size=6.7,color=(.64,.17,.13))
    p.txt(stops[-2]+9,yt-82,"SHEET 1 OF 3",size=8,bold=True)
    p.txt(stops[-2]+9,yt-96,"A2 LANDSCAPE / CORE ~1:75",size=7.2)
    p.txt(stops[-2]+9,yb+15,"NO ORIGINAL APPROVAL OR SIGNATURES",size=6.9)


def _draw(p, inp: CulvertInputs, gad: GADInputs):
    inp.validate(); gad.validate()
    _sheet_border(p,gad)
    _section_aa(p,inp,gad)
    _plan(p,inp,gad)
    _key_plan(p,gad)
    _notes(p,inp,gad)
    _table_and_title(p,inp,gad)
    p.txt(PAGE_W/2,21,"SOURCE-BASED RECONSTRUCTION - NOT A SUBSTITUTE FOR THE ORIGINAL SIGNED DRAWING / ENGINEER-APPROVED CAD",size=8,align="center",bold=True,color=(.65,.15,.14))


def make_sheet1_pdf(inputs: CulvertInputs, gad: GADInputs | None = None) -> bytes:
    """Produce a one-page, fully vector A2-landscape Sheet-1 review PDF."""
    gad=gad or GADInputs()
    buf=BytesIO()
    c=canvas.Canvas(buf, pagesize=(PAGE_W,PAGE_H), pageCompression=1)
    c.setTitle("RCC Box Culvert - GAD Sheet 1 - Review Reconstruction")
    c.setAuthor("Python RCC Culvert Review Tool")
    _draw(_Pen(c),inputs,gad)
    c.showPage(); c.save()
    return buf.getvalue()


def sheet1_preview_png(inputs: CulvertInputs, gad: GADInputs | None = None,
                       dpi: int = 115) -> bytes:
    """Render the generated vector sheet for Streamlit preview (PyMuPDF)."""
    if dpi < 60 or dpi>240: raise ValueError("Preview dpi must be between 60 and 240")
    import fitz
    with fitz.open(stream=make_sheet1_pdf(inputs,gad),filetype="pdf") as d:
        return d[0].get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),alpha=False).tobytes("png")


def export_sheet1_dxf(inputs: CulvertInputs, gad: GADInputs | None = None) -> bytes:
    """Export **paper-layout** DXF with vector geometry/text layers, NOT a 1:1 site CAD model.

    All DXF coordinates are in A2 paper millimetres, preserving vector sheet
    positioning from the PDF; dimensions are annotated but CAD measure is at
    the drawing scale, not at real-world model size.
    """
    import ezdxf
    gad=gad or GADInputs()
    p=_Pen(None,record=True)
    _draw(p,inputs,gad)
    doc=ezdxf.new('R2010')
    doc.header['$INSUNITS']=4  # millimetres
    msp=doc.modelspace()
    cols={"BORDER":7,"PANELS":8,"GEOMETRY":7,"STRUCTURE":7,"WING_WALLS":7,
          "ROAD":7,"RAILING":7,"APRONS":8,"EMBANKMENT":8,"HATCH":8,
          "FOUNDATION":8,"PCC":8,"CURTAIN_WALL":7,"DIMENSIONS":5,
          "TABLE":7,"TITLE_BLOCK":7,"NOTES":7,"KEY_PLAN":7,"TEXT":7,
          "CENTERLINE":8,"PAVEMENT":8,"TERRAIN":8,"BOUNDARY":7,"NORTH":7}
    for layer,color in cols.items():
        doc.layers.new(layer,dxfattribs={'color':color})
    f=_PAPER_TO_MM
    for op,layer,args in p.items:
        if op=='line':
            x1,y1,x2,y2=args
            msp.add_line((x1*f,y1*f),(x2*f,y2*f),dxfattribs={'layer':layer})
        elif op=='path':
            pts,closed=args
            msp.add_lwpolyline([(x*f,y*f) for x,y in pts],close=closed,dxfattribs={'layer':layer})
        elif op=='circle':
            x,y,r=args
            msp.add_circle((x*f,y*f),r*f,dxfattribs={'layer':layer})
        elif op=='text':
            x,y,t,size,angle=args
            # DXF text position is approximate; alignments in PDF are authoritative.
            msp.add_text(t,dxfattribs={'layer':layer,'height':max(size*f,.6),'rotation':angle,'insert':(x*f,y*f)})
    b=BytesIO()
    # ezdxf write is text stream; save into a UTF-8 byte buffer for downloads.
    import io
    output=io.StringIO()
    doc.write(output)
    return output.getvalue().encode('utf-8')


def write_sheet1_files(inputs: CulvertInputs, output_dir: str | Path,
                       gad: GADInputs | None = None):
    """Convenience helper for running a local preview and exporting all files."""
    directory=Path(output_dir)
    directory.mkdir(parents=True,exist_ok=True)
    pdf=directory/'GAD_Sheet1_Review.pdf'
    png=directory/'GAD_Sheet1_Review.png'
    pdf.write_bytes(make_sheet1_pdf(inputs,gad))
    png.write_bytes(sheet1_preview_png(inputs,gad))
    dxf.write_bytes(export_sheet1_dxf(inputs,gad))
    return {'pdf':pdf,'png':png}
