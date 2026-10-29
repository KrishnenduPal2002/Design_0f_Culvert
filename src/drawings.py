"""RCC culvert cross-section and Stage 2/3 *illustrative* load drawings.

Each illustration is a readable reference schematic, NOT an approved
structural drawing, reinforcement detail, STAAD model, or load combination.
The drawing uses the same inputs as the Streamlit calculation engine.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Polygon, Rectangle

from .geometry import calculate_geometry
from .inputs import CulvertInputs

INK = "#243447"
CONCRETE = "#c7d1dc"
HAUNCH = "#91a3b5"
SOIL = "#c45a28"
SURCHARGE = "#2576b7"


def haunch_triangles(inputs: CulvertInputs) -> list[list[tuple[float, float]]]:
    """Return four internal right-triangle polygons; [] when disabled."""
    inputs.validate()
    if inputs.haunch_count == 0:
        return []
    x0 = inputs.wall_thickness_m
    x1 = x0 + inputs.clear_width_m
    y0 = inputs.bottom_slab_thickness_m
    y1 = y0 + inputs.clear_height_m
    a, b = inputs.haunch_width_m, inputs.haunch_height_m
    return [
        [(x0, y0), (x0 + a, y0), (x0, y0 + b)],
        [(x1, y0), (x1 - a, y0), (x1, y0 + b)],
        [(x0, y1), (x0 + a, y1), (x0, y1 - b)],
        [(x1, y1), (x1 - a, y1), (x1, y1 - b)],
    ]


def _section(ax, inputs: CulvertInputs):
    """Draw geometry and return the validated geometry result."""
    geom = calculate_geometry(inputs)
    ax.add_patch(Rectangle(
        (0, 0), geom.external_width_m, geom.external_height_m,
        facecolor=CONCRETE, edgecolor=INK, linewidth=2.5, zorder=2,
    ))
    ax.add_patch(Rectangle(
        (inputs.wall_thickness_m, inputs.bottom_slab_thickness_m),
        inputs.clear_width_m, inputs.clear_height_m,
        facecolor="white", edgecolor=INK, linewidth=2.1, zorder=3,
    ))
    for coords in haunch_triangles(inputs):
        ax.add_patch(Polygon(coords, closed=True, facecolor=HAUNCH,
                             edgecolor=INK, linewidth=1.6, zorder=4))
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    return geom


def _dimension(ax, start, end, label, text_xy, rotation=0, fontsize=10):
    """Keep labels INSIDE their axes, preventing cropped dimension text."""
    ax.annotate("", xy=end, xytext=start,
                arrowprops={"arrowstyle": "<->", "lw": 1.45, "color": INK}, zorder=7)
    ax.text(*text_xy, label, rotation=rotation, fontsize=fontsize,
            ha="center", va="center", color=INK, zorder=8,
            bbox={"boxstyle": "round,pad=.23", "facecolor": "white",
                  "edgecolor": "none", "alpha": .94})


def draw_culvert(inputs: CulvertInputs):
    """Return a legible, dimensioned cross-section figure for the UI/PNG."""
    g = calculate_geometry(inputs)
    w, h = g.external_width_m, g.external_height_m
    wall, bottom = inputs.wall_thickness_m, inputs.bottom_slab_thickness_m
    cw, ch = inputs.clear_width_m, inputs.clear_height_m

    fig = plt.figure(figsize=(12.0, 7.2), dpi=135, facecolor="white")
    grid = fig.add_gridspec(1, 2, width_ratios=[3.8, 1.5],
                           left=.045, right=.965, top=.86, bottom=.14, wspace=.10)
    ax = fig.add_subplot(grid[0, 0])
    info = fig.add_subplot(grid[0, 1])
    _section(ax, inputs)

    # Four separate labels with deliberate space on all sides, not relying on
    # bbox_inches='tight' to rescue text positioned beyond the plotting area.
    cy = bottom + ch * .36
    cx = wall + cw * .70
    _dimension(ax, (wall+.055, cy), (wall+cw-.055, cy),
               f"Clear width  {cw:.3f} m", (wall+cw/2, cy+.13))
    _dimension(ax, (cx, bottom+.06), (cx, bottom+ch-.06),
               f"Clear height  {ch:.3f} m", (cx+.16, bottom+ch/2), rotation=90)
    _dimension(ax, (0, -.28), (w, -.28),
               f"External width  {w:.3f} m", (w/2, -.47))
    _dimension(ax, (w+.29, 0), (w+.29, h),
               f"External height  {h:.3f} m", (w+.54, h/2), rotation=90)
    ax.set_xlim(-.42, w+.85)
    ax.set_ylim(-.69, h+.30)

    info.axis("off")
    haunch_line = (f"4 corners, {inputs.haunch_width_m*1000:.0f} × "
                   f"{inputs.haunch_height_m*1000:.0f} mm" if inputs.haunch_count
                   else "Disabled")
    entries = [
        ("CLEAR OPENING", f"{cw:.3f} m × {ch:.3f} m"),
        ("BOX LENGTH", f"{inputs.length_m:.3f} m"),
        ("SIDE WALL", f"{inputs.wall_thickness_m:.3f} m"),
        ("TOP SLAB", f"{inputs.top_slab_thickness_m:.3f} m"),
        ("BOTTOM SLAB", f"{bottom:.3f} m"),
        ("HAUNCHES", haunch_line),
    ]
    ypos = .94
    for label, val in entries:
        info.text(.03, ypos, label, color="#53677a", fontsize=9, fontweight="bold",
                  va="top", transform=info.transAxes)
        info.text(.03, ypos-.042, val, color=INK, fontsize=12,
                  va="top", transform=info.transAxes)
        ypos -= .145
    fig.suptitle("RCC BOX CULVERT  |  DIMENSIONED CROSS-SECTION",
                 y=.955, fontsize=16, fontweight="bold", color=INK)
    fig.text(.5, .058,
             "SCHEMATIC ONLY  •  Not a construction or reinforcement drawing  •  "
             "Haunch area differs from the reference design-note dead-load table",
             ha="center", fontsize=9, color="#a3342a")
    return fig


def draw_load_schematic(inputs: CulvertInputs, earth_inputs=None, braking_inputs=None,
                        braking_case: str = "Class 70R(W) + A"):
    """Return distinct soil/surcharge illustrations and braking *reference* data.

    Orange arrows are displayed at the left, blue at the right purely to
    separate the diagrams visually. This is NOT an engineering claim that
    each component acts only on that wall. No axle loads, factor combinations,
    structural responses or applied braking directions are derived here.
    """
    from .earth_pressure import EarthPressureInputs, pressure_at_depth
    from .traffic_loads import braking_cases

    if earth_inputs is None:
        earth_inputs = EarthPressureInputs(earth_cushion_m=inputs.earth_cushion_m)
    earth_inputs.validate()
    g = calculate_geometry(inputs)
    w, h = g.external_width_m, g.external_height_m
    wall_bottom = inputs.bottom_slab_thickness_m
    wall_top = wall_bottom + inputs.clear_height_m

    fig = plt.figure(figsize=(13.2, 7.6), dpi=135, facecolor="white")
    grid = fig.add_gridspec(2, 2, width_ratios=[1.2, 1.0],
                           height_ratios=[.85, 1.15],
                           left=.052, right=.96, top=.86, bottom=.13,
                           wspace=.30, hspace=.40)
    ax = fig.add_subplot(grid[:, 0])
    curve = fig.add_subplot(grid[0, 1])
    summary = fig.add_subplot(grid[1, 1])
    _section(ax, inputs)

    ys = [wall_bottom + (wall_top-wall_bottom)*i/8 for i in range(9)]
    samples = [pressure_at_depth(h-y, earth_inputs) for y in ys]
    max_soil = max((p["earth_pressure_t_m2"] for p in samples), default=0)
    arrow_span = min(1.05, w*.30)
    for y, p in zip(ys, samples):
        # Soil pressure increases with depth; use arrow length only to show
        # relative magnitudes, NOT to define a structural loading profile.
        alen = .10 + arrow_span * p["earth_pressure_t_m2"] / max(max_soil, 1e-9)
        ax.add_patch(FancyArrowPatch((-.08-alen, y), (-.08, y),
                                     arrowstyle="-|>", mutation_scale=11,
                                     lw=1.55, color=SOIL, zorder=6))
        ax.add_patch(FancyArrowPatch((w+.82, y), (w+.08, y),
                                     arrowstyle="-|>", mutation_scale=11,
                                     lw=1.45, color=SURCHARGE, zorder=6))
    ax.set_xlim(-1.55, w+1.13)
    ax.set_ylim(-.25, h+.50)
    ax.text(-.63, h+.18, "SOIL", color=SOIL, fontweight="bold", fontsize=11, ha="center")
    ax.text(w+.46, h+.18, "SURCHARGE", color=SURCHARGE,
            fontweight="bold", fontsize=10, ha="center")

    # Chart is tied directly to Stage 2 calculation functions. Showing each
    # pressure separately avoids implying an approved design load combination.
    depths = [h-y for y in ys]
    fine_depths = [min(depths) + (max(depths)-min(depths))*j/80 for j in range(81)]
    soil_pressures = [pressure_at_depth(z, earth_inputs)["earth_pressure_t_m2"]
                      for z in fine_depths]
    surcharge_pressures = [pressure_at_depth(z, earth_inputs)["surcharge_pressure_t_m2"]
                           for z in fine_depths]
    curve.plot(soil_pressures, fine_depths, lw=2.4, color=SOIL, label="Soil pressure")
    curve.plot(surcharge_pressures, fine_depths, lw=2.1, color=SURCHARGE,
               linestyle="--", label="Live-load surcharge")
    curve.invert_yaxis()
    curve.set_xlabel("Lateral pressure (tf/m²)", fontsize=9)
    curve.set_ylabel("Depth below top slab top (m)", fontsize=9)
    curve.set_title("Unfactored pressure components", fontsize=11, pad=11, color=INK)
    curve.grid(alpha=.22)
    curve.legend(loc="lower right", fontsize=8)
    curve.tick_params(labelsize=8)

    p_top = pressure_at_depth(h-wall_top, earth_inputs)
    p_bottom = pressure_at_depth(h-wall_bottom, earth_inputs)
    braking_line = "Braking data: no inputs supplied"
    if braking_inputs is not None:
        entries = braking_cases(braking_inputs)
        selected = next((row for row in entries if row["Case"] == braking_case), None)
        if selected is None:
            raise ValueError(f"Unknown braking case: {braking_case}")
        braking_line = (f"{braking_case}\n"
                        f"Total horizontal force: {selected['Total force (tf)']:.3f} tf\n"
                        f"Per-metre reference: {selected['Per-metre force (tf/m)']:.3f} tf/m")

    summary.axis("off")
    summary.text(.0, .97, "DESIGN-NOTE REFERENCE VALUES", transform=summary.transAxes,
                 ha="left", va="top", fontsize=11, color=INK, fontweight="bold")
    info_lines = (
        f"K₀ = {earth_inputs.k0:.3f}   |   Soil unit weight = {earth_inputs.backfill_density_t_m3:.3f} tf/m³\n"
        f"Soil pressure at wall top: {p_top['earth_pressure_t_m2']:.3f} tf/m²\n"
        f"Soil pressure at wall bottom: {p_bottom['earth_pressure_t_m2']:.3f} tf/m²\n"
        f"Live-load surcharge: {p_top['surcharge_pressure_t_m2']:.3f} tf/m²\n"
        f"Earth cushion: {p_top['cushion_pressure_t_m2']:.3f} tf/m² (separate)"
    )
    summary.text(.0, .82, info_lines, transform=summary.transAxes,
                 va="top", ha="left", fontsize=9.1, color=INK,
                 linespacing=1.35)
    summary.text(.0, .36, "BRAKING-FORCE SUMMARY — NOT APPLIED TO MODEL",
                 transform=summary.transAxes, va="top", ha="left", fontsize=9.5,
                 fontweight="bold", color=INK)
    summary.text(.0, .25, braking_line, transform=summary.transAxes,
                 va="top", ha="left", fontsize=9.4, linespacing=1.4, color=INK)

    fig.suptitle("STAGES 2–3  |  EARTH PRESSURE & BRAKING REFERENCE",
                 y=.953, fontsize=15, fontweight="bold", color=INK)
    fig.text(.5, .072,
             "ILLUSTRATION ONLY  •  Arrow sides distinguish components, not actual load cases  •  "
             "No wheel loads, structural response or approved combinations",
             ha="center", color="#a3342a", fontsize=9)
    return fig


def export_schematic_dxf(inputs: CulvertInputs, path: str | Path):
    """Export the geometry/haunch outlines only (no load/structural design)."""
    import ezdxf

    geom = calculate_geometry(inputs)
    doc = ezdxf.new("R2010")
    doc.layers.new("BOX_OUTLINE", dxfattribs={"color": 7})
    doc.layers.new("HAUNCH", dxfattribs={"color": 5})
    doc.layers.new("ANNOTATION", dxfattribs={"color": 3})
    msp = doc.modelspace()

    def rectangle(x0, y0, width, height):
        msp.add_lwpolyline(
            [(x0, y0), (x0+width, y0), (x0+width, y0+height), (x0, y0+height)],
            close=True, dxfattribs={"layer": "BOX_OUTLINE"},
        )

    rectangle(0, 0, geom.external_width_m, geom.external_height_m)
    rectangle(inputs.wall_thickness_m, inputs.bottom_slab_thickness_m,
              inputs.clear_width_m, inputs.clear_height_m)
    for coords in haunch_triangles(inputs):
        msp.add_lwpolyline(coords, close=True, dxfattribs={"layer": "HAUNCH"})
    msp.add_text(f"CLEAR {inputs.clear_width_m:.3f} x {inputs.clear_height_m:.3f} m",
                 dxfattribs={"layer": "ANNOTATION", "height": .11, "insert": (0, -.22)})
    msp.add_text("SCHEMATIC ONLY - NOT FOR CONSTRUCTION",
                 dxfattribs={"layer": "ANNOTATION", "height": .10, "insert": (0, -.40)})
    doc.saveas(str(path))


def draw_vehicle_loading(inputs: CulvertInputs, axle_results: dict,
                         combo_results: dict | None = None):
    """Visualize selected reference axle positions and effective-width loads.

    This is a longitudinal 1-D position schematic mapped above the culvert
    cross-section for convenience. Axles outside the 3.25m effective span are
    excluded. Arrow positions and heights are illustrative, not a 3-D
    structural load model.
    """
    from matplotlib.patches import FancyArrowPatch
    geom = calculate_geometry(inputs)
    w, h = geom.external_width_m, geom.external_height_m
    span = axle_results['effective_span_m']
    fig = plt.figure(figsize=(13, 7.2), facecolor='white')
    grid=fig.add_gridspec(1,2, width_ratios=[1.28,1],
                          left=.055, right=.96, top=.84, bottom=.17, wspace=.25)
    sec=fig.add_subplot(grid[0,0])
    chart=fig.add_subplot(grid[0,1])
    _section(sec,inputs)
    active=[r for r in axle_results['axles'] if r['on_span']]
    ymax=h+.3
    lift=min(.72,.20*h)
    # A local 3.25m effective-span axis is projected over the 3.50m transverse
    # geometric section for a SCREEN-ONLY schematic. The two axes are different
    # structural directions; this is NOT a real vehicle-loading placement plan.
    left=inputs.wall_thickness_m/2
    for row in active:
        x=left+row['distance_from_left_support_m']*(w/ span)
        sec.add_patch(FancyArrowPatch((x,h+.63),(x,h+.075), arrowstyle='-|>',
                        mutation_scale=18, lw=2, color='#b45309'))
        sec.text(x, h+.74, f"W{row['wheel']}\n{row['wheel_load_tf']:.2f} tf",
                 ha='center', va='bottom',fontsize=9,color='#92400e',fontweight='bold')
    sec.set_xlim(-.28,w+.34)
    sec.set_ylim(-.28,h+1.35)
    sec.text(w/2,-.16,f"Effective span {span:.3f} m (workbook reference)",
             ha='center',va='top',fontsize=9,color=INK)
    if active:
        positions=[r['distance_from_left_support_m'] for r in active]
        intensities=[r['load_per_unit_width_tf_m'] for r in active]
        chart.bar([f"W{r['wheel']}" for r in active],intensities,color='#c98130')
        for i,v in enumerate(intensities):
            chart.text(i,v+.08,f'{v:.2f}',ha='center',fontsize=9)
        chart.set_ylim(0,max(intensities)*1.22)
    else:
        chart.text(.5,.5,'No axles on reference span',ha='center',va='center',
                   transform=chart.transAxes,fontsize=13)
    chart.set_ylabel('Workbook strip-load intensity (tf/m)')
    chart.set_title('Current active axles',fontsize=12,color=INK)
    chart.grid(axis='y',alpha=.2)
    fig.suptitle('STAGE 3  |  VEHICLE WHEEL / IMPACT / DISPERSION REFERENCE',
                 y=.965,fontsize=15,fontweight='bold',color=INK)
    fig.text(.5,.085,
             f"Lead wheel x = {axle_results['parameters']['lead_axle_position_m']:.2f} m   ·   "
             f"Impact = {axle_results['impact_factor']:.3f}   ·   "
             f"Alpha = {axle_results['alpha']:.3f}   ·   "
             f"Active wheels = {axle_results['active_axle_count']}",
             ha='center',fontsize=10,color=INK)
    fig.text(.5,.045,
             'SCHEMATIC ONLY: projected axle axis is not the cross-section axis. '
             'No vehicle code verification, design combinations, or frame analysis.',
             ha='center',fontsize=9,color='#a3342a')
    return fig


def export_vehicle_reference_dxf(inputs: CulvertInputs, axle_results: dict,
                                 path: str | Path):
    """Export a separate reference-only DXF with arrow markers and annotations."""
    import ezdxf
    geom=calculate_geometry(inputs)
    doc=ezdxf.new('R2010')
    doc.layers.new('SCHEMATIC_SECTION',dxfattribs={'color':7})
    doc.layers.new('AXLE_REFERENCE',dxfattribs={'color':2})
    msp=doc.modelspace()
    w,h=geom.external_width_m,geom.external_height_m
    msp.add_lwpolyline([(0,0),(w,0),(w,h),(0,h)],close=True,dxfattribs={'layer':'SCHEMATIC_SECTION'})
    span=axle_results['effective_span_m']
    for row in axle_results['axles']:
        if not row['on_span']:continue
        x=inputs.wall_thickness_m/2 + row['distance_from_left_support_m']*w/span
        msp.add_line((x,h+.6),(x,h+.05),dxfattribs={'layer':'AXLE_REFERENCE'})
        msp.add_text(f"W{row['wheel']} {row['wheel_load_tf']:.2f}tf (reference)",
                     dxfattribs={'height':.09,'insert':(x,h+.67),'layer':'AXLE_REFERENCE'})
    msp.add_text('REFERENCE ONLY: AXLE POSITION PROJECTION; NOT AN ENGINEERING LOAD PLAN',
                 dxfattribs={'height':.10,'insert':(0,-.28),'layer':'AXLE_REFERENCE'})
    doc.saveas(str(path))


def draw_structural_result_annotations(inputs: CulvertInputs, rows: list[dict],
                                       case: str = "ULS", action: str = "MZ"):
    """Place reported/imported member values near schematic locations.

    NOT a bending/shear diagram and NOT output of a calculated frame solver.
    Only top/bottom slab locations have defined source-note references.
    """
    if action not in ("MZ", "V"):
        raise ValueError("action must be MZ or V")
    geom = calculate_geometry(inputs)
    w = geom.external_width_m
    h = geom.external_height_m
    fig = plt.figure(figsize=(12, 6.5), dpi=130)
    ax = fig.add_axes([.04, .12, .52, .78])
    note = fig.add_axes([.62, .17, .34, .67])
    _section(ax, inputs)
    ax.set_xlim(-.50, w+.50)
    ax.set_ylim(-.45, h+.42)
    x0 = inputs.wall_thickness_m
    x1 = x0 + inputs.clear_width_m
    center = (x0+x1)/2
    yt = h - inputs.top_slab_thickness_m/2
    yb = inputs.bottom_slab_thickness_m/2
    map_xy = {
        ("Top slab", "Wall face"): (x0, yt+.25),
        ("Top slab", "Haunch end"): (x1-.22, yt+.25),
        ("Top slab", "Mid span"): (center, yt-.27),
        ("Bottom slab", "Wall face"): (x0, yb+.28),
        ("Bottom slab", "Haunch end"): (x1-.22, yb+.28),
        ("Bottom slab", "Mid span"): (center, yb-.22),
    }
    selected = [r for r in rows if r['case'] == case and r['action'] == action]
    plotted = 0
    for row in selected:
        coords = map_xy.get((row['member'], row['location']))
        if coords:
            x, y = coords
            ax.annotate(f"{row['value']:+.2f}", xy=(x, y),
                        fontsize=9, ha="center", va="center", color="#751a15",
                        bbox=dict(facecolor="#fff6ea", edgecolor="#db8c4d",
                                  boxstyle="round,pad=.26", alpha=.97), zorder=8)
            plotted += 1
    note.axis("off")
    note.text(0, .98, f"{case}  |  {action}", va="top", fontsize=14,
              fontweight="bold", color=INK)
    note.text(0, .83, "Reported / mapped source values", va="top",
              fontsize=11, color="#751a15")
    unit = 'tf.m/m' if action == 'MZ' else 'tf/m'
    note.text(0, .72, f"Unit: {unit}\nMapped slab labels: {plotted}\nSource rows: {len(selected)}",
              va="top", fontsize=10, color=INK, linespacing=1.5)
    note.text(0, .43,
              "Markers are annotations only.\n"
              "No calculated moment/shear diagram.\n"
              "No member-force redistribution.\n"
              "No model, node or local-axis verification.",
              va="top", fontsize=9.5, color="#9d4031", linespacing=1.45)
    fig.suptitle("STAGE 4  |  REPORTED MEMBER ACTIONS — NOT FRAME ANALYSIS",
                 fontsize=14, fontweight="bold", color=INK, y=.97)
    fig.text(.5, .045,
             "UNVERIFIED ANNOTATION  •  Do not use for section design, safety checks or construction",
             fontsize=9, ha="center", color="#a3342a")
    return fig
