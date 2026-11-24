"""Streamlit UI for the RCC box culvert geometry prototype.

Place this file at the project root beside the existing ``src`` directory.
Run: py -3.14 -m streamlit run app.py
"""
from __future__ import annotations

import io
import hashlib
import math
import tempfile
from dataclasses import replace
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
    default_flexural_reinforcement_rows, calculate_flexural_reinforcement,
    default_design_constants, calculate_design_constants,
    calculate_uls_flexural_strength, calculate_moving_vehicle_uls_envelope,
    default_distribution_reinforcement_rows, calculate_distribution_reinforcement,
    calculate_uls_shear_strength_table,
    calculate_live_frame_shear_demands,
    default_uls_stirrup_inputs, calculate_uls_stirrup_design_table,
)
from src.drawings import (draw_culvert, draw_load_schematic, export_schematic_dxf,
                         draw_vehicle_loading, export_vehicle_reference_dxf)
from src.reports import to_csv_bytes, to_json_bytes
from src.earth_pressure import EarthPressureInputs
from src.traffic_loads import BrakingInputs
from src.vehicle_loading import (VehicleLoadingInputs, vehicle_preset)
from src.load_combinations import CombinationInputs
from src.structural_analysis import is_original_geometry
from src.drawings import draw_frame_solver_results
from src.frame_solver import FrameOptions, ThermalLoad, run_frame_analysis, _build_mesh
from src.thermal_moving_envelope import calculate_thermal_moving_envelope
from src.live_static_normals import calculate_connected_normal_moments, calculate_connected_static_actions
from src.live_vehicle_ll_check import calculate_four_vehicle_ll
from src.linked_reinforcement import linked_demands, trial_main_steel, service_overrides, trial_stirrups, calculate_linked_uls_flexural_strength, calculate_linked_uls_shear_strength
from src.calculations import calculate_live_frame_flexural_demands
# The workbook reference benchmark is optional if only app.py was copied.
# The complete installation still includes src/reference_fem.py and
# data/reference_staad_model.json.
try:
    from src.reference_fem import solve_reference_case, run_reference_static_benchmark
except ModuleNotFoundError as exc:
    if exc.name not in ("src.reference_fem", "reference_fem"):
        raise
    solve_reference_case = None
    run_reference_static_benchmark = None
from src.sls_design import calculate_sls_design, sls_demands_csv_template, parse_sls_demands_csv, steel_details_from_uls, reference_table as sls_reference_table
from src.bbs import calculate_bbs, reference_bars
from src.boq import calculate_boq, reference_boq_items
from src.quantity_exports import (bbs_template_csv, boq_template_csv, bbs_result_csv,
                                  boq_result_csv, parse_bbs_csv, parse_boq_csv)
from src.quantity_charts import bbs_weight_chart, boq_concrete_chart
from src.reinforcement_drawings import (
    build_reinforcement_review, figures_for_review, figure_png_bytes,
    make_review_pdf, export_reinforcement_dxf,
)



def calculate_reference_scaled_ll_base_pressure(inputs, vehicle_inputs) -> float:
    """Estimate LL base pressure (tf/m²), anchored to the source's 0.50 value.

    Original calibration: 3.0 m clear width, 0.25 m walls, 12 m length,
    and the Class 70R(W) preset. Preserves 0.500 on *that* case, while
    recalculating for the active axle-load total and base footprint.

    Important: this is a calibrated proxy, NOT a validated foundation
    reaction calculation, code-compliant bearing envelope, or vehicle
    dispersion/contact-pressure model. Do not use for design approval.
    """
    reference_area_m2 = (3.0 + 2.0 * 0.25) * 12.0
    reference_axle_total_tf = sum(vehicle_preset('70rw').wheel_loads_tf)
    current_axle_total_tf = sum(vehicle_inputs.wheel_loads_tf)
    current_base_area_m2 = (
        inputs.clear_width_m + 2.0 * inputs.wall_thickness_m
    ) * inputs.length_m
    values = (reference_axle_total_tf, current_axle_total_tf, current_base_area_m2)
    if not all(math.isfinite(x) for x in values):
        raise ValueError('Base area and vehicle loads must be finite')
    if reference_axle_total_tf <= 0 or current_base_area_m2 <= 0 or current_axle_total_tf < 0:
        raise ValueError('Invalid base area or axle-load total for LL base pressure')
    reference_equivalent_live_load_tf = 0.50 * reference_area_m2
    return (reference_equivalent_live_load_tf
            * current_axle_total_tf / reference_axle_total_tf
            / current_base_area_m2)

st.set_page_config(page_title="RCC Box Culvert | ULS, SLS, BBS & BOQ", page_icon="🏗️", layout="wide", initial_sidebar_state="expanded")

@st.cache_data(show_spinner=False, max_entries=10)
def _cached_moving_uls_envelope(inputs, earth, braking, vehicle, combo, options, step_m, thermal):
    """Cached moving ULS scan including exact active thermal action per position."""
    if thermal is not None and thermal.temperature_factor > 0 and (
        abs(thermal.uniform_delta_c) > 1e-12 or abs(thermal.top_minus_bottom_c) > 1e-12
    ):
        return calculate_thermal_moving_envelope(
            inputs, earth=earth, braking=braking, vehicle=vehicle,
            combo=combo, options=options, step_m=step_m, thermal=thermal,
        )
    return calculate_moving_vehicle_uls_envelope(
        inputs, earth=earth, braking=braking, vehicle=vehicle, combo=combo,
        options=options, step_m=step_m,
    )



def _sls_rare_stress_schedule(sls: dict) -> list[dict]:
    """Format *computed* SLS rare-combination stresses as the 16-row Excel-style schedule.

    The source workbook expresses stresses in t/m² and uses a 100× conversion
    from MPa, rather than exact SI tonne-force conversion.  Preserve that
    reference convention here for side-by-side comparisons; use the unscaled
    MPa results to decide PASS/REVIEW. No original row result is hard-coded.
    """
    # Two "Section" descriptors from the reference table are kept as separate
    # data columns instead of merging cells (which st.dataframe cannot do).
    locations = {
        "S01": ("Top Slab", "End Supp.", "Wall face"),
        "S02": ("Top Slab", "End Supp.", "Haunch end"),
        "S03": ("Top Slab", "Mid Span", ""),
        "S04": ("Bottom Slab", "End Supp.", "Wall face"),
        "S05": ("Bottom Slab", "End Supp.", "Haunch end"),
        "S06": ("Bottom Slab", "Mid Span", ""),
        "S07": ("Side Wall", "Side wall top Supp.", "Earth face"),
        "S08": ("Side Wall", "Side wall top Supp.", "Non-Earth face"),
        "S09": ("Side Wall", "Side wall top Supp. haunch end", "Earth face"),
        "S10": ("Side Wall", "Side wall top Supp. haunch end", "Non-Earth face"),
        "S11": ("Side Wall", "Mid Span", "Earth face"),
        "S12": ("Side Wall", "Mid Span", "Non-Earth face"),
        "S13": ("Side Wall", "Side wall bottom Supp.", "Earth face"),
        "S14": ("Side Wall", "Side wall bottom Supp.", "Non-Earth face"),
        "S15": ("Side Wall", "Side wall bottom Supp. haunch end", "Earth face"),
        "S16": ("Side Wall", "Side wall bottom Supp. haunch end", "Non-Earth face"),
    }
    sections = {s["section_id"]: s for s in sls["sections"]}
    if set(sections) != set(locations) or len(sls["sections"]) != len(locations):
        raise ValueError("SLS rare-combination schedule requires all 16 known sections")

    records = []
    for sid, (component, section, face) in locations.items():
        row = sections[sid]
        # Each value comes from calculate_sls_design() using the current
        # SLS editor inputs and material properties (including zero moments).
        records.append({
            "Component": component,
            "Section": section,
            "Face / location": face,
            "A_st provided (mm²/m)": round(row["provided_as_mm2_per_m"], 2),
            "x_u (mm)": round(row["neutral_axis_mm"], 2),
            "I_cr (m⁴)": round(row["cracked_inertia_m4"], 8),
            "Moment (t·m/m)": round(row["rare_moment_tf_m_per_m"], 3),
            "Concrete stress (t/m²)": round(row["concrete_stress_mpa"] * 100, 1),
            "Concrete limit (t/m²)": round(row["concrete_limit_mpa"] * 100, 1),
            "Steel stress (t/m²)": round(row["steel_stress_mpa"] * 100, 2),
            "Steel limit (t/m²)": round(row["steel_limit_mpa"] * 100, 1),
            "Check": "OK" if row["stress_status"] == "PASS" else "REVIEW",
        })
    return records


