"""Export the combined Stage 1–3 *preliminary* calculation snapshot."""

import csv
import io
import json


def to_json_bytes(result: dict) -> bytes:
    """Include all calculation sections and their explicit limitations."""
    return json.dumps(result, indent=2, ensure_ascii=False).encode("utf-8")


def to_csv_bytes(result: dict) -> bytes:
    """Export scalar summaries; expanded earth profile and load cases are rows."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Section", "Parameter", "Value", "Unit / note"])
    for section in ("inputs", "geometry", "preliminary_self_weight"):
        for key, value in result[section].items():
            writer.writerow([section, key, value, ""])

    if "earth_pressure" in result:
        earth = result["earth_pressure"]
        for name, value in earth.get("parameters", {}).items():
            writer.writerow(["earth_pressure_parameters", name, value, ""])
        writer.writerow(["earth_pressure", "depth_datum", earth["depth_datum"], ""])
        for key, val in earth["pressure_at_bottom_slab_centreline"].items():
            writer.writerow(["earth_pressure_at_bottom", key, val, "See key for units"])
        for row in earth.get("pressure_profile", []):
            writer.writerow(["earth_pressure_profile", row["depth_m"], row["earth_pressure_t_m2"], "depth (m); soil pressure (tf/m²)"])
        writer.writerow(["earth_pressure", "status", earth["status"], ""])

    if "braking_forces" in result:
        braking = result["braking_forces"]
        for name, value in braking.get("parameters", {}).items():
            writer.writerow(["braking_parameters", name, value, ""])
        for case in braking.get("cases", []):
            writer.writerow(["braking_total_tf", case["Case"], case["Total force (tf)"], "tf"])
            writer.writerow(["braking_per_m_tf_m", case["Case"], case["Per-metre force (tf/m)"], "tf/m"])
            writer.writerow(["braking_reference_difference_tf", case["Case"], case["Difference total (tf)"], "tf"])
        writer.writerow(["braking_forces", "status", braking["status"], ""])

    if 'vehicle_axle_loading' in result:
        v=result['vehicle_axle_loading']
        for k in ('effective_span_m','width_span_ratio','alpha','tyre_and_fill_spread_base_width_m',
                  'impact_factor','active_axle_count','sum_active_wheel_forces_tf',
                  'max_active_strip_load_intensity_tf_m'):
            writer.writerow(['vehicle_summary',k,v[k], 'Workbook reference only'])
        for k,x in v['parameters'].items():
            writer.writerow(['vehicle_input',k,x,''])
        for wheel in v['axles']:
            for k,x in wheel.items():
                writer.writerow(['vehicle_axle_'+str(wheel['wheel']),k,x,''])
        writer.writerow(['vehicle_axle_loading','status',v['status'],''])
    if 'vehicle_position_screening' in result:
        v=result['vehicle_position_screening']
        for row in v['samples']:
            writer.writerow(['vehicle_screening_position',row['lead_position_m'],
                             row['sum_strip_intensities_tf_m'],
                             'tf/m SCREENING, not analyzed structural action'])
    if 'load_combinations' in result:
        c=result['load_combinations']
        for key,val in c['factors'].items():
            writer.writerow(['user_factors',key,val,'Unverified'])
        for component in c['components']:
            writer.writerow(['factored_component',component['component'],
                             component['factored'],component['unit']])
        writer.writerow(['load_combinations','status',c['status'],''])
    writer.writerow(["status", "structural_design_status", result["structural_design_status"], ""])
    for i, limitation in enumerate(result.get("limitations", []), 1):
        writer.writerow(["limitations", i, limitation, ""])
    return buffer.getvalue().encode("utf-8-sig")
