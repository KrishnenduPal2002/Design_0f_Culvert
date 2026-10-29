"""Streamlit UI for the RCC box culvert geometry prototype.

Place this file at the project root beside the existing ``src`` directory.
Run: py -3.14 -m streamlit run app.py
"""
from __future__ import annotations

import io
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import streamlit as st

from src.inputs import CulvertInputs
from src.calculations import calculate_preview
from src.drawings import (draw_culvert, draw_load_schematic, export_schematic_dxf,
                         draw_vehicle_loading, export_vehicle_reference_dxf)
from src.reports import to_csv_bytes, to_json_bytes
from src.earth_pressure import EarthPressureInputs
from src.traffic_loads import BrakingInputs
from src.vehicle_loading import (VehicleLoadingInputs, REFERENCE_70RW_WHEELS_TF,
                                 REFERENCE_70RW_SPACINGS_M)
from src.load_combinations import CombinationInputs
from src.structural_analysis import (
    parse_mapped_analysis_csv, example_template_csv, reference_csv,
    is_original_geometry,
)
from src.drawings import draw_structural_result_annotations, draw_frame_solver_results
from src.frame_solver import FrameOptions

st.set_page_config(page_title="RCC Box Culvert | Stage 4B Frame Solver", page_icon="🏗️", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
.block-container {padding-top: 1.6rem; padding-bottom: 2.5rem; max-width: 1450px;}
[data-testid="stMetric"] {background: rgba(128,128,128,.07); border: 1px solid rgba(128,128,128,.15); border-radius: 12px; padding: 14px 18px;}
[data-testid="stMetricLabel"] {font-weight: 600;}
</style>
""", unsafe_allow_html=True)

st.title("🏗️ RCC Box Culvert — Geometry, Loads & 2D Frame Analysis")
st.caption("Single-cell box culvert · editable geometry · four optional triangular internal haunches")
st.warning(
    "**Unverified engineering prototype — NOT for construction.** Stage 4B includes an "
    "experimental in-app 2D frame FEM solver with applied earth, surcharge, fill, "
    "concrete-weight, axle strip-load and braking loads. It calculates member-end "
    "forces and combined effects, but **is not STAAD**, does not establish approved "
    "IRC load factors, and is **not validated for structural design**. The original "
    "haunch-volume discrepancy and structural modelling assumptions remain unresolved."
)

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
    concrete_grade = st.selectbox("Concrete grade (label only)", ["M35", "M25", "M30", "M40", "M45"], index=0)
    steel_grade = st.selectbox("Steel grade (label only)", ["Fe500", "Fe415", "Fe550"], index=0)
    st.caption("Concrete and steel grades are labels only; earth cushion is used in the Stage 2 earth-pressure preview.")
    st.subheader("Stage 2 — Soil and surcharge")
    k0 = st.number_input("Earth pressure coefficient K₀", min_value=0.0, max_value=1.0, value=0.50, step=0.05, format="%.3f")
    soil_density = st.number_input("Backfill density (tf/m³)", min_value=0.01, value=2.0, step=0.1, format="%.3f")
    surcharge_height = st.number_input("Live-load surcharge equivalent height (m)", min_value=0.0, value=1.20, step=0.1, format="%.3f")
    st.subheader("Stage 3 — Braking-force reference")
    force_70rw = st.number_input("Class 70R(W) contribution (tf)", min_value=0.0, value=6.8, step=0.1)
    force_40t = st.number_input("Class 40T contribution (tf)", min_value=0.0, value=8.0, step=0.1)
    force_a = st.number_input("Class A contribution (tf)", min_value=0.0, value=1.14, step=0.01)
    lever_arm = st.number_input("Assumed moment lever arm (m) — illustration only", min_value=0.0, value=1.325, step=0.025, format="%.3f")
    st.subheader("Stage 3 — Wheel loads and impact")
    vehicle_mode = st.selectbox("Vehicle axle dataset", [
        "Class 70R(W) — source Excel Input sheet", "Custom pattern — engineer-supplied"
    ], help="Only the 70R(W) wheel pattern has been extracted from this workbook into the application.")
    default_wheels = ", ".join(f"{x:g}" for x in REFERENCE_70RW_WHEELS_TF)
    default_spacings = ", ".join(f"{x:g}" for x in REFERENCE_70RW_SPACINGS_M)
    if vehicle_mode.startswith("Custom"):
        wheel_csv = st.text_input("Wheel forces (tf), comma-separated", value=default_wheels,
                                  help="Engineering reference only. Provide values from the responsible designer.")
        spacing_csv = st.text_input("Spacing between consecutive wheels (m)", value=default_spacings)
    else:
        wheel_csv = default_wheels
        spacing_csv = default_spacings
        st.caption(f"Reference wheel forces: {default_wheels} tf; spacings: {default_spacings} m")
    lead_axle = st.number_input("Leading wheel x from left support (m)", value=0.25,
                                min_value=0., step=0.05,format="%.3f")
    tyre_width = st.number_input("Tyre transverse contact width (m)", value=0.86, min_value=0.01,step=0.01)
    wearing = st.number_input("Wearing coat for wheel spread (m)", value=0.05,min_value=0.,step=0.01)
    spread_slope = st.number_input("Contact spread per side per unit fill depth",value=1.0,min_value=0.01,step=0.1)
    available_edge = st.number_input("Workbook edge availability T12 (m)", value=2.13,min_value=0.01,step=0.01)
    available_centre = st.number_input("Workbook centre availability T13 (m)",value=1.93,min_value=0.01,step=0.01)
    override_impact = st.checkbox("Override Excel impact factor (engineer-supplied)",value=False)
    manual_impact = st.number_input("Manual impact factor",value=1.25,min_value=1.0,step=0.05,
                                    disabled=not override_impact)
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
        case_name=vehicle_mode,
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
        braking_case=frame_braking_case,
        braking_direction=1 if braking_direction.startswith("Right") else -1,
    )
    result = calculate_preview(
        inputs, plot_earth_inputs, plot_braking_inputs,
        vehicle_inputs, combo_inputs,
        structural_records=uploaded_structural_rows,
        frame_options=frame_options,
        run_frame=enable_frame,
    )
except (ValueError, TypeError) as exc:
    st.error(f"Invalid input: {exc}")
    st.info("Check your dimensions and soil/braking inputs in the sidebar.")
    st.stop()

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

tab_drawing, tab_calcs, tab_earth, tab_traffic, tab_vehicle, tab_combo, tab_solver, tab_analysis, tab_ref, tab_export, tab_notes = st.tabs([
    "📐 Drawing", "🧮 Calculations", "🌍 Earth pressure", "🚛 Braking forces",
    "🚚 Vehicle axles & dispersion", "🧾 Factor sets", "🧱 In-app frame solver", "🏗️ STAAD result comparison",
    "📋 Reference comparison", "📥 Downloads", "ℹ️ Roadmap"
])

with tab_drawing:
    st.subheader("Culvert drawings — geometry and load diagrams")
    st.caption("Two separate, automatically updated schematics. PNG downloads preserve all dimensions and labels.")
    geom_tab, load_tab, axle_tab, fem_tab = st.tabs([
        "📐 Geometry & dimensions", "🌍 Earth pressure & braking", "🚚 Vehicle axle reference", "🧱 Solved frame"
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
        st.info(
            f"Current inputs: clear opening **{width:.3f} × {height:.3f} m**, "
            f"external box **{g['external_width_m']:.3f} × {g['external_height_m']:.3f} m**, "
            + (f"four **{haunch_width} × {haunch_height} mm** triangular haunches."
               if use_haunches else "haunches disabled.")
        )
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
        st.warning(
            "The left/right arrow placement separates different *illustrations* for readability. "
            "It does not specify which side walls receive loads in a real load case. "
            "A separate Stage 4B FEM solver tab now applies selected braking/axle loads to a frame under explicit assumptions. "
            "The illustrative Stage 2–3 drawing is NOT the FEM application diagram. "
            "No verified STAAD actions or code-approved load combinations are established."
        )

    with axle_tab:
        st.markdown("**Stage 3: wheel loads with impact and spreadsheet effective-width dispersion**")
        if vehicle_png_bytes:
            st.image(vehicle_png_bytes, use_container_width=True)
            st.download_button("Download vehicle-loading schematic PNG",data=vehicle_png_bytes,
                               file_name="culvert_vehicle_axle_reference.png",mime="image/png",
                               key="vehicle_diagram_download")
        st.warning("The longitudinal axle reference is projected above a transverse box-section for clarity; "
                   "this does not specify a verified structural application direction or position.")

    with fem_tab:
        st.markdown("**Stage 4B: independent Python frame model and *member-end* result magnitudes**")
        if frame_png_bytes:
            st.image(frame_png_bytes, use_container_width=True,
                     caption="Finite-element model and amplified displacement; NOT a verified STAAD drawing")
            st.download_button("Download solved-frame PNG", frame_png_bytes,
                               file_name="culvert_frame_solver.png", mime="image/png", key="fem_draw_png")
        else:
            st.info("Enable Run in-app FEM structural model in the sidebar.")
        st.warning("Orange displacement is exaggerated. Force bars are local member-END actions, "
                   "not a moment/shear design envelope. Solver assumptions require engineer review.")

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
        st.info("Depth z is measured from the top slab upper surface as inferred from the design-note pressure table. Earth cushion pressure is shown separately, not added automatically to depth-based earth pressure. Confirm the datum and load combinations with the engineer.")
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
        st.warning("This tab does not calculate resultant wall forces, structural moments, STAAD results, ULS/SLS, or design approval. Reference wheel loading is now shown separately in the Vehicle axles tab.")
    except ValueError as exc:
        st.error(f"Earth pressure inputs are invalid: {exc}")

with tab_traffic:
    st.subheader("Stage 3 — Traffic braking-force summary")
    st.caption("Design note, page 2: Class 70R(W) + A and Class 40T + A braking-force summaries. Units are metric tonne-force (tf).")
    st.warning("These braking contributions are copied from the design note. Wheel loading and spreadsheet dispersion are calculated separately in the Vehicle axles tab; complete vehicle standard validation and structural design load combinations remain pending.")
    cases = braking_results["cases"]
    st.latex(r"F_{total}=F_{vehicle}+F_{Class\ A}")
    st.latex(r"q_{braking}=F_{total}/L")
    st.dataframe(cases, use_container_width=True, hide_index=True)
    st.info("Design note reference (12 m): 7.94 tf / 12 ≈ 0.66 tf/m and 9.14 tf / 12 ≈ 0.76 tf/m. The source lists braking moments 0.877 and 1.009 tf·m/m. The adjustable lever arm above is only an illustrative approximation and does not reproduce a verified engineering derivation.")
    st.bar_chart({row["Case"]: row["Per-metre force (tf/m)"] for row in cases})
    st.download_button("Download braking-force comparison CSV",
                       data=("Case,Total force (tf),Per-metre force (tf/m),Reference total (tf),Reference per-metre (tf/m)\n" +
                             "".join(f"{r['Case']},{r['Total force (tf)']:.6f},{r['Per-metre force (tf/m)']:.6f},{r['Note total (tf)']:.3f},{r['Note per metre (tf/m)']:.3f}\n" for r in cases)),
                       file_name="braking_force_comparison.csv", mime="text/csv")

with tab_vehicle:
    st.subheader("Stage 3 — Wheel loads, impact and effective-width dispersion")
    st.caption("Source Excel: Input!C19:D25 wheel pattern; J13 impact; N11:O30 alpha table; C15, I19 and J19 effective width and line load. Units: tf and tf/m.")
    a,b,c,d=st.columns(4)
    a.metric("Effective span", f"{vehicle_results['effective_span_m']:.3f} m")
    b.metric("Impact factor", f"{vehicle_results['impact_factor']:.3f}")
    c.metric("Alpha (effective width)", f"{vehicle_results['alpha']:.3f}")
    d.metric("Active wheels", f"{vehicle_results['active_axle_count']}")
    st.latex(r"b_1=b_{tyre}+2s(t_{wearing}+h_{fill})")
    st.latex(r"b_{eff}=\alpha a(1-a/l_0)")
    st.latex(r"q_{strip}=P_{wheel}I/b_{final}")
    st.dataframe(vehicle_results['axles'],use_container_width=True,hide_index=True)
    st.caption("Inactive wheels are outside the selected effective span. These are individual spreadsheet strip-load intensities, not verified beam/plate reactions or design effects.")
    st.info("Workbook check: leading wheel at x=0 m ≈ 6.810897 tf/m; at x=0.25 m ≈ 5.195599 tf/m for default data. Comparison with the source is meaningful only for the original 3 × 3 m, 12 m configuration.")
    with st.expander("Position screening sweep — reference only",expanded=True):
        screening = result['vehicle_position_screening']
        st.write(f"**{screening['positions_checked']}** sampled leading-wheel positions at **{screening['position_step_m']:.2f} m** intervals")
        st.metric("Largest sampled sum of strip-load intensities",
                  f"{screening['peak_sampled_sum_strip_intensities_tf_m']:.3f} tf/m")
        st.caption(f"Lead wheel position for this screening indicator: {screening['peak_sampled_position_m']:.3f} m")
        st.line_chart(screening['samples'], x='lead_position_m',
                      y='sum_strip_intensities_tf_m')
        st.warning("Summed strip intensities are a SCREENING metric, not design reaction, bending moment or shear.")
    st.warning("Class 40T, Class A, contact patch dispersions in all directions, approved vehicle-placement envelopes and IRC clause validation are still NOT independently reproduced. Use custom data only when provided by the structural engineer.")

with tab_combo:
    st.subheader("Stage 3 — User-defined component load factor set")
    st.warning("These are *not* IRC-approved ULS/SLS load combinations. The program multiplies each load component by its specified factor and **never sums unlike dimensional quantities**. Proper load combinations require a structural analysis model and engineer-confirmed code provisions.")
    st.dataframe(combo_results['components'],use_container_width=True,hide_index=True)
    st.caption("Dead load: kN/m along culvert length; earth/surcharge/cushion: tf/m² lateral pressure; axle: tf/m effective strip; braking: tf/m horizontal force. These are not all interchangeable loads.")
    st.write("Factor set:", combo_results['name'])

with tab_solver:
    st.subheader("Stage 4B — Modelled member forces & combined load effects")
    st.caption("Independent experimental 2D Euler-Bernoulli frame solver, for a 1 m culvert strip. "
               "Not STAAD, not externally benchmarked, and not an IRC-compliant design check.")
    if frame_results is None:
        st.info("Enable **Run in-app FEM structural model** in the sidebar to compute frame actions.")
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
        st.info("Loads are stored and factored in compatible kN per **1 m longitudinal strip**. "
                "For each selected factor set, the combined frame equations are solved, rather than adding raw pressures to forces.")
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
        st.warning("Small equilibrium residuals show numerical force balance, **not** that supports, "
                   "springs, wheel geometry or load factors match the engineer's approved model. "
                   "Obtain a STAAD `.std` model, independent comparisons, and signed engineering review.")

with tab_analysis:
    st.subheader("Stage 4 — Structural analysis results integration")
    st.caption("This separate tab imports engineer-mapped STAAD force results. The new Stage 4B "
               "tab runs an experimental Python frame solver using applied earth, vehicle and other loads. "
               "There is no direct STAAD connection or verified sign/load-case equivalence.")
    st.warning(
        "The design note provides ULS / SLS bending moments (page 4) and ULS shear "
        "forces (page 5). These **reported values are reference data**, not an independently "
        "verified Python analysis. Raw STAAD files must be interpreted and mapped by the engineer."
    )
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
        st.info("No STAAD output uploaded. The charts below show **only design-note reference values**.")

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
        st.info("No values for the selected case and action in this source.")
    st.markdown("**2. Numerical comparison to rounded design-note values**")
    if structural_results['comparison']:
        st.dataframe(structural_results['comparison'], hide_index=True,
                     use_container_width=True)
        st.caption("Differences are for REVIEW only. Zero difference can occur when the same design-note "
                   "values were copied into the CSV; it is not independent validation or a safety PASS.")
    else:
        st.info("Upload independent, engineer-mapped output CSV to see numerical differences.")
    st.markdown("**3. Integration status**")
    st.write(structural_results['status'])
    st.warning("This comparison tab displays imported STAAD-mapped values; Stage 4B separately provides "
               "an experimental Python 2D frame solver with assumed springs and member load application. "
               "There is NO direct STAAD connection, independent model verification, approved ULS/SLS "
               "capacity, shear, crack-width, reinforcement, BBS or BOQ.")

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
    st.info("The reference values apply only to the original design dimensions. Changing inputs does not recalculate the reference design note.")
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
        st.info("DXF export needs ezdxf. Install with: py -3.14 -m pip install ezdxf")
    except Exception as exc:
        st.error(f"DXF export failed: {exc}")

with tab_notes:
    st.subheader("Implementation status and engineering limitations")
    st.markdown("**Stage 1:** Geometry, haunch quantities and preliminary concrete self-weight.")
    st.markdown("**Stage 2:** Earth, surcharge and fill pressure magnitudes. Stage 4B now applies "
                "assumed pressure distributions to the frame members, including vertical fill over the top slab.")
    st.markdown("**Stage 3:** Workbook-reference wheel pattern, impact, dispersion, "
                "braking reference and editable component factors. Stage 4B applies these to the frame.")
    st.markdown("**Stage 4A:** Engineer-mapped STAAD CSV result comparison with reported design-note moments/shears.")
    st.markdown("**Stage 4B:** Experimental Python plane-frame FEM with beam-column stiffness, "
                "soil springs, concrete dead, lateral soil/surcharge/cushion, vertical fill, "
                "axle strip loads and braking load application, plus a combined factor-set solve. "
                "There is no direct STAAD connection, no validated IRC design combination, and no safety certification.")
    st.markdown("**Pending:** Verify frame geometry, loading datum, soil interaction, support arrangement, "
                "wheel distribution and sign conventions against an actual STAAD model; "
                "independently benchmark/mesh-refine the solver; IRC ULS/SLS combinations and "
                "capacity, cracking, shear, reinforcement, BBS and BOQ.")
    st.subheader("Engineering work still required")
    for limitation in result["limitations"]:
        st.write("•", limitation)
    st.warning("Do not use this prototype for design or construction without structural engineering validation and approval.")
