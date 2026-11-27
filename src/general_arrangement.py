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
from reportlab.pdfbase.pdfmetrics import stringWidth
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
        if self.record:
            # Preserve PDF text alignment in both existing DXF exporters.
            width = stringWidth(text, "Helvetica-Bold" if bold else "Helvetica", size)
            shift = width / 2 if align == "center" else width if align == "right" else 0
            a = math.radians(angle)
            self.items.append(("text",layer,(x-shift*math.cos(a),y-shift*math.sin(a),text,size,angle)))

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
    """Longitudinal box envelope in consistent fitted scale; site levels are independent."""
    x0,y0,x1,y1=75,862,1154,1105
    p.rect(x0,y0,x1-x0,y1-y0,lw=.28,layer="PANELS")
    h=inp.clear_height_m; t=inp.top_slab_thickness_m; b=inp.bottom_slab_thickness_m
    L=inp.length_m; il1=gad.invert_level_left_m; il2=gad.invert_level_right_m
    lo=min(il1,il2,gad.ground_level_m)-b-gad.pcc_bedding_mm/1000
    hi=max(il1+h+t,il2+h+t,gad.frl1_m,gad.frl2_m,gad.frl3_m,gad.ground_level_m,il1+gad.wingwall_left_height_m,il2+gad.wingwall_right_height_m)
    # Fit the complete length and vertical envelope, never clamp a physical dimension.
    scale=min(PT_PER_M_AT_75, 920/(L+gad.left_apron_m+gad.right_apron_m+gad.wingwall_left_length_m+gad.wingwall_right_length_m), 118/max(hi-lo,.01))
    xa=125+(gad.left_apron_m+gad.wingwall_left_length_m)*scale; xb=xa+L*scale; mid=(xa+xb)/2
    def y(level): return 903+(level-lo)*scale
    def strip(low,high,layer,fill):
        p.path([(xa,y(il1+low)),(xb,y(il2+low)),(xb,y(il2+high)),(xa,y(il1+high))],closed=True,fill=fill,layer=layer)
    strip(-b,0,"STRUCTURE",(.88,.88,.88));strip(h,h+t,"STRUCTURE",(.82,.85,.87))
    strip(-b-gad.pcc_bedding_mm/1000,-b,"PCC",(.95,.95,.95))
    p.line(xa,y(il1),xb,y(il2),layer="STRUCTURE")
    p.line(xa,y(il1+h),xb,y(il2+h),layer="STRUCTURE")
    p.path([(xa,y(gad.frl1_m)),(mid,y(gad.frl2_m)),(xb,y(gad.frl3_m))],lw=1.2,layer="ROAD")
    p.line(xa-(gad.left_apron_m+gad.wingwall_left_length_m)*scale,y(gad.ground_level_m),xb+(gad.right_apron_m+gad.wingwall_right_length_m)*scale,y(gad.ground_level_m),dash=(5,3),layer="TERRAIN")
    for x,il,sgn,length,wl,wh,name in [(xa,il1,-1,gad.left_apron_m,gad.wingwall_left_length_m,gad.wingwall_left_height_m,"D/S"),(xb,il2,1,gad.right_apron_m,gad.wingwall_right_length_m,gad.wingwall_right_height_m,"U/S")]:
        wing_end=x+sgn*wl*scale
        end=wing_end+sgn*length*scale
        # Wing outline is diagrammatic: length and height are independent site inputs.
        p.path([(x,y(il)),(wing_end,y(il)),(wing_end,y(il+wh*.25)),(x,y(il+wh))],closed=True,layer="WING_WALLS",lw=.7)
        p.line(wing_end,y(il),end,y(il),layer="APRONS")
        p.txt((wing_end+end)/2,y(il)-9,name+" APRON",size=6,align="center")
        p.dim_h(min(wing_end,end),max(wing_end,end),1047,f"{length:g} m",size=6.5)
        p.dim_h(min(x,wing_end),max(x,wing_end),1047,f"WL {wl:.3f}",size=6.5)
        p.txt((x+wing_end)/2,y(il)+18,"WING WALL (REF.)",size=6,align="center")
    p.dim_h(xa,xb,1080,f"BOX LENGTH {L:.3f} m",size=8)
    p.dim_v(y(il1),y(il1+h),xa+12,f"H {h:.3f} m",size=7)
    p.txt(xa+22,y(il1+h/2),f"TOP {t*1000:g} / BOTTOM {b*1000:g} mm",size=7)
    p.txt(xa,1028,f"FRL1 {gad.frl1_m:.3f}",size=7);p.txt(mid,1028,f"FRL2 {gad.frl2_m:.3f}",size=7,align="center");p.txt(xb,1028,f"FRL3 {gad.frl3_m:.3f}",size=7,align="right")
    p.txt(xa,890,f"IL1 {il1:.3f}",size=7);p.txt(xb,890,f"IL2 {il2:.3f}",size=7,align="right")
    p.txt((x0+x1)/2,873,"SECTION A-A | BOX ENVELOPE AND INDEPENDENT SITE LEVELS | REVIEW",size=8,align="center",bold=True)
    # Do not silently reconcile independent survey levels with changed box geometry.
    if min(gad.frl1_m-(il1+h+t),gad.frl3_m-(il2+h+t),gad.frl2_m-((il1+il2)/2+h+t))<0:
        p.txt(x0+12,1100,"REVIEW: ROAD LEVEL BELOW BOX TOP",size=7,color=(.65,.15,.14))


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
    p.txt(stops[-2]+9,yt-96,"A2 LANDSCAPE / FITTED SCALE",size=7.2)
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
    doc.set_modelspace_vport(height=PAGE_H*f*1.08, center=(PAGE_W*f/2,PAGE_H*f/2))
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
    dxf=directory/'GAD_Sheet1_Review.dxf'
    dxf.write_bytes(export_sheet1_dxf(inputs,gad))
    return {'pdf':pdf,'png':png,'dxf':dxf}