def _sls_quasi_crack_schedule(sls: dict) -> list[dict]:
    """Format live SLS crack-width results as the original 16-section worksheet.

    Each displayed engineering value is derived from calculate_sls_design().
    No crack widths, strains, stress results or moments are hard-coded.
    The bonded-reinforcement spacing expression shown in the worksheet is a
    geometric *reference value*, not evidence of the actual bar spacing.
    """
    # Reuse the existing, tested 16-section ordering and descriptive labels.
    locations = _sls_rare_stress_schedule(sls)
    sections = {section['section_id']: section for section in sls['sections']}
    ids = [f'S{number:02d}' for number in range(1, 17)]
    if len(sections) != 16 or set(sections) != set(ids) or len(locations) != 16:
        raise ValueError('Quasi-permanent SLS crack schedule requires all 16 sections')

    rows = []
    for desc, sid in zip(locations, ids):
        section = sections[sid]
        cover = float(section['cover_mm'])
        diameter = float(section['equivalent_bar_diameter_mm'])
        rows.append({
            'Component': desc['Component'],
            'Section': desc['Section'],
            'Face / location': desc['Face / location'],
            'A_st (mm²/m)': round(section['provided_as_mm2_per_m'], 2),
            'A_c,eff (mm²/m)': round(section['effective_tension_area_mm2_per_m'], 0),
            'ρ_p,eff': round(section['effective_reinforcement_ratio'], 5),
            'Φ_eq (mm)': round(diameter, 2),
            'Clear cover (mm)': round(cover, 2),
            '5(c + Φ/2) (mm)': round(5.0 * (cover + diameter / 2.0), 2),
            'S_r,max (mm)': round(section['maximum_crack_spacing_mm'], 2),
            'Quasi moment (tf·m/m)': round(section['quasi_moment_tf_m_per_m'], 5),
            'σ_sc (MPa)': round(section['quasi_steel_stress_mpa'], 1),
            'ε_sm − ε_cm': round(section['mean_strain_difference'], 6),
            'Crack width (mm)': round(section['crack_width_mm'], 3),
        })
    return rows

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
    "s1_side_wall": 0.275,
    "s1_top_slab": 0.25,
    "s1_bottom_slab": 0.275,
    "s1_cells": 1,
    "s1_internal_wall": 0.0,
}
for _key, _default in _S1_DEFAULTS.items():
    if _key not in st.session_state:
        st.session_state[_key] = _default

# This application solves one cell only. An interior dividing wall does not
# exist for a single-cell box. Clear stale values entered in earlier sessions
# before the sidebar widget is instantiated; otherwise the unsupported-model
# guard below stops the entire page after displaying only three metrics.
st.session_state["s1_internal_wall"] = 0.0


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


# This application supports only a single-cell culvert. Reset older sessions
# that may have retained an unsupported multi-cell setting.
_s1_cells_were_changed = int(st.session_state["s1_cells"]) != 1
st.session_state["s1_cells"] = 1
if "s1_effective_span" not in st.session_state or _s1_cells_were_changed:
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
    number_of_cells = st.number_input("Number of cells", min_value=1, max_value=1,
                                     step=1, key="s1_cells", disabled=True,
                                     help="Fixed at 1: this is a single-cell box culvert.")

    st.markdown("**Concrete thicknesses**")
    wall = st.number_input("Side wall thickness (m)", min_value=0.01, step=0.025,
                           format="%.3f", key="s1_side_wall", on_change=_s1_sync_geometry)
    top = st.number_input("Top slab thickness (m)", min_value=0.01, step=0.025,
                          format="%.3f", key="s1_top_slab", on_change=_s1_sync_geometry)
    bottom = st.number_input("Bottom slab thickness (m)", min_value=0.01, step=0.025,
                             format="%.3f", key="s1_bottom_slab", on_change=_s1_sync_geometry)
    internal_wall_thickness_m = st.number_input(
        "Internal wall thickness (m)", min_value=0.0, step=0.025,
        format="%.3f", key="s1_internal_wall", disabled=True,
        help="Not applicable: this app currently supports one cell, with no internal wall. "
             "Edit Side wall thickness to change either of the two exterior walls.")

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
    st.subheader("Stage 4B — Experimental 2D frame solver")
    enable_frame = st.checkbox("Run in-app FEM structural model", value=True,
                               help="Computes *modelled* member actions and foundation reactions; not approved engineering results.")
    frame_e = st.number_input("Concrete elastic modulus (GPa)", min_value=1.0,
                              max_value=100.0, value=32.0, step=1.0)
    frame_mesh = st.slider("Frame divisions per slab/wall", 2, 24, 8)
    scan_vehicle_uls = st.checkbox(
        'Scan axle positions for ULS flexural moment envelope', value=True,
        help='Default ON: analyze multiple axle positions and retain the governing '
             'signed moment for each reinforcement face. This is a sampled experimental '
             'envelope, not a validated IRC moving-load combination.',
    )
    moving_uls_step = st.select_slider(
        'Automatic moving-vehicle scan spacing (m)',
        options=[0.05, 0.10, 0.15, 0.25, 0.50], value=0.15,
        disabled=not scan_vehicle_uls,
        help='Finer spacing solves more vehicle positions. Candidate section '
             'crossings are included independently of this spacing.',
    )
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
        structural_records=None,  # External STAAD/CSV imports intentionally disabled.
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

