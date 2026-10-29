"""Cross-section and load *schematics* for the RCC box-culvert prototype.

These drawings are not structural analysis, reinforcement details, or suitable
for construction. Earth-pressure arrows represent unfactored static pressure;
traffic braking values are reference magnitudes, NOT spatially applied forces.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Polygon, Rectangle

from .geometry import calculate_geometry
from .inputs import CulvertInputs


def haunch_triangles(inputs: CulvertInputs) -> list[list[tuple[float, float]]]:
    """Four internal right triangles, in metres; returns [] if disabled."""
    inputs.validate()
    if inputs.haunch_count == 0:
        return []
    x0, x1 = inputs.wall_thickness_m, inputs.wall_thickness_m + inputs.clear_width_m
    y0, y1 = inputs.bottom_slab_thickness_m, inputs.bottom_slab_thickness_m + inputs.clear_height_m
    a, b = inputs.haunch_width_m, inputs.haunch_height_m
    return [
        [(x0, y0), (x0 + a, y0), (x0, y0 + b)],
        [(x1, y0), (x1 - a, y0), (x1, y0 + b)],
        [(x0, y1), (x0 + a, y1), (x0, y1 - b)],
        [(x1, y1), (x1 - a, y1), (x1, y1 - b)],
    ]


def _section(ax, inputs: CulvertInputs):
    """Draw box and haunch geometry; return external width/height."""
    geom = calculate_geometry(inputs)
    ax.add_patch(Rectangle((0, 0), geom.external_width_m, geom.external_height_m,
                           facecolor="#c5cbd1", edgecolor="#20242b", linewidth=2.1, zorder=2))
    ax.add_patch(Rectangle((inputs.wall_thickness_m, inputs.bottom_slab_thickness_m),
                           inputs.clear_width_m, inputs.clear_height_m,
                           facecolor="white", edgecolor="#20242b", linewidth=1.8, zorder=3))
    for triangle in haunch_triangles(inputs):
        ax.add_patch(Polygon(triangle, closed=True, facecolor="#9eaab5",
                             edgecolor="#20242b", linewidth=1.6, zorder=4))
    ax.set_aspect("equal", adjustable="box")
    ax.set_axis_off()
    return geom


def _double_arrow(ax, start, end, label, offset=(0, 0), fontsize=10, rotation=0):
    ax.annotate("", xy=end, xytext=start,
                arrowprops=dict(arrowstyle="<->", color="#243447", lw=1.1), zorder=7)
    mid_x = (start[0] + end[0]) / 2 + offset[0]
    mid_y = (start[1] + end[1]) / 2 + offset[1]
    ax.text(mid_x, mid_y, label, ha="center", va="center", color="#182638",
            fontsize=fontsize, rotation=rotation, bbox=dict(boxstyle="round,pad=.15", fc="white", ec="none", alpha=.95),
            zorder=8)


def draw_culvert(inputs: CulvertInputs):
    """Dimensioned geometry cross-section with four optional 150x150 mm haunches.

    Returns a matplotlib Figure (the caller is responsible for closing it).
    """
    geom = calculate_geometry(inputs)
    fig, ax = plt.subplots(figsize=(9.5, 7.0), dpi=125)
    _section(ax, inputs)
    w, h = geom.external_width_m, geom.external_height_m
    t = inputs.wall_thickness_m
    b = inputs.bottom_slab_thickness_m

    # Inside clear opening labels refer to un-haunched rectangular dimensions.
    _double_arrow(ax, (t + 0.06, b + inputs.clear_height_m * .34),
                  (t + inputs.clear_width_m - .06, b + inputs.clear_height_m * .34),
                  f"Clear width {inputs.clear_width_m:.3f} m", offset=(0, .10))
    _double_arrow(ax, (t + inputs.clear_width_m * .72, b + .06),
                  (t + inputs.clear_width_m * .72, b + inputs.clear_height_m - .06),
                  f"Clear height {inputs.clear_height_m:.3f} m", offset=(.14, 0), rotation=90)

    # Outer dimensions are outside the section to preserve visibility.
    _double_arrow(ax, (0, -.27), (w, -.27), f"External width {w:.3f} m", offset=(0, -.13))
    _double_arrow(ax, (w + .30, 0), (w + .30, h), f"External height {h:.3f} m",
                  offset=(.55, 0))
    haunch_text = (f"4 internal triangular haunches: "
                   f"{inputs.haunch_width_m * 1000:.0f} x {inputs.haunch_height_m * 1000:.0f} mm"
                   if inputs.haunch_count else "Internal haunches: disabled")
    ax.text(w / 2, h + .22, haunch_text, ha="center", va="bottom", fontsize=10, color="#234a68")
    ax.set_xlim(-.5, w + 1.1)
    ax.set_ylim(-.8, h + .6)
    ax.set_title("RCC Box Culvert | Dimensioned cross-section", fontsize=14, pad=16, fontweight="bold")
    fig.text(.5, .025,
             "SCHEMATIC ONLY — not to scale for construction; haunch dimensions/areas require engineer confirmation.",
             ha="center", va="bottom", fontsize=9, color="#b33b32")
    fig.tight_layout(rect=(0, .055, 1, .96))
    return fig


def draw_load_schematic(inputs: CulvertInputs, earth_inputs=None, braking_inputs=None,
                        braking_case: str = "Class 70R(W) + A"):
    """Separate, illustrative earth/surcharge pressure diagram and braking summary.

    Soil arrows on the left and surcharge arrows on the right are separated
    solely to make the two components legible: in reality the loads may act on
    both side walls under the applicable load case. No structural actions are
    derived here, and no vehicle wheel/axle loads are calculated.
    """
    # Lazy imports keep geometry-only drawings compatible with older projects.
    if earth_inputs is None:
        from .earth_pressure import EarthPressureInputs
        earth_inputs = EarthPressureInputs(earth_cushion_m=inputs.earth_cushion_m)
    from .earth_pressure import pressure_at_depth
    earth_inputs.validate()

    g = calculate_geometry(inputs)
    w, h = g.external_width_m, g.external_height_m
    wall_top = inputs.bottom_slab_thickness_m + inputs.clear_height_m
    wall_bottom = inputs.bottom_slab_thickness_m

    fig, ax = plt.subplots(figsize=(11.5, 7.0), dpi=125)
    _section(ax, inputs)
    ax.set_title("Stage 2–3 | Lateral pressure and braking reference", fontsize=14, fontweight="bold", pad=16)

    # Static pressure depth measured from TOP surface of top slab, to match
    # the source design note's (0.25 m to 3.25 m) wall depths.
    n = 9
    ys = [wall_bottom + (wall_top - wall_bottom) * j / (n - 1) for j in range(n)]
    p_samples = [pressure_at_depth(h - y, earth_inputs) for y in ys]
    max_p = max((p["earth_pressure_t_m2"] for p in p_samples), default=1)
    max_p = max(max_p, 0.001)
    arrow_max = min(1.15, 0.33 * w)
    for y, p in zip(ys, p_samples):
        # Soil component: arrows from left into outside of left wall.
        arrow_len = max(0.05, arrow_max * p["earth_pressure_t_m2"] / max_p)
        x_end = -.06
        ax.add_patch(FancyArrowPatch((x_end - arrow_len, y), (x_end, y),
                                     arrowstyle="-|>", mutation_scale=11,
                                     lw=1.5, color="#c15a28", zorder=6))
        # Surcharge component is uniform with depth, on right for clarity.
        ax.add_patch(FancyArrowPatch((w + .8, y), (w + .06, y),
                                     arrowstyle="-|>", mutation_scale=10,
                                     lw=1.4, color="#287cb4", zorder=6))

    p_top = pressure_at_depth(h - wall_top, earth_inputs)
    p_bottom = pressure_at_depth(h - wall_bottom, earth_inputs)
    p_surcharge = p_top["surcharge_pressure_t_m2"]
    p_cushion = p_top["cushion_pressure_t_m2"]
    annotation = (
        "REFERENCE PRESSURE VALUES (unfactored)\n"
        f"K₀ = {earth_inputs.k0:.3f},  γsoil = {earth_inputs.backfill_density_t_m3:.3f} tf/m³\n"
        f"Soil: at wall top = {p_top['earth_pressure_t_m2']:.3f} tf/m²\n"
        f"Soil: at wall bottom = {p_bottom['earth_pressure_t_m2']:.3f} tf/m²\n"
        f"Uniform surcharge = {p_surcharge:.3f} tf/m²\n"
        f"Earth cushion component = {p_cushion:.3f} tf/m²\n"
        "Cushion is NOT automatically added to soil curve."
    )
    ax.text(w + 1.0, h - .15, annotation, ha="left", va="top", fontsize=10,
            color="#273849", linespacing=1.6,
            bbox=dict(facecolor="#f0f5f9", edgecolor="#bed0dd", boxstyle="round,pad=.6"))

    braking_label = "Braking reference: no braking inputs supplied"
    if braking_inputs is not None:
        from .traffic_loads import braking_cases
        cases = braking_cases(braking_inputs)
        selected = next((case for case in cases if case["Case"] == braking_case), None)
        if selected is None:
            raise ValueError(f"Unknown braking case: {braking_case}")
        braking_label = (f"Braking reference ({braking_case}): "
                         f"F = {selected['Total force (tf)']:.3f} tf; "
                         f"F/L = {selected['Per-metre force (tf/m)']:.3f} tf/m")

    ax.text(w / 2, -.46, braking_label, ha="center", va="center", fontsize=9,
            color="#424a56", bbox=dict(facecolor="#f8f8fa", edgecolor="#dddddf", boxstyle="round,pad=.45"))
    ax.text(w / 2, h + .18, "Orange = soil variation | Blue = uniform surcharge | Arrows separated for clarity",
            ha="center", va="bottom", fontsize=9, color="#34475a")
    ax.set_xlim(-1.5, w + 6.2)
    ax.set_ylim(-.78, h + .60)
    fig.text(.5, .018, "SCHEMATIC ONLY — no vehicle axle loads, load combinations, STAAD solver or verified force application.",
             ha="center", fontsize=9, color="#b33b32")
    fig.tight_layout(rect=(0, .055, 1, .965))
    return fig


def export_schematic_dxf(inputs: CulvertInputs, path: str | Path):
    """CAD-compatible geometry outline with internal haunches; no rebar or loads."""
    import ezdxf
    geom = calculate_geometry(inputs)
    doc = ezdxf.new("R2010")
    doc.layers.new("BOX_OUTLINE", dxfattribs={"color": 7})
    doc.layers.new("HAUNCH", dxfattribs={"color": 5})
    doc.layers.new("ANNOTATION", dxfattribs={"color": 3})
    msp = doc.modelspace()

    def rectangle(x0, y0, width, height):
        msp.add_lwpolyline([(x0, y0), (x0 + width, y0),
                            (x0 + width, y0 + height), (x0, y0 + height)],
                           close=True, dxfattribs={"layer": "BOX_OUTLINE"})

    rectangle(0, 0, geom.external_width_m, geom.external_height_m)
    rectangle(inputs.wall_thickness_m, inputs.bottom_slab_thickness_m,
              inputs.clear_width_m, inputs.clear_height_m)
    for triangle in haunch_triangles(inputs):
        msp.add_lwpolyline(triangle, close=True, dxfattribs={"layer": "HAUNCH"})

    msp.add_text(f"CLEAR {inputs.clear_width_m:.3f} x {inputs.clear_height_m:.3f} m",
                 dxfattribs={"layer": "ANNOTATION", "height": .11,
                             "insert": (0, -.22)})
    msp.add_text("SCHEMATIC ONLY - NOT FOR CONSTRUCTION",
                 dxfattribs={"layer": "ANNOTATION", "height": .10,
                             "insert": (0, -.40)})
    doc.saveas(str(path))
