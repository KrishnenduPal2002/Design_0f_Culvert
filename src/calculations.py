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
from .uls_design import calculate_uls_design, flexural_check, reference_table
from .sls_design import calculate_sls_design
from .bbs import calculate_bbs
from .boq import calculate_boq
from .reinforcement_drawings import build_reinforcement_review



# Stage 1 — Dead Load and SIDL (the supplied design-note example, in tf).
# Unlike preliminary_self_weight (kN), this schedule intentionally includes
# wearing course, earth cushion, crash barriers and parapets. It is a separate
# design-note comparison and must not be added again to concrete self-weight.
def default_dead_load_sidl_rows(inputs: CulvertInputs) -> list[dict]:
    """Editable default schedule; values follow the supplied dead-load table.

    The crash-barrier weight in the reference is 7.00 tf despite the listed
    0.5 × 1.1 m dimensions implying 9.625 tf. We expose an *effective* section
    area of 0.400 m² for that reference instead of pretending it is a rectangle.
    Similarly, the source haunch area is 0.045 m² (distinct from the actual
    triangular 0.15 × 0.15 / 2 = 0.01125 m² geometry model).
    """
    inputs.validate()
    w = inputs.clear_width_m + 2.0 * inputs.wall_thickness_m
    l = inputs.length_m
    concrete = inputs.concrete_density_tf_m3
    soil = inputs.backfill_density_tf_m3
    wearing = inputs.wearing_course_density_tf_m3
    entries = [
        ('Top Slab', 1, l, w, inputs.top_slab_thickness_m, concrete, 'Rectangle', None),
        ('Bottom Slab', 1, l, w, inputs.bottom_slab_thickness_m, concrete, 'Rectangle', None),
        ('Side Wall', 2, l, inputs.wall_thickness_m, inputs.clear_height_m, concrete, 'Rectangle', None),
        ('Int. Wall', 1, l, inputs.internal_wall_thickness_m, inputs.clear_height_m, concrete, 'Rectangle', None),
        # Source dead-load sheet uses 11.0 m effective length and 65 mm wearing
        # course; the geometric parameter page uses 50 mm wearing coat instead.
        ('Wearing Coat', 1, max(l - 1.0, 0.0), w, 0.065, wearing, 'Rectangle', None),
        ('Earth Cushion', 1, max(l - 1.0, 0.0), w, inputs.earth_cushion_m, soil, 'Rectangle', None),
        ('Crash Barrier', 2, w, inputs.ms_railing_width_m, 1.1, concrete, 'Area', 0.400),
        ('Parapet', 2, w, inputs.ms_railing_width_m, 0.3, concrete, 'Rectangle', None),
        ('Haunch', inputs.haunch_count, l, inputs.haunch_width_m, inputs.haunch_height_m, concrete, 'Area', 0.045 if inputs.haunch_count else 0.0),
    ]
    return [
        {
            'Member': member, 'Nos': nos, 'Length (m)': length,
            'Breadth (m)': breadth, 'Height (m)': height,
            'Density (t/m³)': density, 'Method': method, 'Area (m²)': area,
        }
        for member, nos, length, breadth, height, density, method, area in entries
    ]


def calculate_dead_load_sidl(
    inputs: CulvertInputs,
    rows: list[dict] | None = None,
    live_load_base_pressure_tf_m2: float = 0.50,
    base_area_m2: float | None = None,
) -> dict:
    """Compute dead load, SIDL and base pressure from editable schedule.

    Rectangular rows: Nos × Length × Breadth × Height × Density.
    Area rows: Nos × Length × Area × Density.
    All masses here are metric tonne-force equivalents (tf) using the
    design-note convention; not the kN self-weight of the other modules.
    """
    inputs.validate()
    source = default_dead_load_sidl_rows(inputs) if rows is None else rows
    if not isinstance(source, (list, tuple)) or not source:
        raise ValueError('Dead-load schedule must contain at least one row')

    def numeric(record: dict, name: str) -> float:
        try:
            v = float(record[name])
        except (KeyError, TypeError, ValueError) as ex:
            raise ValueError(f"{record.get('Member', 'Row')}: {name} must be numeric") from ex
        if not math.isfinite(v) or v < 0:
            raise ValueError(f"{record.get('Member', 'Row')}: {name} must be finite and nonnegative")
        return v

    output = []
    for row in source:
        row = dict(row)
        method = str(row.get('Method', 'Rectangle'))
        if method not in ('Rectangle', 'Area'):
            raise ValueError(f"{row.get('Member', 'Row')}: unknown calculation method {method!r}")
        n = numeric(row, 'Nos')
        length = numeric(row, 'Length (m)')
        breadth = numeric(row, 'Breadth (m)')
        height = numeric(row, 'Height (m)')
        density = numeric(row, 'Density (t/m³)')
        if method == 'Area':
            area = numeric(row, 'Area (m²)')
        else:
            area = breadth * height
        weight = n * length * area * density
        row['Section area used (m²)'] = area
        row['Weight (t)'] = weight
        output.append(row)
    weight_total = sum(row['Weight (t)'] for row in output)
    geom_area = (inputs.clear_width_m + 2.0 * inputs.wall_thickness_m) * inputs.length_m
    area = geom_area if base_area_m2 is None else float(base_area_m2)
    ll = float(live_load_base_pressure_tf_m2)
    if not math.isfinite(area) or area <= 0:
        raise ValueError('Base slab area must be positive and finite')
    if not math.isfinite(ll) or ll < 0:
        raise ValueError('LL base pressure must be nonnegative and finite')
    dl_pressure = weight_total / area
    pressure = dl_pressure + ll
    capacity = inputs.safe_bearing_capacity_tf_m2
    return {
        'rows': output,
        'total_weight_tf': weight_total,
        'base_slab_area_m2': area,
        'dead_load_base_pressure_tf_m2': dl_pressure,
        'live_load_base_pressure_tf_m2': ll,
        'total_base_pressure_tf_m2': pressure,
        'safe_bearing_capacity_tf_m2': capacity,
        'base_pressure_within_capacity': pressure <= capacity,
        'source_note': (
            'Uses reference effective areas of 0.400 m² for Crash Barrier and '
            '0.045 m² for Haunch. Those areas differ from the product of the displayed '
            'dimensions; review them with the design engineer.'
        ),
    }


# Stage 1 reference nodal spring constants for a one-metre strip of the box.
# The screenshot gives widths rounded to two decimals, but 0.0625 and 0.1375 m
# are needed to reproduce its displayed spring constants at K = 1000 tf/m³.
# This reference table is independent of the experimental FEM spring layout.
def default_spring_constant_rows() -> list[dict]:
    """Return the four editable tributary widths from the spring-constant table."""
    return [
        {'S.No.': 1, 'Node No.': 'Node at centre of side wall', 'Eff. Width (m)': 0.0625},
        {'S.No.': 2, 'Node No.': 'Node at start of haunch (side wall)', 'Eff. Width (m)': 0.1375},
        {'S.No.': 3, 'Node No.': 'Node at end of haunch (side wall)', 'Eff. Width (m)': 0.21},
        {'S.No.': 4, 'Node No.': 'Remaining Node to bottom slab', 'Eff. Width (m)': 0.27},
    ]


