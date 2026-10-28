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
from src.drawings import draw_culvert, export_schematic_dxf
from src.reports import to_csv_bytes, to_json_bytes

st.set_page_config(page_title="RCC Box Culvert | Geometry Preview", page_icon="🏗️", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
.block-container {padding-top: 1.6rem; padding-bottom: 2.5rem; max-width: 1450px;}
[data-testid="stMetric"] {background: rgba(128,128,128,.07); border: 1px solid rgba(128,128,128,.15); border-radius: 12px; padding: 14px 18px;}
[data-testid="stMetricLabel"] {font-weight: 600;}
</style>
""", unsafe_allow_html=True)

st.title("🏗️ RCC Box Culvert — Geometry Dashboard")
st.caption("Single-cell box culvert · editable geometry · four optional triangular internal haunches")
st.warning("**Prototype only:** geometry and unfactored concrete self-weight. No structural analysis, ULS/SLS verification, reinforcement design, or construction approval.")

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
    st.caption("Earth cushion and material grades are recorded but not used in this geometry/self-weight prototype.")

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

try:
    result = calculate_preview(inputs)
except ValueError as exc:
    st.error(f"Invalid input: {exc}")
    st.info("Check the values in the sidebar. Each pair of haunches must fit within the clear opening.")
    st.stop()

g = result["geometry"]
loads = result["preliminary_self_weight"]
# Compatibility with older src/loads.py versions that omit this breakdown key.
# This is the haunch portion of total self-weight, not an additional load.
haunch_self_weight_kn = loads.get(
    "haunch_self_weight_kn",
    g["haunch_volume_m3"] * inputs.concrete_unit_weight_kn_m3,
)

# Prepare the plot before creating tabs so drawing can be displayed reliably.
try:
    fig = draw_culvert(inputs)
    png_buffer = io.BytesIO()
    fig.savefig(png_buffer, format="png", dpi=200, bbox_inches="tight", facecolor="white")
    png_bytes = png_buffer.getvalue()
    plt.close(fig)
except Exception as exc:
    png_bytes = None
    st.error(f"Drawing generation failed: {exc}")

st.subheader("Live design summary")
m1, m2, m3, m4 = st.columns(4)
m1.metric("External width", f"{g['external_width_m']:.3f} m")
m2.metric("External height", f"{g['external_height_m']:.3f} m")
m3.metric("Concrete volume", f"{g['concrete_volume_m3']:.3f} m³")
m4.metric("Self-weight (unfactored)", f"{loads['concrete_self_weight_kn']:,.2f} kN")

tab_drawing, tab_calcs, tab_export, tab_notes = st.tabs([
    "📐 Drawing", "🧮 Calculations", "📥 Downloads", "ℹ️ Scope & assumptions"
])

with tab_drawing:
    st.subheader("Culvert cross-section with internal haunches")
    st.caption("Schematic cross-section (not a reinforcement drawing or construction detail).")
    if png_bytes:
        st.image(png_bytes, caption="Cross-section generated from the current input dimensions", use_container_width=True)
        st.download_button("Download drawing as PNG", data=png_bytes, file_name="culvert_cross_section.png", mime="image/png", key="png_drawing")
    else:
        st.error("Unable to display the drawing. Review the terminal error and ensure Matplotlib is installed.")
    st.info("The four triangular haunches are drawn at the inside corners when the haunch option is enabled.")

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

with tab_export:
    st.subheader("Export current inputs and calculated results")
    d1, d2, d3 = st.columns(3)
    with d1:
        st.download_button("Download JSON", data=to_json_bytes(result), file_name="culvert_results.json", mime="application/json", use_container_width=True)
    with d2:
        st.download_button("Download CSV", data=to_csv_bytes(result), file_name="culvert_results.csv", mime="text/csv", use_container_width=True)
    with d3:
        if png_bytes:
            st.download_button("Download PNG", data=png_bytes, file_name="culvert_cross_section.png", mime="image/png", use_container_width=True, key="png_export")
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            dxf_path = Path(temp_dir) / "culvert_outline.dxf"
            export_schematic_dxf(inputs, dxf_path)
            dxf_bytes = dxf_path.read_bytes()
        st.download_button("Download CAD-compatible DXF schematic", data=dxf_bytes, file_name="culvert_outline.dxf", mime="application/dxf")
    except ImportError:
        st.info("DXF export needs ezdxf. Install with: py -3.14 -m pip install ezdxf")
    except Exception as exc:
        st.error(f"DXF export failed: {exc}")

with tab_notes:
    st.subheader("What this prototype includes")
    st.write("Calculates a single-cell rectangular box, four optional full-length triangular internal haunches, concrete volume, and preliminary unfactored self-weight.")
    st.write("**Assumption:** the clear width and height describe the un-haunched rectangular opening. The haunches occupy part of that opening; they do not increase the external dimensions.")
    st.write("**Not included:** wearing coat, crash barriers, footing extensions, detailed reinforcement, load dispersion, vehicle loading, earth pressure, structural solver results, design code checks, BBS or BOQ.")
    st.subheader("Engineering work still required")
    for limitation in result["limitations"]:
        st.write("•", limitation)
    st.warning("Do not use these outputs for construction without verification and approval by the responsible structural engineer.")