tab_drawing, tab_calcs, tab_earth, tab_traffic, tab_vehicle, tab_combo, tab_solver, tab_bbs, tab_boq, tab_rebar, tab_download = st.tabs([
    "📐 Drawing", "🧮 Calculations", "🌍 Earth pressure", "🚛 Braking forces",
    "🚚 Vehicle axles & dispersion", "🧾 Factor sets", "🧱 In-app frame solver",
    "🪛 BBS steel schedule", "📦 BOQ quantities", "📐 Reinforcement drawings", "Download"
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


_report_messages = []
_report_temp_images = {}
_report_earth_png = None

def _report_notice(renderer, message, *args, **kwargs):
    _report_messages.append(str(message))
    return renderer(message, *args, **kwargs)

with tab_calcs:
    st.subheader("Calculations")
    # Future calculation sub-sections can be added to this list.
    geometry_quantity_tab, dead_load_tab, spring_constant_tab, earth_pressure_calc_tab, braking_force_calc_tab, temperature_calc_tab, design_moment_calc_tab, flexural_reinforcement_calc_tab, design_constants_calc_tab, uls_flexural_strength_calc_tab, distribution_reinforcement_calc_tab, uls_shear_strength_calc_tab, sls_calc_tab = st.tabs([
        "Geometry & Quantity", "Dead Load and SIDL", "Spring Constant", "Earth Pressure", "Braking Force", "Temperature", "Design moment due to different load combination", "Flexural R/F detail of RCC Box", "Design Constants", "ULS Flexural Strength Check", "Distribution Reinforcement", "ULS Shear Strength Check", "SLS Checks"
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
        st.caption("Dead Load of Box — dimensions and densities are recomputed from the existing Design Inputs. Reference-only assumptions are retained where not defined by those inputs.")

        # Automatically populate the existing 9-row, 8-column schedule from
        # the ORIGINAL sidebar Design Inputs; no additional design fields.
        # This retains the same member order, column order and reference
        # assumptions as the current app. The calculation output below is
        # recomputed on every Streamlit rerun.
        base_rows = default_dead_load_sidl_rows(inputs)
        editor_rows = [
            {**r, 'Area (m²)': r['Area (m²)'] if r['Area (m²)'] is not None else 0.0}
            for r in base_rows
        ]
        st.dataframe(
            editor_rows, hide_index=True, use_container_width=True, height=390,
            column_config={
                'Member': st.column_config.TextColumn('Member', width='medium'),
                'Nos': st.column_config.NumberColumn('Nos', format='%d'),
                'Length (m)': st.column_config.NumberColumn('Length (m)', format='%.3f'),
                'Breadth (m)': st.column_config.NumberColumn('Breadth (m)', format='%.3f'),
                'Height (m)': st.column_config.NumberColumn('Height (m)', format='%.3f'),
                'Density (t/m³)': st.column_config.NumberColumn('Density (t/m³)', format='%.3f'),
                'Method': st.column_config.TextColumn('Method'),
                'Area (m²)': st.column_config.NumberColumn('Area (m²)', format='%.3f'),
            },
        )
        edited_rows = base_rows

        # Calibrated *reference-case* live-load bearing-pressure estimate.
        # The source design supplies 0.50 tf/m², but does not supply an
        # independently validated LL reaction-to-bearing-pressure procedure.
        # Convert that datum to an equivalent live-load resultant over the
        # original footprint, then scale it with the selected axle-load total
        # and divide by the *current* base area. This is NOT a design-code
        # bearing-pressure or moving-vehicle FEM reaction envelope.
        ll_base_pressure = calculate_reference_scaled_ll_base_pressure(
            inputs, vehicle_inputs,
        )
        base_area_edit = None  # Always recomputed from the current geometry.
        st.caption(
            'LL base pressure: reference-calibrated estimate from the current '
            'vehicle axle-load total and base slab area. The original 1×3×3, '
            '12 m, Class 70R(W) case yields 0.500 t/m². This is not a '
            'validated traffic bearing-pressure envelope.'
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
                _report_notice(st.error, 'Total base pressure exceeds the entered safe bearing capacity.')
            st.caption('Reference discrepancy: the Crash Barrier and Haunch rows use the reference Area (m²) '
                       'column. Changing their displayed breadth/height alone will not change their weights.')
        except (ValueError, TypeError) as exc:
            _report_notice(st.error, f'Cannot calculate Dead Load and SIDL: {exc}')


    with spring_constant_tab:
        st.subheader('Spring Constant')
        st.caption('Spring constant = current subgrade modulus × tributary width from '
                   'the active bottom-slab FEM mesh (1 m longitudinal strip).')
        # This is the SAME centreline mesh and the SAME half-adjacent-element
        # tributary rule used by src.frame_solver.run_frame_analysis().
        # Keep the source's four-row display as a representative schedule; the
        # actual FEM applies springs to ALL bottom-slab nodes, not just these four.
        _mesh_nodes, _mesh_elements, _mesh_groups, _base_nodes, _span, _rise = _build_mesh(
            inputs, frame_options
        )
        _base_stations = [float(x) for _, x in _base_nodes]
        if len(_base_stations) < 4:
            raise ValueError('FEM bottom-slab mesh must contain at least four support nodes')
        _tributaries = [
            (0.5 * (_base_stations[i] - _base_stations[i-1]) if i else 0.0)
            + (0.5 * (_base_stations[i+1] - _base_stations[i])
               if i + 1 < len(_base_stations) else 0.0)
            for i in range(len(_base_stations))
        ]
        if any(w < 0 for w in _tributaries) or not math.isclose(
            sum(_tributaries), _span, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise ValueError('Derived spring tributaries do not cover the bottom-slab centreline')
        spring_inputs = [
            {**row, 'Eff. Width (m)': _tributaries[i]}
            for i, row in enumerate(default_spring_constant_rows())
        ]
        st.dataframe(
            spring_inputs, hide_index=True, use_container_width=True,
            column_config={
                'S.No.': st.column_config.NumberColumn('S.No.', format='%d'),
                'Node No.': st.column_config.TextColumn('Node No.', width='large'),
                'Eff. Width (m)': st.column_config.NumberColumn('Eff. Width (m)', format='%.4f'),
            },
        )
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
            st.caption(f'Four displayed rows represent the first four bottom-slab mesh nodes; '
                       f'the FEM uses all {len(_base_nodes)} bottom-slab springs. '
                       f'Full mesh tributary width = {sum(_tributaries):.4f} m '
                       f'(centreline span {_span:.4f} m). '
                       'Changing mesh refinement may change individual row widths, '
                       'while total tributary width remains equal to the centreline span.')
        except (ValueError, TypeError) as exc:
            _report_notice(st.error, f'Cannot calculate Spring Constant: {exc}')

    with earth_pressure_calc_tab:
        st.subheader('Earth Pressure')
        st.markdown('**Earth Pressure per meter width**')
        try:
            pressures = result['earth_pressure_reference']
            # Schematic redraws with slab, wall, haunch, and pressure inputs.
            pressure_figure = draw_earth_pressure_design_note(inputs, pressures)
            st.pyplot(pressure_figure, use_container_width=True)
            _report_earth_png = figure_to_png(pressure_figure)
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
            _report_notice(st.error, f'Cannot calculate Earth Pressure: {exc}')


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
            _report_notice(st.error, f'Cannot calculate Braking Force: {exc}')


with temperature_calc_tab:
    st.subheader('Temperature')
    st.caption('Temperature effects as shown in the supplied design note — IRC 6:2017 Clause 215.')
    st.latex(r'F = E_c \alpha \Delta T \times A \times \text{profile factor}')

    tcol1, tcol2 = st.columns(2)
    with tcol1:
        temp_e = st.number_input('Concrete modulus Ec (t/m²)', min_value=1.0,
                                 value=3230000.0, step=1000.0, format='%.2e',
                                 key='temperature_modulus_tf_m2', disabled=True)
    with tcol2:
        temp_alpha = st.number_input('Thermal expansion coefficient α (1/°C)',
                                     min_value=0.0000001, value=0.000012,
                                     step=0.0000001, format='%.6f',
                                     key='temperature_alpha_per_c', disabled=True)
    st.caption(f'Average slab thickness h = {inputs.top_slab_thickness_m:.3f} m (Stage 1 Top Slab Thickness). '
               'Profile factor = 1 for rectangular and 0.5 for triangular regions.')

    temperature_panels = {}
    uniform_temperature_result = None
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
        # Existing design inputs select the geometry. The source temperature
        # profile remains a stated reference assumption, not an editable result.
        ratio = inputs.top_slab_thickness_m / 0.25
        edited = [{**item, 't (m)': item['t (m)'] * ratio} for item in editable]
        st.dataframe(
            edited, hide_index=True, use_container_width=True,
            column_config={
                'Zone': st.column_config.TextColumn('S.No.', width='small'),
                'ΔT (°C)': st.column_config.NumberColumn('ΔT (°C)', format='%.3f'),
                'b (m)': st.column_config.NumberColumn('b (m)', format='%.4f'),
                't (m)': st.column_config.NumberColumn('t (m)', format='%.4f'),
            },
        )
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
            _report_temp_images[mode] = figure_to_png(figure)
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
            temperature_panels[mode] = panel
        except (ValueError, TypeError, KeyError) as ex:
            _report_notice(st.error, f'Cannot calculate temperature {mode}: {ex}')

    with rise_tab:
        _temperature_gradient_panel('rise')
    with fall_tab:
        _temperature_gradient_panel('fall')
    with uniform_tab:
        st.markdown('**Effect of uniform temperature — IRC 6:2017 Clause 215.2**')
        uc1,uc2=st.columns(2)
        with uc1:
            max_air=st.number_input('Maximum shade air temperature (°C)',
                value=42.2, step=0.1, format='%.2f', key='temp_air_max', disabled=True)
            min_air=st.number_input('Minimum shade air temperature (°C)',
                value=2.0, step=0.1, format='%.2f', key='temp_air_min', disabled=True)
            creep_e=st.number_input('Concrete modulus considering creep (t/m²)',
                value=1620000.0, min_value=1.0, step=1000.0,
                format='%.2e', key='temp_creep_E', disabled=True)
        with uc2:
            bridge_max=st.number_input('Bridge temperature at restraint — Maximum (°C)',
                value=32.1, step=0.1, format='%.2f', key='temp_restraint_max', disabled=True)
            bridge_min=st.number_input('Bridge temperature at restraint — Minimum (°C)',
                value=12.1, step=0.1, format='%.2f', key='temp_restraint_min', disabled=True)
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
            uniform_temperature_result = uniform
            st.caption(f"Creep reduction = Ec,creep / Ec = {uniform['application_factor']:.4f}. "
                       'The design note describes this as approximately half. '
                       'Thermal equivalent member loads are now applied in the experimental Python FEM.')
        except (ValueError,TypeError) as ex:
            _report_notice(st.error, f'Cannot calculate uniform temperature: {ex}')


    active_thermal = ThermalLoad()  # overwritten by the selected live temperature case
    # All three temperature cases are solved with the same FEM geometry and
    # structural restraints as the main analysis; no printed STAAD forces are
    # substituted for a computed frame response.
    st.markdown('**Thermal actions in Python FEM — calculated from above tables**')
    temp_case = st.selectbox('Thermal analysis case',
        ['None', 'Uniform rise', 'Uniform fall', 'Gradient rise', 'Gradient fall'],
        key='temperature_fem_case')
    thermal_factor = st.number_input('Thermal load combination factor (engineer-reviewed)',
        min_value=0.0, value=1.0, step=0.05, format='%.2f',
        key='temperature_fem_factor')
    try:
        thermal_cases = {'None': (0.0, 0.0)}
        if uniform_temperature_result is not None:
            thermal_cases['Uniform rise'] = (float(uniform_temperature_result['applied_rise_c']), 0.0)
            thermal_cases['Uniform fall'] = (-float(uniform_temperature_result['applied_fall_c']), 0.0)
        if 'rise' in temperature_panels:
            thermal_cases['Gradient rise'] = (0.0, float(temperature_panels['rise']['equivalent_delta_t_c']))
        if 'fall' in temperature_panels:
            thermal_cases['Gradient fall'] = (0.0, -float(temperature_panels['fall']['equivalent_delta_t_c']))
        if temp_case not in thermal_cases:
            _report_notice(st.warning, 'Temperature case has no valid input results yet.')
        else:
            selected_uniform, selected_gradient = thermal_cases[temp_case]
            active_thermal = ThermalLoad(
                uniform_delta_c=selected_uniform,
                top_minus_bottom_c=selected_gradient,
                expansion_coefficient_per_c=temp_alpha,
                temperature_factor=thermal_factor)
            # Primary analysis uses all current Stage 3 load factors. Results
            # are available for downstream frame-connected checks, but they
            # are not automatically a design-code-approved ULS/SLS envelope.
            if enable_frame:
                frame_results = run_frame_analysis(
                    inputs, earth=plot_earth_inputs, braking=plot_braking_inputs,
                    vehicle=vehicle_inputs, combo=combo_inputs,
                    options=frame_options, thermal=active_thermal)
                result['frame_solver'] = frame_results
                st.caption('Thermal load is included in the active FEM analysis. '
                           'The moving-vehicle ULS scan also solves the selected thermal action '
                           'at each axle position when enabled.')
                with st.expander('Thermal FEM member actions (experimental)', expanded=True):
                    st.dataframe(frame_results['member_action_summary'],
                                 hide_index=True, use_container_width=True)
                    st.caption('Thermal load is a consistent initial-strain action; '
                               'net thermal external force is zero but restrained member forces need not be. '
                               'Confirm gradient direction, material coefficients and combination factors with the engineer.')
            else:
                st.info('Enable the in-app FEM model in Design Inputs to apply temperature loading.')
    except (ValueError, TypeError, KeyError) as ex:
        _report_notice(st.error, f'Cannot apply temperature effects to FEM: {ex}')



# Shared results from the three earlier tabs for the linked ULS display.
# Streamlit executes every tab on each rerun. Keep source readiness explicit:
# locals() inside a generator expression refers to the generator's own scope
# and therefore cannot detect variables assigned by the other tabs.
_uls_linked_inputs = {
    'Design Moment': None,
    'Shear Force': None,
    'Flexural R/F': None,
    'Design Constants': None,
}
_uls_linked_errors = {}

@st.cache_data(show_spinner=False, max_entries=8)
def _cached_design_normal_moments(current_inputs, solver_options):
    return calculate_connected_static_actions(current_inputs, options=solver_options)


@st.cache_data(show_spinner=False, max_entries=4)
def _cached_std_based_four_vehicle_ll(current_inputs, modulus_gpa, scan_spacing,
                                            mesh_divisions, chosen_vehicle, chosen_vehicle_inputs, compression_only):
    """Pure-by-input live FEM evaluation; reruns on changed geometry/load values.

    Cached results are never represented as validated design actions.  The
    reference schedules are for comparison and row mapping only.
    """
    return calculate_four_vehicle_ll(
        current_inputs, default_design_moment_rows(), default_design_shear_rows(),
        data_dir=Path(__file__).resolve().parent / 'data',
        elastic_modulus_gpa=modulus_gpa, scan_step_m=scan_spacing,
        mesh_divisions_per_member=mesh_divisions,
        selected_vehicle_code=(chosen_vehicle if chosen_vehicle in
                               ('70rw', '40t', '70rt', 'sv385') else None),
        selected_vehicle_inputs=chosen_vehicle_inputs,
        compression_only_springs=compression_only,
    )


with design_moment_calc_tab:
    st.subheader('Design moment due to different load combination')
    # Always use the current four-vehicle LL calculation. The comparison
    # controls are hidden; the reference scan spacing remains unchanged.
    ll_scan_step = 0.25
    use_unverified_live_ll = True
    _live_ll_for_tables = None
    try:
        with st.spinner('Recalculating four-vehicle LL for current Design Inputs...'):
            _live_ll_for_tables = _cached_std_based_four_vehicle_ll(
                inputs, float(frame_options.concrete_elastic_modulus_gpa),
                float(ll_scan_step), int(frame_options.mesh_divisions_per_member),
                vehicle_code, vehicle_inputs, frame_options.compression_only_springs,
            )
    except (ValueError, TypeError, KeyError, OSError, RuntimeError,
            ArithmeticError, AssertionError) as exc:
        _report_notice(st.error, 'Live vehicle LL could not be calculated. Both tables retain '
                 'reference LL values: ' + str(exc))
        _live_ll_for_tables = None

    st.caption('Live FEM results require engineer review; changed designs are not independently validated.')

    st.caption('Sign convention: +ve MZ causes tension at the top of the member; '
               '-ve MZ causes tension at the bottom. Units: t-m/m.')

    moment_editable_columns = ('ULS Normal', 'ULS LL', 'Rare Normal', 'Rare LL',
                               'Quasi Normal', 'Quasi LL')
    if 'design_moment_editor_version' not in st.session_state:
        st.session_state['design_moment_editor_version'] = 0
    if st.button('Reset Design Moments', key='design_moment_reset'):
        st.session_state['design_moment_editor_version'] += 1

    # The source quasi-permanent combination excludes vehicle LL in all 26 rows.
    original_moments = [{**row, 'Quasi LL': 0.0} for row in default_design_moment_rows()]
    if _live_ll_for_tables is not None:
        computed_ll_rows = _live_ll_for_tables['moment']
        if len(computed_ll_rows) != len(original_moments) or any(
            int(old['Excel row']) != int(new['Excel row'])
            for old, new in zip(original_moments, computed_ll_rows)
        ):
            _report_notice(st.error, 'LL moment row mapping failed. Original reference LL retained.')
            _live_ll_for_tables = None
        else:
            original_moments = [
                {**old, **{column: float(new[column]) for column in
                          ('ULS LL', 'Rare LL')}, 'Quasi LL': 0.0}
                for old, new in zip(original_moments, computed_ll_rows)
            ]
    # Update the three Normal force columns independently of the four-vehicle LL calculation.
    # If live computation fails, preserve the reference but warn conspicuously;
    # do not claim stale values represent the user's changed geometry.
    _live_static_normal_ok = False
    try:
        _live_static_result = _cached_design_normal_moments(inputs, frame_options)
        live_normal_rows = _live_static_result['moment_normal']
        if len(live_normal_rows) != 26 or len(original_moments) != 26:
            raise ValueError('Expected exactly 26 original moment rows')
        if any(int(ref.get('Excel row', live['Excel row'])) != int(live['Excel row'])
               for ref, live in zip(original_moments, live_normal_rows)):
            raise ValueError('Source row selector does not match the reference schedule')
        original_moments = [
            {**ref, **{key: float(live[key]) for key in
                       ('ULS Normal', 'Rare Normal', 'Quasi Normal')}}
            for ref, live in zip(original_moments, live_normal_rows)
        ]
        _live_static_normal_ok = True
        st.caption('Normal moments are recalculated from the current geometry, '
                   'soil pressure and source static factor sets. Quasi LL is zero by the '
                   'source combination rule.')
    except (ValueError, TypeError, KeyError, ArithmeticError, RuntimeError) as exc:
        _report_notice(st.warning, 'Live Normal FEM values unavailable; Normal and LL columns '
                   'show reference inputs, NOT recalculated actions: ' + str(exc))
    moment_input_rows = [
        {'Member': row['Member'], 'Location': row['Location'], 'Face': row['Face'],
         **{column: row[column] for column in moment_editable_columns}}
        for row in original_moments
    ]
    edited_moments = st.data_editor(
        moment_input_rows,
        key=(f"design_moment_editor_quasi_zero_v1_{st.session_state['design_moment_editor_version']}_"
             f"{hashlib.sha256(repr([(r['ULS Normal'], r['Rare Normal'], r['Quasi Normal'], r['ULS LL'], r['Rare LL'], r['Quasi LL']) for r in original_moments]).encode()).hexdigest()[:12]}"),
        hide_index=True, use_container_width=True, num_rows='fixed', height=440,
        disabled=['Member', 'Location', 'Face', 'ULS Normal', 'Rare Normal', 'Quasi Normal', 'Quasi LL'] + (['ULS LL', 'Rare LL'] if _live_ll_for_tables is not None else []),
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
            {**original, **edited, 'Quasi LL': 0.0}
            for original, edited in zip(original_moments, edited_moments)
        ]
        moment_result = calculate_design_moment_combinations(original_moments if _live_static_normal_ok and _live_ll_for_tables is not None else rows_for_calc)
        _uls_linked_inputs['Design Moment'] = (moment_result['rows'] if _live_static_normal_ok and _live_ll_for_tables is not None else None)
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
                   'for this single-cell reference case. Normal moments are live FEM outputs; '
                   'LL entries are live FEM values when calculation succeeds; failed calculations retain reference values with an error notice. These rows do not change the separate '
                   'separate legacy Stage 5 design summary. The linked calculation checks below use current table demands.')
    except (ValueError, TypeError) as exc:
        _uls_linked_inputs['Design Moment'] = None
        _uls_linked_errors['Design Moment'] = str(exc)
        _report_notice(st.error, f'Cannot calculate Design Moments: {exc}')

    st.divider()
    st.markdown('### ULS Shear Force (t/m)')
    if 'design_shear_editor_version' not in st.session_state:
        st.session_state['design_shear_editor_version'] = 0
    if st.button('Reset Shear Forces', key='design_shear_reset'):
        st.session_state['design_shear_editor_version'] += 1

    original_shears = default_design_shear_rows()
    if _live_ll_for_tables is not None:
        live_shear_ll = _live_ll_for_tables['shear']
        if len(live_shear_ll) != len(original_shears) or any(
            int(old['Excel row']) != int(new['Excel row'])
            for old, new in zip(original_shears, live_shear_ll)
        ):
            _report_notice(st.error, 'LL shear row mapping failed. Original reference LL retained.')
        else:
            original_shears = [
                {**old, 'LL': float(new['LL'])}
                for old, new in zip(original_shears, live_shear_ll)
            ]
    _live_shear_normal_ok = False
    try:
        _computed_shears = _cached_design_normal_moments(inputs, frame_options)['shear_normal']
        if len(_computed_shears) != 16 or len(original_shears) != 16:
            raise ValueError('Expected exactly 16 shear rows')
        if any(int(ref.get('Excel row', live['Excel row'])) != int(live['Excel row'])
               for ref, live in zip(original_shears, _computed_shears)):
            raise ValueError('Shear row selector does not match reference schedule')
        original_shears = [
            {**ref, 'Normal': float(live['Normal'])}
            for ref, live in zip(original_shears, _computed_shears)
        ]
        _live_shear_normal_ok = True
        st.caption('ULS Normal shears are recalculated from the current static FEM '
                   'cases; four-vehicle LL values are recalculated automatically when the live solver succeeds.')
    except (ValueError, TypeError, KeyError, ArithmeticError, RuntimeError) as exc:
        _report_notice(st.warning, 'Live ULS Normal shear unavailable; reference values are shown '
                   'and may not reflect changed inputs: ' + str(exc))
    edited_shear_rows = None
    shear_edit_rows = [
        {'Member': row['Member'], 'Location': row['Location'], 'Face': row['Face'],
         'Normal': row['Normal'], 'LL': row['LL']}
        for row in original_shears
    ]
    edited_shears = st.data_editor(
        shear_edit_rows,
        key=(f"design_shear_editor_{st.session_state['design_shear_editor_version']}_"
             f"{hashlib.sha256(repr([(r['Normal'], r['LL']) for r in original_shears]).encode()).hexdigest()[:12]}"),
        hide_index=True, use_container_width=True, num_rows='fixed', height=460,
        disabled=['Member', 'Location', 'Face', 'Normal'] + (['LL'] if _live_ll_for_tables is not None else []),
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
        if _live_shear_normal_ok and _live_ll_for_tables is not None and len(shear_result['rows']) == 16 and all(float(a['LL']) == float(b['LL']) for a,b in zip(shear_result['rows'], _live_ll_for_tables['shear'])):
            _uls_linked_inputs['Shear Force'] = shear_result['rows']
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
        _report_notice(st.error, f'Cannot calculate ULS shear forces: {exc}')



with flexural_reinforcement_calc_tab:
    st.subheader('Flexural R/F detail of RCC Box')
    st.caption('Trial bar spacing follows the current ULS moments. Reference bar sizes and minimum trial spacing are retained; engineer review is required.')
    st.latex(r'A_{s,\,face}=\sum_i\frac{\pi\,\phi_i^2}{4}\frac{1000}{s_i}\quad(\mathrm{mm}^2/\mathrm{m})')

    if 'flexural_rf_editor_version' not in st.session_state:
        st.session_state['flexural_rf_editor_version'] = 0
    if st.button('Reset Flexural R/F', key='flexural_rf_reset'):
        st.session_state['flexural_rf_editor_version'] += 1

    flexural_defaults = default_flexural_reinforcement_rows()
    _trial_steel_ok = False
    try:
        if _uls_linked_inputs['Design Moment'] is None:
            raise ValueError('Current live moment demands unavailable')
        flexural_defaults, trial_notes = trial_main_steel(inputs, default_design_constants(inputs), _uls_linked_inputs['Design Moment'])
        _trial_steel_ok = True
        for note in trial_notes:
            _report_notice(st.warning, note)
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        _report_notice(st.error, 'Trial reinforcement unavailable: ' + str(exc))
    flexural_columns = {
        'Component': st.column_config.TextColumn('Component', width='medium'),
        'Section': st.column_config.TextColumn('Section', width='medium'),
        'Position': st.column_config.TextColumn('Location / Face', width='medium'),
        'Reference Inner (mm²/m)': st.column_config.NumberColumn('Inner provided — editable (mm²/m)', format='%.2f', min_value=0.0),
        'Reference Outer (mm²/m)': st.column_config.NumberColumn('Outer provided — editable (mm²/m)', format='%.2f', min_value=0.0),
    }
    for face, count in [('Inner', 2), ('Outer', 3)]:
        for number in range(1, count + 1):
            flexural_columns[f'{face} Bar-{number} (mm)'] = st.column_config.NumberColumn(
                f'{face} Bar-{number} (mm)', format='%.0f')
            flexural_columns[f'{face} Spacing-{number} (mm)'] = st.column_config.NumberColumn(
                f'{face} Spacing-{number} (mm)', format='%.0f')
    flexural_inputs = st.data_editor(
        flexural_defaults,
        key=f"flexural_rf_editor_{st.session_state['flexural_rf_editor_version']}_" + hashlib.sha256(repr(flexural_defaults).encode()).hexdigest()[:12],
        hide_index=True, use_container_width=True, num_rows='fixed', height=600,
        disabled=list(flexural_columns),
        column_config=flexural_columns,
    )
    if hasattr(flexural_inputs, 'to_dict'):
        flexural_inputs = flexural_inputs.to_dict('records')
    try:
        if _trial_steel_ok:
            flexural_inputs = flexural_defaults
        flexural_result = calculate_flexural_reinforcement(flexural_inputs)
        _uls_linked_inputs['Flexural R/F'] = flexural_inputs if _trial_steel_ok else None
        st.markdown('**Calculated flexural reinforcement provided at inner and outer faces**')
        result_cols = ['Component', 'Section', 'Position',
                       'Reference Inner (mm²/m)', 'Calculated Inner (mm²/m)',
                       'Reference Outer (mm²/m)', 'Calculated Outer (mm²/m)']
        st.dataframe(
            [{col: row[col] for col in result_cols} for row in flexural_result['rows']],
            hide_index=True, use_container_width=True, height=590,
            column_config={
                **{name: st.column_config.NumberColumn(name, format='%.2f')
                   for name in result_cols if '(mm²/m)' in name},
            },
        )
        if flexural_result['all_match_source']:
            st.write('**Reference comparison:** all 16 rows match the supplied two-decimal areas.')
        else:
            st.write('**Reference comparison:** some edited bar areas differ from the source schedule.')
        st.caption('Blank bar entries mean no additional bar; a shown 0 mm bar contributes zero area. '
                   'The source also displays an “MS Railing Width” note without reinforcement values; '
                   'it is not included as a steel schedule row. '
                   'Calculated areas feed the linked ULS Flexural Strength Check; '
                   'matching the source alone does not verify required steel, anchorage, '
                   'code compliance or the separate ULS design, BBS and detailing drawings.')
    except (ValueError, TypeError) as exc:
        _uls_linked_inputs['Flexural R/F'] = None
        _uls_linked_errors['Flexural R/F'] = str(exc)
        _report_notice(st.error, f'Cannot calculate Flexural R/F: {exc}')


with design_constants_calc_tab:
    st.subheader('Design Constants')
    st.caption('Reference values from the supplied RCC Box design note. '
               'The material constants are separate from the Stage 1 source-modulus '
               'fields because their source units have not been reconciled.')
    design_defaults = default_design_constants(inputs)
    dc_material, dc_cover, dc_reference = st.columns([1, 1.15, 0.95])
    with dc_material:
        st.markdown('**Material properties**')
        dc_fy = st.number_input('fᵧ — yield strength (MPa)', min_value=1.0,
                                value=design_defaults['fy_mpa'], step=5.0, key='dc_fy', disabled=True)
        dc_fck = st.number_input('fck — concrete strength (MPa)', min_value=1.0,
                                 value=design_defaults['fck_mpa'], step=5.0, key='dc_fck', disabled=True)
        dc_es = st.number_input('Eₛ — steel modulus (GPa)', min_value=0.01,
                                value=design_defaults['es_gpa'], step=1.0, key='dc_es', disabled=True)
        dc_ecm = st.number_input('Ecm — concrete modulus (GPa)', min_value=0.01,
                                 value=design_defaults['ecm_gpa'], step=1.0, key='dc_ecm', disabled=True)
        st.caption('Source references: Eₛ — Cl. 6.2.2, IRC:112-2020; '
                   'Ecm — Table 6.5, IRC:112-2020.')
    with dc_cover:
        st.markdown('**Clear Cover**')
        dc_top_bottom = st.number_input('Top Slab bottom cover (mm)', min_value=0.0,
                                        value=design_defaults['top_slab_bottom_cover_mm'],
                                        step=5.0, key='dc_top_bottom', disabled=True)
        dc_top_top = st.number_input('Top Slab top cover (mm)', min_value=0.0,
                                     value=design_defaults['top_slab_top_cover_mm'],
                                     step=5.0, key='dc_top_top', disabled=True)
        dc_bottom = st.number_input('Bottom Slab cover (mm)', min_value=0.0,
                                    value=design_defaults['bottom_slab_cover_mm'],
                                    step=5.0, key='dc_bottom', disabled=True)
        dc_wall_outer = st.number_input('Side Wall Outer Face cover (mm)', min_value=0.0,
                                        value=design_defaults['side_wall_outer_cover_mm'],
                                        step=5.0, key='dc_wall_outer', disabled=True)
        dc_wall_inner = st.number_input('Side Wall Inner Face cover (mm)', min_value=0.0,
                                        value=design_defaults['side_wall_inner_cover_mm'],
                                        step=5.0, key='dc_wall_inner', disabled=True)
    with dc_reference:
        st.markdown('**Source reference constants**')
        dc_m = st.number_input('m — source modular ratio', min_value=0.01,
                               value=design_defaults['modular_ratio_reference'],
                               step=0.01, format='%.2f', key='dc_m', disabled=True)
        dc_fctm = st.number_input('fctm — mean tensile strength (MPa)', min_value=0.01,
                                  value=design_defaults['fctm_mpa'], step=0.1,
                                  key='dc_fctm', disabled=True)
        st.caption('fctm = 2.8 MPa is shown in the source for M35.')
        dc_extra = st.number_input('Extra depth at haunch start (m) — source',
                                   min_value=0.0,
                                   value=design_defaults['haunch_start_extra_depth_m'],
                                   step=0.005, format='%.3f', key='dc_extra', disabled=True)
    try:
        design_values = {
            'fy_mpa': dc_fy, 'fck_mpa': dc_fck,
            'es_gpa': dc_es, 'ecm_gpa': dc_ecm,
            'modular_ratio_reference': dc_m, 'fctm_mpa': dc_fctm,
            'top_slab_bottom_cover_mm': dc_top_bottom,
            'top_slab_top_cover_mm': dc_top_top,
            'bottom_slab_cover_mm': dc_bottom,
            'side_wall_outer_cover_mm': dc_wall_outer,
            'side_wall_inner_cover_mm': dc_wall_inner,
            'haunch_start_extra_depth_m': dc_extra,
        }
        design_values = default_design_constants(inputs)
        dc_result = calculate_design_constants(inputs, design_values)
        _uls_linked_inputs['Design Constants'] = design_values
        st.write(f"**m (source):** {dc_result['values']['modular_ratio_reference']:.2f}  |  "
                 f"**Eₛ / Ecm from displayed values:** "
                 f"{dc_result['modular_ratio_from_displayed_moduli']:.2f}")
        st.markdown('**Section depth and effective depth**')
        st.dataframe(
            dc_result['rows'], hide_index=True, use_container_width=True,
            column_config={
                col: st.column_config.NumberColumn(col, format='%.3f')
                for col in dc_result['rows'][0] if col != 'Section'
            },
        )
        st.caption('Effective depths use the design-note reference offsets: '
                   'clear cover plus an assumed half-bar diameter (10 or 12 mm bars). '
                   'For Side Wall (Non-Earth face), the centre reference uses the '
                   'outer-face cover plus a 5 mm bar radius. The screenshot shows '
                   '50 mm additional depth at the haunch start; this differs from '
                   'the geometric 150 mm haunch and remains an editable reference assumption.')
        if abs(dc_result['values']['modular_ratio_reference'] -
               dc_result['modular_ratio_from_displayed_moduli']) > 0.005:
            st.caption('Source discrepancy: m = 6.19 is printed in the reference, '
                       'but 200 / 32 = 6.25 using the displayed moduli. '
                       'Both are retained without silently altering either value.')
        st.caption('These values feed the linked ULS Flexural Strength Check. '
                   'They do not change the separate ULS/SLS design modules, '
                   'BBS, structural solver demands, or reinforcement detailing.')
    except (ValueError, TypeError) as exc:
        _uls_linked_inputs['Design Constants'] = None
        _uls_linked_errors['Design Constants'] = str(exc)
        _report_notice(st.error, f'Cannot calculate Design Constants: {exc}')



# Linked ULS flexural reference check, placed after Design Constants and
# before Distribution Reinforcement. No solver/ULS design state is modified.
uls_moving_envelope = None  # reused by ULS shear in this same Streamlit rerun
uls_flexural_result = None  # only same-run computed ULS values may feed distribution
with uls_flexural_strength_calc_tab:
    st.subheader('ULS Flexural Strength Check')
    st.markdown('**Check for strength (ULS : Basic Combination) (Max. Bending Moment)**')
    st.latex(r'\frac{x_{u,\max}}{d}=\frac{0.0035}{0.0035+0.87 f_y/E_s}')
    st.latex(r'R_{\lim}=0.36 f_{ck}\left(\frac{x_{u,\max}}{d}\right)\left[1-0.416\left(\frac{x_{u,\max}}{d}\right)\right]')
    st.latex(r'd_{\min}=1000\sqrt{\frac{|M_u|}{100 R_{\lim}}}\quad(\mathrm{mm},\ M_u\mathrm{\ in\ t\cdot m/m})')
    try:
        required_sources = ('Design Constants', 'Flexural R/F', 'Design Moment')
        unavailable_sources = [name for name in required_sources if _uls_linked_inputs[name] is None]
        if unavailable_sources:
            _report_notice(st.error, 'Current linked inputs unavailable: ' + ', '.join(unavailable_sources))
        else:
            uls_flexural_result = calculate_linked_uls_flexural_strength(
                inputs, constants=_uls_linked_inputs['Design Constants'],
                reinforcement_rows=_uls_linked_inputs['Flexural R/F'],
                live_demands=linked_demands(_uls_linked_inputs['Design Moment'], 'ULS Factor load'),
            )
            st.caption('Uses the current four-vehicle Design Moment totals and the workbook’s rounded 10 kN/t flexural convention. Trial capacity checks require engineer review.')
            display_columns = [
                'Component', 'Section', 'Face', 'Xu max/d', 'Xu max (m)',
                'R lim (MPa)', 'd min (mm)', 'd provided (mm)',
                'Depth Check', 'Design Moment (t-m)', 'Ast Reqd. (mm²/m)',
                'Ast Lim (mm²/m)', 'Ast min (mm²/m)',
                'Ast provided (mm²/m)', 'Steel Check',
            ]
            number_formats = {
                'Xu max/d': '%.3f', 'Xu max (m)': '%.3f',
                'R lim (MPa)': '%.3f', 'd min (mm)': '%.2f',
                'd provided (mm)': '%.0f', 'Design Moment (t-m)': '%.3f',
                'Ast Reqd. (mm²/m)': '%.2f', 'Ast Lim (mm²/m)': '%.3f',
                'Ast min (mm²/m)': '%.3f', 'Ast provided (mm²/m)': '%.2f',
            }
            st.dataframe(
                [{column: row[column] for column in display_columns}
                 for row in uls_flexural_result['rows']],
                hide_index=True, use_container_width=True, height=630,
                column_config={
                    **{column: st.column_config.NumberColumn(column, format=fmt)
                       for column, fmt in number_formats.items()},
                    'Depth Check': st.column_config.TextColumn('Check (depth)'),
                    'Steel Check': st.column_config.TextColumn('Check (steel)'),
                    'Section': st.column_config.TextColumn('Section', width='medium'),
                    'Face': st.column_config.TextColumn('Face', width='small'),
                },
            )
            st.write(f"**Depth:** {uls_flexural_result['depth_ok_count']}/"
                     f"{uls_flexural_result['row_count']} OK  |  "
                     f"**Reinforcement:** {uls_flexural_result['steel_ok_count']}/"
                     f"{uls_flexural_result['row_count']} OK")
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        _report_notice(st.error, f'Cannot calculate ULS Flexural Strength Check: {exc}')



def _automatic_distribution_rows(uls_rows):
    """Derive the existing 10 transverse-steel rows from the 16 ULS flexural demands.

    Index mappings reproduce the source design's top/bottom edge/mid and
    wall earth/inner face choices, including the maximum of all five
    checks on each wall face (Design!E101/E102). They are fixed section IDENTITIES, not
    fixed engineering loads or required steel.
    """
    if not isinstance(uls_rows, (list, tuple)) or len(uls_rows) != 16:
        raise ValueError('Automatic distribution requires all 16 ULS flexural rows')
    # Top edges/midpoints, bottom edges/midpoints, wall earth/inner.
    source_indices = ((0,), (2,), (0,), (2,), (4,), (5,), (4,), (5,),
                      (6, 8, 10, 12, 14), (7, 9, 11, 13, 15))
    baseline = default_distribution_reinforcement_rows()
    if len(baseline) != len(source_indices):
        raise ValueError('Distribution table layout changed; expected 10 rows')
    rows = []
    for row_number, (template, uls_index) in enumerate(zip(baseline, source_indices), 1):
        demands = [uls_rows[index].get('Ast Reqd. (mm²/m)') for index in uls_index]
        if any(d is None or not isinstance(d, (float, int)) or isinstance(d, bool)
               or not math.isfinite(d) or d < 0 for d in demands):
            raise ValueError(
                f'Distribution row {row_number}: linked ULS required steel is unavailable; '
                'a reference value must not be substituted'
            )
        required = 0.20 * max(demands)
        diameter = float(template['Diameter (mm)'])
        spacing = float(template['Spacing (mm)'])
        if diameter <= 0 or spacing <= 0:
            raise ValueError('Reference detailing template is invalid')
        bar_area = math.pi * diameter**2 / 4.0
        if required > bar_area * 1000 / spacing + 1e-9:
            # Preserve the workbook's nominal bar diameter and maximum
            # reference spacing, but reduce spacing on 5-mm increments when
            # required steel increases. 50 mm is only a prototype stop
            # condition; full clear-spacing/detailing limits need review.
            spacing = 5.0 * math.floor((bar_area * 1000 / required + 1e-9) / 5.0)
            if spacing < 50.0:
                raise ValueError(
                    f'Distribution row {row_number}: 8-mm bar spacing would be below 50 mm; '
                    'automatic detailing needs an approved alternative diameter/arrangement'
                )
        rows.append({
            **template,
            'Required As (mm²/m)': required,
            'Diameter (mm)': diameter,
            'Spacing (mm)': spacing,
        })
    return rows

with distribution_reinforcement_calc_tab:
    st.subheader('Distribution Reinforcement')
    st.markdown('**Transverse steel**')
    st.caption('Automatic from calculated ULS flexural demand: 20% of required main steel. '
               'Reference: IRC:112-2020, Clause 16.6.1.1(3), subject to engineer verification.')
    st.latex(r'A_{s,\,provided}=\frac{\pi\,\phi^2}{4}\times\frac{1000}{s}\quad (\mathrm{mm}^2/\mathrm{m})')

    try:
        if uls_flexural_result is None:
            raise ValueError('Live ULS flexural demands are unavailable; distribution demand cannot be computed')
        distribution_inputs = _automatic_distribution_rows(uls_flexural_result['rows'])
        st.dataframe(
            distribution_inputs, hide_index=True, use_container_width=True, height=420,
            column_config={
                'Member': st.column_config.TextColumn('S.No. / Member', width='medium'),
                'Surface': st.column_config.TextColumn('Surface', width='small'),
                'Position': st.column_config.TextColumn('Location', width='small'),
                'Required As (mm²/m)': st.column_config.NumberColumn('Aₛₜ Reqd. (mm²/m)', format='%.3f'),
                'Diameter (mm)': st.column_config.NumberColumn('Dia (mm)', format='%.1f'),
                'Spacing (mm)': st.column_config.NumberColumn('Spacing (mm)', format='%.1f'),
            },
        )
        distribution_result = calculate_distribution_reinforcement(distribution_inputs)
        st.markdown('**Calculated Distribution Reinforcement — Steel Provided and Check**')
        st.dataframe(
            distribution_result['rows'],
            hide_index=True, use_container_width=True, height=430,
            column_config={
                'Member': st.column_config.TextColumn('S.No. / Member', width='medium'),
                'Surface': st.column_config.TextColumn('Surface', width='small'),
                'Position': st.column_config.TextColumn('Location', width='small'),
                'Required As (mm²/m)': st.column_config.NumberColumn('Aₛₜ Reqd. (mm²/m)', format='%.3f'),
                'Diameter (mm)': st.column_config.NumberColumn('Dia (mm)', format='%.1f'),
                'Spacing (mm)': st.column_config.NumberColumn('Spacing (mm)', format='%.1f'),
                'Provided As (mm²/m)': st.column_config.NumberColumn('Reinf. Provided (mm²/m)', format='%.3f'),
                'Check': st.column_config.TextColumn('Check', width='small'),
            },
        )
        passed = distribution_result['pass_count']
        st.write(f'**Checks:** {passed} OK / {distribution_result["row_count"]} rows')
        st.caption('Required transverse steel is automatically 20% of the linked 16-row ULS '
                   'required main steel at the corresponding sections. Reference 8-mm bars and '
                   'nominal spacings are retained, with spacing reduced on 5-mm increments when '
                   'necessary. This does not replace structural verification of ULS demands, '
                   'bar development, spacing limits or full IRC detailing.')
    except (ValueError, TypeError) as exc:
        _report_notice(st.error, f'Cannot calculate Distribution Reinforcement: {exc}')


# Reference-equivalent ULS shear-strength check, immediately after
# Distribution Reinforcement in the Calculations tab order.
with uls_shear_strength_calc_tab:
    st.subheader('ULS Shear Strength Check')
    st.markdown('**Check for strength (ULS : Basic Combination) (Max. Shear force)**')
    st.markdown('**Shear resistance without shear reinforcement**')
    st.caption('Reference method: IRC:112-2020, Cl. 10.3.2 and Cl. 305.16.2(ii) Note. '
               'The original sheet notes that the top slab was designed on an effective-width basis. '
               'The strength expressions and section assumptions still require engineering review.')
    try:
        required = ('Design Constants', 'Flexural R/F', 'Shear Force')
        missing = [name for name in required if _uls_linked_inputs.get(name) is None]
        if missing:
            _report_notice(st.error, 'The shear check needs valid inputs from: '+', '.join(missing))
        else:
            shear_strength = calculate_linked_uls_shear_strength(
                inputs, live_shear_rows=_uls_linked_inputs['Shear Force'],
                reinforcement_rows=_uls_linked_inputs['Flexural R/F'],
                constants=_uls_linked_inputs['Design Constants'],
            )
            if len(shear_strength['rows']) != 16 or len(shear_strength['columns']) != 12:
                raise ValueError('The original shear table requires 16 rows and 12 columns')
            st.caption('Uses the current Design Shear totals and linked steel; reference effective-depth assumptions require review.')
            numeric_columns = {
                'Reinforcement (mm²/m)': '%.2f',
                'Effective Depth Provided (mm)': '%.0f',
                'ρl = As/(bw d)': '%.5f',
                'k': '%.3f',
                'Vmin (t/m)': '%.3f',
                'VRdc (t/m)': '%.3f',
                'VRd w/o shear reinf. (t/m)': '%.3f',
                'Design Shear (t/m)': '%.3f',
            }
            st.dataframe(
                [{column: row[column] for column in shear_strength['columns']}
                 for row in shear_strength['rows']],
                hide_index=True, use_container_width=True, height=675,
                column_config={
                    **{col: st.column_config.NumberColumn(col, format=fmt)
                       for col, fmt in numeric_columns.items()},
                    'Component': st.column_config.TextColumn('Component', width='small'),
                    'Section': st.column_config.TextColumn('Section', width='medium'),
                    'Face': st.column_config.TextColumn('Face', width='medium'),
                    'Shear Reinf. Requirement': st.column_config.TextColumn(
                        'Shear Reinf. Requirement', width='medium'),
                },
            )

            # Shear reinforcement design immediately follows the preceding
            # "without stirrups" shear-strength schedule. No static Excel
            # stirrup results are presented as if they were live design results.
            dc_shear=calculate_design_constants(inputs,_uls_linked_inputs['Design Constants'])
            stirrup_defaults=trial_stirrups(shear_strength,dc_shear['values']['fck_mpa'],dc_shear['values']['fy_mpa'])
            if not stirrup_defaults:
                st.info('No shear-critical sections in the current 16-row schedule. '
                        'No stirrups are automatically designed by this table.')
            else:
                dc_shear=calculate_design_constants(inputs,_uls_linked_inputs['Design Constants'])
                stirrup_table=calculate_uls_stirrup_design_table(
                    shear_strength,stirrup_inputs=stirrup_defaults,
                    fck_mpa=dc_shear['values']['fck_mpa'],
                    fy_mpa=dc_shear['values']['fy_mpa'],
                )
                if stirrup_table['fail_count']:
                    _report_notice(st.error, f"{stirrup_table['fail_count']} trial stirrup layout(s) fail capacity, minimum steel or spacing checks; engineer redesign required.")
                st.markdown('**Check for strength (ULS: Basic Combination) — Shear with reinforcement**')
                format_cols={
                    'Design Shear (t/m)':'%.3f','Φ (mm)':'%.0f','Spacing (mm)':'%.1f',
                    'Asw (mm²)':'%.3f','σcp':'%.0f','αcw':'%.0f',
                    'ν1':'%.4f','θ (degree)':'%.1f','VRd,max (t/m)':'%.3f',
                    'VRd,min (t/m)':'%.3f','VRd,s (t/m)':'%.3f',
                    'ρw = Asw/(s bw sinα)':'%.6f','ρw,min':'%.6f',
                    's max (mm)':'%.1f',
                }
                st.dataframe(
                    [{field:r[field] for field in stirrup_table['columns']}
                     for r in stirrup_table['rows']],
                    hide_index=True,use_container_width=True,
                    height=min(440,90+len(stirrup_table['rows'])*38),
                    column_config={
                        **{col:st.column_config.NumberColumn(col,format=fmt)
                           for col,fmt in format_cols.items()},
                        'Component':st.column_config.TextColumn('Component',width='small'),
                        'Section':st.column_config.TextColumn('Section',width='medium'),
                    },
                )

    except (ValueError, TypeError, KeyError, IndexError) as exc:
        _report_notice(st.error, f'Cannot calculate ULS Shear Strength Check: {exc}')


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
        st.markdown("**2. Combined member action peaks (within modeled elements)**")
        st.dataframe(frame_results["member_action_summary"], hide_index=True, use_container_width=True)
        st.caption("Maximum absolute section moment and shear are recovered through each finite element, "
                   "including internal wheel loads; the reported station is measured along the member in metres. "
                   "These are for the selected factored load case, not verified ULS/SLS moving-load envelopes. "
                   "No structural adequacy pass/fail is established.")
        st.caption(f"Global moment equilibrium residual: "
                   f"{frame_results['equilibrium_residual_moment_knm_per_m']:.2e} kN·m per 1 m strip")
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

    if solve_reference_case is not None:
        st.divider()
        st.subheader("Workbook-matched static ULS benchmark — Python (cases 1001–1006)")
        st.caption("Recomputes the exact 41-node, 41-member reference mesh, KFY spring supports, "
                   "source member loads 501–507 (including SIDL and wearing course), and "
                   "left/right surcharge case combinations. No STAAD software is needed. "
                   "The values below are for the ORIGINAL workbook geometry and loads only: "
                   "they do not follow edits to the Stage 1/3 inputs. Vehicle-generated "
                   "traffic envelopes and approved IRC design remain outside this benchmark.")
        if not is_original_geometry(inputs):
            st.warning("Current geometry differs from the original workbook. The benchmark "
                       "below still uses the original 3×3 reference model, not your modified inputs.")
        workbook_case = st.selectbox(
            "Original workbook static ULS combination",
            ["1001", "1002", "1003", "1004", "1005", "1006"],
            index=3, key="reference_uls_case",
        )
        try:
            reference_solved = solve_reference_case(workbook_case)
            st.write("**Independent recomputed member-end forces (tf and tf·m)**")
            st.dataframe(reference_solved['member_end_actions'], hide_index=True,
                         use_container_width=True, height=400)
            st.caption("Global balance residuals (tf / tf·m): "
                       f"Fx = {reference_solved['balance_fx_tf']:.2e}; "
                       f"Fy = {reference_solved['balance_fy_tf']:.2e}; "
                       f"Mz = {reference_solved['balance_moment_tfm']:.2e}. "
                       "Equilibrium is necessary, not sufficient, for structural validity.")
            benchmark_row = next((row for row in reference_solved['member_end_actions']
                                  if row['member'] == 101 and row['end'] == 'start'), None)
            if benchmark_row and workbook_case == "1004":
                st.caption(f"Member 101 at node 1, 1004: Python Mz = "
                           f"{benchmark_row['Mz_tfm']:.4f} tf·m; "
                           "workbook Output(ULS) = −6.310 tf·m (rounded).")
        except (ValueError, OSError, KeyError) as exc:
            st.error(f"Cannot run original-workbook static benchmark: {exc}")

    else:
        st.info("The optional workbook static benchmark is not installed. Add `src/reference_fem.py` and `data/reference_staad_model.json` to enable it.")

# Separate Stage 5 ULS design-checks tab removed from the interface.
# Core src.uls_design calculations remain available to the project.

with sls_calc_tab:
    pass  # removed Streamlit reminder banner
    sls_source_mode = "Live linked Design Moment"
    source_name = "Current Rare and Quasi Design Moment totals and linked trial reinforcement"
    try:
        if _uls_linked_inputs['Design Moment'] is None or uls_flexural_result is None:
            raise ValueError('Live design moments or linked reinforcement unavailable')
        demand_overrides = service_overrides(_uls_linked_inputs['Design Moment'], uls_flexural_result['rows'],
            _uls_linked_inputs['Flexural R/F'], _uls_linked_inputs['Design Constants'])
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        _report_notice(st.error, 'Cannot calculate linked SLS: ' + str(exc))
        st.stop()
    # Preserve the existing SLS default assumptions without displaying controls.
    sls_es = 200.0
    sls_ecm = 32.30824972296583
    sls_kt = 0.5
    sls_k1 = 0.8
    sls_k2 = 0.5
    use_crack_limit = False  # No engineer-approved limit supplied.
    sls_crack_limit = None

    share_with_uls = True

    sls_options = {
        "es_gpa": sls_es, "ecm_gpa": sls_ecm,
        "k_t": sls_kt, "k_1": sls_k1, "k_2": sls_k2,
        "permissible_crack_width_mm": sls_crack_limit if use_crack_limit else None,
    }
    try:
        expected_ids = {f'S{i:02d}' for i in range(1, 17)}
        if set(demand_overrides) != expected_ids or any(len(v) != 7 for v in demand_overrides.values()):
            raise ValueError('Incomplete live SLS inputs; reference fallback blocked')
        st.caption(f"Live SLS — current side wall: {inputs.wall_thickness_m * 1000:.1f} mm. Both tables use the same current calculation.")
        sls = calculate_sls_design(inputs, demand_overrides, demand_source=source_name, **sls_options)
        changes = 0
        sls["input_mode"] = sls_source_mode
        sls["editable_values_changed"] = changes
        result["sls_design"] = sls
        result["limitations"] = [v for v in result["limitations"] if not v.startswith("Stage 6 SLS demand source:")]
        result["limitations"].append(f"Stage 6 SLS demand source: {source_name}. No certified verification of SLS loads or crack limit.")
    except (ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
        _report_notice(st.error, f"Invalid SLS input: {exc}")
        st.stop()

    rare_view, crack_view = st.tabs(["Rare-combination stresses", "Quasi-permanent crack widths"])
    with rare_view:
        st.markdown('### SLS Rare Combination — Concrete and Steel Stress Check')
        st.markdown(f"**Modular ratio, m = {sls['materials']['es_gpa'] / sls['materials']['ecm_gpa']:.2f}**")
        rare_rows = _sls_rare_stress_schedule(sls)
        st.dataframe(
            rare_rows, hide_index=True, use_container_width=True, height=665,
            column_config={
                "A_st provided (mm²/m)": st.column_config.NumberColumn("A_st provided (mm²/m)", format="%.2f"),
                "x_u (mm)": st.column_config.NumberColumn("x_u (mm)", format="%.2f"),
                "I_cr (m⁴)": st.column_config.NumberColumn("I_cr (m⁴)", format="%.5f"),
                "Moment (t·m/m)": st.column_config.NumberColumn("Moment (t·m/m)", format="%.3f"),
                "Concrete stress (t/m²)": st.column_config.NumberColumn("Concrete stress (t/m²)", format="%.1f"),
                "Concrete limit (t/m²)": st.column_config.NumberColumn("Concrete permissible (t/m²)", format="%.1f"),
                "Steel stress (t/m²)": st.column_config.NumberColumn("Tensile stress (t/m²)", format="%.2f"),
                "Steel limit (t/m²)": st.column_config.NumberColumn("Tensile permissible (t/m²)", format="%.1f"),
            },
        )
    with crack_view:
        st.markdown('### SLS Quasi-Permanent Combination — Crack Width Check')
        with st.expander('Crack-width equations (IRC:112 worksheet reference)', expanded=False):
            st.latex(r'w_k = s_{r,\max}(\varepsilon_{sm}-\varepsilon_{cm})')
            st.latex(r'\rho_{p,\mathrm{eff}} = \frac{A_s}{A_{c,\mathrm{eff}}}')
            st.latex(r'A_{c,\mathrm{eff}}=b \min\left[2.5(h-d),\frac{h-x}{3},\frac{h}{2}\right]')
            st.latex(r's_{r,\max}=3.4c+\frac{0.425k_1k_2\phi_{eq}}{\rho_{p,\mathrm{eff}}}')
            st.caption('Using the selected concrete/steel, cracked section, cover and bond assumptions.')
        st.caption('Live design: uses current section depths, face-specific cover, reinforcement and Quasi moments. The original design note uses 50 mm crack-calculation cover for every row and the original 250 mm wall model; see the separate reference comparison below.')
        quasi_rows = _sls_quasi_crack_schedule(sls)
        st.dataframe(
            quasi_rows, hide_index=True, use_container_width=True, height=665,
            column_config={
                'A_st (mm²/m)': st.column_config.NumberColumn('A_st (mm²/m)', format='%.2f'),
                'A_c,eff (mm²/m)': st.column_config.NumberColumn('A_c,eff (mm²/m)', format='%.0f'),
                'ρ_p,eff': st.column_config.NumberColumn('ρ_p,eff', format='%.5f'),
                'Φ_eq (mm)': st.column_config.NumberColumn('Φ_eq (mm)', format='%.2f'),
                'Clear cover (mm)': st.column_config.NumberColumn('Clear cover (mm)', format='%.1f'),
                '5(c + Φ/2) (mm)': st.column_config.NumberColumn('5(c + Φ/2) (mm)', format='%.2f'),
                'S_r,max (mm)': st.column_config.NumberColumn('S_r,max (mm)', format='%.2f'),
                'Quasi moment (tf·m/m)': st.column_config.NumberColumn('Quasi moment (tf·m/m)', format='%.5f'),
                'σ_sc (MPa)': st.column_config.NumberColumn('σ_sc (MPa)', format='%.1f'),
                'ε_sm − ε_cm': st.column_config.NumberColumn('ε_sm − ε_cm', format='%.6f'),
                'Crack width (mm)': st.column_config.NumberColumn('Crack width (mm)', format='%.3f'),
            },
        )
        if use_crack_limit:
            status_rows = [{
                'Component': r['Component'], 'Section': r['Section'],
                'Face / location': r['Face / location'],
                'Crack width (mm)': r['Crack width (mm)'],
                'Permissible width (mm)': sls['permissible_crack_width_mm'],
                'Check': sls['sections'][i]['crack_status'],
            } for i, r in enumerate(quasi_rows)]
            st.markdown('**Crack-width acceptance check**')
            st.dataframe(status_rows, hide_index=True, use_container_width=True)

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


# The PDF is regenerated from this rerun, not from saved/reference PDF bytes.
with tab_download:
    st.subheader("Download")
    st.caption("Design Note PDF — generated from the current Design Inputs and calculation tables.")
    try:
        from src.design_note_report import build_design_note_pdf
        design_note_pdf = build_design_note_pdf(globals())
        st.download_button("Download Design Note (PDF)", data=design_note_pdf,
                           file_name="Design_Note_RCC_Box_Culvert.pdf", mime="application/pdf",
                           key="live_calculation_design_note_pdf", on_click="ignore")
    except Exception as exc:
        st.error(f"Design Note PDF unavailable: {exc}")