def calculate_spring_constants(inputs: CulvertInputs, rows: list[dict] | None = None) -> dict:
    """Compute t/m nodal springs using K(tf/m³) × width(m) × 1-m strip.

    The source sheet uses a 1 m longitudinal strip. These are reference
    effective widths, not generated nodal tributary widths from FEM meshing.
    Do not substitute them for actual frame support springs without mapping.
    """
    inputs.validate()
    modulus = float(inputs.subgrade_reaction_modulus_tf_m3)
    if not math.isfinite(modulus) or modulus <= 0:
        raise ValueError('Subgrade reaction modulus must be positive and finite')
    src = default_spring_constant_rows() if rows is None else rows
    if not isinstance(src, (list, tuple)) or len(src) != 4:
        raise ValueError('Spring constant table requires exactly four node rows')
    output = []
    for expected, row in zip(range(1, 5), src):
        row = dict(row)
        if row.get('S.No.') != expected:
            raise ValueError(f'Spring constant row number must be {expected}')
        if row.get('Node No.') != default_spring_constant_rows()[expected - 1]['Node No.']:
            raise ValueError(f'Spring constant node {expected} does not match the reference node')
        try:
            width = float(row['Eff. Width (m)'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f'Node {expected}: Effective width must be a number') from exc
        if not math.isfinite(width) or width < 0:
            raise ValueError(f'Node {expected}: Effective width must be finite and nonnegative')
        output.append({
            'S.No.': expected,
            'Node No.': row['Node No.'],
            'Eff. Width (m)': width,
            'Spring constant (t/m)': modulus * width * 1.0,
        })
    return {
        'rows': output,
        'subgrade_reaction_modulus_tf_m3': modulus,
        'strip_width_m': 1.0,
    }


# Calculation subsection: Earth Pressure per metre width.
# Depths are derived from the active geometric parameters (upper face of top
# slab as H=0), NOT stored as the fixed depths of the original 1x3x3 example.
# Keep earth, surcharge, and earth-cushion lateral pressures separate, as in
# the supplied design-note screenshot. No resultant structural forces here.
def calculate_earth_pressure_table(
    inputs: CulvertInputs,
    earth_params: EarthPressureInputs | None = None,
) -> dict:
    """Calculate earth pressure at slab, haunch, and wall stations.

    Pressure p(H)=K0*gamma_backfill*H (tf/m²). The source screenshot has
    K0=0.5 and gamma=2 tf/m³, so numerical earth pressure equals H.
    Surcharge and cushion use their equivalent soil heights independently.
    """
    inputs.validate()
    p = earth_params or EarthPressureInputs(
        k0=inputs.earth_pressure_coefficient_k0,
        backfill_density_t_m3=inputs.backfill_density_tf_m3,
        surcharge_equivalent_height_m=inputs.surcharge_equivalent_height_m,
        earth_cushion_m=inputs.earth_cushion_m,
    )
    p.validate()
    if not math.isclose(p.earth_cushion_m, inputs.earth_cushion_m, abs_tol=1e-9):
        raise ValueError('Earth cushion in earth-pressure inputs must match design inputs')

    top = inputs.top_slab_thickness_m
    clear_h = inputs.clear_height_m
    bottom = inputs.bottom_slab_thickness_m
    stations = [
        ('at C/L of top slab', top / 2.0),
    ]
    if inputs.haunch_count:
        stations += [
            ('at end of top haunch', top),
            ('at start of top haunch', top + inputs.haunch_height_m),
        ]
    stations += [('at mid of side wall', top + clear_h / 2.0)]
    if inputs.haunch_count:
        stations += [
            ('at start of bottom haunch', top + clear_h - inputs.haunch_height_m),
            ('at end of bottom haunch', top + clear_h),
        ]
    stations += [('at C/L of bottom slab', top + clear_h + bottom / 2.0)]
    rows = []
    for number, (location, depth) in enumerate(stations, start=1):
        values = pressure_at_depth(depth, p)
        rows.append({
            'S.No.': number,
            'Earth Pressure at': location,
            'H (m)': depth,
            'Earth Pressure (t/m²)': values['earth_pressure_t_m2'],
        })
    return {
        'rows': rows,
        'k0': p.k0,
        'backfill_density_tf_m3': p.backfill_density_t_m3,
        'surcharge_equivalent_height_m': p.surcharge_equivalent_height_m,
        'earth_cushion_m': p.earth_cushion_m,
        'surcharge_pressure_tf_m2': p.k0 * p.backfill_density_t_m3 * p.surcharge_equivalent_height_m,
        'cushion_pressure_tf_m2': p.k0 * p.backfill_density_t_m3 * p.earth_cushion_m,
        'max_depth_m': top + clear_h + bottom / 2.0,
        'pressure_gradient_tf_m3': p.k0 * p.backfill_density_t_m3,
    }



def calculate_braking_force_table(
    inputs: CulvertInputs,
    braking_params: BrakingInputs | None = None,
) -> dict:
    """Calculate the two design-note braking-force summaries from live inputs.

    Each total horizontal force is main vehicle contribution plus Class A.
    Braking line load = total horizontal force / culvert length (tf/m).
    The moment = line load * the user-entered *illustrative* lever arm
    (tf·m/m). The note's 0.877 / 1.009 values follow a 1.325 m arm.

    This calculation reproduces reported arithmetic; it does not establish
    IRC-approved lane selection, load factors or structural design actions.
    """
    inputs.validate()
    p = braking_params or BrakingInputs(length_m=inputs.length_m)
    p.validate()
    if not math.isclose(p.length_m, inputs.length_m, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError('Braking force length must match culvert length')

    cases = []
    for label, main, main_name in (
        ('Class-70RW + A', p.class_70rw_force_tf, 'Force due to 70R-W'),
        ('Class-40T + A', p.class_40t_force_tf, 'Force due to 40T'),
    ):
        total = main + p.class_a_force_tf
        distributed = total / p.length_m
        cases.append({
            'Case': label,
            'Main force label': main_name,
            'Main force (t)': main,
            'Class A force (t)': p.class_a_force_tf,
            'Total horizontal force (t)': total,
            'Braking force (t/m)': distributed,
            'Moment due to braking (t-m/m)': distributed * p.assumed_lever_arm_m,
        })

    return {
        'cases': cases,
        'length_m': p.length_m,
        'lever_arm_m': p.assumed_lever_arm_m,
        'reference_note': 'Design note reference, IRC 6:2017 Clause 211.2; source contribution values are supplied inputs.',
    }

# Stage 1 — Temperature effects calculated from the supplied IRC 6:2017
# design-note example. This independently calculates simplified equivalent
# thermal-strip forces; it is NOT an approved STAAD thermal load case.
TEMPERATURE_MODULUS_DEFAULT_TF_M2 = 3_230_000.0  # displayed as 3.23E+06 in source
TEMPERATURE_ALPHA_DEFAULT_PER_C = 1.2e-5


def default_temperature_gradient_rows(mode: str) -> list[dict]:
    """Editable source gradient subdivisions for temperature rise or fall.

    'Rectangle' applies the full strain over the listed section area.
    'Triangle' uses 1/2 of its area (linearly varying temperature).
    Opposing top/bottom zones contribute signed eccentricities to moment.
    """
    if mode == 'rise':
        data = (
            ('F1', 4.9, 1.0, 0.075, 'Rectangle', 'Top', 'surface'),
            ('F2', 8.3, 1.0, 0.075, 'Triangle', 'Top', 'surface'),
            ('F3', 4.9, 1.0, 0.100, 'Triangle', 'Top', 'after_top'),
            ('F4', 0.3, 1.0, 0.075, 'Triangle', 'Bottom', 'surface'),
        )
    elif mode == 'fall':
        data = (
            ('F1', 1.0, 1.0, 0.050, 'Rectangle', 'Top', 'surface'),
            ('F2', 2.1, 1.0, 0.050, 'Triangle', 'Top', 'surface'),
            ('F3', 1.0, 1.0, 0.0625, 'Triangle', 'Top', 'after_top'),
            ('F4', 1.0, 1.0, 0.050, 'Triangle', 'Bottom', 'surface'),
            ('F5', 0.2, 1.0, 0.050, 'Rectangle', 'Bottom', 'surface'),
            ('F6', 0.2, 1.0, 0.0625, 'Triangle', 'Bottom', 'after_bottom'),
        )
    else:
        raise ValueError('Temperature mode must be rise or fall')
    return [
        {'Zone': zone, 'ΔT (°C)': dtemp, 'b (m)': breadth, 't (m)': thick,
         'Profile': shape, 'Face': face, 'Position': pos}
        for zone, dtemp, breadth, thick, shape, face, pos in data
    ]


def calculate_temperature_gradient(
    inputs: CulvertInputs, mode: str,
    rows: list[dict] | None = None,
    elastic_modulus_tf_m2: float = TEMPERATURE_MODULUS_DEFAULT_TF_M2,
    thermal_expansion_per_c: float = TEMPERATURE_ALPHA_DEFAULT_PER_C,
) -> dict:
    """Calculate thermal forces and moment for 1-m strip from editable zones.

    F = E * alpha * ΔT * b * t * (1 for rectangular, 1/2 triangular).
    Moment about mid-depth = sum(F * signed eccentricity).
    Equivalent linear temperature difference from EI alpha ΔT/h = moment.
    Geometry follows the supplied source sketch (overlapping top layers are
    represented as superposed rectangles/triangles, not extra slab area).
    """
    inputs.validate()
    if mode not in ('rise', 'fall'):
        raise ValueError('Temperature mode must be rise or fall')
    E = float(elastic_modulus_tf_m2)
    alpha = float(thermal_expansion_per_c)
    if not math.isfinite(E) or E <= 0 or not math.isfinite(alpha) or alpha <= 0:
        raise ValueError('Temperature E and thermal expansion must be positive finite numbers')
    source = default_temperature_gradient_rows(mode) if rows is None else rows
    standard = default_temperature_gradient_rows(mode)
    if len(source) != len(standard):
        raise ValueError(f'{mode}: requires {len(standard)} temperature force rows')
    h = float(inputs.top_slab_thickness_m)
    checked = []
    for i, (row, expected) in enumerate(zip(source, standard), start=1):
        row = dict(row)
        if row.get('Zone') != expected['Zone']:
            raise ValueError(f'Temperature row {i}: Zone must be {expected["Zone"]}')
        for fixed_name in ('Profile', 'Face', 'Position'):
            if row.get(fixed_name, expected[fixed_name]) != expected[fixed_name]:
                raise ValueError(f'Temperature row {i}: {fixed_name} cannot be changed')
        for col in ('ΔT (°C)', 'b (m)', 't (m)'):
            try:
                value = float(row[col])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f'Temperature {mode} {row["Zone"]}: {col} must be numeric') from exc
            if not math.isfinite(value) or value < 0:
                raise ValueError(f'Temperature {mode} {row["Zone"]}: {col} must be finite and nonnegative')
            row[col] = value
        checked.append(row)

    surface_thickness_top = max(r['t (m)'] for r in checked if r['Face'] == 'Top' and r['Position'] == 'surface')
    surface_thickness_bottom = max(r['t (m)'] for r in checked if r['Face'] == 'Bottom' and r['Position'] == 'surface')
    top_extra = max((r['t (m)'] for r in checked if r['Face'] == 'Top' and r['Position'] == 'after_top'), default=0.0)
    bottom_extra = max((r['t (m)'] for r in checked if r['Face'] == 'Bottom' and r['Position'] == 'after_bottom'), default=0.0)
    if surface_thickness_top + top_extra + surface_thickness_bottom + bottom_extra > h + 1e-9:
        raise ValueError('Temperature gradient layers exceed the selected top-slab thickness')

    output = []
    for row in checked:
        shape_factor = 1.0 if row['Profile'] == 'Rectangle' else 0.5
        area = row['b (m)'] * row['t (m)']
        force = E * alpha * row['ΔT (°C)'] * area * shape_factor
        offset_from_face = (surface_thickness_top if row['Position'] == 'after_top'
                            else surface_thickness_bottom if row['Position'] == 'after_bottom' else 0.0)
        centroid_in_layer = row['t (m)'] * (0.5 if shape_factor == 1.0 else 1.0/3.0)
        acting_at = offset_from_face + centroid_in_layer
        e = h/2.0 - acting_at if row['Face'] == 'Top' else -(h/2.0 - acting_at)
        output.append({
            'Zone': row['Zone'], 'ΔT (°C)': row['ΔT (°C)'],
            'b (m)': row['b (m)'], 't (m)': row['t (m)'],
            'Profile': row['Profile'], 'Face': row['Face'],
            'A=b×t (m²)': area, 'Shape factor': shape_factor,
            'Force F (t)': force, 'Acting at (m)': acting_at,
            'Eccentricity e (m)': e, 'F×e (t·m)': force * e,
        })

    total_force = sum(r['Force F (t)'] for r in output)
    net_moment = sum(r['F×e (t·m)'] for r in output)
    # Equivalent gradient from the beam free-curvature relation M=EIαΔT/h,
    # with a 1-m longitudinal strip, I=bh³/12, b=1 m.
    inertia = h**3 / 12.0
    equivalent_delta_t = net_moment * h / (E * inertia * alpha)
    reference = {
        'rise': {'total_force_t': 36.25, 'net_moment_t_m': 2.568, 'equivalent_delta_t_c': 12.718},
        'fall': {'total_force_t': 6.78, 'net_moment_t_m': 0.32, 'equivalent_delta_t_c': 1.60},
    }[mode]
    return {
        'mode': mode,
        'rows': output,
        'elastic_modulus_tf_m2': E,
        'thermal_expansion_per_c': alpha,
        'slab_thickness_m': h,
        'inertia_m4_per_m': inertia,
        'total_force_t': total_force,
        'net_moment_t_m': net_moment,
        'equivalent_delta_t_c': equivalent_delta_t,
        'reference': reference,
        'source_note': 'Reference values may differ slightly due to rounded E and printed intermediate values.',
    }


def calculate_uniform_temperature(
    maximum_air_c: float = 42.2,
    minimum_air_c: float = 2.0,
    restraint_bridge_max_c: float = 32.1,
    restraint_bridge_min_c: float = 12.1,
    creep_concrete_modulus_tf_m2: float = 1_620_000.0,
    application_factor: float | None = None,
    elastic_modulus_tf_m2: float = TEMPERATURE_MODULUS_DEFAULT_TF_M2,
) -> dict:
    """Calculate the source's uniform-temperature rise and fall for STAAD.

    Rise = T_air,max - T_bridge,restraint,min
    Fall = T_bridge,restraint,max - T_air,min
    Applied temperature = rise/fall times the ratio of creep/full modulus
    (or an explicitly supplied reduction factor).
    This returns reference temperature inputs only, not member stresses.
    """
    vals = (maximum_air_c, minimum_air_c, restraint_bridge_max_c,
            restraint_bridge_min_c, creep_concrete_modulus_tf_m2, elastic_modulus_tf_m2)
    vals = tuple(float(v) for v in vals)
    if not all(math.isfinite(v) for v in vals):
        raise ValueError('Uniform temperature inputs must be finite')
    maxair, minair, maxrest, minrest, creep, full_E = vals
    if full_E <= 0:
        raise ValueError('Full concrete modulus must be positive')
    factor = (creep / full_E) if application_factor is None else float(application_factor)
    if maxair < minair or maxrest < minrest:
        raise ValueError('Maximum temperature cannot be less than minimum temperature')
    if creep <= 0 or not math.isfinite(factor) or factor <= 0 or factor > 1:
        raise ValueError('Creep modulus must be positive and application factor must be in (0,1]')
    rise = maxair - minrest
    fall = maxrest - minair
    return {
        'maximum_air_c': maxair,
        'minimum_air_c': minair,
        'restraint_bridge_max_c': maxrest,
        'restraint_bridge_min_c': minrest,
        'creep_concrete_modulus_tf_m2': creep,
        'full_concrete_modulus_tf_m2': full_E,
        'application_factor': factor,
        'shade_air_difference_c': maxair - minair,
        'temperature_rise_c': rise,
        'temperature_fall_c': fall,
        'applied_rise_c': rise * factor,
        'applied_fall_c': fall * factor,
    }


def calculate_temperature_effects(inputs: CulvertInputs, rise_rows: list[dict] | None = None,
                                  fall_rows: list[dict] | None = None,
                                  elastic_modulus_tf_m2: float = TEMPERATURE_MODULUS_DEFAULT_TF_M2,
                                  thermal_expansion_per_c: float = TEMPERATURE_ALPHA_DEFAULT_PER_C,
                                  uniform_options: dict | None = None) -> dict:
    """One grouped result for the three temperature sections."""
    return {
        'rise': calculate_temperature_gradient(inputs, 'rise', rise_rows,
                                              elastic_modulus_tf_m2, thermal_expansion_per_c),
        'fall': calculate_temperature_gradient(inputs, 'fall', fall_rows,
                                              elastic_modulus_tf_m2, thermal_expansion_per_c),
        'uniform': calculate_uniform_temperature(**(uniform_options or {})),
    }



# Original workbook: `load` worksheet, rows 215-244; moments in t-m/m.
# Only numerically complete rows are retained (the four interior-support
# rows with Excel #VALUE! are intentionally excluded).
# These are workbook reference actions, NOT forces recalculated from current
# geometry, vehicle type or experimental frame analysis.
_DESIGN_MOMENT_ROWS = [
    {'Excel row': 215, 'Member': 'Top Slab', 'Location': 'End Supp.', 'Face': 'Wall face', 'ULS Normal': 3.347, 'ULS LL': 3.093, 'Rare Normal': 2.725, 'Rare LL': 2.062, 'Quasi Normal': 1.568, 'Quasi LL': 0.0},
    {'Excel row': 216, 'Member': 'Top Slab', 'Location': 'End Supp.', 'Face': 'Haunch end', 'ULS Normal': 2.65, 'ULS LL': 2.073, 'Rare Normal': 1.762, 'Rare LL': 1.382, 'Quasi Normal': 0.763, 'Quasi LL': 0.0},
    {'Excel row': 217, 'Member': 'Top Slab', 'Location': 'Mid Span', 'Face': '', 'ULS Normal': -2.078, 'ULS LL': -4.677, 'Rare Normal': -1.53, 'Rare LL': -3.118, 'Quasi Normal': -1.53, 'Quasi LL': 0.0},
    {'Excel row': 220, 'Member': 'Bottom Slab', 'Location': 'End Supp.', 'Face': 'Wall face', 'ULS Normal': -5.523, 'ULS LL': -1.8944999999999999, 'Rare Normal': -3.83, 'Rare LL': -1.263, 'Quasi Normal': -1.836, 'Quasi LL': 0.0},
    {'Excel row': 221, 'Member': 'Bottom Slab', 'Location': 'End Supp.', 'Face': 'Haunch end', 'ULS Normal': -4.589, 'ULS LL': -1.5374999999999999, 'Rare Normal': -3.061, 'Rare LL': -1.025, 'Quasi Normal': -1.161, 'Quasi LL': 0.0},
    {'Excel row': 222, 'Member': 'Bottom Slab', 'Location': 'Mid Span', 'Face': '', 'ULS Normal': 3.537, 'ULS LL': 4.665, 'Rare Normal': 2.61, 'Rare LL': 3.11, 'Quasi Normal': 2.61, 'Quasi LL': 0.0},
    {'Excel row': 225, 'Member': 'Side Wall', 'Location': '1st side wall top supp. wall face', 'Face': 'Earth face', 'ULS Normal': 3.432, 'ULS LL': 3.045, 'Rare Normal': 2.401, 'Rare LL': 2.03, 'Quasi Normal': 1.298, 'Quasi LL': 0.0},
    {'Excel row': 226, 'Member': 'Side Wall', 'Location': '2nd side wall top supp. wall face', 'Face': 'Earth face', 'ULS Normal': -3.427, 'ULS LL': -3.0435, 'Rare Normal': -2.359, 'Rare LL': -2.029, 'Quasi Normal': -1.298, 'Quasi LL': 0.0},
    {'Excel row': 227, 'Member': 'Side Wall', 'Location': '1st side wall top supp. wall face', 'Face': 'Non Earth face', 'ULS Normal': 0.0, 'ULS LL': -0.025500000000000002, 'Rare Normal': 0.0, 'Rare LL': -0.017, 'Quasi Normal': 0.0, 'Quasi LL': 0.0},
    {'Excel row': 228, 'Member': 'Side Wall', 'Location': '2nd side wall top supp. wall face', 'Face': 'Non Earth face', 'ULS Normal': 0.0, 'ULS LL': 0.44699999999999995, 'Rare Normal': 0.0, 'Rare LL': 0.298, 'Quasi Normal': 0.0, 'Quasi LL': 0.0},
    {'Excel row': 229, 'Member': 'Side Wall', 'Location': '1st side wall top Supp. Haunch end', 'Face': 'Earth face', 'ULS Normal': 2.788, 'ULS LL': 2.985, 'Rare Normal': 1.98, 'Rare LL': 1.99, 'Quasi Normal': 0.986, 'Quasi LL': 0.0},
    {'Excel row': 230, 'Member': 'Side Wall', 'Location': '2nd side wall top Supp. Haunch end', 'Face': 'Earth face', 'ULS Normal': -2.783, 'ULS LL': -2.985, 'Rare Normal': -1.932, 'Rare LL': -1.99, 'Quasi Normal': -0.986, 'Quasi LL': 0.0},
    {'Excel row': 231, 'Member': 'Side Wall', 'Location': '1st side wall top Supp. Haunch end', 'Face': 'Non Earth face', 'ULS Normal': -0.024, 'ULS LL': 0.0, 'Rare Normal': 0.0, 'Rare LL': 0.0, 'Quasi Normal': 0.0, 'Quasi LL': 0.0},
    {'Excel row': 232, 'Member': 'Side Wall', 'Location': '2nd side wall top Supp. Haunch end', 'Face': 'Non Earth face', 'ULS Normal': 0.019, 'ULS LL': 0.342, 'Rare Normal': 0.0, 'Rare LL': 0.228, 'Quasi Normal': 0.0, 'Quasi LL': 0.0},
    {'Excel row': 233, 'Member': 'Side Wall', 'Location': '1st side wall Mid Span', 'Face': 'Earth face', 'ULS Normal': 1.232, 'ULS LL': 2.511, 'Rare Normal': 0.908, 'Rare LL': 1.674, 'Quasi Normal': 0.908, 'Quasi LL': 0.0},
    {'Excel row': 234, 'Member': 'Side Wall', 'Location': '1st side wall Mid Span', 'Face': 'Earth face', 'ULS Normal': -1.232, 'ULS LL': -2.5125, 'Rare Normal': -0.908, 'Rare LL': -1.675, 'Quasi Normal': -0.908, 'Quasi LL': 0.0},
    {'Excel row': 235, 'Member': 'Side Wall', 'Location': '2nd side wall Mid Span', 'Face': 'Non Earth face', 'ULS Normal': -2.44, 'ULS LL': 0.0, 'Rare Normal': -1.528, 'Rare LL': 0.0, 'Quasi Normal': -0.831, 'Quasi LL': 0.0},
    {'Excel row': 236, 'Member': 'Side Wall', 'Location': '2nd side wall Mid Span', 'Face': 'Non Earth face', 'ULS Normal': 2.44, 'ULS LL': 0.0, 'Rare Normal': 1.528, 'Rare LL': 0.0, 'Quasi Normal': 0.831, 'Quasi LL': 0.0},
    {'Excel row': 237, 'Member': 'Side Wall', 'Location': '1st side wall Bottom Supp. Wall face', 'Face': 'Earth face', 'ULS Normal': 4.959, 'ULS LL': 2.9265, 'Rare Normal': 3.43, 'Rare LL': 1.951, 'Quasi Normal': 1.886, 'Quasi LL': 0.0},
    {'Excel row': 238, 'Member': 'Side Wall', 'Location': '2nd side wall Bottom Supp.  Wall face', 'Face': 'Earth face', 'ULS Normal': -4.954, 'ULS LL': -2.94, 'Rare Normal': -3.582, 'Rare LL': -1.96, 'Quasi Normal': -1.886, 'Quasi LL': 0.0},
    {'Excel row': 239, 'Member': 'Side Wall', 'Location': '1st side wall Bottom Supp. Wall face', 'Face': 'Non Earth face', 'ULS Normal': 0.0, 'ULS LL': 0.0, 'Rare Normal': 0.0, 'Rare LL': 0.0, 'Quasi Normal': 0.0, 'Quasi LL': 0.0},
    {'Excel row': 240, 'Member': 'Side Wall', 'Location': '2nd side wall Bottom Supp.  Wall face', 'Face': 'Non Earth face', 'ULS Normal': 0.0, 'ULS LL': 0.0, 'Rare Normal': 0.0, 'Rare LL': 0.0, 'Quasi Normal': 0.0, 'Quasi LL': 0.0},
    {'Excel row': 241, 'Member': 'Side Wall', 'Location': '1st side wall Bottom Supp. Haunch end', 'Face': 'Earth face', 'ULS Normal': 3.632, 'ULS LL': 2.8725, 'Rare Normal': 2.542, 'Rare LL': 1.915, 'Quasi Normal': 1.323, 'Quasi LL': 0.0},
    {'Excel row': 242, 'Member': 'Side Wall', 'Location': '2nd side wall Bottom Supp. Haunch end', 'Face': 'Earth face', 'ULS Normal': -3.627, 'ULS LL': -1.695, 'Rare Normal': -2.689, 'Rare LL': -1.676, 'Quasi Normal': -1.323, 'Quasi LL': 0.0},
    {'Excel row': 243, 'Member': 'Side Wall', 'Location': '1st side wall Bottom Supp. Haunch end', 'Face': 'Non Earth face', 'ULS Normal': 0.0, 'ULS LL': 0.0, 'Rare Normal': 0.0, 'Rare LL': 0.0, 'Quasi Normal': 0.0, 'Quasi LL': 0.0},
    {'Excel row': 244, 'Member': 'Side Wall', 'Location': '2nd side wall Bottom Supp. Haunch end', 'Face': 'Non Earth face', 'ULS Normal': 0.0, 'ULS LL': 0.0, 'Rare Normal': 0.0, 'Rare LL': 0.0, 'Quasi Normal': 0.0, 'Quasi LL': 0.0},
]


DESIGN_MOMENT_COMPONENTS = (
    'ULS Normal', 'ULS LL', 'Rare Normal', 'Rare LL',
    'Quasi Normal', 'Quasi LL',
)


def default_design_moment_rows() -> list[dict]:
    """Return editable signed moment component references from the workbook.

    These do not respond to culvert geometry or active vehicles; all moments
    require structural analysis for changed cases. An `Excel row` is retained
    internally for traceability and need not be shown in the Streamlit table.
    """
    return [dict(row) for row in _DESIGN_MOMENT_ROWS]


def calculate_design_moment_combinations(rows: list[dict] | None = None) -> dict:
    """Recalculate totals = Normal + LL for ULS, Rare and Quasi moments.

    Units: t-m per metre length of culvert. Signed values are preserved.
    No load-factor derivation, structural solution or IRC validation is implied.
    """
    source = default_design_moment_rows() if rows is None else rows
    if not isinstance(source, (list, tuple)) or not source:
        raise ValueError('Design moment data must contain at least one row')
    result_rows = []
    for index, item in enumerate(source, 1):
        if not isinstance(item, dict):
            raise ValueError(f'Row {index} must be a mapping')
        row = dict(item)
        for col in DESIGN_MOMENT_COMPONENTS:
            try:
                raw = row[col]
                if isinstance(raw, bool):
                    raise ValueError()
                value = float(raw)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f'Row {index}: {col} must be numeric') from exc
            if not math.isfinite(value):
                raise ValueError(f'Row {index}: {col} must be finite')
            row[col] = value
        for name in ('ULS', 'Rare', 'Quasi'):
            row[name + ' Total'] = row[name + ' Normal'] + row[name + ' LL']
        # The original Excel sheet names the ULS total as 'Factor load'.
        # Alias, not an extra load multiplier: preserve the existing ULS Total key.
        row['ULS Factor load'] = row['ULS Total']
        result_rows.append(row)
    return {
        'rows': result_rows,
        'row_count': len(result_rows),
        'unit': 't-m/m',
        'reference_excel_sheet': 'load',
        'skipped_invalid_excel_rows': (218, 219, 223, 224),
        'status': 'Excel reference or user-edited moment components only; not solved structural actions',
    }