# Native model-space GAD export. The existing PDF/PNG renderer is retained.
def export_sheet1_dxf(inputs: CulvertInputs, gad: GADInputs | None = None) -> bytes:
    """1:1 millimetre geometry; A2 paper viewports. Site outlines are review-only."""
    import ezdxf
    from ezdxf.enums import TextEntityAlignment
    from io import StringIO
    inputs.validate()
    gad = (gad or GADInputs()).validate()
    if inputs.number_of_cells != 1:
        raise ValueError('Only single-cell GAD is supported')
    L,B,H,t,b,w = (1000*float(v) for v in (inputs.length_m,inputs.clear_width_m,inputs.clear_height_m,inputs.top_slab_thickness_m,inputs.bottom_slab_thickness_m,inputs.wall_thickness_m))
    W=B+2*w
    a,c,u,v,bl,br = (1000*float(q) for q in (gad.left_apron_m,gad.right_apron_m,gad.wingwall_left_length_m,gad.wingwall_right_length_m,gad.wingwall_left_breadth_m,gad.wingwall_right_breadth_m))
    doc=ezdxf.new('R2010'); doc.units=4
    m=doc.modelspace()
    for name,col in [('BOX',7),('SITE_REFERENCE',8),('DIMENSIONS',2),('ANNOTATION',7),('CENTERLINE',4),('PCC',8),('SHEET',7),('APRON_HATCH',8),('ROAD',7),('RAILING',7),('WING_WALL',7),('CURTAIN_WALL',7),('LEVELS',8),('SECTION_MARKS',7)]:
        doc.layers.new(name,dxfattribs={'color':col})
    def poly(p,pts,layer='BOX',close=False):
        return p.add_lwpolyline(pts,close=close,dxfattribs={'layer':layer})
    def rect(p,x,y,ww,hh,layer='BOX'):
        return poly(p,[(x,y),(x+ww,y),(x+ww,y+hh),(x,y+hh)],layer,True)
    def line(p,x,y,xx,yy,layer='BOX'):
        p.add_line((x,y),(xx,yy),dxfattribs={'layer':layer})
    def text(p,x,y,s,h=150,center=False,layer='ANNOTATION'):
        e=p.add_text(str(s),dxfattribs={'height':h,'layer':layer}); e.set_placement((x,y),align=TextEntityAlignment.MIDDLE_CENTER if center else TextEntityAlignment.LEFT)
    def dim(p,x1,x2,y,base):
        d=p.add_linear_dim(base=(x1,y),p1=(x1,base),p2=(x2,base),angle=0,dimstyle='Standard',override={'dimtxt':140,'dimasz':100,'dimdec':0},dxfattribs={'layer':'DIMENSIONS'});d.render()
    def vdim(p,y1,y2,x,base):
        d=p.add_linear_dim(base=(x,y1),p1=(base,y1),p2=(base,y2),angle=90,dimstyle='Standard',override={'dimtxt':140,'dimasz':100,'dimdec':0},dxfattribs={'layer':'DIMENSIONS'});d.render()
    def hatch_rect(x,y,ww,hh):
        # Native hatch, not an image; ANSI pattern is symbolic stone fill.
        h=m.add_hatch(dxfattribs={'layer':'APRON_HATCH'})
        h.set_pattern_fill('AR-CONC',scale=25)
        h.paths.add_polyline_path([(x,y),(x+ww,y),(x+ww,y+hh),(x,y+hh)],is_closed=True)
    def leader(x,y,tx,ty,label):
        line(m,x,y,tx,ty,'ANNOTATION'); text(m,tx+80,ty+50,label,135)
    # Plan: longitudinal axis is horizontal as in reference Sheet 1.
    rect(m,0,-W/2,L,W)
    for y in (-B/2,B/2): line(m,0,y,L,y)
    line(m,-u-a-500,0,L+v+c+500,0,'CENTERLINE')
    line(m,L/2,-max(bl,br)/2-500,L/2,max(bl,br)/2+500,'CENTERLINE')
    for end,sgn,run,breadth,apron in [(0,-1,u,bl,a),(L,1,v,br,c)]:
        ex=end+sgn*run
        for side in (-1,1): line(m,end,side*W/2,ex,side*breadth/2,'SITE_REFERENCE')
        line(m,ex,-breadth/2,ex,breadth/2,'SITE_REFERENCE')
        rect(m,min(ex,ex+sgn*apron),-breadth/2,apron,breadth,'SITE_REFERENCE')
        hatch_rect(min(ex,ex+sgn*apron),-breadth/2,apron,breadth)
        text(m,ex+sgn*apron/2,0,'FLEXIBLE APRON',180,True)
        # Wing wall axes only: wall thickness and footing not established.
        for side in (-1,1):
            line(m,end,side*W/2,ex,side*breadth/2,'WING_WALL')
        text(m,(end+ex)/2,breadth/2+150,'U-TYPE WING WALL (AXIS)',135,True)
        text(m,ex,-breadth/2-230,'CURTAIN WALL LOCATION - REVIEW',120,True)
        dim(m,min(ex,end),max(ex,end),max(bl,br)/2+450, max(bl,br)/2)
        dim(m,min(ex,ex+sgn*apron),max(ex,ex+sgn*apron),max(bl,br)/2+450,max(bl,br)/2)
    pedestal=gad.railing_pedestal_width_m*1000
    if 2*pedestal>=L: raise ValueError('Pedestals exceed box length')
    for x in (0,L-pedestal):
        rect(m,x,-W/2,pedestal,W,'RAILING')
        line(m,x+pedestal/2,-W/2,x+pedestal/2,W/2,'RAILING')
    dim(m,pedestal,L/2,-max(bl,br)/2-300,-W/2)
    dim(m,L/2,L-pedestal,-max(bl,br)/2-300,-W/2)
    for x,label in ((-u-a-250,'A'),(L+v+c+250,'A')):
        m.add_circle((x,0),180,dxfattribs={'layer':'SECTION_MARKS'});text(m,x,0,label,150,True)
    for y in (-max(bl,br)/2-500,max(bl,br)/2+1000):
        m.add_circle((L/2,y),180,dxfattribs={'layer':'SECTION_MARKS'});text(m,L/2,y,'C',150,True)
    text(m,-u/2,-W/2-300,'D/S',170,True);text(m,L+v/2,-W/2-300,'U/S',170,True)
    dim(m,0,L,max(bl,br)/2+450,W/2)
    vdim(m,-W/2,W/2,L+350,L)
    text(m,L/2,-max(bl,br)/2-700,'PLAN - SITE OUTLINES SUBJECT TO REVIEW',180,True)
    # Separate longitudinal section with explicit invert levels; constant input height.
    oy=max(bl,br)/2+6500+b
    il0=oy; il1=oy+1000*(gad.invert_level_right_m-gad.invert_level_left_m)
    poly(m,[(0,il0-b),(L,il1-b),(L,il1),(0,il0)],close=True)
    poly(m,[(0,il0+H),(L,il1+H),(L,il1+H+t),(0,il0+H+t)],close=True)
    for x,y in [(0,il0),(L,il1)]: line(m,x,y,x,y+H,'SITE_REFERENCE')
    pcc=gad.pcc_bedding_mm
    poly(m,[(0,il0-b-pcc),(L,il1-b-pcc),(L,il1-b),(0,il0-b)],'PCC',True)
    road=[(0,oy+1000*(gad.frl1_m-gad.invert_level_left_m)),(L/2,oy+1000*(gad.frl2_m-gad.invert_level_left_m)),(L,oy+1000*(gad.frl3_m-gad.invert_level_left_m))]
    poly(m,road,'ROAD')
    # Road profile uses supplied levels; pavement thickness is not assumed.
    # Wing elevations are site reference outlines, not a designed wall profile.
    for end,sgn,run,apron,il,wh in ((0,-1,u,a,il0,gad.wingwall_left_height_m),(L,1,v,c,il1,gad.wingwall_right_height_m)):
        ex=end+sgn*run
        top=il+1000*wh
        poly(m,[(end,il),(ex,il),(ex,il+1000),(end,top)],'WING_WALL',True)
        # 1000 mm end height is shown on reference Sheet 1; verify site tie-in.
        line(m,ex,il,ex+sgn*apron,il,'SITE_REFERENCE')
        leader((end+ex)/2,(il+top)/2,(end+ex)/2,top+300,'U-TYPE WING WALL - REVIEW')
        # Curtain location only; exact vertical set-out not supplied.
        line(m,ex,il-400,ex,il+150,'CURTAIN_WALL')
        leader(ex,il,ex+sgn*apron*.35,il-600,'CURTAIN WALL - SEE SHEET 2')
        text(m,ex+sgn*apron/2,il+150,'FLEXIBLE APRON',145,True)
    for x,y in (road[0],road[-1]):
        # Reference railing symbol; height not used as a fabrication dimension.
        leader(x,y,x+(350 if x==0 else -1600),y+750,'MS RAILING / PEDESTAL - DETAIL PENDING')
    leader(L*.35,il0-b-pcc,L*.20,il0-b-pcc-350,f'{pcc:g} mm PCC BEDDING')
    text(m,L/2,il0+H/2,'CLEAR OPENING',180,True)
    for j,(x,y) in enumerate(road,1): text(m,x,y+220,f'FRL{j} {getattr(gad,f"frl{j}_m"):.3f}',150,True)
    for x,y,n,z in [(0,il0,1,gad.invert_level_left_m),(L,il1,2,gad.invert_level_right_m)]: text(m,x,y+200,f'IL-{n} {z:.3f}',140)
    gl=oy+1000*(gad.ground_level_m-gad.invert_level_left_m)
    line(m,-u-a,gl,L+v+c,gl,'SITE_REFERENCE'); text(m,-u-a,gl+150,'G.L. (site input)')
    dim(m,0,L,max(y for x,y in road)+650, max(y for x,y in road))
    vdim(m,il0,il0+H,-450,0); vdim(m,il0-b,il0,-450,0)
    vdim(m,il0+H,il0+H+t,-450,0)
    text(m,L/2,oy-b-pcc-650,'SECTION A-A - SITE TIE-INS FOR REVIEW',180,True)
    # Paper layout references the model using independent viewports.
    sheet=doc.layouts.new('GAD A2'); sheet.page_setup(size=(594,420),margins=(0,0,0,0),units='mm')
    rect(sheet,8,8,578,404,'SHEET')
    xmin=-u-a-700; xmax=L+v+c+700; cx=(xmin+xmax)/2
    def viewport(center,size,cy,span):
        vh=max(span,(xmax-xmin)*size[1]/size[0])*1.06
        sheet.add_viewport(center=center,size=size,view_center_point=(cx,cy),view_height=vh,dxfattribs={'layer':'SHEET','flags':16384})
    viewport((202,315),(376,155),oy+H/2,max(H+4500,abs(gl-oy)+H+3000))
    viewport((202,171),(376,128),0,max(bl,br)+2000)
    text(sheet,400,390,'KEY PLAN / SITE DATA',3.5)
    for i,s in enumerate([f'Chainage: {gad.chainage}',f'Flow direction: {gad.flow_direction}', 'Key plan alignment not supplied.', 'Wing wall axes and elevations only.', 'Curtain wall set-out pending.', 'Apron hatch symbolic; review only.', 'Not for construction.']): text(sheet,400,380-i*7,s,2.8)
    rect(sheet,14,52,566,36,'SHEET')
    for i,(k,val) in enumerate([('Length',L),('Clear span',B),('Clear height',H),('Side wall',w),('Top slab',t),('Base slab',b)]):
        x=14+i*94; line(sheet,x,52,x,88,'SHEET'); text(sheet,x+4,77,k,3); text(sheet,x+4,65,f'{val:g} mm',3.5)
    rect(sheet,14,14,566,32,'SHEET'); text(sheet,20,34,'GENERAL ARRANGEMENT DRAWING OF BOX CULVERT',4); text(sheet,20,22,f'CH. {gad.chainage} | SHEET 1 OF 3 | A2 | ENGINEERING REVIEW',3)
    text(sheet,440,34,'MODEL: 1:1 mm',3); text(sheet,440,22,'Approval: pending',3)
    doc.set_modelspace_vport(height=oy+H+max(bl,br)/2+3000,center=(cx,(oy+H-max(bl,br)/2)/2))
    out=StringIO(); doc.write(out); return out.getvalue().encode('utf-8')

