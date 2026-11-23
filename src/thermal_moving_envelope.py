"""Optional moving-load envelope with the active thermal initial strain.

EXPERIMENTAL. For the *same* current FEM model and each scanned vehicle
position, solve traffic and thermal together; do not add positive envelope
magnitudes or reuse a cached envelope made for a different thermal case.
"""
from __future__ import annotations
from dataclasses import replace
import math

from .inputs import CulvertInputs
from .frame_solver import FrameOptions, ThermalLoad, run_frame_analysis
from .earth_pressure import EarthPressureInputs
from .traffic_loads import BrakingInputs
from .vehicle_loading import VehicleLoadingInputs
from .load_combinations import CombinationInputs
from .calculations import calculate_live_frame_flexural_demands, calculate_live_frame_shear_demands


def calculate_thermal_moving_envelope(
    inputs: CulvertInputs, *, earth: EarthPressureInputs,
    braking: BrakingInputs, vehicle: VehicleLoadingInputs,
    combo: CombinationInputs, options: FrameOptions,
    thermal: ThermalLoad, step_m: float = .15, max_scenarios: int = 240,
) -> dict:
    inputs.validate(); vehicle.validate(); combo.validate(); options.validate(); thermal.validate()
    if not inputs.supports_single_cell_model:
        raise ValueError('The current model supports one cell')
    if vehicle.loading_type not in ('70rw', '40t', '70rt', 'sv385', 'custom'):
        raise ValueError('Unknown moving vehicle type')
    if not .025 <= step_m <= 1 or max_scenarios < 8:
        raise ValueError('Invalid vehicle scan step or scenario cap')
    span = inputs.clear_width_m + inputs.wall_thickness_m
    offsets = [0.0]
    for d in vehicle.axle_spacings_m:
        offsets.append(offsets[-1] + float(d))
    full_travel = span + offsets[-1]
    wall_face = min(span / 2, inputs.wall_thickness_m / 2)
    haunch = min(span/2, wall_face + (inputs.haunch_width_m if inputs.haunch_count else 0))
    stations = tuple(sorted({0., wall_face, haunch, span/2, span-haunch, span-wall_face, span}))
    if combo.vehicle_factor == 0:
        positions = [float(vehicle.lead_axle_position_m)]
    else:
        count = max(1, math.ceil(full_travel / step_m))
        anchors = {round(off + station, 8) for off in offsets for station in stations}
        anchors.update({0., round(full_travel,8), round(vehicle.lead_axle_position_m,8)})
        if len(anchors) > max_scenarios:
            raise ValueError('More critical positions than the scan cap')
        grid = {round(j*full_travel/count,8) for j in range(count+1)}
        positions = sorted(anchors | grid)
        if len(positions) > max_scenarios:
            extra = sorted(grid - anchors)
            remaining = max_scenarios - len(anchors)
            picks = ([extra[min(len(extra)-1,int((j+.5)*len(extra)/remaining))]
                      for j in range(remaining)] if remaining and extra else [])
            positions = sorted(anchors | set(picks))
    best_moments = None
    best_shears = None
    seen = set()
    def evaluate(lead):
        nonlocal best_moments, best_shears
        lead = round(float(lead),8)
        if lead in seen:
            return
        seen.add(lead)
        solved = run_frame_analysis(
            inputs, earth=earth, braking=braking,
            vehicle=replace(vehicle, lead_axle_position_m=lead),
            combo=combo, options=options, thermal=thermal)
        moments = calculate_live_frame_flexural_demands(inputs, solved)
        shears = calculate_live_frame_shear_demands(inputs, solved)
        if len(moments) != 16 or len(shears) != 16:
            raise ValueError('FEM must supply all 16 design sections')
        if best_moments is None:
            best_moments = [None] * len(moments)
            best_shears = [None] * len(shears)
        for i, r in enumerate(moments):
            if best_moments[i] is None or r['moment_tf_m_per_m'] > best_moments[i]['moment_tf_m_per_m']:
                best_moments[i] = {**r, 'governing_lead_axle_position_m':lead}
        for i, r in enumerate(shears):
            if best_shears[i] is None or r['Total'] > best_shears[i]['Total']:
                best_shears[i] = {**r, 'governing_lead_axle_position_m':lead}
    for p in positions:
        evaluate(p)
    if combo.vehicle_factor > 0 and len(seen) < max_scenarios:
        refinements = set()
        for r in best_moments + best_shears:
            pos = r['governing_lead_axle_position_m']
            refinements.update((round(max(0.,min(full_travel,pos-step_m/2)),8),
                                round(max(0.,min(full_travel,pos+step_m/2)),8)))
        for p in sorted(refinements - seen)[:max_scenarios - len(seen)]:
            evaluate(p)
    return {'rows':best_moments, 'shear_rows':best_shears,
            'scenario_count':len(seen), 'thermal_load':thermal,
            'search_step_m':step_m,
            'assumptions':'Experimental scanned FEM with thermal action solved at each vehicle position; not validated IRC design.'}