# Source: original workbook `load` worksheet, rows 259-278, columns A-F.
# Excel interior-support rows 262, 263, 267 and 268 are #VALUE!, and are
# intentionally not given invented values for the single-cell culvert.
# These are source ULS shear-force references, not active frame-solver outputs.
_DESIGN_SHEAR_ROWS = [
    {'Excel row': 259, 'Member': 'Top Slab', 'Location': 'End Supp.', 'Face': 'Wall face', 'Normal': 4.882, 'LL': 0.0},
    {'Excel row': 260, 'Member': 'Top Slab', 'Location': 'End Supp.', 'Face': 'Haunch end', 'Normal': 4.410, 'LL': 0.0},
    {'Excel row': 261, 'Member': 'Top Slab', 'Location': 'Mid Span', 'Face': '', 'Normal': 0.0, 'LL': 0.0},
    {'Excel row': 264, 'Member': 'Bottom Slab', 'Location': 'End Supp.', 'Face': 'Wall face', 'Normal': 7.563, 'LL': 14.661},
    {'Excel row': 265, 'Member': 'Bottom Slab', 'Location': 'End Supp.', 'Face': 'Haunch end', 'Normal': 6.396, 'LL': 12.237},
    {'Excel row': 266, 'Member': 'Bottom Slab', 'Location': 'Mid Span', 'Face': '', 'Normal': 4.282, 'LL': 3.5775},
    {'Excel row': 269, 'Member': 'Side Wall', 'Location': '1st side wall top Supp.', 'Face': 'Wall face', 'Normal': 5.334, 'LL': 0.783},
    {'Excel row': 270, 'Member': 'Side Wall', 'Location': '2nd side wall top Supp.', 'Face': 'Wall face', 'Normal': 5.334, 'LL': 0.783},
    {'Excel row': 271, 'Member': 'Side Wall', 'Location': '1st side wall top Supp.', 'Face': 'Haunch end', 'Normal': 4.977, 'LL': 0.783},
    {'Excel row': 272, 'Member': 'Side Wall', 'Location': '2nd side wall top Supp.', 'Face': 'Haunch end', 'Normal': 4.977, 'LL': 0.783},
    {'Excel row': 273, 'Member': 'Side Wall', 'Location': 'Mid Span', 'Face': '1st side wall', 'Normal': 1.425, 'LL': 0.783},
    {'Excel row': 274, 'Member': 'Side Wall', 'Location': 'Mid Span', 'Face': '2nd side wall', 'Normal': 1.422, 'LL': 0.783},
    {'Excel row': 275, 'Member': 'Side Wall', 'Location': '1st side wall Bottom Supp.', 'Face': 'Wall face', 'Normal': 9.352, 'LL': 0.783},
    {'Excel row': 276, 'Member': 'Side Wall', 'Location': '2nd side wall Bottom Supp.', 'Face': 'Wall face', 'Normal': 9.349, 'LL': 0.783},
    {'Excel row': 277, 'Member': 'Side Wall', 'Location': '1st side wall Bottom Supp.', 'Face': 'Haunch end', 'Normal': 8.355, 'LL': 0.783},
    {'Excel row': 278, 'Member': 'Side Wall', 'Location': '2nd side wall Bottom Supp.', 'Face': 'Haunch end', 'Normal': 8.351, 'LL': 0.783},
]


