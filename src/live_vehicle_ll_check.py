"""Recompute four independent moving-load envelopes and compare original design rows.

NO workbook output numbers are used to compute FEM actions. The original
reference values are compared only AFTER the predictions. This is an audit
view, not an engineering-validated source for approved design tables.
"""
from __future__ import annotations
from pathlib import Path
import math

from .frame_solver import FrameOptions
from .design_action_stage8 import (
    predict_independent_vehicle_end_envelopes, load_rules, evaluate_worksheet_ll,
)
from .vehicle_summary_mapper import load_summary_selectors, calculate_vehicle_summary_cells

VEHICLES = ('70rw', '40t', '70rt', 'sv385')


def calculate_four_vehicle_ll(inputs, reference_moments, reference_shears, *,
                              data_dir, elastic_modulus_gpa=32., scan_step_m=.25,
                              selected_vehicle_code=None, selected_vehicle_inputs=None):
    """Return live predictions, comparisons, and a strong validation status.

    All vehicle runs use the independent FEM at current geometry/subgrade.
    Overrides affect the selected vehicle only, not the other three presets.
    """
    if len(reference_moments) != 26 or len(reference_shears) != 16:
        raise ValueError('Reference tables must contain 26 moment and 16 shear rows')
    folder=Path(data_dir)
    options=FrameOptions(
        concrete_elastic_modulus_gpa=float(elastic_modulus_gpa),
        foundation_modulus_tf_m3=float(inputs.subgrade_reaction_modulus_tf_m3),
        mesh_divisions_per_member=8, compression_only_springs=False,
        sidl_centre_member_repeat_count=2,
    )
    override={selected_vehicle_code:selected_vehicle_inputs} if (
        selected_vehicle_code in VEHICLES and selected_vehicle_inputs is not None
    ) else {}
    envelope=predict_independent_vehicle_end_envelopes(
        inputs, options=options, step_m=float(scan_step_m),
        vehicle_ids=VEHICLES, vehicle_overrides=override,
    )
    selectors=load_summary_selectors(folder/'stage8_summary_selectors.json')
    cells={vehicle:calculate_vehicle_summary_cells(envelope['envelopes'][vehicle], selectors, vehicle)
           for vehicle in VEHICLES}
    predicted=evaluate_worksheet_ll(load_rules(folder/'stage8_ll_rules.json'), cells)
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
        'validation':'UNVERIFIED: four-vehicle FEM differs from original workbook',
    }
