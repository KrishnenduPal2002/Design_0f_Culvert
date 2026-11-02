"""Integrate Stage 1 geometry and Stage 2/3 preliminary loading previews.

These results are UNFACTORED, presented in separate categories, and are NOT
STAAD/structural-analysis loads or valid ULS/SLS design combinations.

Units matter: the geometry module uses m, m³ and kN; the Stage 2/3 design-note
references use metric tonne-force (tf) and tf/m² or tf/m. DO NOT add a lateral
pressure or a braking line load to total vertical concrete self-weight.
"""

from __future__ import annotations

from dataclasses import asdict
import math

from .inputs import CulvertInputs
from .geometry import calculate_geometry
from .loads import calculate_preliminary_self_weight
from .earth_pressure import (
    EarthPressureInputs,
    pressure_at_depth,
    design_note_reference_rows,
)
from .traffic_loads import BrakingInputs, braking_cases
from .vehicle_loading import VehicleLoadingInputs, calculate_axle_dispersion, scan_lead_axle_positions
from .load_combinations import CombinationInputs, calculate_component_factors
from .structural_analysis import is_original_geometry, build_structural_analysis_snapshot
from .frame_solver import FrameOptions, run_frame_analysis
from .uls_design import calculate_uls_design
from .sls_design import calculate_sls_design