def default_design_shear_rows() -> list[dict]:
    """Editable ULS shear components from the original `load` worksheet."""
    return [dict(row) for row in _DESIGN_SHEAR_ROWS]


def calculate_design_shear_combinations(rows: list[dict] | None = None) -> dict:
    """Compute ULS shear Total = Normal + LL, in t/m.

    Values come from the reference workbook or user edits; this does not solve
    structural shear forces from changing geometry or vehicle combinations.
    """
    source = default_design_shear_rows() if rows is None else rows
    if not isinstance(source, (list, tuple)) or not source:
        raise ValueError('ULS shear-force data must contain at least one row')
    output=[]
    for index, item in enumerate(source, 1):
        if not isinstance(item, dict):
            raise ValueError(f'ULS shear row {index} must be a mapping')
        row=dict(item)
        for col in ('Normal', 'LL'):
            try:
                v=row[col]
                if isinstance(v, bool):
                    raise ValueError()
                v=float(v)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f'ULS shear row {index}: {col} must be numeric') from exc
            if not math.isfinite(v):
                raise ValueError(f'ULS shear row {index}: {col} must be finite')
            row[col]=v
        row['Total']=row['Normal']+row['LL']
        output.append(row)
    return {
        'rows':output,
        'row_count':len(output),
        'unit':'t/m',
        'reference_excel_sheet':'load',
        'skipped_invalid_excel_rows':(262,263,267,268,279,280,281,282,283),
        'status':'Excel reference or edited ULS shear components; not live frame-solver results',
    }




