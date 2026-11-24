"""Recompute four independent moving-load envelopes and compare original design rows.

NO workbook output numbers are used to compute FEM actions. The original
reference values are compared only AFTER the predictions. The original STD model is benchmarked at its own geometry; changed inputs
remain independent predictions requiring engineering validation.
"""
from __future__ import annotations
from pathlib import Path
import math

from .solve_std_vehicle import predict_std_based_vehicle_envelopes
from .design_action_stage8 import load_rules, evaluate_worksheet_ll
from .vehicle_summary_mapper import load_summary_selectors, calculate_vehicle_summary_cells

VEHICLES = ('70rw', '40t', '70rt', 'sv385')


def calculate_four_vehicle_ll(inputs, reference_moments, reference_shears, *,
                              data_dir, elastic_modulus_gpa=32., scan_step_m=.25,
                              mesh_divisions_per_member=8, selected_vehicle_code=None, selected_vehicle_inputs=None,
                              compression_only_springs=False):
    """Return live predictions, comparisons, and a strong validation status.

    All vehicle runs use the independent FEM at current geometry/subgrade.
    Overrides affect the selected vehicle only, not the other three presets.
    """
    if len(reference_moments) != 26 or len(reference_shears) != 16:
        raise ValueError('Reference tables must contain 26 moment and 16 shear rows')
    if isinstance(mesh_divisions_per_member, bool) or not (2 <= int(mesh_divisions_per_member) <= 24):
        raise ValueError('Vehicle FEM mesh divisions must be between 2 and 24')
    if compression_only_springs:
        raise ValueError("STD vehicle solver uses the original bilateral springs; compression-only support is not validated for LL")
    folder=Path(data_dir)
    override={selected_vehicle_code:selected_vehicle_inputs} if (
        selected_vehicle_code in VEHICLES and selected_vehicle_inputs is not None
    ) else {}
    envelope=predict_std_based_vehicle_envelopes(
        inputs, elastic_modulus_gpa=float(elastic_modulus_gpa),
        mesh_divisions_per_member=int(mesh_divisions_per_member),
        step_m=float(scan_step_m), vehicle_overrides=override,
    )
    selectors=load_summary_selectors(folder/'stage8_summary_selectors.json')
    cells={vehicle:calculate_vehicle_summary_cells(envelope['envelopes'][vehicle], selectors, vehicle)
           for vehicle in VEHICLES}
    rules = load_rules(folder/'stage8_ll_rules.json')
    # Reject inconsistent configuration rather than silently use traffic in quasi.
    if any(row['rules']['Quasi LL']['operation'] != 'literal_zero'
           for row in rules['moment']):
        raise ValueError('Original quasi-permanent combination requires zero vehicle LL')
    predicted=evaluate_worksheet_ll(rules, cells)
    if any(float(row['Quasi LL']) != 0.0 for row in predicted['moment']):
        raise ValueError('Unexpected nonzero quasi-permanent vehicle LL')
    if len(predicted['moment'])!=26 or len(predicted['shear'])!=16:
        raise AssertionError('Incorrect LL section mapping')
    comparisons=[]
    for kind, ref_rows, cols in (
        ('Design Moment',reference_moments,('ULS LL','Rare LL','Quasi LL')),
        ('Shear Force',reference_shears,('LL',)),
    ):
        key='moment' if kind=='Design Moment' else 'shear'
        for index,(reference,live) in enumerate(zip(ref_rows,predicted[key]),1):
            row_id=int(live['Excel row'])
            if int(reference.get('Excel row',row_id))!=row_id:
                raise ValueError('Design-table row order does not match Excel')
            for col in cols:
                original=float(reference[col]);calculated=float(live[col])
                if not math.isfinite(calculated):raise ValueError('Nonfinite predicted LL')
                comparisons.append({
                    'Section':kind, 'Row':index, 'Component':col,
                    'Reference':round(original,4),
                    'Live FEM':round(calculated,4),
                    'Difference':round(calculated-original,4),
                    'Within 0.025':abs(calculated-original)<=0.025,
                })
    return {
        'moment':predicted['moment'], 'shear':predicted['shear'],
        'comparison':comparisons,
        'matched':sum(row['Within 0.025'] for row in comparisons),
        'tested':len(comparisons),
        'scan_info':envelope['scan_info'],
        'validation': ('Source STD reference benchmark: within tolerance' if envelope['near_reference_case'] and all(row['Within 0.025'] for row in comparisons) else 'UNVERIFIED for this design input set; engineering validation required'),
        'reference_geometry':envelope['reference_geometry'],
        'reference_case':envelope['reference_case'],
        'near_reference_case':envelope['near_reference_case'],
        'reference_loads':envelope['reference_loads'],
        'reference_modulus_gpa':envelope['reference_modulus_gpa'],
    }
