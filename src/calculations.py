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