# Flexural R/F detail of RCC Box — source provided-steel schedule.
# These supplied steel areas are REFERENCE values, not independently derived
# required steel from ULS, and are not applied to reinforcement drawings/BBS.
# A blank (None) bar is omitted; a 0-mm bar with a defined spacing is a
# literal zero bar in the source table and contributes no steel area.
_FLEXURAL_REINFORCEMENT_SOURCE = [
    # Component, Section, Position, inner source, outer source,
    # inner [(dia, spacing),...], outer [(dia, spacing),...]
    ('Top Slab', 'End Supp.', 'Wall face', 436.33, 907.57,
     [(10, 180)], [(8, 180), (12, 180), (0, 180)]),
    ('Top Slab', 'End Supp.', 'Haunch end', 436.33, 907.57,
     [(10, 180)], [(8, 180), (12, 180), (0, 180)]),
    ('Top Slab', 'Mid Span', '', 872.66, 279.25,
     [(10, 180), (10, 180)], [(8, 180)]),
    ('Bottom Slab', 'End Supp.', 'Wall face', 628.32, 1064.65,
     [(12, 180)], [(10, 180), (12, 180), (0, 180)]),
    ('Bottom Slab', 'End Supp.', 'Haunch end', 628.32, 1064.65,
     [(12, 180)], [(10, 180), (12, 180), (0, 180)]),
    ('Bottom Slab', 'Mid Span', '', 1064.65, 436.33,
     [(12, 180), (10, 180)], [(10, 180)]),
    ('Side Wall', '1st side wall top Supp.', 'Wall face', 436.33, 907.57,
     [(10, 180)], [(12, 180), (8, 180), (0, 180)]),
    ('Side Wall', '2nd side wall top Supp.', 'Wall face', 436.33, 907.57,
     [(10, 180)], [(12, 180), (8, 180), (0, 180)]),
    ('Side Wall', '1st side wall top Supp.', 'Haunch end', 436.33, 907.57,
     [(10, 180)], [(12, 180), (8, 180), (0, 180)]),
    ('Side Wall', '2nd side wall top Supp.', 'Haunch end', 436.33, 907.57,
     [(10, 180)], [(12, 180), (8, 180), (0, 180)]),
    ('Side Wall', 'Mid Span', '1st side wall', 436.33, 628.32,
     [(10, 180)], [(12, 180)]),
    ('Side Wall', 'Mid Span', '2nd side wall', 436.33, 628.32,
     [(10, 180)], [(12, 180)]),
    ('Side Wall', '1st side wall bott. Supp.', 'Wall face', 436.33, 1064.65,
     [(10, 180)], [(12, 180), (10, 180), (0, 180)]),
    ('Side Wall', '2nd side wall bott. Supp.', 'Wall face', 436.33, 1064.65,
     [(10, 180)], [(12, 180), (10, 180), (0, 180)]),
    ('Side Wall', '1st side wall bott. Supp.', 'Haunch end', 436.33, 1064.65,
     [(10, 180)], [(12, 180), (10, 180), (0, 180)]),
    ('Side Wall', '2nd side wall bott. Supp.', 'Haunch end', 436.33, 1064.65,
     [(10, 180)], [(12, 180), (10, 180), (0, 180)]),
]


def default_flexural_reinforcement_rows() -> list[dict]:
    """Return 16 rows matching the attached RCC Box flexural R/F schedule."""
    result = []
    for member, section, position, ref_inner, ref_outer, inner, outer in _FLEXURAL_REINFORCEMENT_SOURCE:
        row = {
            'Component': member,
            'Section': section,
            'Position': position,
            'Reference Inner (mm²/m)': ref_inner,
            'Reference Outer (mm²/m)': ref_outer,
        }
        for face, bars, slots in [('Inner', inner, 2), ('Outer', outer, 3)]:
            for i in range(1, slots + 1):
                bar, spacing = bars[i - 1] if i <= len(bars) else (None, None)
                row[f'{face} Bar-{i} (mm)'] = bar
                row[f'{face} Spacing-{i} (mm)'] = spacing
        result.append(row)
    return result


def calculate_flexural_reinforcement(rows: list[dict] | None = None) -> dict:
    """Recompute provided bar area on inner and outer faces in mm²/m.

    Each nonzero diameter contributes π*d²/4 * (1000/s). Reference columns
    are the screenshot's two-decimal provided areas, *not* required steel.
    Differences from reference are informational only; there is no IRC/ULS
    adequacy check here. Blank bar positions and explicit 0 mm bars add zero.
    """
    source = default_flexural_reinforcement_rows() if rows is None else rows
    if not isinstance(source, (list, tuple)) or not source:
        raise ValueError('Flexural reinforcement rows must be a non-empty list')
    calculated_rows = []
    for index, entry in enumerate(source, 1):
        if not isinstance(entry, dict):
            raise ValueError(f'Flexural row {index} must be a mapping')
        row = dict(entry)
        for label in ('Component', 'Section', 'Position'):
            if not isinstance(row.get(label), str):
                raise ValueError(f'Flexural row {index}: {label} must be text')
        for face, slot_count in [('Inner', 2), ('Outer', 3)]:
            ref_key = f'Reference {face} (mm²/m)'
            try:
                raw_reference = row[ref_key]
                if isinstance(raw_reference, bool):
                    raise ValueError()
                reference = float(raw_reference)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f'Flexural row {index}: {ref_key} must be numeric') from exc
            if not math.isfinite(reference) or reference < 0:
                raise ValueError(f'Flexural row {index}: {ref_key} must be finite and nonnegative')
            row[ref_key] = reference
            area = 0.0
            for slot in range(1, slot_count + 1):
                dia_key = f'{face} Bar-{slot} (mm)'
                spacing_key = f'{face} Spacing-{slot} (mm)'
                raw_dia, raw_spacing = row.get(dia_key), row.get(spacing_key)
                # Pandas-backed st.data_editor converts blank numeric cells to NaN.
                def blank(value):
                    return (value is None or value == '' or
                            (isinstance(value, (int, float)) and math.isnan(value)))
                dia = None if blank(raw_dia) else raw_dia
                spacing = None if blank(raw_spacing) else raw_spacing
                if dia is not None:
                    try:
                        if isinstance(dia, bool):
                            raise ValueError()
                        dia = float(dia)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f'Flexural row {index}: {dia_key} must be numeric') from exc
                    if not math.isfinite(dia) or dia < 0:
                        raise ValueError(f'Flexural row {index}: {dia_key} must be finite and nonnegative')
                if spacing is not None:
                    try:
                        if isinstance(spacing, bool):
                            raise ValueError()
                        spacing = float(spacing)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f'Flexural row {index}: {spacing_key} must be numeric') from exc
                    if not math.isfinite(spacing) or spacing < 0:
                        raise ValueError(f'Flexural row {index}: {spacing_key} must be finite and nonnegative')
                if dia is not None and dia > 0:
                    if spacing is None or spacing <= 0:
                        raise ValueError(f'Flexural row {index}: positive {dia_key} needs positive {spacing_key}')
                    area += math.pi * dia ** 2 / 4.0 * (1000.0 / spacing)
                row[dia_key], row[spacing_key] = dia, spacing
            if not math.isfinite(area):
                raise ValueError(f'Flexural row {index}: calculated area is not finite')
            row[f'Calculated {face} (mm²/m)'] = area
            row[f'Difference {face} (mm²/m)'] = area - reference
            row[f'{face} matches source'] = round(area, 2) == round(reference, 2)
        calculated_rows.append(row)
    return {
        'rows': calculated_rows,
        'row_count': len(calculated_rows),
        'all_match_source': all(row['Inner matches source'] and row['Outer matches source']
                                for row in calculated_rows),
        'unit': 'mm²/m',
        'reference': 'Supplied Flexural R/F detail of RCC Box table',
        'note': 'Comparison with supplied provided-steel values, not a flexural capacity or code adequacy check',
        'status': 'Reference bar schedule; not automatically connected to ULS design, BBS or reinforcement drawings',
    }

# Distribution reinforcement — supplied transverse-steel reference schedule.
# The per-metre required As is an editable reference value, not a live
# derivation from the ULS main steel. See IRC:112-2020, Cl. 16.6.1.1(3).
_DISTRIBUTION_REINFORCEMENT_ROWS = [
    {'Member': '1. Top Slab', 'Surface': 'Top', 'Position': 'Edge', 'Required As (mm²/m)': 141.830, 'Diameter (mm)': 8.0, 'Spacing (mm)': 290.0},
    {'Member': '1. Top Slab', 'Surface': 'Top', 'Position': 'Mid', 'Required As (mm²/m)': 169.926, 'Diameter (mm)': 8.0, 'Spacing (mm)': 290.0},
    {'Member': '1. Top Slab', 'Surface': 'Bottom', 'Position': 'Edge', 'Required As (mm²/m)': 141.830, 'Diameter (mm)': 8.0, 'Spacing (mm)': 290.0},
    {'Member': '1. Top Slab', 'Surface': 'Bottom', 'Position': 'Mid', 'Required As (mm²/m)': 169.926, 'Diameter (mm)': 8.0, 'Spacing (mm)': 290.0},
    {'Member': '2. Bottom Slab', 'Surface': 'Top', 'Position': 'Edge', 'Required As (mm²/m)': 153.999, 'Diameter (mm)': 8.0, 'Spacing (mm)': 225.0},
    {'Member': '2. Bottom Slab', 'Surface': 'Top', 'Position': 'Mid', 'Required As (mm²/m)': 210.849, 'Diameter (mm)': 8.0, 'Spacing (mm)': 225.0},
    {'Member': '2. Bottom Slab', 'Surface': 'Bottom', 'Position': 'Edge', 'Required As (mm²/m)': 153.999, 'Diameter (mm)': 8.0, 'Spacing (mm)': 225.0},
    {'Member': '2. Bottom Slab', 'Surface': 'Bottom', 'Position': 'Mid', 'Required As (mm²/m)': 210.849, 'Diameter (mm)': 8.0, 'Spacing (mm)': 225.0},
    {'Member': '3. Side Wall', 'Surface': 'Earth', 'Position': '', 'Required As (mm²/m)': 192.748, 'Diameter (mm)': 8.0, 'Spacing (mm)': 250.0},
    {'Member': '3. Side Wall', 'Surface': 'Inner', 'Position': '', 'Required As (mm²/m)': 67.964, 'Diameter (mm)': 8.0, 'Spacing (mm)': 300.0},
]


# Reference subsection: Design Constants and effective-depth schedule for the
# original 1x3x3 single-cell design note. These data are NOT used as structural
# design input for the separate ULS/SLS/BBS modules or experimental frame solver.
# They DO feed the linked reference-only ULS Flexural Strength Check below.
def default_design_constants(inputs: CulvertInputs) -> dict:
    """Return editable design-note values; source material units are explicit.

    The original Stage 1 modulus inputs have unit ambiguity, so they are NOT
    silently reinterpreted as GPa here. Separate reference moduli are used.
    """
    inputs.validate()
    return {
        'fy_mpa': float(str(inputs.steel_grade).replace('Fe', '')),
        'fck_mpa': float(str(inputs.concrete_grade).replace('M', '')),
        'es_gpa': 200.0,
        'ecm_gpa': 32.0,
        'modular_ratio_reference': 6.19,
        'fctm_mpa': 2.8,
        'top_slab_bottom_cover_mm': 50.0,
        'top_slab_top_cover_mm': 75.0,
        'bottom_slab_cover_mm': 75.0,
        'side_wall_outer_cover_mm': 75.0,
        'side_wall_inner_cover_mm': 50.0,
        # The source effective-depth table adds 50 mm at the haunch start,
        # not the full geometric haunch size of 150 mm. Expose explicitly.
        'haunch_start_extra_depth_m': 0.05 if inputs.haunch_count else 0.0,
    }


