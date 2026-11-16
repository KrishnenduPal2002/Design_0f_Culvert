"""Streamlit UI for the RCC box culvert geometry prototype.

Place this file at the project root beside the existing ``src`` directory.
Run: py -3.14 -m streamlit run app.py
"""
from __future__ import annotations

import io
import math
import hashlib
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import streamlit as st

from src.inputs import CulvertInputs
from src.general_arrangement import GADInputs, make_sheet1_pdf, sheet1_preview_png, export_sheet1_dxf
from src.detailed_sections import Sheet2Inputs, make_sheet2_pdf, sheet2_preview_png, export_sheet2_dxf
from src.calculations import (
    calculate_preview, default_dead_load_sidl_rows, calculate_dead_load_sidl,
    default_spring_constant_rows, calculate_spring_constants,
    calculate_earth_pressure_table, calculate_braking_force_table,
    default_temperature_gradient_rows, calculate_temperature_gradient, calculate_uniform_temperature,
    default_design_moment_rows, calculate_design_moment_combinations,
    default_design_shear_rows, calculate_design_shear_combinations,
)
from src.drawings import (draw_culvert, draw_load_schematic, export_schematic_dxf,
                         draw_vehicle_loading, export_vehicle_reference_dxf)
from src.reports import to_csv_bytes, to_json_bytes
from src.earth_pressure import EarthPressureInputs
from src.traffic_loads import BrakingInputs
from src.vehicle_loading import (VehicleLoadingInputs, vehicle_preset)
from src.load_combinations import CombinationInputs
from src.structural_analysis import (
    parse_mapped_analysis_csv, example_template_csv, reference_csv,
    is_original_geometry,
)
from src.drawings import draw_structural_result_annotations, draw_frame_solver_results
from src.frame_solver import FrameOptions
from src.uls_design import calculate_uls_design, reference_table
from src.uls_inputs import uls_demands_csv_template, parse_uls_demands_csv
from src.drawings import draw_uls_reference_comparison
from src.sls_design import calculate_sls_design, sls_demands_csv_template, parse_sls_demands_csv, steel_details_from_uls
from src.bbs import calculate_bbs, reference_bars
from src.boq import calculate_boq, reference_boq_items
from src.quantity_exports import (bbs_template_csv, boq_template_csv, bbs_result_csv,
                                  boq_result_csv, parse_bbs_csv, parse_boq_csv)
from src.quantity_charts import bbs_weight_chart, boq_concrete_chart
from src.reinforcement_drawings import (
    build_reinforcement_review, figures_for_review, figure_png_bytes,
    make_review_pdf, export_reinforcement_dxf,
)