def calculate_preview(
    inputs: CulvertInputs,
    earth_pressure_inputs: EarthPressureInputs | None = None,
    braking_inputs: BrakingInputs | None = None,
    vehicle_inputs: VehicleLoadingInputs | None = None,
    combination_inputs: CombinationInputs | None = None,
    structural_records: list[dict] | None = None,
    frame_options: FrameOptions | None = None,
    run_frame: bool = False,
    uls_design_overrides: dict[int, dict] | None = None,
    uls_design_demand_source: str = "Original Excel ULS reference (not verified against active loads)",
    sls_design_overrides: dict[str, dict] | None = None,
    sls_demand_source: str = "Original Excel SLS reference (not verified against active loads)",
    sls_design_options: dict | None = None,
) -> dict:
    """Return one consistent snapshot of the available analysis stages.

    Optional Stage 2/3 parameters default to the original design-note values,
    but their cushion and length must always match the culvert geometry.

    Structural actions are returned only when experimental frame solve is enabled;
    otherwise this contains preview loads, not design forces.
    """
    inputs.validate()
    earth = earth_pressure_inputs or EarthPressureInputs(earth_cushion_m=inputs.earth_cushion_m)
    braking = braking_inputs or BrakingInputs(length_m=inputs.length_m)
    earth.validate()
    braking.validate()

    if not math.isclose(earth.earth_cushion_m, inputs.earth_cushion_m, rel_tol=0, abs_tol=1e-9):
        raise ValueError("Earth-pressure cushion and culvert earth cushion must match")
    if not math.isclose(braking.length_m, inputs.length_m, rel_tol=0, abs_tol=1e-9):
        raise ValueError("Braking distribution length must match culvert length")

    geom = calculate_geometry(inputs)
    self_weight = calculate_preliminary_self_weight(inputs, geom)

    # In the design note the depth datum used in the pressure table is the
    # upper face of the top slab.  This is a provisional interpretation;
    # a structural engineer must confirm the load datum and applications.
    bottom_centreline_depth_m = (
        inputs.top_slab_thickness_m
        + inputs.clear_height_m
        + inputs.bottom_slab_thickness_m / 2.0
    )
    soil_pressures = [
        pressure_at_depth(bottom_centreline_depth_m * i / 100.0, earth)
        for i in range(101)
    ]
    earth_results = {
        "parameters": asdict(earth),
        "depth_datum": "Below top slab upper surface (provisional design-note interpretation)",
        "bottom_slab_centreline_depth_m": bottom_centreline_depth_m,
        "pressure_at_bottom_slab_centreline": soil_pressures[-1],
        "pressure_profile": soil_pressures,
        "design_note_reference_comparison": design_note_reference_rows(earth),
        "status": "Unfactored preliminary pressure magnitudes only; NOT applied structural loads",
    }
    braking_results = {
        "parameters": asdict(braking),
        "cases": braking_cases(braking),
        "status": "Braking-force arithmetic from provided contributions; NOT full vehicle loading",
    }

    vehicle = calculate_axle_dispersion(inputs, vehicle_inputs or VehicleLoadingInputs())
    result = {
        "inputs": asdict(inputs),
        "geometry": geom.to_dict(),
        "preliminary_self_weight": self_weight.to_dict(),
        "earth_pressure": earth_results,
        "braking_forces": braking_results,
        "vehicle_axle_loading": vehicle,
        "vehicle_position_screening": scan_lead_axle_positions(inputs, vehicle_inputs or VehicleLoadingInputs()),
        "structural_design_status": "NOT IMPLEMENTED OR VERIFIED",
        "limitations": [
            "No STAAD structural analysis or equivalent frame solver",
            "Stage 2 earth/surcharge/cushion pressures are preliminary magnitudes only; no member-force application or combined effects",
            "Stage 3 includes workbook-reference 70R(W), 40T Bogie, equivalent 70R tracked, 385T SV and Custom load stations; physical placements and code rules are not fully verified",
            "Factors are user-supplied component multipliers, NOT approved IRC ULS/SLS load combinations",
            "No member forces, reactions, bending moments, or shears are computed from vehicle loads",
            "ULS reference checks and SLS rare/quasi reference checks are implemented; no validated design load envelopes or detailing checks",
            "No BBS, BOQ or construction-ready reinforcement details",
            "No approval or certification of structural safety",
            "Volumes include four simplified triangular haunches but omit barriers and other special geometry; haunch area differs from the design note",
        ],
    }
    result["load_combinations"] = calculate_component_factors(result, combination_inputs or CombinationInputs())
    result["structural_analysis"] = build_structural_analysis_snapshot(
        structural_records, reference_geometry_matches=is_original_geometry(inputs)
    )
    # Stage 4 imports user-mapped actions for inspection; it DOES NOT calculate
    # structural response from any Stage 2/3 loads or replace a frame solver.
    result["limitations"][0] = (
        "No in-app structural solver or direct STAAD connection; Stage 4 accepts "
        "engineer-mapped CSV force results for comparison only"
    )
    if run_frame:
        frame = run_frame_analysis(
            inputs, earth, braking, vehicle_inputs or VehicleLoadingInputs(),
            combination_inputs or CombinationInputs(), frame_options or FrameOptions(),
        )
        result["frame_solver"] = frame
        result["earth_pressure"]["status"] = (
            "Lateral soil/surcharge/cushion and vertical cover loads are applied to an "
            "EXPERIMENTAL 2D frame using assumptions; NOT verified IRC/STAAD loads"
        )
        result["load_combinations"]["frame_solver_link"] = (
            "Selected component factors are applied together to the frame load vector; "
            "the model is solved once for combined actions. NOT an approved IRC combination."
        )
        result["structural_design_status"] = (
            "IN-APP 2D FRAME PROTOTYPE SOLVED — NOT VALIDATED FOR DESIGN"
        )
        result["limitations"] = [
            "A real Python 2D frame solver is implemented but NOT STAAD; no direct STAAD connection or independent verification",
            "Stage 2 soil/surcharge/cushion loads are applied to model members using provisional location, datum and direction assumptions",
            "Stage 3 70R(W), 40T and custom wheel strip-loads can be applied to the prototype frame; 70R tracked and 385T require separate verified structural mapping and are not solved in the app",
            "Applied factor sets yield combined frame actions, but are USER CONFIGURED, not code-approved ULS/SLS combinations",
            "Frame member-end forces, moments and support reactions are calculated for this simplified model; no verified force envelopes",
            "Excel-reference ULS and SLS checks available, but not validated against active solver loads; no certified reinforcement design",
            "No BBS, BOQ or construction-ready reinforcement details",
            "No approval or certification of structural safety",
            "Haunch mass is approximated at frame corners; haunch stiffness and barriers are omitted; design-note area discrepancy unresolved",
        ]
    # Stage 5 ULS is a traceable translation of Design!52:166 for the original
    # case, not a capacity check against the experimental model's member forces.
    result["uls_design"] = calculate_uls_design(
        inputs, uls_design_overrides, demand_source=uls_design_demand_source
    )
    result["limitations"].append(
        f"Stage 5 ULS demand source: {uls_design_demand_source}. Design forces, "
        "section mappings and steel details require engineer review; unvalidated FEM is not approved input."
    )
    # Stage 6 SLS is an independent serviceability calculation. It shares the
    # materials/section properties but never substitutes ULS factored actions.
    result["sls_design"] = calculate_sls_design(
        inputs, sls_design_overrides, demand_source=sls_demand_source,
        **(sls_design_options or {}),
    )
    result["limitations"].append(
        "Stage 6 SLS uses selected rare/quasi service moments and original section properties; "
        "not a validated design envelope. Crack-width acceptance needs an engineer-approved limit."
    )
    if structural_records:
        result["limitations"].append(
            "Imported member forces have not been checked for STAAD model validity, "
            "sign convention, load combinations or structural safety"
        )
    return result