def calculate_design_constants(
    inputs: CulvertInputs, constants: dict | None = None,
) -> dict:
    """Recreate the four effective-depth rows of the reference design note.

    d = D - (clear cover + nominal bar radius) / 1000. Nominal 10 mm and
    12 mm bar offsets are only *reference assumptions* that reproduce the
    tabulated effective depths, not an independent detailing verification.
    The reference non-earth side-wall centre uses outer cover + 5 mm.
    """
    inputs.validate()
    source = default_design_constants(inputs)
    if constants is not None:
        if not isinstance(constants, dict):
            raise ValueError('Design constants must be supplied as a mapping')
        unknown = set(constants) - set(source)
        if unknown:
            raise ValueError(f'Unknown design constant(s): {sorted(unknown)}')
        source.update(constants)
    values = {}
    for field, raw in source.items():
        try:
            num = float(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f'{field} must be numeric') from exc
        if not math.isfinite(num) or (num < 0 if field.endswith('_cover_mm') or field == 'haunch_start_extra_depth_m' else num <= 0):
            raise ValueError(f'{field} must be finite and nonnegative (positive for material properties)')
        values[field] = num

    def effective(total_depth: float, cover_mm: float, diameter_mm: float) -> float:
        depth = float(total_depth) - (cover_mm + diameter_mm / 2.0) / 1000.0
        if depth <= 0:
            raise ValueError('An effective depth is not positive; check thicknesses and covers')
        return depth

    top = inputs.top_slab_thickness_m
    bottom = inputs.bottom_slab_thickness_m
    wall = inputs.wall_thickness_m
    extra = values['haunch_start_extra_depth_m']
    top_bottom = values['top_slab_bottom_cover_mm']
    top_top = values['top_slab_top_cover_mm']
    bottom_cover = values['bottom_slab_cover_mm']
    outer = values['side_wall_outer_cover_mm']
    inner = values['side_wall_inner_cover_mm']
    # (section, total depth at centre, centre cover, centre nominal bar,
    #  total depth at haunch end, end cover, end bar, start cover, start bar)
    specifications = [
        ('Top Slab', top, top_bottom, 10.0, top, top_top, 12.0, top_top, 12.0),
        ('Bottom Slab', bottom, bottom_cover, 12.0, bottom, bottom_cover, 12.0, bottom_cover, 12.0),
        ('Side Wall (Earth face)', wall, outer, 12.0, wall, outer, 12.0, outer, 12.0),
        ('Side Wall (Non-Earth face)', wall, outer, 10.0, wall, inner, 10.0, inner, 10.0),
    ]
    rows = []
    for section, center_total, center_cover, center_bar, end_total, end_cover, end_bar, start_cover, start_bar in specifications:
        start_total = end_total + extra
        rows.append({
            'Section': section,
            'Total depth at center (m)': center_total,
            'Effective depth at center (m)': effective(center_total, center_cover, center_bar),
            'Total depth at haunch end (m)': end_total,
            'Effective depth at haunch end (m)': effective(end_total, end_cover, end_bar),
            'Total depth at haunch start (m)': start_total,
            'Effective depth at haunch start (m)': effective(start_total, start_cover, start_bar),
        })
    return {
        'values': values,
        'modular_ratio_from_displayed_moduli': values['es_gpa'] / values['ecm_gpa'],
        'rows': rows,
        'row_count': len(rows),
        'note': ('The reference states m=6.19, whereas Es/Ecm = 200/32 = 6.25 from the '
                 'rounded moduli. Reference 50 mm haunch-start thickening and the '
                 'nominal effective-depth offsets require engineering verification.'),
    }



# Excel Design!57-76 (valid single-cell flexural rows only) — the 16 visible
# rows in "Check for strength (ULS : Basic Combination) (Max. Bending Moment)".
# Link indices are explicit to prevent a non-applicable internal-wall row from
# being accidentally mapped to this schedule. The side-wall moment link
# selects the largest |ULS Total| from the two reference sides, as in the
# provided design-note envelope. Signs are NOT used here to redesign faces.
# (component, section, face, design-constant row, effective-depth column,
#  flexural schedule row, flexural face, moment Excel row IDs)
_ULS_FLEXURAL_LINKS = [
    ('Top Slab', 'End Supp.', 'Wall face', 0, 'Effective depth at haunch start (m)', 0, 'Outer', (215,)),
    ('Top Slab', 'End Supp.', 'Haunch end', 0, 'Effective depth at haunch end (m)', 1, 'Outer', (216,)),
    ('Top Slab', 'Mid Span', '', 0, 'Effective depth at center (m)', 2, 'Inner', (217,)),
    ('Bottom Slab', 'End Supp.', 'Wall face', 1, 'Effective depth at haunch start (m)', 3, 'Outer', (220,)),
    ('Bottom Slab', 'End Supp.', 'Haunch end', 1, 'Effective depth at haunch end (m)', 4, 'Outer', (221,)),
    ('Bottom Slab', 'Mid Span', '', 1, 'Effective depth at center (m)', 5, 'Inner', (222,)),
    ('Side Wall', 'Side wall top supp.', 'Outer face', 2, 'Effective depth at haunch start (m)', 6, 'Outer', (225, 226)),
    ('Side Wall', 'Side wall top supp.', 'Inner face', 3, 'Effective depth at haunch start (m)', 7, 'Inner', (227, 228)),
    ('Side Wall', 'Side wall top supp. haunch end', 'Outer face', 2, 'Effective depth at haunch end (m)', 8, 'Outer', (229, 230)),
    ('Side Wall', 'Side wall top supp. haunch end', 'Inner face', 3, 'Effective depth at haunch end (m)', 9, 'Inner', (231, 232)),
    ('Side Wall', 'Mid Span', 'Outer face', 2, 'Effective depth at center (m)', 10, 'Outer', (233, 234)),
    ('Side Wall', 'Mid Span', 'Inner face', 3, 'Effective depth at center (m)', 11, 'Inner', (235, 236)),
    ('Side Wall', 'Side wall bottom supp.', 'Outer face', 2, 'Effective depth at haunch start (m)', 12, 'Outer', (237, 238)),
    ('Side Wall', 'Side wall bottom supp.', 'Inner face', 3, 'Effective depth at haunch start (m)', 13, 'Inner', (239, 240)),
    ('Side Wall', 'Side wall bottom supp. haunch end', 'Outer face', 2, 'Effective depth at haunch end (m)', 14, 'Outer', (241, 242)),
    ('Side Wall', 'Side wall bottom supp. haunch end', 'Inner face', 3, 'Effective depth at haunch end (m)', 15, 'Inner', (243, 244)),
]




def calculate_live_frame_flexural_demands(inputs: CulvertInputs, frame_results: dict) -> list[dict]:
    """Recover face-specific design moments from one *experimental* FEM solution.

    Uses exact section recovery from element-end actions, including actual
    interior axle point loads and distributed loads. Positive local bending
    gives tension on the -local-y face; the section schedule maps that local
    orientation to RCC inner/outer faces. This is a physically reasoned mapping
    for this model's orientation, but is NOT independently verified against
    source STAAD axes or IRC section sign conventions.

    Each returned demand is the positive tensile-side *magnitude* at the
    specified section; an opposite-sign moment does not use the opposite-face
    steel. The left/right wall envelope is taken over both physical walls.
    """
    from .earth_pressure import TF_TO_KN
    from .frame_solver import recover_section_moment_knm

    if not inputs.supports_single_cell_model:
        raise ValueError('Live FEM flexure is only supported for a single cell')
    if not isinstance(frame_results, dict) or not frame_results.get('converged'):
        raise ValueError('A converged live FEM result is required')
    span = inputs.clear_width_m + inputs.wall_thickness_m
    rise = inputs.clear_height_m + (inputs.top_slab_thickness_m + inputs.bottom_slab_thickness_m)/2
    for key, expected in (('frame_width_m',span),('frame_height_m',rise),('strip_width_m',1.0)):
        actual=frame_results.get(key)
        if not isinstance(actual,(int,float)) or not math.isfinite(actual) or not math.isclose(actual,expected,rel_tol=0,abs_tol=1e-6):
            raise ValueError(f'Live FEM {key} does not match current geometry; rerun the analysis')

    actions=frame_results.get('member_end_actions')
    profiles=frame_results.get('member_load_profiles')
    if not isinstance(actions,list) or not actions:
        raise ValueError('Live FEM member-end moment results are missing')
    if not isinstance(profiles,list) or not profiles:
        raise ValueError('Live FEM distributed/point-load profiles are missing; update src/frame_solver.py')
    by_profile = {(r['member'],r['element']):r for r in profiles}
    groups = {}
    for row in actions:
        member=row.get('member');end=row.get('end')
        if member not in ('Top slab','Bottom slab','Left wall','Right wall') or end not in ('start','end'):
            raise ValueError('Unexpected FEM member or element-end label')
        for key in ('moment_knm','shear_kn','x_m','y_m'):
            if not isinstance(row.get(key),(float,int)) or not math.isfinite(row[key]):
                raise ValueError(f'Invalid FEM {key}')
        groups.setdefault((member,row['element']),{})[end]=row
    segments={key:[] for key in ('Top slab','Bottom slab','Left wall','Right wall')}
    for (member,eid),ends in groups.items():
        if set(ends) != {'start','end'}:
            raise ValueError(f'Incomplete FEM end actions for element {eid}')
        a,b=ends['start'],ends['end']
        axis='x_m' if 'slab' in member else 'y_m'
        x0,x1=a[axis],b[axis]
        if x1 <= x0:
            raise ValueError('FEM member element axes must increase along x or y')
        if (member,eid) not in by_profile:
            raise ValueError('A solved element is missing its load profile')
        profile=by_profile[(member,eid)]
        L=x1-x0
        if not math.isclose(profile['length_m'],L,abs_tol=1e-6,rel_tol=0):
            raise ValueError('FEM load profile does not match member segment length')
        m_end=recover_section_moment_knm(a['shear_kn'],a['moment_knm'],L,L,profile)
        if not math.isclose(m_end,b['moment_knm'],abs_tol=1e-5,rel_tol=1e-6):
            raise ValueError(f'Inconsistent FEM section recovery at element {eid}')
        segments[member].append((x0,x1,a,profile))

    def signed_section_samples(member: str, station: float) -> list[float]:
        result=[]
        for x0,x1,a,profile in segments[member]:
            if x0-1e-8 <= station <= x1+1e-8:
                x=max(0.0,min(x1-x0,station-x0))
                # Section recovery accounts for the axle and pressure profile,
                # rather than linearly interpolating end moments.
                result.append(recover_section_moment_knm(
                    a['shear_kn'],a['moment_knm'],x1-x0,x,profile))
        if not result:
            raise ValueError(f'No FEM element covers {member} at {station:.3f} m')
        return result

    slab_haunch=min(span/2,inputs.wall_thickness_m/2+inputs.haunch_width_m)
    top_haunch=min(rise/2,inputs.top_slab_thickness_m/2+inputs.haunch_height_m)
    bottom_haunch=min(rise/2,inputs.bottom_slab_thickness_m/2+inputs.haunch_height_m)
    targets=[
        [('Top slab',0.0),('Top slab',span)],
        [('Top slab',slab_haunch),('Top slab',span-slab_haunch)],
        [('Top slab',span/2)],
        [('Bottom slab',0.0),('Bottom slab',span)],
        [('Bottom slab',slab_haunch),('Bottom slab',span-slab_haunch)],
        [('Bottom slab',span/2)],
    ]
    for y in (rise,rise-top_haunch,rise/2,0.0,bottom_haunch):
        pair=[('Left wall',y),('Right wall',y)]
        targets.extend((pair,pair))
    if len(targets)!=len(_ULS_FLEXURAL_LINKS):
        raise AssertionError('Live FEM station schedule must have 16 rows')

    results=[]
    for ix,places in enumerate(targets):
        component,section,face,*_= _ULS_FLEXURAL_LINKS[ix]
        candidates=[]
        for member,station in places:
            # Sagging/positive in model axes: tension on -local-y side.
            # Slabs: top = outer / bottom = inner for the upper slab;
            # bottom=outer / top=inner for the lower slab.
            if member=='Top slab':
                tension_sign = -1 if face in ('Wall face','Haunch end') else +1
            elif member=='Bottom slab':
                tension_sign = +1 if face in ('Wall face','Haunch end') else -1
            elif member=='Left wall':
                tension_sign = -1 if face=='Outer face' else +1
            else:
                tension_sign = +1 if face=='Outer face' else -1
            for signed_knm in signed_section_samples(member,station):
                tensile_m=max(0.0,tension_sign*signed_knm)
                candidates.append((tensile_m,member,station,signed_knm))
        if not candidates:
            raise ValueError(f'No FEM candidates for ULS row {ix+1}')
        governing=max(candidates,key=lambda r:r[0])
        tensile,member,station,signed=governing
        results.append({
            'row':ix+1,'moment_tf_m_per_m':tensile/TF_TO_KN,
            'signed_moment_tf_m_per_m':signed/TF_TO_KN if tensile>0 else 0.0,
            'mapped_member':member,'station_m':station,
            'face_resolved':True,'reference_face':face,
        })
    return results