st.set_page_config(page_title="RCC Box Culvert | ULS, SLS, BBS & BOQ", page_icon="🏗️", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
.block-container {padding-top: 1.6rem; padding-bottom: 2.5rem; max-width: 1450px;}
[data-testid="stMetric"] {background: rgba(128,128,128,.07); border: 1px solid rgba(128,128,128,.15); border-radius: 12px; padding: 14px 18px;}
[data-testid="stMetricLabel"] {font-weight: 600;}
</style>
""", unsafe_allow_html=True)

st.title("🏗️ RCC Box Culvert — Loads, ULS, SLS, BBS & BOQ")
st.caption("Single-cell box culvert · editable geometry · four optional triangular internal haunches")
pass  # removed Streamlit reminder banner

# Stage 1 — editable, *linked* geometry fields. Each edit updates its
# dependent dimensions before the next Streamlit render. The supported
# structural analysis itself is still a single-cell model.
_S1_DEFAULTS = {
    "s1_clear_width": 3.0,
    "s1_clear_height": 3.0,
    "s1_side_wall": 0.25,
    "s1_top_slab": 0.25,
    "s1_bottom_slab": 0.275,
    "s1_cells": 1,
    "s1_internal_wall": 0.0,
}
for _key, _default in _S1_DEFAULTS.items():
    if _key not in st.session_state:
        st.session_state[_key] = _default


def _s1_sync_geometry():
    w = float(st.session_state["s1_clear_width"])
    h = float(st.session_state["s1_clear_height"])
    wall = float(st.session_state["s1_side_wall"])
    top = float(st.session_state["s1_top_slab"])
    bottom = float(st.session_state["s1_bottom_slab"])
    cells = int(st.session_state["s1_cells"])
    inner = float(st.session_state["s1_internal_wall"])
    st.session_state["s1_effective_span"] = (
        w + wall if cells == 1 else w + (wall + inner) / 2.0
    )
    st.session_state["s1_cc_height"] = h + (top + bottom) / 2.0
    st.session_state["s1_total_width"] = (
        cells * w + 2.0 * wall + (cells - 1) * inner
    )


def _s1_set_width_from_span():
    cells = int(st.session_state["s1_cells"])
    wall = float(st.session_state["s1_side_wall"])
    inner = float(st.session_state["s1_internal_wall"])
    offset = wall if cells == 1 else (wall + inner) / 2.0
    st.session_state["s1_clear_width"] = max(
        0.01, float(st.session_state["s1_effective_span"]) - offset
    )
    _s1_sync_geometry()


def _s1_set_height_from_cc():
    top = float(st.session_state["s1_top_slab"])
    bottom = float(st.session_state["s1_bottom_slab"])
    st.session_state["s1_clear_height"] = max(
        0.01, float(st.session_state["s1_cc_height"]) - (top + bottom) / 2.0
    )
    _s1_sync_geometry()


def _s1_set_width_from_total():
    cells = int(st.session_state["s1_cells"])
    wall = float(st.session_state["s1_side_wall"])
    inner = float(st.session_state["s1_internal_wall"])
    non_clear = 2.0 * wall + (cells - 1) * inner
    st.session_state["s1_clear_width"] = max(
        0.01, (float(st.session_state["s1_total_width"]) - non_clear) / cells
    )
    _s1_sync_geometry()


if "s1_effective_span" not in st.session_state:
    _s1_sync_geometry()

with st.sidebar:
    st.header("Design inputs")
    st.caption("All dimensions are in metres unless otherwise stated.")
    # Stage 1: Keep existing working input variables while grouping all
    # design-note parameters under its two original headings.
    st.subheader("Stage 1 — Geometric Parameters")
    st.markdown("**Clear opening and structure**")
    width = st.number_input("Clear width of cell (m)", min_value=0.01, step=0.05,
                            format="%.3f", key="s1_clear_width", on_change=_s1_sync_geometry)
    height = st.number_input("Clear height of cell (m)", min_value=0.01, step=0.05,
                             format="%.3f", key="s1_clear_height", on_change=_s1_sync_geometry)
    length = st.number_input("Total length of structure (m)", min_value=0.01,
                             value=12.00, step=0.25, format="%.3f")
    number_of_cells = st.number_input("Number of cells", min_value=1, max_value=20,
                                     step=1, key="s1_cells", on_change=_s1_sync_geometry)

    st.markdown("**Concrete thicknesses**")
    wall = st.number_input("Side wall thickness (m)", min_value=0.01, step=0.025,
                           format="%.3f", key="s1_side_wall", on_change=_s1_sync_geometry)
    top = st.number_input("Top slab thickness (m)", min_value=0.01, step=0.025,
                          format="%.3f", key="s1_top_slab", on_change=_s1_sync_geometry)
    bottom = st.number_input("Bottom slab thickness (m)", min_value=0.01, step=0.025,
                             format="%.3f", key="s1_bottom_slab", on_change=_s1_sync_geometry)
    internal_wall_thickness_m = st.number_input(
        "Internal wall thickness (m)", min_value=0.0, step=0.025,
        format="%.3f", key="s1_internal_wall", on_change=_s1_sync_geometry)

    effective_span_m = st.number_input(
        "Effective single span (m)", min_value=0.01, step=0.05, format="%.3f",
        key="s1_effective_span", on_change=_s1_set_width_from_span,
        help="Editing this changes clear width so the dimensions remain consistent.")
    cc_height_m = st.number_input(
        "C/C height of structure (m)", min_value=0.01, step=0.05, format="%.4f",
        key="s1_cc_height", on_change=_s1_set_height_from_cc,
        help="Editing this changes clear height so the dimensions remain consistent.")
    total_width_m = st.number_input(
        "Total width (m)", min_value=0.01, step=0.05, format="%.3f",
        key="s1_total_width", on_change=_s1_set_width_from_total,
        help="Editing this changes each cell's clear width to maintain the overall width.")

    st.markdown("**Haunches and other geometric parameters**")
    use_haunches = st.checkbox("Include four corner haunches", value=True)
    haunch_width = st.number_input("Haunch horizontal size (mm)", min_value=1, value=150, step=10)
    haunch_height = st.number_input("Haunch vertical size (mm)", min_value=1, value=150, step=10)
    fill = st.number_input("Height of earth cushion (m)", min_value=0.0, value=0.30, step=0.05, format="%.3f")
    wearing_coat_mm = st.number_input("Wearing coat thickness (mm) — reference", min_value=0,
                                      value=50, step=5,
                                      help="Reference thickness from design note; vehicle-specific dispersion settings remain editable separately.")
    ms_railing_width_m = st.number_input("MS railing width (m) — reference", min_value=0.0,
                                         value=0.50, step=0.05, format="%.3f")

    st.subheader("Stage 1 — Basic Parameters")
    soil_friction_angle_deg = st.number_input("Angle of internal friction of soil φ (°) — reference",
                                             min_value=0.0, max_value=90.0, value=30.0, step=1.0)
    k0 = st.number_input("Earth pressure coefficient K₀", min_value=0.0, max_value=1.0,
                         value=0.50, step=0.05, format="%.3f")
    soil_density = st.number_input("Density of backfill (tf/m³)", min_value=0.01,
                                   value=2.0, step=0.1, format="%.3f")
    wearing_course_density = st.number_input("Density of wearing course (tf/m³) — reference",
                                              min_value=0.01, value=2.2, step=0.1, format="%.3f")
    concrete_density_tf_m3 = st.number_input("Density of concrete (t/m³) — reference", min_value=0.01,
                    value=2.50, step=0.1, format="%.2f")
    gamma = st.number_input("Concrete unit weight (kN/m³)", min_value=0.01, value=25.0,
                            step=0.5, format="%.2f",
                            help="The design note also lists density of concrete as 2.50 t/m³; the existing model uses 25 kN/m³ by default.")
    surcharge_height = st.number_input("Live-load surcharge equivalent height (m)", min_value=0.0,
                                       value=1.20, step=0.1, format="%.3f")
    safe_bearing_capacity = st.number_input("Safe bearing capacity (tf/m²) — reference",
                                            min_value=0.0, value=10.0, step=0.5, format="%.2f")
    frame_k = st.number_input("Modulus of subgrade reaction (tf/m³)", min_value=1.0,
                              value=1000.0, step=100.0)
    concrete_grade = st.selectbox("Concrete grade for ULS / SLS equations",
                                  ["M35", "M25", "M30", "M40", "M45"], index=0)
    steel_grade = st.selectbox("Steel grade for ULS / SLS equations",
                               ["Fe500", "Fe415", "Fe550"], index=0)
    # Free-text entry lets engineers type decimal or scientific notation,
    # exactly as shown in the design note (e.g. 2.0E+05 and 3.2E+04).
    steel_modulus_text = st.text_input(
        "Modulus of Elasticity of steel, Es (kN/m²)",
        value="2.0E+05", key="stage1_steel_modulus_text")
    concrete_modulus_text = st.text_input(
        "Modulus of Elasticity of concrete, Ec (kN/m²)",
        value="3.2E+04", key="stage1_concrete_modulus_text")

    def parse_modulus_value(raw_value: str, field_name: str) -> float:
        try:
            parsed = float(raw_value.strip().replace(",", ""))
        except (TypeError, ValueError):
            st.error(f"{field_name} must be a number, for example 2.0E+05.")
            st.stop()
        if not math.isfinite(parsed) or parsed <= 0:
            st.error(f"{field_name} must be a positive, finite number.")
            st.stop()
        return parsed

    steel_modulus_reference = parse_modulus_value(steel_modulus_text, "Steel modulus Es")
    concrete_modulus_reference = parse_modulus_value(concrete_modulus_text, "Concrete modulus Ec")
    st.caption("Reference-only inputs are displayed from the design note but do not alter structural results. "
               "The source's Es/Ec units require confirmation.")

    st.subheader("Stage 3 — Braking-force reference")
    force_70rw = st.number_input("Class 70R(W) contribution (tf)", min_value=0.0, value=6.8, step=0.1)
    force_40t = st.number_input("Class 40T contribution (tf)", min_value=0.0, value=8.0, step=0.1)
    force_a = st.number_input("Class A contribution (tf)", min_value=0.0, value=1.14, step=0.01)
    lever_arm = st.number_input("Assumed moment lever arm (m) — illustration only", min_value=0.0, value=1.325, step=0.025, format="%.3f")
    st.subheader("Stage 3 — Vehicle loading patterns")
    case_names = {
        '70rw': 'Class 70R(W) — wheeled (Excel reference)',
        '40t': 'Class 40T — bogie: two 20 tf loads',
        '70rt': 'Class 70R(T) — tracked (5 equivalent stations)',
        'sv385': '385T — special multi-axle vehicle (SV)',
        'custom': 'Custom Pattern — engineer-supplied (retained)',
    }
    vehicle_code = st.selectbox(
        'Vehicle loading type', list(case_names), format_func=lambda k: case_names[k],
        key='selected_vehicle_type',
    )
    preset_inputs = vehicle_preset(vehicle_code)
    st.caption('Each pattern has independent editable loads, spacings, contact width and impact settings. '
               'Defaults are extracted from the source Excel Input worksheet.')
    wheel_csv = st.text_area(
        'Equivalent load stations / wheel / axle loads (tf), comma-separated',
        value=', '.join(f'{v:g}' for v in preset_inputs.wheel_loads_tf),
        height=80, key=f'wheel_values_{vehicle_code}',
    )
    spacing_csv = st.text_area(
        'Consecutive longitudinal spacing (m), comma-separated',
        value=', '.join(f'{v:g}' for v in preset_inputs.axle_spacings_m),
        height=72, key=f'wheel_distances_{vehicle_code}',
    )
    if vehicle_code == '70rt':
        pass  # removed Streamlit reminder banner
    elif vehicle_code == 'sv385':
        pass  # removed Streamlit reminder banner
    elif vehicle_code == '40t':
        st.caption('Workbook: 40T BOGIE, two 20 tf loads at 1.22 m spacing; not a tracked load.')
    elif vehicle_code == 'custom':
        pass  # removed Streamlit reminder banner
    lead_axle = st.number_input(
        'Leading station / first axle position x (m)', min_value=0.0,
        value=preset_inputs.lead_axle_position_m, step=0.05, format='%.3f',
        key=f'lead_x_{vehicle_code}',
    )
    tyre_width = st.number_input(
        'Reference transverse contact width (m)', min_value=0.01,
        value=preset_inputs.tyre_transverse_width_m, step=0.01,
        key=f'tyre_width_{vehicle_code}',
    )
    wearing = st.number_input(
        'Wearing coat for spread (m)', min_value=0.0,
        value=preset_inputs.wearing_coat_m, step=0.01,
        key=f'wearing_{vehicle_code}',
    )
    spread_slope = st.number_input(
        'Spread per side per unit fill depth', min_value=0.01,
        value=preset_inputs.fill_spread_each_side_per_m, step=0.1,
        key=f'spread_{vehicle_code}',
    )
    available_edge = st.number_input(
        'Reference edge availability (m)', min_value=0.01,
        value=preset_inputs.avail_edge_width_m, step=0.01,
        key=f'edge_avail_{vehicle_code}',
    )
    available_centre = st.number_input(
        'Reference centre availability (m)', min_value=0.01,
        value=preset_inputs.avail_centre_width_m, step=0.01,
        key=f'centre_avail_{vehicle_code}',
        help='Used for wheeled, bogie, tracked and custom reference dispersion.',
    )
    sv_gap1, sv_gap2 = preset_inputs.sv_gap1_m, preset_inputs.sv_gap2_m
    if vehicle_code == 'sv385':
        sv_gap1 = st.number_input(
            'SV transverse gap 1 — Excel U1375 (m)', min_value=0.01,
            value=preset_inputs.sv_gap1_m, step=0.01, key='sv_gap1',
        )
        sv_gap2 = st.number_input(
            'SV transverse gap 2 — Excel U1376 (m)', min_value=0.01,
            value=preset_inputs.sv_gap2_m, step=0.01, key='sv_gap2',
        )
        override_impact = False
        manual_impact = 1.0
        st.caption('Special Vehicle: impact factor fixed at 1.0 in this reference model. '
                   'IRC:6 special-vehicle rules require separate engineering verification.')
    else:
        override_impact = st.checkbox(
            'Override spreadsheet impact factor — engineer supplied',
            value=False, key=f'impact_override_{vehicle_code}',
        )
        manual_impact = st.number_input(
            'Manual impact multiplier', min_value=1.0,
            value=1.25, step=0.05, disabled=not override_impact,
            key=f'manual_impact_{vehicle_code}',
        )
    st.subheader("Stage 3 — User-defined load factors")
    st.caption("No factors below are certified IRC ULS/SLS values. Enter an engineer-reviewed factor set.")
    combo_name = st.text_input("Factor set name",value="User-configured illustration (NOT approved)")
    f_dead = st.number_input("Concrete self-weight factor",value=1.0,min_value=0.0,step=.05)
    f_soil = st.number_input("Earth pressure factor",value=1.0,min_value=0.0,step=.05)
    f_surcharge = st.number_input("Surcharge pressure factor",value=1.0,min_value=0.0,step=.05)
    f_cushion = st.number_input("Earth cushion factor",value=1.0,min_value=0.0,step=.05)
    f_vehicle = st.number_input("Axle strip-load factor",value=1.0,min_value=0.0,step=.05)
    f_braking = st.number_input("Braking-force factor",value=1.0,min_value=0.0,step=.05)
    st.subheader("Stage 4 — Analysis results (CSV import)")
    st.caption("Upload results MAPPED and checked by the engineer. Raw STAAD files are NOT accepted; "
               "node IDs, local axes, load cases, sign and units require manual verification.")
    uploaded_structural_file = st.file_uploader(
        "Import mapped STAAD result CSV", type=["csv"], key="stage4_analysis_upload",
        help="Required columns: case,member,location,action,value,unit. Use the template in the new Structural analysis tab.",
    )
    st.subheader("Stage 4B — Experimental 2D frame solver")
    enable_frame = st.checkbox("Run in-app FEM structural model", value=True,
                               help="Computes *modelled* member actions and foundation reactions; not approved engineering results.")
    frame_e = st.number_input("Concrete elastic modulus (GPa)", min_value=1.0,
                              max_value=100.0, value=32.0, step=1.0)
    frame_mesh = st.slider("Frame divisions per slab/wall", 2, 24, 8)
    frame_contact = st.checkbox("Compression-only springs (experimental contact iteration)", value=False)
    frame_braking_case = st.selectbox("Braking load in FEM model", ["Class 70R(W) + A", "Class 40T + A"])
    braking_direction = st.selectbox("Braking global horizontal direction", ["Right (+x)", "Left (-x)"])
    st.caption("Support scheme: one horizontal anchor at bottom-left; vertical Winkler springs along bottom slab. "
               "These are engineering assumptions, not verified STAAD supports.")
    st.subheader("Drawing options")
    braking_case_for_drawing = st.selectbox("Load schematic braking case", ["Class 70R(W) + A", "Class 40T + A"])


inputs = CulvertInputs(
    clear_width_m=width,
    clear_height_m=height,
    length_m=length,
    wall_thickness_m=wall,
    top_slab_thickness_m=top,
    bottom_slab_thickness_m=bottom,
    earth_cushion_m=fill,
    concrete_unit_weight_kn_m3=gamma,
    concrete_grade=concrete_grade,
    steel_grade=steel_grade,
    haunch_width_m=haunch_width / 1000.0,
    haunch_height_m=haunch_height / 1000.0,
    haunch_count=4 if use_haunches else 0,
    number_of_cells=int(number_of_cells),
    internal_wall_thickness_m=internal_wall_thickness_m,
    wearing_coat_thickness_m=wearing_coat_mm / 1000.0,
    ms_railing_width_m=ms_railing_width_m,
    soil_friction_angle_deg=soil_friction_angle_deg,
    earth_pressure_coefficient_k0=k0,
    backfill_density_tf_m3=soil_density,
    wearing_course_density_tf_m3=wearing_course_density,
    concrete_density_tf_m3=concrete_density_tf_m3,
    surcharge_equivalent_height_m=surcharge_height,
    safe_bearing_capacity_tf_m2=safe_bearing_capacity,
    subgrade_reaction_modulus_tf_m3=frame_k,
    steel_modulus_source_value=steel_modulus_reference,
    concrete_modulus_source_value=concrete_modulus_reference,
)

# All five geometry controls above are editable. Do not run the single-cell
# structural solver, ULS/SLS, BOQ or drawings on an unsupported multi-cell model.
if not inputs.supports_single_cell_model:
    pass  # removed Streamlit reminder banner
    _p1, _p2, _p3 = st.columns(3)
    _p1.metric("Cells", f"{inputs.number_of_cells}")
    _p2.metric("Reference total width", f"{inputs.total_width_m:.3f} m")
    _p3.metric("C/C height", f"{inputs.centreline_height_m:.4f} m")
    st.stop()

# Prepare the Stage 2 and 3 inputs ONCE, and send the same parameters through
# src/calculations.py. This ensures the UI, JSON and CSV all agree.
plot_earth_inputs = EarthPressureInputs(
    k0=k0, backfill_density_t_m3=soil_density,
    surcharge_equivalent_height_m=surcharge_height,
    earth_cushion_m=fill,
)
plot_braking_inputs = BrakingInputs(
    length_m=length, class_70rw_force_tf=force_70rw,
    class_40t_force_tf=force_40t, class_a_force_tf=force_a,
    assumed_lever_arm_m=lever_arm,
)

def parse_csv_floats(value: str) -> tuple[float,...]:
    return tuple(float(x.strip()) for x in value.split(",") if x.strip())

try:
    vehicle_inputs = VehicleLoadingInputs(
        wheel_loads_tf=parse_csv_floats(wheel_csv),
        axle_spacings_m=parse_csv_floats(spacing_csv),
        lead_axle_position_m=lead_axle,
        tyre_transverse_width_m=tyre_width, wearing_coat_m=wearing,
        fill_spread_each_side_per_m=spread_slope,
        avail_edge_width_m=available_edge, avail_centre_width_m=available_centre,
        impact_override=manual_impact if override_impact else None,
        case_name=case_names[vehicle_code],
        loading_type=vehicle_code,
        sv_gap1_m=sv_gap1,
        sv_gap2_m=sv_gap2,
    )
    combo_inputs = CombinationInputs(
        name=combo_name, concrete_dead_factor=f_dead,
        soil_factor=f_soil, surcharge_factor=f_surcharge,
        cushion_factor=f_cushion, vehicle_factor=f_vehicle,
        braking_factor=f_braking,
    )
    uploaded_structural_rows = (
        parse_mapped_analysis_csv(uploaded_structural_file.getvalue())
        if uploaded_structural_file else None
    )
    frame_options = FrameOptions(
        concrete_elastic_modulus_gpa=frame_e,
        foundation_modulus_tf_m3=frame_k,
        mesh_divisions_per_member=int(frame_mesh),
        compression_only_springs=frame_contact,
        braking_case='Class 40T + A' if vehicle_code == '40t' else frame_braking_case,
        braking_direction=1 if braking_direction.startswith("Right") else -1,
    )
    result = calculate_preview(
        inputs, plot_earth_inputs, plot_braking_inputs,
        vehicle_inputs, combo_inputs,
        structural_records=uploaded_structural_rows,
        frame_options=frame_options,
        run_frame=enable_frame and vehicle_code in ('70rw', '40t', 'custom'),
    )
except (ValueError, TypeError) as exc:
    st.error(f"Invalid input: {exc}")
    pass  # removed Streamlit reminder banner
    st.stop()

if enable_frame and vehicle_code in ('70rt', 'sv385'):
    pass  # removed Streamlit reminder banner

g = result["geometry"]
loads = result["preliminary_self_weight"]
earth_results = result["earth_pressure"]
braking_results = result["braking_forces"]
vehicle_results = result["vehicle_axle_loading"]
combo_results = result["load_combinations"]
structural_results = result["structural_analysis"]
frame_results = result.get("frame_solver")
# Compatibility with older src/loads.py versions that omit this breakdown key.
# This is the haunch portion of total self-weight, not an additional load.
haunch_self_weight_kn = loads.get(
    "haunch_self_weight_kn",
    g["haunch_volume_m3"] * inputs.concrete_unit_weight_kn_m3,
)

def figure_to_png(figure):
    with io.BytesIO() as buffer:
        figure.savefig(buffer, format="png", dpi=170,
                       bbox_inches="tight", facecolor="white")
        return buffer.getvalue()

try:
    fig = draw_culvert(inputs)
    png_bytes = figure_to_png(fig)
    plt.close(fig)
except Exception as exc:
    png_bytes = None
    st.error(f"Geometry drawing generation failed: {exc}")

try:
    load_fig = draw_load_schematic(
        inputs, earth_inputs=plot_earth_inputs,
        braking_inputs=plot_braking_inputs,
        braking_case=braking_case_for_drawing,
    )
    load_png_bytes = figure_to_png(load_fig)
    plt.close(load_fig)
except Exception as exc:
    load_png_bytes = None
    st.error(f"Load schematic generation failed: {exc}")

try:
    vehicle_fig = draw_vehicle_loading(inputs, vehicle_results, combo_results)
    vehicle_png_bytes = figure_to_png(vehicle_fig)
    plt.close(vehicle_fig)
except Exception as exc:
    vehicle_png_bytes = None
    st.error(f"Vehicle axle schematic failed: {exc}")

frame_png_bytes = None
if frame_results:
    try:
        fem_fig = draw_frame_solver_results(inputs, frame_results)
        frame_png_bytes = figure_to_png(fem_fig)
        plt.close(fem_fig)
    except Exception as exc:
        st.error(f"Frame result plot generation failed: {exc}")

st.subheader("Live design summary")
m1, m2, m3, m4 = st.columns(4)
m1.metric("External width", f"{g['external_width_m']:.3f} m")
m2.metric("External height", f"{g['external_height_m']:.3f} m")
m3.metric("Concrete volume", f"{g['concrete_volume_m3']:.3f} m³")
m4.metric("Self-weight (unfactored)", f"{loads['concrete_self_weight_kn']:,.2f} kN")

tab_drawing, tab_calcs, tab_earth, tab_traffic, tab_vehicle, tab_combo, tab_solver, tab_analysis, tab_uls, tab_sls, tab_bbs, tab_boq, tab_rebar, tab_ref, tab_export, tab_notes = st.tabs([
    "📐 Drawing", "🧮 Calculations", "🌍 Earth pressure", "🚛 Braking forces",
    "🚚 Vehicle axles & dispersion", "🧾 Factor sets", "🧱 In-app frame solver", "🏗️ STAAD result comparison",
    "🛡️ ULS design checks", "🔎 SLS checks", "🪛 BBS steel schedule", "📦 BOQ quantities", "📐 Reinforcement drawings", "📋 Reference comparison", "📥 Downloads", "ℹ️ Roadmap"
])

with tab_drawing:
    st.subheader("Culvert drawings — geometry and load diagrams")
    st.caption("Two separate, automatically updated schematics. PNG downloads preserve all dimensions and labels.")
    geom_tab, load_tab, axle_tab, fem_tab, gad_tab, details_tab = st.tabs([
        "📐 Geometry & dimensions", "🌍 Earth pressure & braking", "🚚 Vehicle axle reference", "🧱 Solved frame", "📄 GAD Sheet 1", "📄 Detailed Sections Sheet 2"
    ])
    with geom_tab:
        st.markdown("**Dimensioned cross-section**")
        if png_bytes is not None:
            st.image(png_bytes, use_container_width=True,
                     caption="Clear/external dimensions, slab thicknesses, and optional corner haunches")
            st.download_button("Download geometry drawing (PNG)", data=png_bytes,
                               file_name="culvert_dimensioned_section.png", mime="image/png",
                               key="geometry_png_download")
        else:
            st.error("Geometry drawing could not be generated. See the error message above.")
        pass  # removed Streamlit reminder banner
        st.caption("Dimensions are schematic; reinforcement, haunch-area reconciliation and construction details remain unverified.")
    with load_tab:
        st.markdown("**Stage 2–3: earth-pressure and braking-reference diagram**")
        if load_png_bytes is not None:
            st.image(load_png_bytes, use_container_width=True,
                     caption="Orange: depth-dependent soil pressure · Blue: uniform surcharge · Braking reference shown separately")
            st.download_button("Download load reference drawing (PNG)", data=load_png_bytes,
                               file_name="culvert_earth_pressure_braking.png", mime="image/png",
                               key="loads_png_download")
        else:
            st.error("Load diagram could not be generated. See the error message above.")
        pass  # removed Streamlit reminder banner

    with axle_tab:
        st.markdown("**Stage 3: wheel loads with impact and spreadsheet effective-width dispersion**")
        if vehicle_png_bytes:
            st.image(vehicle_png_bytes, use_container_width=True)
            st.download_button("Download vehicle-loading schematic PNG",data=vehicle_png_bytes,
                               file_name="culvert_vehicle_axle_reference.png",mime="image/png",
                               key="vehicle_diagram_download")
        pass  # removed Streamlit reminder banner

    with fem_tab:
        st.markdown("**Stage 4B: independent Python frame model and *member-end* result magnitudes**")
        if frame_png_bytes:
            st.image(frame_png_bytes, use_container_width=True,
                     caption="Finite-element model and amplified displacement; NOT a verified STAAD drawing")
            st.download_button("Download solved-frame PNG", frame_png_bytes,
                               file_name="culvert_frame_solver.png", mime="image/png", key="fem_draw_png")
        else:
            pass  # removed Streamlit reminder banner
        pass  # removed Streamlit reminder banner

    with gad_tab:
        st.subheader("General Arrangement Drawing — Sheet 1")
        st.caption("Rebuilt from the layout of your original Sheet 1: Section A-A, plan, key plan, notes, design data and A2 title block. "
                   "The box geometry is dynamic; wing walls, site levels, aprons and other site geometry remain reference values unless edited.")
        pass  # removed Streamlit reminder banner
        ca,cb,cc=st.columns([1,1,1])
        chain=ca.text_input("Drawing chainage",value="36+925",key="gad_chainage")
        apron_l=cb.number_input("D/S apron length (m)",value=6.0,min_value=0.1,step=0.5,key="gad_apron_left")
        apron_r=cc.number_input("U/S apron length (m)",value=3.0,min_value=0.1,step=0.5,key="gad_apron_right")
        with st.expander("Site reference dimensions and levels — confirm with your sir before changing"):
            a1,a2,a3,a4=st.columns(4)
            wing_l=a1.number_input("Wing-wall I length (m)",value=4.924,min_value=.1,step=.1,key="gad_wing1")
            wing_r=a2.number_input("Wing-wall II length (m)",value=4.900,min_value=.1,step=.1,key="gad_wing2")
            frl1=a3.number_input("FRL1 (m)",value=32.214,step=.01,key="gad_frl1")
            frl2=a4.number_input("FRL2 (m)",value=32.351,step=.01,key="gad_frl2")
            b1,b2,b3,b4=st.columns(4)
            frl3=b1.number_input("FRL3 (m)",value=32.214,step=.01,key="gad_frl3")
            gl=b2.number_input("Ground level (m)",value=29.219,step=.01,key="gad_gl")
            il1=b3.number_input("IL1 (m)",value=28.790,step=.01,key="gad_il1")
            il2=b4.number_input("IL2 (m)",value=28.802,step=.01,key="gad_il2")
        try:
            gad_inputs = GADInputs(chainage=chain, left_apron_m=apron_l,
                                   right_apron_m=apron_r,
                                   wingwall_left_length_m=wing_l,
                                   wingwall_right_length_m=wing_r,
                                   frl1_m=frl1,frl2_m=frl2,frl3_m=frl3,
                                   ground_level_m=gl,
                                   invert_level_left_m=il1,
                                   invert_level_right_m=il2)
            gad_pdf=make_sheet1_pdf(inputs,gad_inputs)
            gad_png=sheet1_preview_png(inputs,gad_inputs,dpi=115)
            gad_dxf=export_sheet1_dxf(inputs,gad_inputs)
            compare=st.checkbox("Compare with original PDF Sheet 1",value=True,key="gad_compare")
            reference_image=Path(__file__).resolve().parent/"data"/"original_gad_sheet1_reference.png"
            if compare and reference_image.exists():
                col_left,col_right=st.columns(2)
                with col_left:
                    st.markdown("**Original Sheet 1 — supplied PDF**")
                    st.image(str(reference_image),use_container_width=True)
                with col_right:
                    st.markdown("**Python-generated Sheet 1 — editable**")
                    st.image(gad_png,use_container_width=True)
            else:
                st.image(gad_png,use_container_width=True,caption="Reconstructed A2 landscape General Arrangement Drawing")
            d1,d2,d3=st.columns(3)
            with d1:
                st.download_button("Download Sheet 1 PDF",gad_pdf,file_name="GAD_Sheet1_Review.pdf",
                                   mime="application/pdf",key="gad_sheet1_pdf")
            with d2:
                st.download_button("Download Sheet 1 PNG",gad_png,file_name="GAD_Sheet1_Review.png",
                                   mime="image/png",key="gad_sheet1_png")
            with d3:
                st.download_button("Download Sheet 1 DXF (paper layout)",gad_dxf,
                                   file_name="GAD_Sheet1_Review.dxf",mime="application/dxf",key="gad_sheet1_dxf")
            pass  # removed Streamlit reminder banner
        except Exception as error:
            st.error(f"GAD Sheet 1 generation failed: {error}")


    with details_tab:
        st.subheader("General Arrangement - Sheet 2: Detailed Sections")
        st.caption("Reconstructed from the second page of the original General Arrangement Drawing: "
                   "Section B-B, Section C-C, Curtain Walls Type I/II, Shear Key X, and the U-type wing wall reference.")
        pass  # removed Streamlit reminder banner
        ds1,ds2,ds3=st.columns(3)
        sheet2_chain=ds1.text_input("Sheet 2 chainage",value="36+925",key="sheet2_chainage")
        sheet2_pcc=ds2.number_input("PCC bedding thickness (mm)",min_value=25,max_value=200,
                                   value=75,step=5,key="sheet2_pcc")
        sheet2_slope=ds3.number_input("Reference embankment slope (H:1V)",min_value=1.0,
                                     max_value=4.0,value=2.0,step=.25,key="sheet2_slope")
        with st.expander("Other reference details (verify against approved drawing)"):
            d1,d2,d3=st.columns(3)
            geo_gsm=d1.number_input("Non-woven geotextile (GSM)",value=150,min_value=50,max_value=500,
                                    step=25,key="sheet2_geotextile")
            key_gap=d2.number_input("Shear-key interface gap (mm)",min_value=5,max_value=80,value=20,
                                     step=5,key="sheet2_gap")
            key_toe=d3.number_input("Shear-key toe width (mm)",min_value=100,max_value=800,value=300,
                                     step=25,key="sheet2_toe")
        try:
            detailed=Sheet2Inputs(chainage=sheet2_chain, pcc_bedding_mm=int(sheet2_pcc),
                                  earth_fill_slope_h_to_v=float(sheet2_slope),
                                  geo_textile_gsm=int(geo_gsm), shear_key_gap_mm=int(key_gap),
                                  shear_key_toe_mm=int(key_toe))
            d2_pdf=make_sheet2_pdf(inputs,detailed)
            d2_png=sheet2_preview_png(inputs,detailed,dpi=115)
            d2_dxf=export_sheet2_dxf(inputs,detailed)
            ref_sheet2=Path(__file__).resolve().parent/"data"/"original_gad_sheet2_reference.png"
            compare_d2=st.checkbox("Compare generated Sheet 2 with original PDF",value=True,
                                   key="sheet2_compare")
            if compare_d2 and ref_sheet2.exists():
                orig_col,draw_col=st.columns(2)
                with orig_col:
                    st.markdown("**Original Sheet 2 - supplied PDF**")
                    st.image(str(ref_sheet2),use_container_width=True)
                with draw_col:
                    st.markdown("**Generated Sheet 2 - Python**")
                    st.image(d2_png,use_container_width=True)
            else:
                st.image(d2_png,use_container_width=True)
            f1,f2,f3=st.columns(3)
            with f1:
                st.download_button("Download Sheet 2 PDF",d2_pdf,"GAD_Sheet2_Review.pdf",
                                   "application/pdf",key="sheet2_pdf")
            with f2:
                st.download_button("Download Sheet 2 PNG",d2_png,"GAD_Sheet2_Review.png",
                                   "image/png",key="sheet2_png")
            with f3:
                st.download_button("Download Sheet 2 DXF (paper layout)",d2_dxf,"GAD_Sheet2_Review.dxf",
                                   "application/dxf",key="sheet2_dxf")
            pass  # removed Streamlit reminder banner
        except Exception as error:
            st.error(f"Detailed Section Sheet 2 generation failed: {error}")

def draw_earth_pressure_design_note(inputs: CulvertInputs, pressures: dict):
    """Draw a labelled earth-pressure cross-section inspired by the source note.

    Illustration only: arrows show schematic pressures rather than verified
    structural load application positions or directions.
    """
    fig, ax = plt.subplots(figsize=(11.5, 6.8), facecolor='white')
    ax.set_facecolor('white')
    top = inputs.top_slab_thickness_m
    bottom = inputs.bottom_slab_thickness_m
    clear_h = inputs.clear_height_m
    wall = inputs.wall_thickness_m
    total_h = top + clear_h + bottom
    h = inputs.haunch_height_m if inputs.haunch_count else 0.0
    b = inputs.haunch_width_m if inputs.haunch_count else 0.0

    # Keep geometric thicknesses and the schematic clear part visible at any
    # reasonable input dimensions, without altering the actual calculations.
    d = max(total_h, 1.0)
    projection = max(0.8, 0.18 * d)
    x_wall = 0.0
    x_inside = wall
    box_w = max(projection, 2.5 * wall)
    x_right = x_inside + box_w

    # Box perimeter (top and bottom slabs, side wall), black as in source.
    ax.plot([x_wall, x_right], [0, 0], color='#171717', lw=2.0)
    ax.plot([x_wall, x_wall], [0, total_h], color='#171717', lw=2.0)
    ax.plot([x_wall, x_right], [total_h, total_h], color='#171717', lw=2.0)
    ax.plot([x_inside, x_inside], [top + h, top + clear_h - h], color='#171717', lw=2.0)
    ax.plot([x_inside + b, x_right], [top, top], color='#171717', lw=2.0)
    ax.plot([x_inside + b, x_inside], [top, top + h], color='#171717', lw=2.0)
    ax.plot([x_inside, x_inside + b], [top + clear_h - h, top + clear_h], color='#171717', lw=2.0)
    ax.plot([x_inside + b, x_right], [top + clear_h, top + clear_h], color='#171717', lw=2.0)
    ax.plot([x_inside, x_inside], [top, top + h], color='#171717', lw=0.9, ls=':')
    ax.plot([x_inside, x_inside], [top + clear_h - h, top + clear_h], color='#171717', lw=0.9, ls=':')

    # Soil pressure: triangular arrow lengths scale with the current soil
    # pressure magnitude. All loads are schematic (not a member load map).
    gradient = pressures['pressure_gradient_tf_m3']
    maximum_pressure = gradient * pressures['max_depth_m']
    wedge_width = min(2.45, 1.9 * maximum_pressure / 3.3875) if maximum_pressure > 0 else 0.0
    if wedge_width > 0:
        ax.plot([x_wall, -wedge_width], [0, total_h], color='#7d7d7d', lw=1.3)
        ax.plot([-wedge_width, x_wall], [total_h, total_h], color='#7d7d7d', lw=1.3)
        ax.plot([x_wall, x_wall], [0, total_h], color='#777', lw=0.7)
        for idx in range(1, 18):
            yy = total_h * idx / 18.0
            xx = -wedge_width * yy / total_h
            if abs(xx) > 0.08:
                ax.annotate('', xy=(x_wall - 0.03, yy), xytext=(xx, yy),
                            arrowprops=dict(arrowstyle='-|>', color='#8a8a8a', lw=0.65, mutation_scale=7))

    # Equivalent surcharge is a separate, uniform pressure (if positive).
    surcharge_x = -0.48
    if pressures['surcharge_pressure_tf_m2'] > 0:
        ax.plot([surcharge_x, surcharge_x], [0, total_h], color='#444', lw=0.9)
        for yy in [total_h * j / 12.0 for j in range(1,12)]:
            ax.annotate('', xy=(x_wall - 0.08, yy), xytext=(surcharge_x, yy),
                        arrowprops=dict(arrowstyle='-|>', color='#373737', lw=0.85, mutation_scale=6))
    ax.annotate(f"Surcharge ({pressures['surcharge_equivalent_height_m']:.2f} m)",
                xy=(surcharge_x + 0.08, 0), xytext=(-2.15, -0.43),
                arrowprops=dict(arrowstyle='->', color='#333', lw=0.9),
                ha='center', va='center', color='#333', fontsize=9)
    ax.annotate('Earth Pressure', xy=(-wedge_width * 0.5, total_h * 0.53),
                xytext=(-2.65, total_h * 0.35),
                arrowprops=dict(arrowstyle='->', lw=0.9, color='#333'),
                ha='center', va='center', fontsize=9, color='#333')

    # Reference levels and label callouts at right.
    xpos = x_right + 0.06
    label_x = x_right + 0.85
    entries = pressures['rows']
    minimum_spread = max(total_h * .085, 0.18)
    text_ys = []
    for row in entries:
        y = row['H (m)']
        if text_ys and y - text_ys[-1] < minimum_spread:
            y = text_ys[-1] + minimum_spread
        text_ys.append(y)
    for row, y_label in zip(entries, text_ys):
        y_actual = row['H (m)']
        ax.plot([xpos, label_x - 0.17], [y_actual, y_label],
                color='#777', lw=0.8, ls=(0, (3, 2)))
        ax.text(label_x, y_label, row['Earth Pressure at'], va='center',
                ha='left', fontsize=8.2, color='#333')
    ax.text(x_inside + 0.05, total_h * 0.51, 'C/L', color='#444', fontsize=8)
    ax.text(x_inside + 0.2, top/2, 'C/L', color='#444', fontsize=7.5)
    ax.text(x_inside + 0.2, top + clear_h + bottom/2, 'C/L', color='#444', fontsize=7.5)

    max_label = max(text_ys, default=total_h)
    ax.set_xlim(-3.1, label_x + 2.55)
    ax.set_ylim(max(max_label, total_h) + 0.4, -0.7)
    ax.axis('off')
    ax.set_title('Earth Pressure Diagram', y=-0.055, fontsize=12, weight='bold', color='#222')
    fig.tight_layout()
    return fig



def draw_temperature_gradient_diagram(temperature_result: dict):
    """Live Python sketch of thermal-rise/fall profile and force regions.

    This is an explanatory redraw of the source's slab cross section,
    not a STAAD member-load application drawing.
    """
    mode = temperature_result['mode']
    r = {entry['Zone']: entry for entry in temperature_result['rows']}
    h = temperature_result['slab_thickness_m']
    fig, ax = plt.subplots(figsize=(10.2, 4.6))
    fig.patch.set_facecolor('white')
    ax.set_facecolor('white')
    def thick(zone): return r[zone]['t (m)']
    def tval(zone): return r[zone]['ΔT (°C)']

    if mode == 'rise':
        tupper = max(thick('F1'), thick('F2'))
        # Positive profile, with superposed rectangular and triangular bands.
        profile = [(0, tval('F1') + tval('F2')),
                   (tupper, tval('F3')),
                   (tupper + thick('F3'), 0),
                   (h - thick('F4'), 0), (h, tval('F4'))]
        sign = 1.0
        title = 'Temperature Rise — Gradient Diagram'
        max_temp = max(1.0, tval('F1') + tval('F2'))
        zones = [('F2', thick('F2')*0.22), ('F1', thick('F1')*0.73),
                 ('F3', tupper + thick('F3')*.45),
                 ('F4', h-thick('F4')*.48)]
    else:
        tupper = max(thick('F1'), thick('F2'))
        tbottom = max(thick('F4'), thick('F5'))
        profile = [(0, tval('F1')+tval('F2')),
                   (tupper, tval('F3')),
                   (tupper + thick('F3'), 0),
                   (h-tbottom-thick('F6'), 0),
                   (h-tbottom, tval('F5')),
                   (h, tval('F4')+tval('F5'))]
        sign = -1.0
        title = 'Temperature Fall — Gradient Diagram'
        max_temp = max(1.0, tval('F1')+tval('F2'), tval('F4')+tval('F5'))
        zones = [('F2', thick('F2')*.22), ('F1', thick('F1')*.73),
                 ('F3', tupper + thick('F3')*.42),
                 ('F6', h-tbottom-thick('F6')*.56),
                 ('F4', h-thick('F4')*.22), ('F5', h-thick('F5')*.73)]

    scale = 3.4 / max_temp
    depths, profile_temperatures = zip(*profile)
    x = [sign * d * scale for d in profile_temperatures]
    ax.plot(x, depths, lw=2.1, color='#182b45', zorder=5)
    ax.plot([0, 0], [0, h], lw=2.0, color='#101010')
    ax.plot([-4.4, 4.4], [h, h], lw=0.75, color='#444')
    ax.hlines([0], -0.35, 0.35, lw=1.0, colors='#333')

    for row in temperature_result['rows']:
        y = row['Acting at (m)'] if row['Face']=='Top' else h-row['Acting at (m)']
        ax.plot([0, sign*0.28], [y,y], ls=':', lw=.8, color='#999')
    for name, y in zones:
        # Arrow direction matches warm (+) or cool (-) side of reference sketch.
        arrow_start = 0.12*sign
        arrow_end = 0.83*sign
        ax.annotate('', xy=(arrow_end, y), xytext=(arrow_start, y),
                    arrowprops=dict(arrowstyle='->', lw=1.1, color='#b44c29'), zorder=8)
        ax.text(arrow_end + sign*.16, y, name, fontsize=9.5,
                ha='left' if sign > 0 else 'right', va='center', weight='bold', color='#b44c29')

    ydim = [-.007, h+.008]
    # Dimension markers use actual thicknesses, scaling with the input slab h.
    offset_x = 3.95 if sign > 0 else -3.95
    ax.annotate('', xy=(offset_x, ydim[0]), xytext=(offset_x, ydim[1]),
                arrowprops=dict(arrowstyle='<->', color='#555', lw=.8))
    ax.text(offset_x + sign*.11, h/2, f'h = {h:.3f} m', rotation=90,
            ha='center', va='center', color='#444', fontsize=8.6)
    ax.text(0.1, -.022, 'Top face', fontsize=9.2, color='#444')
    ax.text(0.1, h+.024, 'Bottom face', fontsize=9.2, color='#444')
    label_x = sign*3.0
    label_ha = 'left' if sign > 0 else 'right'
    ax.text(label_x, -.022,
            f"Top ΔT = {sum(tval(z) for z in ['F1','F2']):.2f} °C",
            fontsize=9, ha=label_ha, color='#182b45')
    for name in (['F3','F4'] if mode=='rise' else ['F3','F4','F6']):
        row = r[name]
        y = row['Acting at (m)'] if row['Face']=='Top' else h-row['Acting at (m)']
        ax.text(sign*2.4, y, f"{name}: {row['ΔT (°C)']:g} °C",
                ha=label_ha, va='center', fontsize=8.2, color='#222')
    ax.text(0, h+.066, 'Slab centreline at h/2', fontsize=9, ha='center', color='#777')
    ax.hlines(h/2, -0.6, 0.6, ls=':', lw=.8, colors='#888')
    ax.set_xlim(-4.75, 4.75)
    ax.set_ylim(h+.09, -.065)
    ax.set_title(title, fontsize=13, weight='bold', pad=11)
    ax.axis('off')
    fig.tight_layout()
    return fig


with tab_calcs:
    st.subheader("Calculations")
    # Future calculation sub-sections can be added to this list.
    geometry_quantity_tab, dead_load_tab, spring_constant_tab, earth_pressure_calc_tab, braking_force_calc_tab, temperature_calc_tab, design_moment_calc_tab = st.tabs([
        "Geometry & Quantity", "Dead Load and SIDL", "Spring Constant", "Earth Pressure", "Braking Force", "Temperature", "Design moment due to different load combination"
    ])
    with geometry_quantity_tab:
        st.subheader("Geometry and quantity calculations")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**External dimensions**")
            st.latex(r"W_{ext}=W_{clear}+2t_{wall}")
            st.write(f"{width:.3f} + 2 × {wall:.3f} = **{g['external_width_m']:.3f} m**")
            st.latex(r"H_{ext}=H_{clear}+t_{top}+t_{bottom}")
            st.write(f"{height:.3f} + {top:.3f} + {bottom:.3f} = **{g['external_height_m']:.3f} m**")
            st.markdown("**Concrete volume**")
            st.latex(r"V_{box}=(W_{ext}H_{ext}-W_{clear}H_{clear})L")
            st.write(f"Rectangular box volume: **{g['box_volume_without_haunches_m3']:.3f} m³**")
            st.latex(r"V_{haunch}=n\left(\frac{a b}{2}\right)L")
            st.write(f"Haunch volume: **{g['haunch_volume_m3']:.3f} m³**")
            st.write(f"Total concrete volume: **{g['concrete_volume_m3']:.3f} m³**")
        with c2:
            st.markdown("**Concrete volume breakdown**")
            rows = [
                {"Component": "Top slab", "Volume (m³)": g["top_slab_volume_m3"]},
                {"Component": "Bottom slab", "Volume (m³)": g["bottom_slab_volume_m3"]},
                {"Component": "Side walls", "Volume (m³)": g["walls_volume_m3"]},
                {"Component": "Internal haunches", "Volume (m³)": g["haunch_volume_m3"]},
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)
            st.markdown("**Preliminary self-weight**")
            st.latex(r"W_{concrete}=V_{concrete}\gamma_{concrete}")
            st.write(f"{g['concrete_volume_m3']:.3f} × {gamma:.2f} = **{loads['concrete_self_weight_kn']:,.2f} kN**")
            st.write(f"Haunch contribution (already included): **{haunch_self_weight_kn:,.2f} kN**")
            st.caption("Self-weight is for the full specified length, not per metre. No load factors are applied.")


    with dead_load_tab:
        st.subheader("Dead Load and SIDL")
        st.caption("Dead Load of Box — reference schedule in tonnes-force (t). Edit the dimensions and densities below.")

        # The editable rows are the source of truth for this subsection. The
        # Quantity/Weight output below is recalculated immediately on changes.
        base_rows = default_dead_load_sidl_rows(inputs)
        editor_rows = [
            {**r, 'Area (m²)': r['Area (m²)'] if r['Area (m²)'] is not None else 0.0}
            for r in base_rows
        ]
        edited_rows = st.data_editor(
            editor_rows, key='dead_load_sidl_editor', hide_index=True,
            use_container_width=True, num_rows='fixed', height=390,
            disabled=['Member', 'Method'],
            column_config={
                'Member': st.column_config.TextColumn('Member', width='medium'),
                'Nos': st.column_config.NumberColumn('Nos', min_value=0, step=1, format='%d'),
                'Length (m)': st.column_config.NumberColumn('Length (m)', min_value=0.0, format='%.3f'),
                'Breadth (m)': st.column_config.NumberColumn('Breadth (m)', min_value=0.0, format='%.3f'),
                'Height (m)': st.column_config.NumberColumn('Height (m)', min_value=0.0, format='%.3f'),
                'Density (t/m³)': st.column_config.NumberColumn('Density (t/m³)', min_value=0.0, format='%.3f'),
                'Method': st.column_config.TextColumn('Method'),
                'Area (m²)': st.column_config.NumberColumn('Area (m²)', min_value=0.0, format='%.3f'),
            },
        )
        if hasattr(edited_rows, 'to_dict'):
            edited_rows = edited_rows.to_dict('records')

        p1, p2 = st.columns(2)
        with p1:
            ll_base_pressure = st.number_input(
                'LL Base Pressure (t/m²)', min_value=0.0, value=0.50,
                step=0.05, format='%.3f', key='dead_load_sidl_ll_pressure',
            )
        with p2:
            base_area_default = (inputs.clear_width_m + 2.0 * inputs.wall_thickness_m) * inputs.length_m
            use_area_from_geometry = st.checkbox(
                'Calculate base slab area from current geometry', value=True,
                key='dead_load_sidl_use_geom_area',
            )
            base_area_edit = None
            if not use_area_from_geometry:
                base_area_edit = st.number_input(
                    'Base slab area (m²)', min_value=0.001,
                    value=float(base_area_default), step=0.5,
                    format='%.3f', key='dead_load_sidl_area_override',
                )
        try:
            dead_load = calculate_dead_load_sidl(
                inputs, edited_rows,
                live_load_base_pressure_tf_m2=ll_base_pressure,
                base_area_m2=base_area_edit,
            )
            st.markdown('**Calculated member weights**')
            weights = [
                {'Sl. No.': i, 'Member': row['Member'],
                 'Section area used (m²)': round(row['Section area used (m²)'], 4),
                 'Weight (t)': round(row['Weight (t)'], 4)}
                for i, row in enumerate(dead_load['rows'], start=1)
            ]
            st.dataframe(weights, hide_index=True, use_container_width=True)
            st.markdown('**Base pressure summary**')
            summary_rows = [
                {'Calculation': 'Total weight on structure', 'Value': dead_load['total_weight_tf'], 'Unit': 't'},
                {'Calculation': 'Area of base slab', 'Value': dead_load['base_slab_area_m2'], 'Unit': 'm²'},
                {'Calculation': 'DL Base Pressure', 'Value': dead_load['dead_load_base_pressure_tf_m2'], 'Unit': 't/m²'},
                {'Calculation': 'LL Base Pressure', 'Value': dead_load['live_load_base_pressure_tf_m2'], 'Unit': 't/m²'},
                {'Calculation': 'Total Base Pressure', 'Value': dead_load['total_base_pressure_tf_m2'], 'Unit': 't/m²'},
                {'Calculation': 'Safe Bearing Capacity', 'Value': dead_load['safe_bearing_capacity_tf_m2'], 'Unit': 't/m²'},
            ]
            st.dataframe(
                [{**r, 'Value': round(r['Value'], 3)} for r in summary_rows],
                hide_index=True, use_container_width=True,
            )
            if dead_load['base_pressure_within_capacity']:
                st.write('**Reference comparison:** Total base pressure is below the entered safe bearing capacity.')
            else:
                st.error('Total base pressure exceeds the entered safe bearing capacity.')
            st.caption('Reference discrepancy: the Crash Barrier and Haunch rows use the editable Area (m²) '
                       'column. Changing their displayed breadth/height alone will not change their weights.')
        except (ValueError, TypeError) as exc:
            st.error(f'Cannot calculate Dead Load and SIDL: {exc}')


    with spring_constant_tab:
        st.subheader('Spring Constant')
        st.caption('Calculation of spring constant — edit the effective widths below. '
                   'The modulus of subgrade reaction comes from Stage 1 Basic Parameters.')
        spring_inputs = st.data_editor(
            default_spring_constant_rows(),
            key='stage1_spring_constant_editor',
            hide_index=True, use_container_width=True, num_rows='fixed',
            disabled=['S.No.', 'Node No.'],
            column_config={
                'S.No.': st.column_config.NumberColumn('S.No.', format='%d'),
                'Node No.': st.column_config.TextColumn('Node No.', width='large'),
                'Eff. Width (m)': st.column_config.NumberColumn(
                    'Eff. Width (m)', min_value=0.0, step=0.0025, format='%.4f'
                ),
            },
        )
        if hasattr(spring_inputs, 'to_dict'):
            spring_inputs = spring_inputs.to_dict('records')
        try:
            springs = calculate_spring_constants(inputs, spring_inputs)
            st.markdown('**Calculated spring constants**')
            st.dataframe(
                [{**row,
                  'Eff. Width (m)': round(row['Eff. Width (m)'], 4),
                  'Spring constant (t/m)': round(row['Spring constant (t/m)'], 2)}
                 for row in springs['rows']],
                hide_index=True, use_container_width=True,
                column_config={
                    'S.No.': st.column_config.NumberColumn('S.No.', format='%d'),
                    'Node No.': st.column_config.TextColumn('Node No.', width='large'),
                    'Eff. Width (m)': st.column_config.NumberColumn('Eff. Width (m)', format='%.4f'),
                    'Spring constant (t/m)': st.column_config.NumberColumn('Spring constant (t/m)', format='%.2f'),
                },
            )
            st.caption(f"Subgrade modulus: {springs['subgrade_reaction_modulus_tf_m3']:.2f} t/m³ · "
                       'Spring constant = Modulus × Effective width × 1 m strip.')
            st.caption('The source screenshot rounds the first two widths to 0.06 and 0.14 m. '
                       'The precise editable defaults 0.0625 and 0.1375 m reproduce its '
                       '62.50 and 137.50 t/m results. These node widths are not '
                       'automatically derived from changed geometry or mapped to the FEM solver.')
        except (ValueError, TypeError) as exc:
            st.error(f'Cannot calculate Spring Constant: {exc}')

    with earth_pressure_calc_tab:
        st.subheader('Earth Pressure')
        st.markdown('**Earth Pressure per meter width**')
        try:
            pressures = result['earth_pressure_reference']
            # Schematic redraws with slab, wall, haunch, and pressure inputs.
            pressure_figure = draw_earth_pressure_design_note(inputs, pressures)
            st.pyplot(pressure_figure, use_container_width=True)
            plt.close(pressure_figure)

            st.markdown('**Earth pressure at different locations**')
            st.dataframe(
                [{**row,
                  'H (m)': round(row['H (m)'], 4),
                  'Earth Pressure (t/m²)': round(row['Earth Pressure (t/m²)'], 4)}
                 for row in pressures['rows']],
                hide_index=True, use_container_width=True,
                column_config={
                    'S.No.': st.column_config.NumberColumn('S.No.', format='%d'),
                    'Earth Pressure at': st.column_config.TextColumn('Earth Pressure at', width='large'),
                    'H (m)': st.column_config.NumberColumn('H (m)', format='%.4f'),
                    'Earth Pressure (t/m²)': st.column_config.NumberColumn('Earth Pressure (t/m²)', format='%.4f'),
                },
            )
            p1, p2 = st.columns(2)
            with p1:
                st.metric('LL Surcharge Pressure', f"{pressures['surcharge_pressure_tf_m2']:.3f} t/m²")
                st.caption(f"Equivalent surcharge height: {pressures['surcharge_equivalent_height_m']:.3f} m")
            with p2:
                st.metric('Horizontal pressure due to Earth Cushion',
                          f"{pressures['cushion_pressure_tf_m2']:.3f} t/m²")
                st.caption(f"Earth cushion height: {pressures['earth_cushion_m']:.3f} m")
            st.caption('Earth pressure = K₀ × backfill density × depth H. '
                       'Surcharge and cushion pressures are shown separately, not added to each station pressure.')
        except (ValueError, TypeError) as exc:
            st.error(f'Cannot calculate Earth Pressure: {exc}')


    with braking_force_calc_tab:
        st.subheader('Braking Force')
        st.caption('As per Clause 211.2, IRC 6:2017, the design note states 20% for the first two lanes '
                   'and 5% for each lane in excess of two.')
        try:
            braking_calcs = result['braking_force_reference']
            for braking_case in braking_calcs['cases']:
                st.markdown(f"**{braking_case['Case']}**")
                st.dataframe([
                    {'Particulars': braking_case['Main force label'],
                     'Value': f"{braking_case['Main force (t)']:.2f}", 'Unit': 't'},
                    {'Particulars': 'Force due to Class A',
                     'Value': f"{braking_case['Class A force (t)']:.2f}", 'Unit': 't'},
                    {'Particulars': 'Total horizontal force',
                     'Value': f"{braking_case['Total horizontal force (t)']:.2f}", 'Unit': 't'},
                    {'Particulars': 'Braking force',
                     'Value': f"{braking_case['Braking force (t/m)']:.2f}", 'Unit': 't/m'},
                    {'Particulars': 'Moment due to braking',
                     'Value': f"{braking_case['Moment due to braking (t-m/m)']:.3f}", 'Unit': 't-m/m'},
                ], hide_index=True, use_container_width=True,
                    column_config={
                        'Particulars': st.column_config.TextColumn('Particulars', width='large'),
                        'Value': st.column_config.TextColumn('Value', width='small'),
                        'Unit': st.column_config.TextColumn('Unit', width='small'),
                    })
            st.caption('The results update using the Stage 3 braking contributions and culvert length in the '
                       'Design Inputs sidebar. The moment uses the entered reference lever arm '
                       f"({braking_calcs['lever_arm_m']:.3f} m). Values are design-note arithmetic, "
                       'not verified structural load combinations.')
        except (ValueError, TypeError, KeyError) as exc:
            st.error(f'Cannot calculate Braking Force: {exc}')


with temperature_calc_tab:
    st.subheader('Temperature')
    st.caption('Temperature effects as shown in the supplied design note — IRC 6:2017 Clause 215.')
    st.latex(r'F = E_c \alpha \Delta T \times A \times \text{profile factor}')

    tcol1, tcol2 = st.columns(2)
    with tcol1:
        temp_e = st.number_input('Concrete modulus Ec (t/m²)', min_value=1.0,
                                 value=3230000.0, step=1000.0, format='%.2e',
                                 key='temperature_modulus_tf_m2')
    with tcol2:
        temp_alpha = st.number_input('Thermal expansion coefficient α (1/°C)',
                                     min_value=0.0000001, value=0.000012,
                                     step=0.0000001, format='%.6f',
                                     key='temperature_alpha_per_c')
    st.caption(f'Average slab thickness h = {inputs.top_slab_thickness_m:.3f} m (Stage 1 Top Slab Thickness). '
               'Profile factor = 1 for rectangular and 0.5 for triangular regions.')

    rise_tab, fall_tab, uniform_tab = st.tabs(['Temperature Rise', 'Temperature Fall', 'Uniform Temperature'])

    def _temperature_gradient_panel(mode: str):
        st.markdown('**Effect of temperature gradient**')
        st.markdown(f'**Temperature {mode.capitalize()}**')
        base = default_temperature_gradient_rows(mode)
        editable = [
            {'Zone': item['Zone'], 'ΔT (°C)': item['ΔT (°C)'],
             'b (m)': item['b (m)'], 't (m)': item['t (m)'],
             'Profile': item['Profile'], 'Face': item['Face']}
            for item in base
        ]
        edited = st.data_editor(
            editable, key=f'temp_{mode}_editor', hide_index=True,
            use_container_width=True, num_rows='fixed',
            disabled=['Zone', 'Profile', 'Face'],
            column_config={
                'Zone': st.column_config.TextColumn('S.No.', width='small'),
                'ΔT (°C)': st.column_config.NumberColumn('ΔT (°C)', min_value=0.0, format='%.3f'),
                'b (m)': st.column_config.NumberColumn('b (m)', min_value=0.0, format='%.4f'),
                't (m)': st.column_config.NumberColumn('t (m)', min_value=0.0, format='%.4f'),
            },
        )
        if hasattr(edited,'to_dict'):
            edited = edited.to_dict('records')
        try:
            temp_rows=[]
            for row, original in zip(edited, base):
                temp_rows.append({**row, 'Position': original['Position']})
            panel = calculate_temperature_gradient(
                inputs, mode, rows=temp_rows,
                elastic_modulus_tf_m2=temp_e, thermal_expansion_per_c=temp_alpha,
            )
            figure = draw_temperature_gradient_diagram(panel)
            st.pyplot(figure, use_container_width=True)
            plt.close(figure)
            st.markdown('**Calculated force table**')
            display=[]
            for row in panel['rows']:
                display.append({
                    'S.No.': row['Zone'], 'ΔT (°C)': round(row['ΔT (°C)'],3),
                    'b (m)': round(row['b (m)'],4), 't (m)': round(row['t (m)'],4),
                    'A=b×t (m²)': round(row['A=b×t (m²)'],5),
                    'Force F (t)': round(row['Force F (t)'],2),
                    'Acting at (m)': round(row['Acting at (m)'],4),
                    'Eccentricity e (m)': round(row['Eccentricity e (m)'],4),
                })
            st.dataframe(display, hide_index=True, use_container_width=True)
            st.markdown('**Calculated summary**')
            c1,c2,c3=st.columns(3)
            c1.metric('Σ F',f"{panel['total_force_t']:.2f} t")
            c2.metric('Σ F×e',f"{panel['net_moment_t_m']:.3f} t·m")
            c3.metric('Equivalent ΔT',f"{panel['equivalent_delta_t_c']:.3f} °C")
            ref=panel['reference']
            st.caption(f"Screenshot reference: Σ F = {ref['total_force_t']:.2f} t, "
                       f"Σ F×e = {ref['net_moment_t_m']:.3f} t·m, "
                       f"Equivalent ΔT = {ref['equivalent_delta_t_c']:.3f} °C. "
                       'Small differences can arise from rounded source modulus and intermediate values.')
            st.latex(r'M = E_c I \alpha \frac{\Delta T_{eq}}{h}')
        except (ValueError, TypeError, KeyError) as ex:
            st.error(f'Cannot calculate temperature {mode}: {ex}')

    with rise_tab:
        _temperature_gradient_panel('rise')
    with fall_tab:
        _temperature_gradient_panel('fall')
    with uniform_tab:
        st.markdown('**Effect of uniform temperature — IRC 6:2017 Clause 215.2**')
        uc1,uc2=st.columns(2)
        with uc1:
            max_air=st.number_input('Maximum shade air temperature (°C)',
                value=42.2, step=0.1, format='%.2f', key='temp_air_max')
            min_air=st.number_input('Minimum shade air temperature (°C)',
                value=2.0, step=0.1, format='%.2f', key='temp_air_min')
            creep_e=st.number_input('Concrete modulus considering creep (t/m²)',
                value=1620000.0, min_value=1.0, step=1000.0,
                format='%.2e', key='temp_creep_E')
        with uc2:
            bridge_max=st.number_input('Bridge temperature at restraint — Maximum (°C)',
                value=32.1, step=0.1, format='%.2f', key='temp_restraint_max')
            bridge_min=st.number_input('Bridge temperature at restraint — Minimum (°C)',
                value=12.1, step=0.1, format='%.2f', key='temp_restraint_min')
        try:
            uniform=calculate_uniform_temperature(
                maximum_air_c=max_air, minimum_air_c=min_air,
                restraint_bridge_max_c=bridge_max, restraint_bridge_min_c=bridge_min,
                creep_concrete_modulus_tf_m2=creep_e, elastic_modulus_tf_m2=temp_e,
            )
            items = [
                ('Difference between maximum and minimum shade air temperature',uniform['shade_air_difference_c'],'°C'),
                ('Load effect due to temperature rise',uniform['temperature_rise_c'],'°C'),
                ('Load effect due to temperature fall',uniform['temperature_fall_c'],'°C'),
                ('Temperature rise for applying in STAAD',uniform['applied_rise_c'],'°C'),
                ('Temperature fall for applying in STAAD',uniform['applied_fall_c'],'°C'),
            ]
            st.dataframe([{'Particulars':x, 'Value':round(v,1), 'Unit':u} for x,v,u in items],
                         use_container_width=True,hide_index=True)
            st.caption(f"Creep reduction = Ec,creep / Ec = {uniform['application_factor']:.4f}. "
                       'The design note describes this as approximately half. '
                       'STAAD temperature values are not automatically applied to the frame model.')
        except (ValueError,TypeError) as ex:
            st.error(f'Cannot calculate uniform temperature: {ex}')



with design_moment_calc_tab:
    st.subheader('Design moment due to different load combination')
    st.caption('Sign convention: +ve MZ causes tension at the top of the member; '
               '-ve MZ causes tension at the bottom. Units: t-m/m.')
    st.caption('Edit the Normal and LL components below. ULS, Rare and Quasi-Permanent totals '
               'are calculated in the result table; values initially come from the original Excel load sheet.')

    moment_editable_columns = ('ULS Normal', 'ULS LL', 'Rare Normal', 'Rare LL',
                               'Quasi Normal', 'Quasi LL')
    if 'design_moment_editor_version' not in st.session_state:
        st.session_state['design_moment_editor_version'] = 0
    if st.button('Reset design moments to Excel values', key='design_moment_reset'):
        st.session_state['design_moment_editor_version'] += 1

    original_moments = default_design_moment_rows()
    moment_input_rows = [
        {'Member': row['Member'], 'Location': row['Location'], 'Face': row['Face'],
         **{column: row[column] for column in moment_editable_columns}}
        for row in original_moments
    ]
    edited_moments = st.data_editor(
        moment_input_rows,
        key=f"design_moment_editor_{st.session_state['design_moment_editor_version']}",
        hide_index=True, use_container_width=True, num_rows='fixed', height=440,
        disabled=['Member', 'Location', 'Face'],
        column_config={
            'Member': st.column_config.TextColumn('Member', width='small'),
            'Location': st.column_config.TextColumn('Location', width='medium'),
            'Face': st.column_config.TextColumn('Face', width='small'),
            **{name: st.column_config.NumberColumn(name, format='%.4f')
               for name in moment_editable_columns},
        },
    )
    if hasattr(edited_moments, 'to_dict'):
        edited_moments = edited_moments.to_dict('records')
    try:
        # Preserve the original Excel row labels for source traceability, without
        # exposing any internal IDs or requiring an Excel file on Streamlit Cloud.
        rows_for_calc = [
            {**original, **edited}
            for original, edited in zip(original_moments, edited_moments)
        ]
        moment_result = calculate_design_moment_combinations(rows_for_calc)
        st.markdown('**Calculated moments — ULS (Normal, LL, Factor load), Rare and Quasi-Permanent**')
        moment_columns = ['Member', 'Location', 'Face',
                          'ULS Normal', 'ULS LL', 'ULS Factor load',
                          'Rare Normal', 'Rare LL', 'Rare Total',
                          'Quasi Normal', 'Quasi LL', 'Quasi Total']
        st.dataframe(
            [{col: row[col] for col in moment_columns} for row in moment_result['rows']],
            hide_index=True, use_container_width=True, height=520,
            column_config={
                col: st.column_config.NumberColumn(col, format='%.4f')
                for col in moment_columns[3:]
            },
        )
        st.caption('The four interior-support Excel rows containing #VALUE! were omitted '
                   'for this single-cell reference case. Edited values update this table '
                   'only; they are not automatically applied to ULS/SLS design checks.')
    except (ValueError, TypeError) as exc:
        st.error(f'Cannot calculate Design Moments: {exc}')

    st.divider()
    st.markdown('### ULS Shear Force (t/m)')
    st.caption('Values are from the original Excel ULS shear-force table. '
               'Edit Normal or LL; the Total is recalculated below. '
               'These reference shear forces are not automatically linked to ULS design demands.')
    if 'design_shear_editor_version' not in st.session_state:
        st.session_state['design_shear_editor_version'] = 0
    if st.button('Reset shear forces to Excel values', key='design_shear_reset'):
        st.session_state['design_shear_editor_version'] += 1

    original_shears = default_design_shear_rows()
    shear_edit_rows = [
        {'Member': row['Member'], 'Location': row['Location'], 'Face': row['Face'],
         'Normal': row['Normal'], 'LL': row['LL']}
        for row in original_shears
    ]
    edited_shears = st.data_editor(
        shear_edit_rows,
        key=f"design_shear_editor_{st.session_state['design_shear_editor_version']}",
        hide_index=True, use_container_width=True, num_rows='fixed', height=460,
        disabled=['Member', 'Location', 'Face'],
        column_config={
            'Member': st.column_config.TextColumn('Member', width='small'),
            'Location': st.column_config.TextColumn('Location', width='medium'),
            'Face': st.column_config.TextColumn('Face', width='medium'),
            'Normal': st.column_config.NumberColumn('Normal (t/m)', format='%.4f'),
            'LL': st.column_config.NumberColumn('LL (t/m)', format='%.4f'),
        },
    )
    if hasattr(edited_shears, 'to_dict'):
        edited_shears = edited_shears.to_dict('records')
    try:
        edited_shear_rows = [
            {**original, **edited}
            for original, edited in zip(original_shears, edited_shears)
        ]
        shear_result = calculate_design_shear_combinations(edited_shear_rows)
        st.markdown('**Calculated ULS Shear Force — Normal, LL and Total**')
        st.dataframe(
            [{col: row[col] for col in ('Member', 'Location', 'Face', 'Normal', 'LL', 'Total')}
             for row in shear_result['rows']],
            hide_index=True, use_container_width=True, height=500,
            column_config={
                'Normal': st.column_config.NumberColumn('Normal (t/m)', format='%.4f'),
                'LL': st.column_config.NumberColumn('LL (t/m)', format='%.4f'),
                'Total': st.column_config.NumberColumn('Total (t/m)', format='%.4f'),
            },
        )
    except (ValueError, TypeError) as exc:
        st.error(f'Cannot calculate ULS shear forces: {exc}')

with tab_earth:
    st.subheader("Stage 2 — Earth pressure and surcharge")
    st.caption("Reference: Design Note Of Box Culvert 1x3x3, page 2. Pressure unit tf/m² means metric tonne-force per square metre. 1 tf/m² = 9.80665 kPa.")
    try:
        # Read the central calculation result; do not recalculate here.
        st.latex(r"p_{soil}(z)=K_0\gamma_{soil}z")
        st.latex(r"p_{surcharge}=K_0\gamma_{soil}h_{eq}")
        st.latex(r"p_{cushion}=K_0\gamma_{soil}h_{fill}")
        pass  # removed Streamlit reminder banner
        sample = earth_results["pressure_at_bottom_slab_centreline"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Soil pressure at reference bottom", f"{sample['earth_pressure_t_m2']:.3f} tf/m²")
        c2.metric("Uniform live-load surcharge", f"{sample['surcharge_pressure_t_m2']:.3f} tf/m²")
        c3.metric("Cushion pressure (separate)", f"{sample['cushion_pressure_t_m2']:.3f} tf/m²")
        profile = earth_results["pressure_profile"]
        depths = [row["depth_m"] for row in profile]
        soil_pressures = [row["earth_pressure_t_m2"] for row in profile]
        combined_pressures = [row["soil_plus_surcharge_t_m2"] for row in profile]
        pressure_fig, pressure_ax = plt.subplots(figsize=(8, 5))
        pressure_ax.plot(soil_pressures, depths, label="Earth pressure")
        pressure_ax.plot(combined_pressures, depths, label="Earth + live-load surcharge (illustrative)")
        pressure_ax.invert_yaxis()
        pressure_ax.set_xlabel("Lateral pressure (tf/m²)")
        pressure_ax.set_ylabel("Depth below top slab upper surface (m)")
        pressure_ax.grid(alpha=0.25)
        pressure_ax.legend()
        st.pyplot(pressure_fig, clear_figure=True)
        st.subheader("Compare with design note — page 2")
        st.dataframe(earth_results["design_note_reference_comparison"], hide_index=True, use_container_width=True)
        st.caption("Reference pressures only match when K₀=0.5 and soil density=2.0 tf/m³. Locations are the original design-note depths, not recalculated for modified geometry.")
        pass  # removed Streamlit reminder banner
    except ValueError as exc:
        st.error(f"Earth pressure inputs are invalid: {exc}")

with tab_traffic:
    st.subheader("Stage 3 — Traffic braking-force summary")
    st.caption("Design note, page 2: Class 70R(W) + A and Class 40T + A braking-force summaries. Units are metric tonne-force (tf).")
    pass  # removed Streamlit reminder banner
    cases = braking_results["cases"]
    st.latex(r"F_{total}=F_{vehicle}+F_{Class\ A}")
    st.latex(r"q_{braking}=F_{total}/L")
    st.dataframe(cases, use_container_width=True, hide_index=True)
    pass  # removed Streamlit reminder banner
    st.bar_chart({row["Case"]: row["Per-metre force (tf/m)"] for row in cases})
    st.download_button("Download braking-force comparison CSV",
                       data=("Case,Total force (tf),Per-metre force (tf/m),Reference total (tf),Reference per-metre (tf/m)\n" +
                             "".join(f"{r['Case']},{r['Total force (tf)']:.6f},{r['Per-metre force (tf/m)']:.6f},{r['Note total (tf)']:.3f},{r['Note per metre (tf/m)']:.3f}\n" for r in cases)),
                       file_name="braking_force_comparison.csv", mime="text/csv")

with tab_vehicle:
    st.subheader("Stage 3 — Wheel loads, impact and effective-width dispersion")
    st.caption('Selected: ' + case_names[vehicle_code] + '. Workbook Input sections: '
               '70R(W) rows 19+, 40T row 586+, 70R(T) row 961+, 385T row 1375+; '
               'Custom remains editable. Values are workbook-reference only.')
    a,b,c,d=st.columns(4)
    a.metric("Effective span", f"{vehicle_results['effective_span_m']:.3f} m")
    b.metric("Impact factor", f"{vehicle_results['impact_factor']:.3f}")
    c.metric("Alpha (effective width)", f"{vehicle_results['alpha']:.3f}")
    d.metric('Active load stations', f"{vehicle_results['active_axle_count']}")
    st.metric('Gross reference vehicle load', f"{vehicle_results['vehicle_gross_pattern_load_tf']:.1f} tf")
    st.latex(r"b_1=b_{tyre}+2s(t_{wearing}+h_{fill})")
    st.latex(r"b_{eff}=\alpha a(1-a/l_0)")
    st.latex(r"q_{strip}=P_{wheel}I/b_{final}")
    st.dataframe(vehicle_results['axles'],use_container_width=True,hide_index=True)
    st.caption("Inactive wheels are outside the selected effective span. These are individual spreadsheet strip-load intensities, not verified beam/plate reactions or design effects.")
    workbook_checks = {
        '70rw': '70R(W), x=0.25 m: first station ≈ 5.195599 tf/m',
        '40t': '40T, x=0.25 m: first station ≈ 6.112469 tf/m',
        '70rt': '70R(T), x=0.25 m: first equivalent station ≈ 4.278729 tf/m',
        'sv385': '385T, x=1.0 m: first trailer axle ≈ 3.272727 tf/m',
        'custom': 'Custom Pattern: no verified Excel reference output',
    }
    pass  # removed Streamlit reminder banner
    pass  # removed Streamlit reminder banner
    with st.expander("Position screening sweep — reference only",expanded=True):
        screening = result['vehicle_position_screening']
        st.write(f"**{screening['positions_checked']}** sampled leading-wheel positions at **{screening['position_step_m']:.2f} m** intervals")
        st.metric("Largest sampled sum of strip-load intensities",
                  f"{screening['peak_sampled_sum_strip_intensities_tf_m']:.3f} tf/m")
        st.caption(f"Lead wheel position for this screening indicator: {screening['peak_sampled_position_m']:.3f} m")
        st.line_chart(screening['samples'], x='lead_position_m',
                      y='sum_strip_intensities_tf_m')
        pass  # removed Streamlit reminder banner
    pass  # removed Streamlit reminder banner

with tab_combo:
    st.subheader("Stage 3 — User-defined component load factor set")
    pass  # removed Streamlit reminder banner
    st.dataframe(combo_results['components'],use_container_width=True,hide_index=True)
    st.caption("Dead load: kN/m along culvert length; earth/surcharge/cushion: tf/m² lateral pressure; axle: tf/m effective strip; braking: tf/m horizontal force. These are not all interchangeable loads.")
    st.write("Factor set:", combo_results['name'])

with tab_solver:
    st.subheader("Stage 4B — Modelled member forces & combined load effects")
    st.caption("Independent experimental 2D Euler-Bernoulli frame solver, for a 1 m culvert strip. "
               "Not STAAD, not externally benchmarked, and not an IRC-compliant design check.")
    if frame_results is None:
        pass  # removed Streamlit reminder banner
    else:
        st.success(f"Frame equations solved: {frame_results['node_count']} nodes, "
                   f"{frame_results['element_count']} elements, "
                   f"{frame_results['foundation_active_springs']} active base springs.")
        k1,k2,k3,k4 = st.columns(4)
        k1.metric("Max model node translation", f"{frame_results['max_nodal_displacement_mm']:.4f} mm")
        k2.metric("Base vertical spring reaction", f"{frame_results['total_foundation_reaction_kn_per_m']:.3f} kN / strip")
        k3.metric("Force residual Fx", f"{frame_results['equilibrium_residual_fx_kn_per_m']:.2e} kN")
        k4.metric("Force residual Fy", f"{frame_results['equilibrium_residual_fy_kn_per_m']:.2e} kN")
        st.markdown("**1. Applied load components — physically assembled on frame members**")
        st.dataframe(frame_results["load_components"], hide_index=True, use_container_width=True)
        pass  # removed Streamlit reminder banner
        st.markdown("**2. Combined member end actions**")
        st.dataframe(frame_results["member_action_summary"], hide_index=True, use_container_width=True)
        st.caption("End actions are local member sign conventions. These are maximum absolute *element-end* values, "
                   "not full-span peaks or ULS/SLS envelopes. No structural adequacy pass/fail is calculated.")
        if frame_png_bytes:
            st.image(frame_png_bytes, use_container_width=True)
            st.download_button("Download FEM model figure (PNG)", frame_png_bytes,
                               file_name="frame_model_results.png", mime="image/png", key="fem_png_tab")
        a1,a2,a3 = st.tabs(["Member end force table", "Foundation reactions", "Model assumptions"])
        with a1:
            st.dataframe(frame_results["member_end_actions"], hide_index=True, use_container_width=True)
        with a2:
            st.dataframe(frame_results["foundation_spring_reactions"], hide_index=True, use_container_width=True)
            st.write("Horizontal anchor reaction (kN/strip):",
                     round(frame_results["bottom_left_horizontal_anchor_reaction_kn_per_m"], 5))
            st.write("Total applied vertical load (kN/strip):",
                     round(frame_results["total_applied_fy_kn_per_m"], 5))
        with a3:
            st.json(frame_results["options"])
            for assumption in frame_results["limitations"]:
                st.write("•", assumption)
        pass  # removed Streamlit reminder banner

with tab_analysis:
    st.subheader("Stage 4 — Structural analysis results integration")
    st.caption("This separate tab imports engineer-mapped STAAD force results. The new Stage 4B "
               "tab runs an experimental Python frame solver using applied earth, vehicle and other loads. "
               "There is no direct STAAD connection or verified sign/load-case equivalence.")
    pass  # removed Streamlit reminder banner
    st.markdown("**1. Prepare STAAD data using the required CSV schema**")
    st.code("case,member,location,action,value,unit\n"
            "ULS,Top slab,Mid span,MZ,-6.8,tf.m/m", language="csv")
    st.caption("That example is transcribed from the design note, not an independently computed STAAD result. "
               "For genuine comparison, upload independent STAAD outputs with the SAME member/case/location "
               "and MZ sign convention. Allowed cases: ULS, SLS_RARE, SLS_QUASI; "
               "actions: MZ (tf.m/m), V (tf/m).")
    x1, x2 = st.columns(2)
    with x1:
        st.download_button("Download blank STAAD-mapping CSV", example_template_csv(),
                           file_name="staad_mapped_results_TEMPLATE.csv", mime="text/csv",
                           key="stage4_blank_template")
    with x2:
        st.download_button("Download rounded design-note references CSV", reference_csv(),
                           file_name="design_note_reference_ONLY.csv", mime="text/csv",
                           key="stage4_reference_csv")
    if uploaded_structural_rows:
        st.success(f"Loaded {len(uploaded_structural_rows)} mapped values from the uploaded CSV (not verified).")
    else:
        pass  # removed Streamlit reminder banner

    if not structural_results['reference_geometry_matches']:
        st.error("Current geometry differs from the original 1×3×3 m, 12 m, 0.3 m fill reference. "
                 "Do NOT compare these historical reference actions to this modified geometry as "
                 "a valid design test.")
    else:
        st.caption("Original geometry matches the input dimensions. This does not verify "
                   "STAAD load cases, supports, stiffness, or sign convention.")
    src_choice = st.radio("Show member annotations from", ["Design note (reported reference)",
                           "Imported mapped results"], horizontal=True,
                          disabled=not uploaded_structural_rows,
                          key="stage4_diagram_source")
    col_case, col_act = st.columns(2)
    with col_case:
        selected_case = st.selectbox("Analysis case", ["ULS", "SLS_RARE", "SLS_QUASI"],
                                     key="stage4_case")
    with col_act:
        available_actions = ["MZ", "V"] if selected_case == "ULS" else ["MZ"]
        selected_action = st.selectbox("Member action", available_actions, key="stage4_action")
    diagram_rows = (structural_results['imported']
                    if src_choice.startswith('Imported') and uploaded_structural_rows
                    else structural_results['reference'])
    selected_rows = [row for row in diagram_rows
                     if row['case'] == selected_case and row['action'] == selected_action]
    st.dataframe(selected_rows, hide_index=True, use_container_width=True)
    if selected_rows:
        try:
            action_figure = draw_structural_result_annotations(
                inputs, diagram_rows, case=selected_case, action=selected_action)
            image_bytes = figure_to_png(action_figure)
            plt.close(action_figure)
            st.image(image_bytes, use_container_width=True,
                     caption="Reported slab-location values ONLY; not a solved or continuous member-force diagram")
            st.download_button("Download annotated member actions (PNG)", image_bytes,
                               file_name=f"stage4_{selected_case}_{selected_action}_reference.png",
                               mime="image/png", key="stage4_actions_png")
        except Exception as err:
            st.error(f"Stage 4 annotation drawing failed: {err}")
    else:
        pass  # removed Streamlit reminder banner
    st.markdown("**2. Numerical comparison to rounded design-note values**")
    if structural_results['comparison']:
        st.dataframe(structural_results['comparison'], hide_index=True,
                     use_container_width=True)
        st.caption("Differences are for REVIEW only. Zero difference can occur when the same design-note "
                   "values were copied into the CSV; it is not independent validation or a safety PASS.")
    else:
        pass  # removed Streamlit reminder banner
    st.markdown("**3. Integration status**")
    st.write(structural_results['status'])
    pass  # removed Streamlit reminder banner

with tab_uls:
    st.subheader("Stage 5 — Ultimate Limit State (ULS) design inputs")
    st.caption("Flexure, distribution steel, shear capacity, and stirrup checks based on the original engineering workbook formulas.")
    pass  # removed Streamlit reminder banner
    source_uls = result["uls_design"]
    if not source_uls["reference_geometry_matches"]:
        st.error("The current geometry, fill, material or haunch data differ from the original reference. "
                 "Update section depths, reinforcement and every demand for the revised structure. "
                 "Do not reuse the old design actions as an approved design.")
    else:
        pass  # removed Streamlit reminder banner

    demand_mode = st.radio(
        "Where do the ULS bending moments and shear forces come from?",
        ["Original Excel reference", "Manual engineer-entered forces", "Engineer-mapped ULS force CSV"],
        horizontal=False,
        key="uls_demand_mode",
    )
    st.caption("Concrete and steel grades come from the Design inputs sidebar. Enter nonnegative Mu/Vu "
               "demand magnitudes after your engineer verifies the governing face and section. "
               "Effective depths and reinforcement are editable but are not auto-recomputed from cover or bar layout.")

    # Download a blank, complete mapping template. Identifiers are internal to
    # the file; the engineer maps real STAAD ULS section demands and their sign.
    st.download_button(
        "Download ULS force mapping template (CSV)",
        data=uls_demands_csv_template(reference_table()),
        file_name="uls_forces_mapping_template.csv", mime="text/csv",
        key="uls_force_template",
    )
    if uploaded_structural_rows:
        pass  # removed Streamlit reminder banner

    imported_uls = {}
    current_source = "Original Excel cached ULS demands (reference case only)"
    editor_suffix = "reference"
    if demand_mode == "Manual engineer-entered forces":
        current_source = "Manually entered ULS design-section demands (review required)"
        editor_suffix = "manual"
        pass  # removed Streamlit reminder banner
    elif demand_mode == "Engineer-mapped ULS force CSV":
        current_source = "Engineer-mapped ULS force CSV (not independently verified)"
        uploaded_uls_csv = st.file_uploader(
            "Upload completed ULS force mapping CSV", type=["csv"], key="uls_forces_csv_upload",
        )
        if uploaded_uls_csv is None:
            pass  # removed Streamlit reminder banner
            st.stop()
        try:
            csv_bytes = uploaded_uls_csv.getvalue()
            imported_uls = parse_uls_demands_csv(csv_bytes, reference_table())
            editor_suffix = "mapped_" + hashlib.sha256(csv_bytes).hexdigest()[:12]
        except ValueError as exc:
            st.error(f"Cannot use the uploaded ULS force CSV: {exc}")
            st.stop()
        st.success("Complete mapped ULS demands imported. Review the bending-moment signs, "
                   "critical faces, support locations and load combinations with your sir.")

    # Apply a complete imported force source BEFORE initializing the tables.
    base_uls = calculate_uls_design(inputs, imported_uls, demand_source=current_source)
    st.markdown("#### Edit ULS section inputs")
    with st.expander("Flexure inputs — bending moment, effective depth and main reinforcement", expanded=True):
        flex_input = [
            {"row": r["row"], "member": r["member"], "location": r["location"],
             "moment_tf_m_per_m": r["moment_tf_m_per_m"],
             "effective_depth_mm": r["effective_depth_mm"],
             "provided_as_mm2_per_m": r["provided_as_mm2_per_m"]}
            for r in base_uls["flexure"]
        ]
        flex_input = st.data_editor(
            flex_input, key="uls_flex_editor_" + editor_suffix, hide_index=True,
            disabled=["row", "member", "location"], use_container_width=True, num_rows="fixed",
            column_config={"row": None},
        )
    with st.expander("Shear inputs — shear force, effective depth and longitudinal reinforcement"):
        shear_input = [
            {"row": r["row"], "member": r["member"], "location": r["location"],
             "design_shear_tf_per_m": r["design_shear_tf_per_m"],
             "effective_depth_mm": r["effective_depth_mm"],
             "provided_as_mm2_per_m": r["provided_as_mm2_per_m"]}
            for r in base_uls["shear_without_stirrups"]
        ]
        shear_input = st.data_editor(
            shear_input, key="uls_shear_editor_" + editor_suffix, hide_index=True,
            disabled=["row", "member", "location"], use_container_width=True, num_rows="fixed",
            column_config={"row": None},
        )
    with st.expander("Distribution reinforcement — bar diameter, spacing and steel requirement"):
        distrib_input = [
            {"row": r["row"], "member": r["member"], "location": r["location"],
             "required_as_mm2_per_m": r["required_as_mm2_per_m"],
             "diameter_mm": r["diameter_mm"], "spacing_mm": r["spacing_mm"]}
            for r in base_uls["distribution"]
        ]
        distrib_input = st.data_editor(
            distrib_input, key="uls_distribution_editor_" + editor_suffix, hide_index=True,
            disabled=["row", "member", "location"], use_container_width=True, num_rows="fixed",
            column_config={"row": None},
        )
    with st.expander("Stirrups — number of legs, diameter, spacing and angle"):
        stirrup_input = [
            {"row": r["row"], "member": r["member"], "location": r["location"],
             "legs": r["legs"], "bar_dia_mm": r["bar_dia_mm"],
             "spacing_mm": r["spacing_mm"], "theta_deg": r["theta_deg"]}
            for r in base_uls["stirrups"]
        ]
        stirrup_input = st.data_editor(
            stirrup_input, key="uls_stirrups_editor_" + editor_suffix, hide_index=True,
            disabled=["row", "member", "location"], use_container_width=True, num_rows="fixed",
            column_config={"row": None},
        )

    def _uls_edited_records(value):
        return value.to_dict("records") if hasattr(value, "to_dict") else list(value)

    override_fields = {
        "flexure": (flex_input, ("moment_tf_m_per_m", "effective_depth_mm", "provided_as_mm2_per_m")),
        "shear_without_stirrups": (shear_input, ("design_shear_tf_per_m", "effective_depth_mm", "provided_as_mm2_per_m")),
        "distribution": (distrib_input, ("required_as_mm2_per_m", "diameter_mm", "spacing_mm")),
        "stirrups": (stirrup_input, ("legs", "bar_dia_mm", "spacing_mm", "theta_deg")),
    }
    overrides = {}
    changes = 0
    try:
        for group, (edited, keys) in override_fields.items():
            originals = {r["row"]: r for r in base_uls[group]}
            for row in _uls_edited_records(edited):
                idx = int(row["row"])
                if idx not in originals:
                    raise ValueError("Unknown ULS section identifier")
                revised = {}
                for field in keys:
                    raw = row[field]
                    if raw is None or str(raw).strip() == "":
                        raise ValueError(f"{row['member']} / {row['location']}: {field} is blank")
                    value = float(raw)
                    if not math.isfinite(value):
                        raise ValueError(f"{row['member']} / {row['location']}: {field} is not finite")
                    if field in ("moment_tf_m_per_m", "design_shear_tf_per_m") and value < 0:
                        raise ValueError("Enter nonnegative ULS demand magnitude after verifying the tension face")
                    if field == "legs":
                        if value < 1 or not value.is_integer():
                            raise ValueError("Number of stirrup legs must be a positive integer")
                        value = int(value)
                    revised[field] = value
                    if not math.isclose(float(value), float(originals[idx][field]), abs_tol=1e-9, rel_tol=0):
                        changes += 1
                overrides[idx] = {**imported_uls.get(idx, {}), **revised}
        if demand_mode == "Original Excel reference" and changes:
            current_source = "Edited original Excel ULS reference: user-supplied trial values"
        uls = calculate_uls_design(inputs, overrides, demand_source=current_source)
        uls["input_mode"] = demand_mode
        uls["editable_values_changed"] = changes
        uls["mapped_uls_force_rows"] = sum(bool(x) for x in imported_uls.values())
        result["uls_design"] = uls
        result["limitations"] = [
            message for message in result.get("limitations", [])
            if not message.startswith("Stage 5 ULS demand source:")
        ]
        result["limitations"].append(
            f"Stage 5 ULS demand source: {current_source}. Independent verification of "
            "load factors, action signs, design sections, and reinforcement is still required."
        )
    except (ValueError, TypeError, KeyError) as exc:
        st.error(f"Invalid ULS engineering input: {exc}")
        st.stop()

    if changes:
        pass  # removed Streamlit reminder banner
    st.caption(f"**Current ULS force source:** {uls['demand_source']}")
    a1, a2, a3 = st.columns(3)
    a1.metric("Flexural sections evaluated", len(uls["flexure"]))
    a2.metric("Shear locations evaluated", len(uls["shear_without_stirrups"]))
    a3.metric("Sections needing review", uls["review_count"])
    st.caption("'PASS' means ONLY that the limited workbook-equivalent equations pass. "
               "It does not certify structural strength or compliance with IRC.")

    sec_flex, sec_dist, sec_shear, sec_links, sec_audit = st.tabs([
        "Flexural strength", "Distribution steel", "Shear without stirrups", "Stirrup design", "Source audit"
    ])
    with sec_flex:
        st.dataframe([{
            "Member": r["member"], "Location": r["location"],
            "Mu (tf·m/m)": round(r["moment_tf_m_per_m"], 3),
            "d (mm)": round(r["effective_depth_mm"], 1),
            "Min depth (mm)": round(r["d_min_mm"], 1),
            "As required (mm²/m)": None if r["as_required_mm2_per_m"] is None else round(r["as_required_mm2_per_m"], 2),
            "As provided (mm²/m)": round(r["provided_as_mm2_per_m"], 2),
            "Status": r["status"],
        } for r in uls["flexure"]], use_container_width=True, hide_index=True)
    with sec_dist:
        st.dataframe([{
            "Member": r["member"], "Location": r["location"],
            "As required (mm²/m)": round(r["required_as_mm2_per_m"], 2),
            "As provided (mm²/m)": round(r["provided_as_mm2_per_m"], 2),
            "Bar diameter (mm)": r["diameter_mm"], "Spacing (mm)": r["spacing_mm"],
            "Status": r["status"],
        } for r in uls["distribution"]], use_container_width=True, hide_index=True)
    with sec_shear:
        st.dataframe([{
            "Member": r["member"], "Location": r["location"],
            "Vu (tf/m)": round(r["design_shear_tf_per_m"], 3),
            "Shear resistance (tf/m)": round(r["resistance_tf_per_m"], 3),
            "Stirrups required": r["stirrups_required"],
        } for r in uls["shear_without_stirrups"]], use_container_width=True, hide_index=True)
    with sec_links:
        st.dataframe([{
            "Location": r["location"],
            "Vu (tf/m)": round(r["design_shear_tf_per_m"], 3),
            "VRds (tf/m)": round(r["vrd_s_tf_per_m"], 3),
            "Spacing (mm)": r["spacing_mm"],
            "Max spacing (mm)": round(r["max_spacing_mm"], 1),
            "Strength": r["shear_strength_check"],
            "Spacing": r["stirrup_spacing_check"],
            "Status": r["status"],
        } for r in uls["stirrups"]], use_container_width=True, hide_index=True)
        if any(r["spacing_mm"] > r["max_spacing_mm"] for r in uls["stirrups"]):
            st.error("The source workbook lists 'OK' for two stirrup checks, but the supplied "
                     "spacing exceeds its own calculated limits. Ask the engineer to review.")
    with sec_audit:
        st.json({
            "demand_source": uls["demand_source"],
            "reference_geometry_matches": uls["reference_geometry_matches"],
            "source_audit": uls["source"],
            "excluded_sections": uls["skipped_nonapplicable_rows"],
        })
        for note in uls["reference_warnings"] + uls["limitations"]:
            pass  # removed Streamlit reminder banner
    try:
        chart = draw_uls_reference_comparison(uls)
        chart_png = figure_to_png(chart)
        plt.close(chart)
        st.image(chart_png, use_container_width=True)
        st.download_button("Download ULS checks chart (PNG)", chart_png,
                           file_name="uls_checks_chart.png", mime="image/png", key="uls_chart_download")
    except Exception as err:
        st.error(f"ULS chart could not be rendered: {err}")

with tab_sls:
    st.subheader("Stage 6 — Serviceability Limit State (SLS)")
    pass  # removed Streamlit reminder banner
    sls_source_mode = st.radio(
        "SLS demand source",
        ["Original Excel reference", "Manual engineer-entered demands", "Engineer-mapped SLS force CSV"],
        horizontal=False, key="sls_source_mode",
    )
    st.download_button(
        "Download SLS force template (CSV)", sls_demands_csv_template(),
        file_name="sls_forces_template.csv", mime="text/csv", key="sls_force_template",
    )
    source_name = "Original Excel SLS reference moments (not verified for changed dimensions or loading)"
    demand_overrides = {}
    if sls_source_mode == "Manual engineer-entered demands":
        source_name = "Engineer-entered manual SLS moments (requires review)"
    if sls_source_mode == "Engineer-mapped SLS force CSV":
        source_name = "Uploaded engineer-mapped SLS moments (not independently verified)"
        file = st.file_uploader("Upload complete SLS force CSV", type="csv", key="sls_csv_upload")
        if file is None:
            pass  # removed Streamlit reminder banner
            st.stop()
        try:
            demand_overrides = parse_sls_demands_csv(file.getvalue())
        except (ValueError, TypeError) as exc:
            st.error(f"Invalid SLS force file: {exc}")
            st.stop()

    with st.expander("SLS material constants and acceptance criteria", expanded=True):
        st.caption("Concrete and steel grades come from the sidebar. The values below are the reference-section assumptions, not automatic IRC approvals.")
        mat1, mat2, mat3 = st.columns(3)
        with mat1:
            sls_es = st.number_input("Steel modulus Es (GPa)", min_value=1.0, value=200.0, step=5.0, key="sls_es")
            sls_ecm = st.number_input("Concrete modulus Ecm (GPa)", min_value=1.0, value=32.30824972296583, step=0.5, key="sls_ecm")
        with mat2:
            sls_kt = st.number_input("Crack parameter kt", min_value=0.0, value=0.5, step=0.05, key="sls_kt")
            sls_k1 = st.number_input("Bond parameter k1", min_value=0.01, value=0.8, step=0.05, key="sls_k1")
        with mat3:
            sls_k2 = st.number_input("Strain parameter k2", min_value=0.01, value=0.5, step=0.05, key="sls_k2")
            use_crack_limit = st.checkbox("Specify engineer-approved permissible crack width", value=False, key="sls_enable_crack_limit")
            sls_crack_limit = st.number_input("Permissible crack width (mm)", min_value=0.01, value=0.30, step=0.05, disabled=not use_crack_limit, key="sls_crack_limit")
        if not use_crack_limit:
            st.caption("No crack-width PASS or FAIL will be reported until a verified permissible limit is set.")

    share_with_uls = st.checkbox(
        "Use current ULS effective depths and provided reinforcement as SLS starting values",
        value=False, key="sls_uls_shared_steel",
    )
    if share_with_uls:
        source_name += "; optionally copied current ULS section d/As (review required)"
        pass  # removed Streamlit reminder banner
        for sid, shared in steel_details_from_uls(result["uls_design"]).items():
            demand_overrides.setdefault(sid, {}).update(shared)

    sls_options = {
        "es_gpa": sls_es, "ecm_gpa": sls_ecm,
        "k_t": sls_kt, "k_1": sls_k1, "k_2": sls_k2,
        "permissible_crack_width_mm": sls_crack_limit if use_crack_limit else None,
    }
    try:
        base_sls = calculate_sls_design(inputs, demand_overrides, demand_source=source_name, **sls_options)
        fields = [
            "rare_moment_tf_m_per_m", "quasi_moment_tf_m_per_m", "section_depth_mm",
            "effective_depth_mm", "provided_as_mm2_per_m", "equivalent_bar_diameter_mm", "cover_mm",
        ]
        records = []
        for section in base_sls["sections"]:
            records.append({
                "section_id": section["section_id"], "member": section["member"],
                "location": section["location"],
                **{field: section[field] for field in fields},
            })
        with st.expander("Edit service moments, section depths, cover and reinforcement", expanded=True):
            st.caption("Rare and quasi moments are independent SLS inputs. Check their source, sign and governing reinforcement face with your sir.")
            editor_tag = "sls_editor_" + sls_source_mode + ("_with_uls_steel" if share_with_uls else "_independent")
            edited = st.data_editor(
                records, hide_index=True, num_rows="fixed", use_container_width=True,
                disabled=["section_id", "member", "location"], key=editor_tag,
                column_config={
                    "section_id": st.column_config.TextColumn("Section ID"),
                    "member": st.column_config.TextColumn("Member"),
                    "location": st.column_config.TextColumn("Design location"),
                    "rare_moment_tf_m_per_m": st.column_config.NumberColumn("Rare moment (tf·m/m)", format="%.3f"),
                    "quasi_moment_tf_m_per_m": st.column_config.NumberColumn("Quasi moment (tf·m/m)", format="%.3f"),
                    "section_depth_mm": st.column_config.NumberColumn("Section depth (mm)", format="%.1f"),
                    "effective_depth_mm": st.column_config.NumberColumn("Effective depth (mm)", format="%.1f"),
                    "provided_as_mm2_per_m": st.column_config.NumberColumn("Provided steel (mm²/m)", format="%.2f"),
                    "equivalent_bar_diameter_mm": st.column_config.NumberColumn("Bar diameter (mm)", format="%.2f"),
                    "cover_mm": st.column_config.NumberColumn("Cover (mm)", format="%.1f"),
                },
            )
        edited_rows = edited.to_dict("records") if hasattr(edited, "to_dict") else list(edited)
        user_overrides = {}
        changes = 0
        originals = {r["section_id"]: r for r in records}
        for row in edited_rows:
            ident = row["section_id"]
            if ident not in originals:
                raise ValueError("Unknown SLS section identifier")
            updated = {}
            for field in fields:
                num = float(row[field])
                if not math.isfinite(num):
                    raise ValueError(f"Non-finite SLS input for {ident} / {field}")
                updated[field] = num
                if not math.isclose(num, originals[ident][field], rel_tol=0, abs_tol=1e-9):
                    changes += 1
            user_overrides[ident] = updated
        if changes and sls_source_mode == "Original Excel reference":
            source_name = "Original Excel SLS reference edited by user (trial inputs)"
        sls = calculate_sls_design(inputs, user_overrides, demand_source=source_name, **sls_options)
        sls["input_mode"] = sls_source_mode
        sls["editable_values_changed"] = changes
        result["sls_design"] = sls
        result["limitations"] = [v for v in result["limitations"] if not v.startswith("Stage 6 SLS demand source:")]
        result["limitations"].append(f"Stage 6 SLS demand source: {source_name}. No certified verification of SLS loads or crack limit.")
    except (ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
        st.error(f"Invalid SLS input: {exc}")
        st.stop()

    st.caption(f"**Current SLS demand source:** {sls['demand_source']}")
    met1, met2, met3 = st.columns(3)
    met1.metric("Serviceability sections", len(sls["sections"]))
    met2.metric("Stress checks needing review", sls["rare_review_count"])
    met3.metric("Crack checks needing review", sls["crack_review_count"] if use_crack_limit else "Limit not set")
    rare_view, crack_view, audit_view = st.tabs(["Rare-combination stresses", "Quasi-permanent crack widths", "Source and limitations"])
    with rare_view:
        st.dataframe([{
            "Member": row["member"], "Location": row["location"],
            "Rare moment (tf·m/m)": round(row["rare_moment_tf_m_per_m"], 3),
            "Concrete stress (MPa)": round(row["concrete_stress_mpa"], 3),
            "Concrete limit (MPa)": round(row["concrete_limit_mpa"], 3),
            "Steel stress (MPa)": round(row["steel_stress_mpa"], 3),
            "Steel limit (MPa)": round(row["steel_limit_mpa"], 3),
            "Stress check": row["stress_status"],
        } for row in sls["sections"]], hide_index=True, use_container_width=True)
    with crack_view:
        st.dataframe([{
            "Member": row["member"], "Location": row["location"],
            "Quasi moment (tf·m/m)": round(row["quasi_moment_tf_m_per_m"], 3),
            "Crack width (mm)": round(row["crack_width_mm"], 4),
            "Maximum crack spacing (mm)": round(row["maximum_crack_spacing_mm"], 1),
            "Permissible width (mm)": row["permissible_crack_width_mm"],
            "Crack check": row["crack_status"],
        } for row in sls["sections"]], hide_index=True, use_container_width=True)
    with audit_view:
        st.write("Source:", sls["demand_source"])
        st.write("Original reference geometry:", "Matches" if sls["reference_geometry_matches"] else "Differs")
        for limitation in sls["limitations"]:
            pass  # removed Streamlit reminder banner
    st.caption("Stress PASS means only that the translated reference equations are below their reported limits. "
               "It does not approve the design, section properties, vehicle loads or analysis model.")

# Stage 7: quantity worksheets are not structural design checks. They use
# reference cut lengths/counts and general BOQ quantities unless edited by sir.
with tab_bbs:
    st.subheader("Stage 7 — Bar Bending Schedule (BBS)")
    pass  # removed Streamlit reminder banner
    st.caption("Change bar diameter, cutting length or count to calculate a trial steel quantity. "
               "Only an approved reinforcement drawing and engineer-reviewed schedule can authorize fabrication.")
    bbs_col1,bbs_col2=st.columns(2)
    with bbs_col1:
        allowance=st.number_input("Additional lap/bend allowance (%) — reference 5%", min_value=0.0,
                                  max_value=100.0, value=5.0, step=0.5, key="bbs_allowance_pct")
    with bbs_col2:
        st.download_button("Download editable BBS template (CSV)", bbs_template_csv(),
                           file_name="bbs_input_template.csv",mime="text/csv",key="bbs_template")
    bbs_upload=st.file_uploader("Optional: import saved BBS template",type=["csv"],key="bbs_upload")
    bbs_base=reference_bars()
    if bbs_upload is not None:
        try:
            bbs_base=parse_bbs_csv(bbs_upload.getvalue())
            pass  # removed Streamlit reminder banner
        except (ValueError,TypeError) as exc:
            st.error(f"BBS CSV rejected: {exc}")
            st.stop()
    edit_bbs=[{'id':r['id'],'Bar mark':r['mark'],'Member':r['member'],
               'Dia (mm)':float(r['diameter_mm']),
               'Spacing (mm)':float(r['spacing_mm']) if r['spacing_mm'] is not None else 0.0,
               'Cut length (mm)':float(r['cut_length_mm']), 'Bar count':int(r['bar_count'])}
              for r in bbs_base]
    edited_bbs=st.data_editor(edit_bbs,disabled=['id','Bar mark','Member'],
                              use_container_width=True,hide_index=True,num_rows='fixed',
                              key='stage7_bbs_editor',height=425)
    if hasattr(edited_bbs,'to_dict'):
        edited_bbs=edited_bbs.to_dict('records')
    index_bbs={r['id']:dict(r) for r in bbs_base}
    for r in edited_bbs:
        entry=index_bbs[r['id']]
        entry['diameter_mm']=float(r['Dia (mm)'])
        entry['cut_length_mm']=float(r['Cut length (mm)'])
        entry['bar_count']=float(r['Bar count'])
        entry['spacing_mm']=float(r['Spacing (mm)'])
    try:
        bbs_result=calculate_bbs(list(index_bbs.values()),allowance)
        result['bbs']=bbs_result
        result['boq']=calculate_boq(inputs,bbs_result)
        result['reinforcement_review']=build_reinforcement_review(inputs,bbs_result)
        bm1,bm2,bm3=st.columns(3)
        bm1.metric('Scheduled steel (before allowance)',f"{bbs_result['net_steel_kg']:,.2f} kg")
        bm2.metric('Allowance',f"{bbs_result['allowance_kg']:,.2f} kg")
        bm3.metric('Scheduled steel (total)',f"{bbs_result['total_steel_t']:.3f} t")
        st.dataframe([{'Bar mark':x['mark'],'Member':x['member'],'Weight (kg)':round(x['weight_kg'],3),
                       'Status':x['status']} for x in bbs_result['lines']],use_container_width=True,
                     hide_index=True,height=380)
        plot=bbs_weight_chart(bbs_result)
        st.pyplot(plot)
        plt.close(plot)
        st.download_button('Download calculated BBS (CSV)',bbs_result_csv(bbs_result),
                           file_name='bbs_calculated_trial.csv',mime='text/csv',key='bbs_result')
        for msg in bbs_result['warnings']:
            pass  # removed Streamlit reminder banner
    except (ValueError,TypeError) as exc:
        st.error(f"Cannot calculate BBS: {exc}")

with tab_boq:
    st.subheader("Stage 7 — Bill of Quantities (BOQ)")
    pass  # removed Streamlit reminder banner
    st.caption("SLS drawings are not required for this tab. The charts here are quantity summaries, "
               "not construction drawings or reinforcement detailing.")
    st.download_button('Download editable BOQ template (CSV)',boq_template_csv(),
                       file_name='boq_input_template.csv',mime='text/csv',key='boq_template')
    boq_upload=st.file_uploader('Optional: import saved BOQ template',type=['csv'],key='boq_upload')
    from_csv_overrides={}
    if boq_upload is not None:
        try:
            from_csv_overrides=parse_boq_csv(boq_upload.getvalue())
        except (ValueError,TypeError) as exc:
            st.error(f"BOQ CSV rejected: {exc}")
            st.stop()
    source_boq=reference_boq_items()
    boq_edit_rows=[{'id':x['id'], 'Item':x['description'], 'Unit':x['unit'],
                    'Excel reference':float(x['reference_quantity']),
                    'Working quantity':float(from_csv_overrides.get(x['id'],x['reference_quantity']))}
                   for x in source_boq]
    edited_boq=st.data_editor(boq_edit_rows,disabled=['id','Item','Unit','Excel reference'],
                              use_container_width=True,hide_index=True,num_rows='fixed',
                              key='stage7_boq_editor',height=440)
    if hasattr(edited_boq,'to_dict'):
        edited_boq=edited_boq.to_dict('records')
    overrides={}
    for row in edited_boq:
        original=next(x['reference_quantity'] for x in source_boq if x['id']==row['id'])
        newval=float(row['Working quantity'])
        if not math.isclose(newval,original,rel_tol=0,abs_tol=1e-9):
            overrides[row['id']]=newval
    try:
        boq_result=calculate_boq(inputs,result.get('bbs'),overrides)
        result['boq']=boq_result
        core=boq_result['core_quantities']
        st.markdown('#### Geometry-linked culvert quantities')
        st.dataframe(core,hide_index=True,use_container_width=True)
        vol=next(x['quantity'] for x in core if x['description'].startswith('TOTAL'))
        metric1,metric2,metric3=st.columns(3)
        metric1.metric('Current culvert concrete',f'{vol:,.3f} m³')
        metric2.metric('Original BOQ culvert concrete',f"{boq_result['reference_box_concrete_m3']:,.3f} m³")
        metric3.metric('BOQ steel estimator vs BBS',f"{boq_result['boq_vs_bbs_rebar_difference_t']:+,.3f} t")
        plot=boq_concrete_chart(boq_result)
        st.pyplot(plot)
        plt.close(plot)
        st.download_button('Download reviewed BOQ quantities (CSV)',boq_result_csv(boq_result),
                           file_name='boq_calculated_trial.csv',mime='text/csv',key='boq_result')
        for message in boq_result['warnings']:
            pass  # removed Streamlit reminder banner
    except (ValueError,TypeError) as exc:
        st.error(f"Cannot calculate BOQ: {exc}")

with tab_rebar:
    st.subheader("Stage 8 — Reinforcement drawing review sheets")
    pass  # removed Streamlit reminder banner
    pass  # removed Streamlit reminder banner
    rebar_bbs = result.get("bbs")
    rebar_review = build_reinforcement_review(inputs, rebar_bbs)
    result["reinforcement_review"] = rebar_review
    a1,a2,a3=st.columns(3)
    a1.metric("PDF scheduled marks",len(rebar_review["schedule"]))
    a2.metric("BBS-only variants",len(rebar_review["bbs_only_marks"]))
    a3.metric("Review issues",len(rebar_review["issues"]))
    drawing_names = ["Box reinforcement section", "Top and bottom slab plans",
                     "Side wall elevations", "Bar mark and shape schedule", "Corner and lap reference details"]
    drawing_view = st.selectbox("Select reinforcement view",drawing_names, key="stage8_view")
    st.caption("Views update with geometry; the original workbook bar counts and cutting lengths "
               "DO NOT update automatically when you edit geometry.")
    drawing_functions = {
        "Box reinforcement section": "draw_reinforcement_section",
        "Top and bottom slab plans": "draw_reinforcement_plans",
        "Side wall elevations": "draw_side_wall_elevation",
        "Bar mark and shape schedule": "draw_bar_shape_schedule",
        "Corner and lap reference details": "draw_corner_and_lap_details",
    }
    try:
        import src.reinforcement_drawings as rd
        figure = (rd.draw_bar_shape_schedule(rebar_bbs) if drawing_view=="Bar mark and shape schedule"
                  else getattr(rd,drawing_functions[drawing_view])(inputs,rebar_bbs))
        drawing_png = figure_png_bytes(figure)
        plt.close(figure)
        st.image(drawing_png, use_container_width=True,
                 caption=f"{drawing_view}: schematic reference only, not to scale")
        st.download_button("Download selected view (PNG)",data=drawing_png,
                           file_name="reinforcement_"+drawing_view.lower().replace(" ","_")+".png",
                           mime="image/png",key="stage8_selected_png")
        pdf_data=make_review_pdf(inputs,rebar_bbs)
        st.download_button("Download 5-sheet review drawing (PDF)",data=pdf_data,
                           file_name="culvert_reinforcement_review_5_sheets.pdf",
                           mime="application/pdf",key="stage8_review_pdf")
        with tempfile.TemporaryDirectory() as tmp:
            dxf_file=export_reinforcement_dxf(inputs,rebar_bbs,Path(tmp)/"culvert_reinforcement_review.dxf")
            dxf_bytes=dxf_file.read_bytes()
        st.download_button("Download reinforcement reference (DXF)",data=dxf_bytes,
                           file_name="culvert_reinforcement_reference.dxf",
                           mime="application/dxf",key="stage8_review_dxf")
    except Exception as exc:
        st.error(f"Could not generate reinforcement drawing: {exc}")
    st.markdown("#### PDF bar schedule vs working BBS")
    st.dataframe([{
        "Bar mark":x["mark"],"Zone":x["zone"],"PDF dia (mm)":x["diameter_mm"],
        "PDF spacing / note":x["spacing"],"Shape":x["shape"],
        "BBS count":x["bbs_count"],"BBS cut (mm)":x["bbs_cut_mm"],"Check":x["status"]
    } for x in rebar_review["schedule"]],hide_index=True,use_container_width=True,height=410)
    with st.expander("Items to confirm with your sir",expanded=True):
        for issue in rebar_review["issues"]:st.write("•",issue)
    st.caption("PDF reference: reinforcement details drawing, sheet 3. The downloaded PDF "
               "contains five Python-generated schematic pages, not the original certified drawing.")

with tab_ref:
    st.subheader("Reference design note comparison — example case only")
    st.caption("The design note reports component dead loads in tonnes (t) using 2.50 t/m³ concrete density. These are reference quantities, not independently verified design results.")
    reference = [
        ("Top slab", 26.25, g["top_slab_volume_m3"] * 2.5),
        ("Bottom slab", 28.88, g["bottom_slab_volume_m3"] * 2.5),
        ("Side walls", 45.00, g["walls_volume_m3"] * 2.5),
        ("Four haunches", 5.40, g["haunch_volume_m3"] * 2.5),
    ]
    st.dataframe([{"Component": name, "Design note (t)": ref, "Python schematic (t)": round(calc, 3), "Difference (t)": round(calc - ref, 3)} for name, ref, calc in reference], hide_index=True, use_container_width=True)
    st.error("**Haunch discrepancy:** 150 × 150 mm right triangles have 0.01125 m² area each. The design note dead-load table instead uses 0.045 m² per haunch. For four haunches over 12 m, this is 0.54 m³ vs 2.16 m³. Do not treat either as final until the engineer confirms the cross-section.")
    pass  # removed Streamlit reminder banner
    st.caption("Reference: Design Note Of Box Culvert 1x3x3, page 1, Geometric Parameters and Calculation of dead load and SIDL.")

with tab_export:
    st.subheader("Export current inputs and calculated results")
    st.caption("JSON/CSV include stages 1–3, design-note reference, imported data and Stage 4B "
               "frame results when the solver is enabled. Model results are NOT independently validated and "
               "no code-approved load combinations are implied.")
    d1, d2, d3 = st.columns(3)
    with d1:
        st.download_button("Download JSON", data=to_json_bytes(result), file_name="culvert_results.json", mime="application/json", use_container_width=True)
    with d2:
        st.download_button("Download CSV", data=to_csv_bytes(result), file_name="culvert_results.csv", mime="text/csv", use_container_width=True)
    with d3:
        if png_bytes:
            st.download_button("Download PNG", data=png_bytes, file_name="culvert_cross_section.png", mime="image/png", use_container_width=True, key="png_export")
    if vehicle_png_bytes:
        st.download_button("Download vehicle-reference PNG",data=vehicle_png_bytes,
                           file_name="culvert_vehicle_axle_reference.png",mime="image/png",
                           key="vehicle_png_export")
    if frame_png_bytes:
        st.download_button("Download in-app frame results PNG", frame_png_bytes,
                           file_name="culvert_in_app_frame.png",mime="image/png",key="frame_export_png")
    if load_png_bytes:
        st.download_button("Download load schematic PNG", data=load_png_bytes,
                           file_name="culvert_load_schematic.png", mime="image/png", key="png_load_export")
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            dxf_path = Path(temp_dir) / "culvert_outline.dxf"
            export_schematic_dxf(inputs, dxf_path)
            dxf_bytes = dxf_path.read_bytes()
        st.download_button("Download CAD-compatible DXF schematic", data=dxf_bytes, file_name="culvert_outline.dxf", mime="application/dxf")
        with tempfile.TemporaryDirectory() as temp_dir:
            vehicle_dxf_path = Path(temp_dir) / "culvert_vehicle_reference.dxf"
            export_vehicle_reference_dxf(inputs, vehicle_results, vehicle_dxf_path)
            vehicle_dxf = vehicle_dxf_path.read_bytes()
        st.download_button("Download axle-reference DXF (schematic only)", data=vehicle_dxf,
                           file_name="culvert_vehicle_reference.dxf",mime="application/dxf")
    except ImportError:
        pass  # removed Streamlit reminder banner
    except Exception as exc:
        st.error(f"DXF export failed: {exc}")

with tab_notes:
    st.subheader("Implementation status and engineering limitations")
    st.markdown("**Stage 1:** Geometry, haunch quantities and preliminary concrete self-weight.")
    st.markdown("**Stage 2:** Earth, surcharge and fill pressure magnitudes. Stage 4B now applies "
                "assumed pressure distributions to the frame members, including vertical fill over the top slab.")
    st.markdown('**Stage 3:** Four workbook-derived vehicle presets (70R wheeled, 40T bogie, '
                '70R tracked equivalent, 385T SV) plus Custom Pattern. Reference impact/dispersion '
                'are computed separately. The Stage 4B mapping is not valid for every pattern.')
    st.markdown("**Stage 4A:** Engineer-mapped STAAD CSV result comparison with reported design-note moments/shears.")
    st.markdown("**Stage 4B:** Experimental Python plane-frame FEM with beam-column stiffness, "
                "soil springs, concrete dead, lateral soil/surcharge/cushion, vertical fill, "
                "axle strip loads and braking load application, plus a combined factor-set solve. "
                "There is no direct STAAD connection, no validated IRC design combination, and no safety certification.")
    st.markdown("**Stage 5 ULS:** Reference checks now calculate flexure, "
                "distribution reinforcement, nominal shear capacity and stirrup checks. "
                "Demands are still cached reference values, not approved envelopes.")
    st.markdown("**Stage 8 reinforcement drawings:** Box section, slab plans, bar-mark shapes, X/Y/Z and lap reference sketches; PDF/DXF/PNG review output. Missing bar diameters, h3 and construction details remain unverified.")
    st.markdown("**Stage 7 BBS/BOQ:** Editable BBS (2) reference bar masses and dynamic core concrete volumes; "
                "general BOQ quantities are cached/manual, not a completed engineering takeoff.")
    st.markdown("**Stage 6 SLS:** Original design-sheet short-term rare stress and quasi-permanent crack-width checks; "
                "editable trial inputs and complete engineer-mapped SLS demands. Long-term and deflection remain pending.")
    st.markdown("**Pending:** Verify frame geometry, loading datum, soil interaction, support arrangement, "
                "wheel distribution and sign conventions against an actual STAAD model; "
                "independently benchmark/mesh-refine the solver; IRC ULS/SLS combinations and "
                "structural design capacity, long-term SLS, deflection, and verified bar/detail quantities.")
    st.subheader("Engineering work still required")
    for limitation in result["limitations"]:
        st.write("•", limitation)
    pass  # removed Streamlit reminder banner
