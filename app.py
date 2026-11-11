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
from src.calculations import calculate_preview
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
from src.boq import calculate_boq, editable_boq_template, calculate_editable_boq, BOQ_GROUPS, boq_sketch_parameters
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




# Streamlit BOQ editors use on_change to recompute read-only quantity cells.
_BOQ_INPUT_MAP = {
    'Nos': 'nos',
    'Length (m)': 'length_m',
    'Width (m)': 'width_m',
    'Depth (m)': 'depth_m',
}


def _boq_update_group(group, widget_key):
    """Apply edits before rerender, including recalculated quantity cells."""
    changes = st.session_state.get(widget_key, {}).get('edited_rows', {})
    matched = [r for r in st.session_state['_boq_rows']
               if (r['section'], r['group']) == group]
    for row_index, fields in changes.items():
        row = matched[int(row_index)]
        for display_name, value in fields.items():
            field = _BOQ_INPUT_MAP.get(display_name)
            if field is not None:
                row[field] = value
    # A fresh widget key prevents the old read-only Quantity values from being
    # reused after this rerun by Streamlit's persistent data-editor state.
    st.session_state['_boq_editor_revision'] += 1


def _boq_editor_data(rows):
    return [
        {'Item': r['item'], 'Unit': 'Cum', 'Nos': r['nos'],
         'Length (m)': r['length_m'], 'Width (m)': r['width_m'],
         'Depth (m)': r['depth_m'], 'Quantity (m³)': round(r['quantity_m3'], 3)}
        for r in rows
    ]


with st.sidebar:
    st.header("Design inputs")
    st.caption("All dimensions are in metres unless otherwise stated.")
    st.subheader("Clear opening")
    width = st.number_input("Clear width (m)", min_value=0.01, value=3.00, step=0.05, format="%.3f")
    height = st.number_input("Clear height (m)", min_value=0.01, value=3.00, step=0.05, format="%.3f")
    length = st.number_input("Culvert length (m)", min_value=0.01, value=12.00, step=0.25, format="%.3f")
    st.subheader("Concrete thicknesses")
    wall = st.number_input("Side wall thickness (m)", min_value=0.01, value=0.25, step=0.025, format="%.3f")
    top = st.number_input("Top slab thickness (m)", min_value=0.01, value=0.25, step=0.025, format="%.3f")
    bottom = st.number_input("Bottom slab thickness (m)", min_value=0.01, value=0.275, step=0.025, format="%.3f")
    st.subheader("Internal haunches")
    use_haunches = st.checkbox("Include four corner haunches", value=True)
    haunch_width = st.number_input("Haunch horizontal size (mm)", min_value=1, value=150, step=10)
    haunch_height = st.number_input("Haunch vertical size (mm)", min_value=1, value=150, step=10)
    st.subheader("Other parameters")
    fill = st.number_input("Earth cushion (m) — reference only", min_value=0.0, value=0.30, step=0.05, format="%.3f")
    gamma = st.number_input("Concrete unit weight (kN/m³)", min_value=0.01, value=25.0, step=0.5, format="%.2f")
    concrete_grade = st.selectbox("Concrete grade for ULS / SLS equations", ["M35", "M25", "M30", "M40", "M45"], index=0)
    steel_grade = st.selectbox("Steel grade for ULS / SLS equations", ["Fe500", "Fe415", "Fe550"], index=0)
    st.caption("Grades feed ULS / SLS equations. Both checks require independently verified demands and reinforcement details.")
    st.subheader("Stage 2 — Soil and surcharge")
    k0 = st.number_input("Earth pressure coefficient K₀", min_value=0.0, max_value=1.0, value=0.50, step=0.05, format="%.3f")
    soil_density = st.number_input("Backfill density (tf/m³)", min_value=0.01, value=2.0, step=0.1, format="%.3f")
    surcharge_height = st.number_input("Live-load surcharge equivalent height (m)", min_value=0.0, value=1.20, step=0.1, format="%.3f")
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
        pass
    elif vehicle_code == 'sv385':
        pass
    elif vehicle_code == '40t':
        st.caption('Workbook: 40T BOGIE, two 20 tf loads at 1.22 m spacing; not a tracked load.')
    elif vehicle_code == 'custom':
        pass
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
    frame_k = st.number_input("Subgrade spring modulus (tf/m³)", min_value=1.0,
                              value=1000.0, step=100.0)
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
)

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
    pass
    st.stop()