def calculate_moving_vehicle_uls_envelope(
    inputs: CulvertInputs,
    *,
    earth: EarthPressureInputs | None = None,
    braking: BrakingInputs | None = None,
    vehicle: VehicleLoadingInputs | None = None,
    combo: CombinationInputs | None = None,
    options: FrameOptions | None = None,
    step_m: float = 0.15,
    max_scenarios: int = 240,
) -> dict:
    """Scan a vehicle train over the span and envelop face-resolved FEM moments.

    Axle positions run from the first axle at the left support through the last
    axle leaving the right support. Candidate locations include all mesh/haunch
    and midspan section crossing positions plus a bounded uniform scan. These
    give a numerically sampled envelope, NOT guaranteed global maxima or an
    IRC-approved moving load / ULS combination. An engineer must verify traffic
    placement, factors, foundation, haunch stiffness, mesh and tension faces.
    """
    from dataclasses import replace
    inputs.validate()
    if not inputs.supports_single_cell_model:
        raise ValueError('Moving-load FEM requires a single-cell culvert')
    earth=earth or EarthPressureInputs(earth_cushion_m=inputs.earth_cushion_m)
    braking=braking or BrakingInputs(length_m=inputs.length_m)
    vehicle=vehicle or VehicleLoadingInputs()
    combo=combo or CombinationInputs()
    options=options or FrameOptions()
    vehicle.validate();combo.validate();options.validate()
    if vehicle.loading_type not in ('70rw','40t','custom'):
        raise ValueError('Automatic moving FEM loading is not supported for this vehicle preset')
    if not math.isfinite(step_m) or not 0.025<=step_m<=1.0:
        raise ValueError('Scan step must be between 0.025 and 1 m')
    if not isinstance(max_scenarios,int) or max_scenarios<8:
        raise ValueError('max_scenarios must be at least 8')
    span=inputs.clear_width_m+inputs.wall_thickness_m
    offsets=[0.0]
    for dist in vehicle.axle_spacings_m:
        offsets.append(offsets[-1]+dist)
    full_travel=span+offsets[-1]
    if combo.vehicle_factor==0:
        positions=[vehicle.lead_axle_position_m]
    else:
        # Ensure all axles cross both critical frame stations and sampled
        # positions in between. Include both span ends.
        haunch=min(span/2,inputs.wall_thickness_m/2+inputs.haunch_width_m)
        stations=(0.0,haunch,span/2,span-haunch,span)
        positions=sorted({round(i*full_travel/max(1,math.ceil(full_travel/step_m)),8)
                          for i in range(max(1,math.ceil(full_travel/step_m))+1)} |
                         {round(o+x,8) for o in offsets for x in stations} |
                         {round(vehicle.lead_axle_position_m,8)})
        if len(positions)>max_scenarios:
            # Keep the section crossing points and a uniformly spaced subset.
            # Explicitly report the thinning so users know it is not exact.
            anchors={round(o+x,8) for o in offsets for x in stations}
            anchors.update({0.0,round(full_travel,8),round(vehicle.lead_axle_position_m,8)})
            anchors=sorted(anchors)
            if len(anchors)>max_scenarios:
                raise ValueError('Too many axle stations for the FEM envelope cap; increase max_scenarios')
            other=[x for x in positions if x not in set(anchors)]
            remaining=max_scenarios-len(anchors)
            if remaining:
                selected=[other[min(len(other)-1, int((j+.5)*len(other)/remaining))]
                          for j in range(remaining)]
                positions=sorted(set(anchors+selected))
            else:
                positions=anchors
    best=[None]*len(_ULS_FLEXURAL_LINKS)
    for lead in positions:
        solved=run_frame_analysis(inputs,earth,braking,
                                  replace(vehicle,lead_axle_position_m=lead),combo,options)
        current=calculate_live_frame_flexural_demands(inputs,solved)
        for i,r in enumerate(current):
            if best[i] is None or r['moment_tf_m_per_m']>best[i]['moment_tf_m_per_m']+1e-11:
                best[i]={**r,'governing_lead_axle_position_m':lead}
    assert all(r is not None for r in best)
    return {
        'rows':best,
        'scenario_count':len(positions),
        'first_lead_position_m':positions[0],
        'last_lead_position_m':positions[-1],
        'vehicle_loading_type':vehicle.loading_type,
        'search_step_m':step_m,
        'load_factors':{k:getattr(combo,k) for k in (
            'concrete_dead_factor','soil_factor','surcharge_factor',
            'cushion_factor','vehicle_factor','braking_factor')},
        'assumptions':('Sampled moving-load face-specific envelope, experimental ',
                       '2D frame; not independently validated against STAAD or IRC ULS.'),
    }