# Complete editable presentation sheet for CAD viewers without layout support.
export_sheet1_measured_dxf = export_sheet1_dxf

def export_sheet1_dxf(inputs: CulvertInputs, gad: GADInputs | None = None) -> bytes:
    import ezdxf
    from ezdxf import bbox
    from ezdxf.enums import TextEntityAlignment
    from io import StringIO
    class SheetPen(_Pen):
        def txt(self,x,y,text,size=8,bold=False,align='left',color=(.15,.19,.23),angle=0,layer='TEXT'):
            self.items.append(('aligned',layer,(x,y,str(text),size,angle,align)))
    p=SheetPen(None,record=True)
    _draw(p,inputs,gad or GADInputs())
    doc=ezdxf.new('R2010'); doc.units=4; m=doc.modelspace(); f=_PAPER_TO_MM
    for op,layer,a in p.items:
        if layer not in doc.layers: doc.layers.new(layer,dxfattribs={'color':2 if layer=='DIMENSIONS' else 7})
        attr={'layer':layer}
        if op=='line':
            x,y,u,v=a; m.add_line((x*f,y*f),(u*f,v*f),dxfattribs=attr)
        elif op=='path':
            pts,closed=a; m.add_lwpolyline([(x*f,y*f) for x,y in pts],close=closed,dxfattribs=attr)
        elif op=='circle':
            x,y,r=a; m.add_circle((x*f,y*f),r*f,dxfattribs=attr)
        elif op=='aligned':
            x,y,t,size,angle,align=a
            m.add_text(t,dxfattribs={**attr,'height':size*f,'rotation':angle}).set_placement((x*f,y*f),align={'left':TextEntityAlignment.LEFT,'center':TextEntityAlignment.CENTER,'right':TextEntityAlignment.RIGHT}[align])
    m.add_text('PAPER-SCALE REVIEW SHEET - USE FULL-SIZE EXPORT FOR MEASUREMENTS',dxfattribs={'height':2.4,'insert':(26,3)})
    ext=bbox.extents(m); doc.set_modelspace_vport(height=max(ext.size.y,ext.size.x/1.35)*1.10,center=(ext.center.x,ext.center.y))
    out=StringIO(); doc.write(out); return out.getvalue().encode('utf-8')