if enable_frame and vehicle_code in ('70rt', 'sv385'):
    st.warning('Stage 4B solver was **NOT run** for this tracked / special vehicle. '
               'Its load mapping and braking rules require a separate validated model; '
               'we are displaying the reference calculations and drawings only.')

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

tab_drawing, tab_calcs, tab_earth, tab_traffic, tab_vehicle, tab_combo, tab_solver, tab_analysis, tab_uls, tab_sls, tab_bbs, tab_boq, tab_rebar = st.tabs([
    "📐 Drawing", "🧮 Calculations", "🌍 Earth pressure", "🚛 Braking forces",
    "🚚 Vehicle axles & dispersion", "🧾 Factor sets", "🧱 In-app frame solver", "🏗️ STAAD result comparison",
    "🛡️ ULS design checks", "🔎 SLS checks", "🪛 BBS steel schedule", "📦 BOQ quantities", "📐 Reinforcement drawings"
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
        pass
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

    with axle_tab:
        st.markdown("**Stage 3: wheel loads with impact and spreadsheet effective-width dispersion**")
        if vehicle_png_bytes:
            st.image(vehicle_png_bytes, use_container_width=True)
            st.download_button("Download vehicle-loading schematic PNG",data=vehicle_png_bytes,
                               file_name="culvert_vehicle_axle_reference.png",mime="image/png",
                               key="vehicle_diagram_download")

    with fem_tab:
        st.markdown("**Stage 4B: independent Python frame model and *member-end* result magnitudes**")
        if frame_png_bytes:
            st.image(frame_png_bytes, use_container_width=True,
                     caption="Finite-element model and amplified displacement; NOT a verified STAAD drawing")
            st.download_button("Download solved-frame PNG", frame_png_bytes,
                               file_name="culvert_frame_solver.png", mime="image/png", key="fem_draw_png")
        else:
            pass

    with gad_tab:
        st.subheader("General Arrangement Drawing — Sheet 1")
        st.caption("Rebuilt from the layout of your original Sheet 1: Section A-A, plan, key plan, notes, design data and A2 title block. "
                   "The box geometry is dynamic; wing walls, site levels, aprons and other site geometry remain reference values unless edited.")
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
            pass
        except Exception as error:
            st.error(f"GAD Sheet 1 generation failed: {error}")


    with details_tab:
        st.subheader("General Arrangement - Sheet 2: Detailed Sections")
        st.caption("Reconstructed from the second page of the original General Arrangement Drawing: "
                   "Section B-B, Section C-C, Curtain Walls Type I/II, Shear Key X, and the U-type wing wall reference.")
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
            pass
        except Exception as error:
            st.error(f"Detailed Section Sheet 2 generation failed: {error}")

with tab_calcs:
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

with tab_earth:
    st.subheader("Stage 2 — Earth pressure and surcharge")
    st.caption("Reference: Design Note Of Box Culvert 1x3x3, page 2. Pressure unit tf/m² means metric tonne-force per square metre. 1 tf/m² = 9.80665 kPa.")
    try:
        # Read the central calculation result; do not recalculate here.
        st.latex(r"p_{soil}(z)=K_0\gamma_{soil}z")
        st.latex(r"p_{surcharge}=K_0\gamma_{soil}h_{eq}")
        st.latex(r"p_{cushion}=K_0\gamma_{soil}h_{fill}")
        pass
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
    except ValueError as exc:
        st.error(f"Earth pressure inputs are invalid: {exc}")

with tab_traffic:
    st.subheader("Stage 3 — Traffic braking-force summary")
    st.caption("Design note, page 2: Class 70R(W) + A and Class 40T + A braking-force summaries. Units are metric tonne-force (tf).")
    cases = braking_results["cases"]
    st.latex(r"F_{total}=F_{vehicle}+F_{Class\ A}")
    st.latex(r"q_{braking}=F_{total}/L")
    st.dataframe(cases, use_container_width=True, hide_index=True)
    pass
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
    pass
    with st.expander("Position screening sweep — reference only",expanded=True):
        screening = result['vehicle_position_screening']
        st.write(f"**{screening['positions_checked']}** sampled leading-wheel positions at **{screening['position_step_m']:.2f} m** intervals")
        st.metric("Largest sampled sum of strip-load intensities",
                  f"{screening['peak_sampled_sum_strip_intensities_tf_m']:.3f} tf/m")
        st.caption(f"Lead wheel position for this screening indicator: {screening['peak_sampled_position_m']:.3f} m")
        st.line_chart(screening['samples'], x='lead_position_m',
                      y='sum_strip_intensities_tf_m')

with tab_combo:
    st.subheader("Stage 3 — User-defined component load factor set")
    st.dataframe(combo_results['components'],use_container_width=True,hide_index=True)
    st.caption("Dead load: kN/m along culvert length; earth/surcharge/cushion: tf/m² lateral pressure; axle: tf/m effective strip; braking: tf/m horizontal force. These are not all interchangeable loads.")
    st.write("Factor set:", combo_results['name'])

with tab_solver:
    st.subheader("Stage 4B — Modelled member forces & combined load effects")
    st.caption("Independent experimental 2D Euler-Bernoulli frame solver, for a 1 m culvert strip. "
               "Not STAAD, not externally benchmarked, and not an IRC-compliant design check.")
    if frame_results is None:
        pass
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
        pass
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

with tab_analysis:
    st.subheader("Stage 4 — Structural analysis results integration")
    st.caption("This separate tab imports engineer-mapped STAAD force results. The new Stage 4B "
               "tab runs an experimental Python frame solver using applied earth, vehicle and other loads. "
               "There is no direct STAAD connection or verified sign/load-case equivalence.")
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
        pass

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
        pass
    st.markdown("**2. Numerical comparison to rounded design-note values**")
    if structural_results['comparison']:
        st.dataframe(structural_results['comparison'], hide_index=True,
                     use_container_width=True)
        st.caption("Differences are for REVIEW only. Zero difference can occur when the same design-note "
                   "values were copied into the CSV; it is not independent validation or a safety PASS.")
    else:
        pass
    st.markdown("**3. Integration status**")
    st.write(structural_results['status'])

with tab_uls:
    st.subheader("Stage 5 — Ultimate Limit State (ULS) design inputs")
    st.caption("Flexure, distribution steel, shear capacity, and stirrup checks based on the original engineering workbook formulas.")
    source_uls = result["uls_design"]
    if not source_uls["reference_geometry_matches"]:
        st.error("The current geometry, fill, material or haunch data differ from the original reference. "
                 "Update section depths, reinforcement and every demand for the revised structure. "
                 "Do not reuse the old design actions as an approved design.")

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
        pass

    imported_uls = {}
    current_source = "Original Excel cached ULS demands (reference case only)"
    editor_suffix = "reference"
    if demand_mode == "Manual engineer-entered forces":
        current_source = "Manually entered ULS design-section demands (review required)"
        editor_suffix = "manual"
        pass
    elif demand_mode == "Engineer-mapped ULS force CSV":
        current_source = "Engineer-mapped ULS force CSV (not independently verified)"
        uploaded_uls_csv = st.file_uploader(
            "Upload completed ULS force mapping CSV", type=["csv"], key="uls_forces_csv_upload",
        )
        if uploaded_uls_csv is None:
            pass
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
        pass
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
            st.warning(note)
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
    pass
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
            pass
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
        st.warning("Only section depth-to-steel mapping is shared—not ULS bending moments or shears. "
                   "Ask your sir to confirm the same reinforcement faces are applicable to SLS.")
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
            st.warning(limitation)
    st.caption("Stress PASS means only that the translated reference equations are below their reported limits. "
               "It does not approve the design, section properties, vehicle loads or analysis model.")

# Stage 7: quantity worksheets are not structural design checks. They use
# reference cut lengths/counts and general BOQ quantities unless edited by sir.
with tab_bbs:
    st.subheader("Stage 7 — Bar Bending Schedule (BBS)")
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
            pass
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
        # Show completed BBS lines only in the read-only summary. Incomplete source
        # entries stay in the editable BBS input table and in bbs_result/CSV so they
        # cannot be silently counted or substituted with assumed bar sizes.
        visible_bbs_lines = [
            x for x in bbs_result['lines']
            if not x['status'].startswith(('Missing bar diameter', 'Missing cutting length'))
        ]
        st.dataframe([{'Bar mark':x['mark'],'Member':x['member'],'Weight (kg)':round(x['weight_kg'],3),
                       'Status':x['status']} for x in visible_bbs_lines],use_container_width=True,
                     hide_index=True,height=380)
        plot=bbs_weight_chart(bbs_result)
        st.pyplot(plot)
        plt.close(plot)
        st.download_button('Download calculated BBS (CSV)',bbs_result_csv(bbs_result),
                           file_name='bbs_calculated_trial.csv',mime='text/csv',key='bbs_result')
    except (ValueError,TypeError) as exc:
        st.error(f"Cannot calculate BBS: {exc}")

with tab_boq:
    st.subheader('Bill of Quantities (BOQ)')
    st.caption('Edit Nos, Length, Width or Depth directly in the tables. '
               'Quantity and subtotals calculate automatically in m³.')

    # Start from the current design inputs. Reset only if the sidebar geometry
    # actually changes; unrelated tabs/reruns must preserve BOQ edits.
    boq_signature = (inputs.clear_width_m, inputs.clear_height_m, inputs.length_m,
                     inputs.wall_thickness_m, inputs.top_slab_thickness_m,
                     inputs.bottom_slab_thickness_m, inputs.haunch_width_m,
                     inputs.haunch_height_m, inputs.haunch_count)
    if st.session_state.get('_boq_signature') != boq_signature:
        st.session_state['_boq_signature'] = boq_signature
        st.session_state['_boq_rows'] = editable_boq_template(inputs)
        st.session_state['_boq_editor_revision'] = st.session_state.get('_boq_editor_revision', 0) + 1

    if st.button('Reset BOQ dimensions to sidebar inputs', key='boq_reset'):
        st.session_state['_boq_rows'] = editable_boq_template(inputs)
        st.session_state['_boq_editor_revision'] += 1
        st.rerun()

    boq_inputs = st.session_state['_boq_rows']
    try:
        boq_data = calculate_editable_boq(boq_inputs)
    except ValueError as exc:
        st.error(f'Invalid BOQ input: {exc}')
        st.stop()

    group_headings = {
        ('Substructure', 'Up to 5m'): 'SUBSTRUCTURE — Up to 5m height',
        ('Substructure', 'Above 5m'): 'SUBSTRUCTURE — Above 5m height',
        ('Superstructure', 'Top slab'): 'SUPERSTRUCTURE — Top slab',
    }
    for group in BOQ_GROUPS:
        st.markdown('#### ' + group_headings[group])
        group_rows = [r for r in boq_data['rows'] if (r['section'], r['group']) == group]
        editor_key = f'boq_edit_{group[0].lower()}_{group[1].replace(" ", "_")}_{st.session_state["_boq_editor_revision"]}'
        st.data_editor(
            _boq_editor_data(group_rows),
            key=editor_key, on_change=_boq_update_group, args=(group, editor_key),
            disabled=['Item', 'Unit', 'Quantity (m³)'],
            hide_index=True, use_container_width=True, num_rows='fixed',
            column_config={
                'Item': st.column_config.TextColumn('Item', width='large'),
                'Unit': st.column_config.TextColumn('Unit', width='small'),
                'Nos': st.column_config.NumberColumn('Nos', min_value=0, step=1, format='%d'),
                'Length (m)': st.column_config.NumberColumn('Length (m)', min_value=0.0, step=0.05, format='%.3f'),
                'Width (m)': st.column_config.NumberColumn('Width (m)', min_value=0.0, step=0.05, format='%.3f'),
                'Depth (m)': st.column_config.NumberColumn('Depth (m)', min_value=0.0, step=0.025, format='%.3f'),
                'Quantity (m³)': st.column_config.NumberColumn('Quantity (m³)', format='%.3f'),
            },
        )
        st.markdown(f'**Subtotal: {boq_data["groups"][group]:,.3f} m³**')

    st.markdown('#### Total concrete')
    a,b,c = st.columns(3)
    a.metric('Substructure', f'{boq_data["substructure_m3"]:,.3f} m³')
    b.metric('Superstructure', f'{boq_data["superstructure_m3"]:,.3f} m³')
    c.metric('TOTAL RCC', f'{boq_data["total_concrete_m3"]:,.3f} m³')

    # CSV contains the CURRENT, recalculated values, not a cached reference.
    import csv
    boq_csv = io.StringIO(newline='')
    writer = csv.writer(boq_csv)
    writer.writerow(['Section', 'Group', 'Item', 'Unit', 'Nos', 'Length (m)', 'Width (m)',
                     'Depth (m)', 'Quantity (m³)'])
    for r in boq_data['rows']:
        writer.writerow([r['section'], r['group'], r['item'], 'Cum', r['nos'],
                         r['length_m'], r['width_m'], r['depth_m'], round(r['quantity_m3'], 6)])
    for group, qty in boq_data['groups'].items():
        writer.writerow([group[0], group[1], 'SUBTOTAL', 'Cum', '', '', '', '', round(qty, 6)])
    writer.writerow(['', '', 'TOTAL RCC', 'Cum', '', '', '', '', round(boq_data['total_concrete_m3'], 6)])
    st.download_button('Download BOQ CSV', boq_csv.getvalue().encode('utf-8-sig'),
                       file_name='editable_culvert_boq.csv', mime='text/csv', key='boq_editable_csv')

    with st.expander('Other dimensions from reference (retaining wall, apron, curtain wall)'):
        st.caption('You can edit these reference dimensions, but they do not have '
                   'verified quantity formulas and are not included in the totals above.')
        ref_rows = boq_sketch_parameters(inputs)['other_works_reference']
        if '_boq_site_reference_rows' not in st.session_state:
            st.session_state['_boq_site_reference_rows'] = ref_rows
        st.data_editor(st.session_state['_boq_site_reference_rows'],
                       hide_index=True, use_container_width=True, num_rows='fixed',
                       disabled=['Parameter', 'Unit'], key='boq_other_dims_editor')

    # Keep legacy BOQ result for project-wide exports and report consumers.
    # Its geometry-linked quantities follow the sidebar, not editable BOQ rows.
    try:
        result['boq'] = calculate_boq(inputs, result.get('bbs'), {})
    except (ValueError, TypeError) as exc:
        st.error(f'BOQ calculation failed: {exc}')
    st.caption('BOQ edits affect the displayed schedule and its CSV, not structural analysis or drawings. '
               'A qualified engineer must check quantities before construction use.')

with tab_rebar:
    st.subheader("Stage 8 — Reinforcement drawing review sheets")
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