def calculate_uls_flexural_strength(
    inputs: CulvertInputs,
    *,
    constants: dict | None = None,
    moment_rows: list[dict] | None = None,
    reinforcement_rows: list[dict] | None = None,
    frame_results: dict | None = None,
    live_demands: list[dict] | None = None,
) -> dict:
    """Linked 16-row ULS flexural check: Excel or live experimental FEM demands.

    Sources in the same Streamlit run:
      - Design Constants: fy, fck, Es, fctm and effective-depth positions.
      - Design Moments: absolute governing ULS moment for each linked location.
      - Flexural R/F: actual provided inner/outer bar area per metre.

    Uses src.uls_design.flexural_check for section capacity arithmetic.
    When frame_results is given, moments are sampled from the active,
    already-solved experimental 2D frame at approximate reference stations;
    the live mode does not use Excel demands. The model is NOT a validated
    ULS envelope and face assignments are not independently checked.
    Does not alter Stage 5 ULS, SLS, BBS, BOQ or detailing outputs.
    """
    dc = calculate_design_constants(inputs, constants)
    steel = calculate_flexural_reinforcement(reinforcement_rows)
    materials = dc['values']
    if frame_results is not None and live_demands is not None:
        raise ValueError('Provide either one current FEM case or a moving FEM envelope, not both')
    live_moments = (live_demands if live_demands is not None else
                    calculate_live_frame_flexural_demands(inputs, frame_results)
                    if frame_results is not None else None)
    if live_moments is not None:
        if len(live_moments) != len(_ULS_FLEXURAL_LINKS):
            raise ValueError('FEM envelope must contain exactly 16 section demands')
        for index, item in enumerate(live_moments):
            demand=item.get('moment_tf_m_per_m') if isinstance(item,dict) else None
            if not isinstance(demand,(float,int)) or not math.isfinite(demand) or demand < 0:
                raise ValueError(f'Invalid FEM envelope moment at row {index+1}')
    # Live analysis must not depend on or silently use the historical Excel table.
    if live_moments is None:
        moments = calculate_design_moment_combinations(moment_rows)
        by_excel = {r['Excel row']: r for r in moments['rows']}
        if len(by_excel) != len(moments['rows']):
            raise ValueError('Duplicated Excel row IDs in the Design Moment table')
    else:
        by_excel = {}
    if len(steel['rows']) != 16:
        raise ValueError('Flexural R/F reference schedule must contain 16 section rows')
    source_refs = reference_table()['flexure']
    if len(source_refs) != len(_ULS_FLEXURAL_LINKS):
        raise ValueError('ULS flexural reference schedule no longer matches the 16-row mapping')

    result_rows = []
    for ix, link in enumerate(_ULS_FLEXURAL_LINKS):
        component, section, face, dc_index, depth_field, steel_index, steel_face, excel_ids = link
        live = live_moments[ix] if live_moments is not None else None
        if live is None:
            try:
                candidates = [by_excel[ident] for ident in excel_ids]
            except KeyError as exc:
                raise ValueError(f'Missing linked Design Moment Excel row {exc.args[0]}') from exc
            governing = max(candidates, key=lambda x: abs(x['ULS Factor load']))
            moment = abs(governing['ULS Factor load'])
        else:
            governing = None
            moment = live['moment_tf_m_per_m']
        bar_row = steel['rows'][steel_index]
        steel_area = bar_row[f'Calculated {steel_face} (mm²/m)']
        if live_moments is None:
            # Reference mode remains an EXACT reproduction of the source depth
            # schedule, including its 170mm inner-wall centre value.
            depth_mm=dc['rows'][dc_index][depth_field]*1000.0
            depth_basis='Excel design-constant schedule'
        else:
            # Live mode uses the currently edited reinforcement bar diameter
            # and correct face clear cover. Source Design Constants gives 170mm
            # for the inner-wall midspan despite listing a 50mm inner cover;
            # correct live geometrical depth is 250-50-10/2=195mm.
            # Largest occupied bar is a conservative single-layer nominal size;
            # multilayer bar centroid/cover must be verified by the designer.
            diameters=[bar_row.get(f'{steel_face} Bar-{k} (mm)')
                       for k in range(1,4 if steel_face=='Outer' else 3)]
            positive_diameters=[float(d) for d in diameters if d is not None and float(d)>0]
            if not positive_diameters:
                raise ValueError(f'ULS row {ix+1}: no reinforcement bar exists on the {steel_face.lower()} face')
            diameter=max(positive_diameters)
            if component=='Top Slab':
                cover=materials['top_slab_top_cover_mm'] if steel_face=='Outer' else materials['top_slab_bottom_cover_mm']
            elif component=='Bottom Slab':
                cover=materials['bottom_slab_cover_mm']
            else:
                cover=materials['side_wall_outer_cover_mm'] if steel_face=='Outer' else materials['side_wall_inner_cover_mm']
            if 'haunch start' in depth_field:
                total_depth_field='Total depth at haunch start (m)'
            elif 'haunch end' in depth_field:
                total_depth_field='Total depth at haunch end (m)'
            else:
                total_depth_field='Total depth at center (m)'
            total_depth_mm=dc['rows'][dc_index][total_depth_field]*1000.0
            depth_mm=total_depth_mm-cover-diameter/2.0
            if depth_mm<=0:
                raise ValueError(f'ULS row {ix+1}: cover and bar diameter leave no effective depth')
            depth_basis='Live cover and current face bar (largest diameter as nominal outer layer)'
        if bar_row['Component'] != component:
            raise ValueError(f'Flexural R/F row {steel_index + 1} does not match {component}')
        computed = flexural_check(
            moment_tf_m_per_m=moment,
            effective_depth_mm=depth_mm,
            provided_as_mm2_per_m=steel_area,
            fck_mpa=materials['fck_mpa'],
            fy_mpa=materials['fy_mpa'],
            fctm_mpa=materials['fctm_mpa'],
            steel_modulus_gpa=materials['es_gpa'],
            # Keep rounded workbook arithmetic in Excel-reference mode,
            # but use the exact metric tonne-force conversion for the
            # physical kN-based live frame solution.
            tonne_force_to_kn=(9.80665 if live_moments is not None else 10.0),
        )
        if computed['as_required_mm2_per_m'] is None:
            steel_check = 'NOT OK'
        else:
            steel_check = ('OK' if computed['reinforcement_check'] == 'OK' and
                           computed['reinforcement_type'] == 'Under-reinforced'
                           else 'NOT OK')
        row = {
            'Component': component,
            'Section': section,
            'Face': face,
            'Xu max/d': computed['xu_max_over_d'],
            'Xu max (m)': computed['xu_max_mm'] / 1000.0,
            'R lim (MPa)': computed['r_lim_mpa'],
            'd min (mm)': computed['d_min_mm'],
            'd provided (mm)': computed['effective_depth_mm'],
            'Effective depth basis':depth_basis,
            'Depth Check': computed['depth_check'],
            'Design Moment (t-m)': moment,
            'Ast Reqd. (mm²/m)': computed['as_required_mm2_per_m'],
            'Ast Lim (mm²/m)': computed['as_limit_mm2_per_m'],
            'Ast min (mm²/m)': computed['as_min_mm2_per_m'],
            'Ast provided (mm²/m)': steel_area,
            'Steel Check': steel_check,
            'Source Design Row': source_refs[ix]['row'],
            'Governing Moment Excel Row': (governing['Excel row'] if governing else None),
            'Governing Signed MZ (t-m/m)': (live['signed_moment_tf_m_per_m'] if live else governing['ULS Factor load']),
            'Demand Source': ('Live experimental FEM' if live else 'Excel reference/edited'),
            'Live FEM Member': (live['mapped_member'] if live else None),
            'Live FEM Station (m)': (live['station_m'] if live else None),
            'Governing axle position (m)': (live.get('governing_lead_axle_position_m') if live else None),
            'Source Design Moment (t-m)': source_refs[ix]['moment_tf_m_per_m'],
            'Source d min (mm)': source_refs[ix]['excel_d_min_mm'],
            'Source As required (mm²/m)': source_refs[ix]['excel_as_required_mm2_per_m'],
        }
        result_rows.append(row)
    return {
        'rows': result_rows,
        'row_count': len(result_rows),
        'depth_ok_count': sum(row['Depth Check'] == 'OK' for row in result_rows),
        'steel_ok_count': sum(row['Steel Check'] == 'OK' for row in result_rows),
        'linked_subsections': ('Design Constants', 'Design moment due to different load combination',
                               'Flexural R/F detail of RCC Box'),
        'source_reference': ('Experimental in-app frame solver' if live_moments is not None
                             else 'Original Excel Design worksheet, ULS Basic Combination flexural rows'),
        'scope_note': (
            'Experimental FEM moments update with active geometry, vehicle and factors. '
            'Moving-load scans (where used) evaluate multiple axle positions; local '
            'moment signs distinguish inner and outer tension faces. This mapping, '
            'foundations, haunch stiffness, vehicle model and factor set require '
            'engineer verification. Separate Stage 5 checks remain unchanged.'
            if live_moments is not None else
            'Workbook-reference moments only; editing moment signs does not reassign '
            'inner/outer tension faces. Separate Stage 5 checks are unchanged.'
        ),
    }

def default_distribution_reinforcement_rows() -> list[dict]:
    """Return ten editable transverse-steel rows from the supplied screenshot."""
    return [dict(row) for row in _DISTRIBUTION_REINFORCEMENT_ROWS]


def calculate_distribution_reinforcement(rows: list[dict] | None = None) -> dict:
    """Calculate bar area per metre and compare to reference required steel.

    A_s,provided = (pi * diameter_mm**2 / 4) * (1000 / spacing_mm).
    This is an area check against entered As, not a full IRC 112 check or
    a live computation of As_required = 20% of main reinforcement.
    """
    source = default_distribution_reinforcement_rows() if rows is None else rows
    if not isinstance(source, (list, tuple)) or not source:
        raise ValueError('Distribution reinforcement rows must be a non-empty list')
    output = []
    for i, item in enumerate(source, 1):
        if not isinstance(item, dict):
            raise ValueError(f'Distribution reinforcement row {i} must be a mapping')
        row = dict(item)
        for name in ('Member', 'Surface', 'Position'):
            if name not in row or not isinstance(row[name], str):
                raise ValueError(f'Row {i}: {name} must be text')
        for name in ('Required As (mm²/m)', 'Diameter (mm)', 'Spacing (mm)'):
            try:
                raw = row[name]
                if isinstance(raw, bool):
                    raise ValueError()
                number = float(raw)
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError(f'Row {i}: {name} must be numeric') from exc
            if not math.isfinite(number):
                raise ValueError(f'Row {i}: {name} must be finite')
            if number < 0 or (name != 'Required As (mm²/m)' and number <= 0):
                raise ValueError(f'Row {i}: {name} must be ' +
                                 ('non-negative' if name == 'Required As (mm²/m)' else 'positive'))
            row[name] = number
        dia = row['Diameter (mm)']
        spacing = row['Spacing (mm)']
        provided = math.pi * dia**2 / 4.0 * (1000.0 / spacing)
        if not math.isfinite(provided):
            raise ValueError(f'Row {i}: calculated steel is not finite')
        row['Provided As (mm²/m)'] = provided
        row['Check'] = 'OK' if provided + 1e-9 >= row['Required As (mm²/m)'] else 'NOT OK'
        output.append(row)
    return {
        'rows': output,
        'row_count': len(output),
        'pass_count': sum(row['Check'] == 'OK' for row in output),
        'unit': 'mm²/m',
        'reference': 'Supplied Distribution Reinforcement — Transverse steel table',
        'code_reference': 'IRC:112-2020, Clause 16.6.1.1(3)',
        'note': 'Secondary transverse steel in one-way slabs: at least 20% of main reinforcement',
        'status': 'Provided-area comparison only; not full IRC or live ULS verification',
    }

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
    bbs_bar_rows: list[dict] | None = None,
    bbs_allowance_pct: float = 5.0,
    boq_quantity_overrides: dict[str, float] | None = None,
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
        "dead_load_sidl": calculate_dead_load_sidl(inputs),
        "spring_constants": calculate_spring_constants(inputs),
        "earth_pressure_reference": calculate_earth_pressure_table(inputs, earth),
        "earth_pressure": earth_results,
        "braking_forces": braking_results,
        "braking_force_reference": calculate_braking_force_table(inputs, braking),
        "design_moment_combinations": calculate_design_moment_combinations(),
        "design_shear_combinations": calculate_design_shear_combinations(),
        "flexural_reinforcement": calculate_flexural_reinforcement(),
        "distribution_reinforcement": calculate_distribution_reinforcement(),
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
            "BBS/BOQ trial quantities available; construction-ready reinforcement details remain pending",
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
            "BBS/BOQ trial quantities available; construction-ready reinforcement details remain pending",
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
    # Stage 7 BBS is editable bar-schedule arithmetic; BOQ only recalculates the
    # basic culvert concrete quantities. Other BOQ rows stay reference/manual.
    result["bbs"] = calculate_bbs(bbs_bar_rows, bbs_allowance_pct)
    result["boq"] = calculate_boq(inputs, result["bbs"], boq_quantity_overrides)
    result["reinforcement_review"] = build_reinforcement_review(inputs, result["bbs"])
    result["limitations"].append(
        "Stage 7 BBS uses editable cached BBS (2) mark lengths/counts/diameters; "
        "bar shapes, cut lengths, bends, laps and revised geometry require engineer approval."
    )
    result["limitations"].append(
        "Stage 7 BOQ dynamically updates core culvert concrete only; other line quantities "
        "are original Excel values or manually entered, not re-estimated from dimensions."
    )
    result["limitations"].append(
        "Stage 8 drawing sheets reproduce selected bar schedules and typical layouts from the supplied "
        "reinforcement PDF. Missing diameters, h3, bend/lap lengths, full placing geometry and "
        "fabrication detailing require engineer checking. NOT for construction."
    )
    if structural_records:
        result["limitations"].append(
            "Imported member forces have not been checked for STAAD model validity, "
            "sign convention, load combinations or structural safety"
        )
    return result
