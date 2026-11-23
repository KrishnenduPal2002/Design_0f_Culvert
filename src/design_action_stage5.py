"""Stage 5: independent workbook load-case actions and source-definition audit.

Standalone structural diagnostics. NEVER writes into Streamlit tables, and
NEVER uses the workbook's answer moments/shears as computed model forces.
The source 502 intensities below are comparison targets only.
"""
from __future__ import annotations

from dataclasses import replace
import csv
import io
import math

from .frame_solver import FrameOptions, run_frame_analysis
from .load_combinations import CombinationInputs, workbook_uls_basic_combinations
from .design_action_stations import reference_stations, sample_solved_case
from .earth_pressure import TF_TO_KN

# SOURCE, not model input: Staad Input(ULS)!A304:D311, rows 304–311.
# Row 311 lists "208  208  UNI GY  -0.7213114754". It is not yet
# established whether the STAAD reader applies that duplicate identifier twice.
SOURCE_502_TOP_MEMBER_TFM = (
    -1.4789915966386555, -1.256855575868373, -0.9695103208052107,
    -0.8534885301657857, -0.7831945629468358, -0.742429837312482,
    -0.7235984425508503, -0.7213114754098361, -0.7235984425508503,
    -0.742429837312482, -0.7831945629468358, -0.8534885301657857,
    -0.9695103208052107, -1.256855575868373, -1.4789915966386555,
)
SOURCE_503_TOP_TFM = -0.11  # Source: Staad Input(ULS)!D314
LOAD_CASE_COMPONENTS = {
    501: 'concrete_dead', 502: 'additional_sidl', 503: 'wearing_course',
    504: 'soil', 505: 'cushion', 506: 'surcharge', 507: 'surcharge',
}


def _one_case_combo(case_number: int) -> CombinationInputs:
    if case_number not in LOAD_CASE_COMPONENTS:
        raise ValueError(f'Unknown independent source case {case_number}')
    flags = dict(concrete_dead_factor=0.0, additional_sidl_factor=0.0,
                 wearing_course_factor=0.0, soil_factor=0.0,
                 cushion_factor=0.0, surcharge_factor=0.0,
                 surcharge_side='none', vehicle_factor=0.0, braking_factor=0.0)
    factor_by_name = {
        'concrete_dead':'concrete_dead_factor', 'additional_sidl':'additional_sidl_factor',
        'wearing_course':'wearing_course_factor', 'soil':'soil_factor',
        'cushion':'cushion_factor', 'surcharge':'surcharge_factor',
    }
    component = LOAD_CASE_COMPONENTS[case_number]
    flags[factor_by_name[component]] = 1.0
    if case_number in (506, 507):
        flags['surcharge_side'] = 'left' if case_number == 506 else 'right'
    return CombinationInputs(name=f'Independent source {case_number} {component}', **flags)


def solve_independent_cases(inputs, *, earth=None, braking=None, vehicle=None,
                            options=None, solver=run_frame_analysis):
    """Solve seven unfactored original 501–507 cases separately.

    The decomposition requires a linear spring response; reject nonlinear
    compression-only supports instead of reporting invalid superposition.
    A calling solver must return a converged result with member force profiles.
    """
    options = FrameOptions() if options is None else options
    if options.compression_only_springs:
        raise ValueError('Independent action superposition requires bilateral (linear) springs')
    output = {}
    for case in range(501, 508):
        combo = _one_case_combo(case)
        result = solver(inputs, earth, braking, vehicle, combo, options)
        if not isinstance(result, dict) or not result.get('converged'):
            raise ValueError(f'Independent source case {case} did not converge')
        if not result.get('member_end_actions') or not result.get('member_load_profiles'):
            raise ValueError(f'Source case {case} missing signed member actions / load profiles')
        output[case] = result
    return output


def _reference_geometry(inputs) -> bool:
    expected = {
        'clear_width_m':3., 'clear_height_m':3., 'wall_thickness_m':.25,
        'top_slab_thickness_m':.25, 'bottom_slab_thickness_m':.275,
        'earth_cushion_m':.3, 'ms_railing_width_m':.5,
        'concrete_density_tf_m3':2.5, 'wearing_coat_thickness_m':.05,
        'wearing_course_density_tf_m3':2.2, 'haunch_width_m':.15,
        'haunch_count':4,
    }
    return all(math.isclose(float(getattr(inputs, key)), val, abs_tol=1e-9)
               for key, val in expected.items())


def audit_workbook_permanent_member_loads(inputs, independent, *, tolerance_tf_m=1e-5):
    """Compare source 502 & 503 member intensities, without altering model loads.

    Only valid for exact reference geometry and the original 15 top members;
    other geometries must not be compared to the 1x3x3 fixed target values.
    The result identifies a mismatch, NOT whether STAAD will apply a repeated
    member number twice in a single load command.
    """
    if not _reference_geometry(inputs):
        raise ValueError('Source member-load comparison applies only to original 1x3x3 input')
    records = []
    for case, reference in ((502, SOURCE_502_TOP_MEMBER_TFM),
                            (503, (SOURCE_503_TOP_TFM,)*15)):
        if case not in independent:
            raise ValueError(f'Missing independent source case {case}')
        profiles = [p for p in independent[case]['member_load_profiles']
                    if p['member'] == 'Top slab']
        if len(profiles) != 15:
            raise ValueError(f'Expected 15 top-slab members, got {len(profiles)}; mesh is not source-compatible')
        for ordinal, (profile, expected_tf_m) in enumerate(zip(profiles, reference)):
            start = float(profile['py_start_kn_per_m']) / TF_TO_KN
            end = float(profile['py_end_kn_per_m']) / TF_TO_KN
            if not all(math.isfinite(x) for x in (start, end)):
                raise ValueError('Nonfinite member load')
            if abs(start-end) > tolerance_tf_m:
                raise ValueError('Reference member load is uniform; solver profile is not')
            diff = start-expected_tf_m
            records.append({
                'Source load case':case, 'Workbook top member':201+ordinal,
                'Source intensity (tf/m)':expected_tf_m,
                'FEM applied intensity (tf/m)':start,
                'Difference (tf/m)':diff,
                'Status':'PASS' if abs(diff)<=tolerance_tf_m else 'MISMATCH',
                'Note':('Workbook lists member 208 twice in one load-list row; '
                        'verify STAAD duplicate-ID handling before changing factor'
                        if case == 502 and ordinal == 7 else ''),
            })
    return records


def check_uls_against_independent_cases(inputs, independent, *, earth=None,
                                       braking=None, vehicle=None, options=None,
                                       solver=run_frame_analysis,
                                       atol=1e-6, rtol=1e-8):
    """Independently reconstruct six cases by superposition of 501–507.

    Compares physical section actions in signed kN units, not envelopes or
    magnitudes. This checks the *same model*'s arithmetic and does not certify
    agreement with the design-note member-end results.
    """
    if set(independent) != set(range(501,508)):
        raise ValueError('Exactly seven independent cases 501–507 are required')
    options = FrameOptions() if options is None else options
    if options.compression_only_springs:
        raise ValueError('ULS superposition requires bilateral springs')
    stations = reference_stations(inputs)
    samples = {case:{r['Station key']:r for r in sample_solved_case(solved, stations)}
               for case, solved in independent.items()}
    fields = ('Signed moment (kN.m/m)', 'Signed shear (kN/m)')
    cases = workbook_uls_basic_combinations()
    output=[]
    for number, combo in zip(range(1001,1007), cases):
        actual = solver(inputs, earth, braking, vehicle, combo, options)
        if not actual.get('converged'):
            raise ValueError(f'ULS case {number} did not converge')
        actual_samples = sample_solved_case(actual, stations)
        factors = {
            501:combo.concrete_dead_factor,
            502:combo.effective_additional_sidl_factor,
            503:combo.effective_wearing_course_factor,
            504:combo.soil_factor,
            505:combo.cushion_factor,
            506:combo.surcharge_factor if combo.surcharge_side in ('left','both') else 0.,
            507:combo.surcharge_factor if combo.surcharge_side in ('right','both') else 0.,
        }
        for row in actual_samples:
            key=row['Station key']
            for field in fields:
                direct=float(row[field])
                prediction=sum(factors[case]*float(samples[case][key][field])
                               for case in factors)
                if not (math.isfinite(direct) and math.isfinite(prediction)):
                    raise ValueError('Nonfinite ULS action in independent comparison')
                delta=direct-prediction
                tol=atol+rtol*max(abs(direct),abs(prediction))
                output.append({
                    'Combination':number, 'Station key':key, 'Member':row['Member'],
                    'Quantity':field, 'Direct FEM action':direct,
                    'Independent 501-507 reconstruction':prediction,
                    'Difference':delta, 'Tolerance':tol,
                    'Status':'PASS' if abs(delta)<=tol else 'REVIEW',
                })
    return output


def csv_text(rows):
    if not rows: raise ValueError('No rows to export')
    out=io.StringIO(newline='')
    writer=csv.DictWriter(out,fieldnames=list(rows[0]))
    writer.writeheader(); writer.writerows(rows)
    return out.getvalue()


def summary(rows):
    if not rows: raise ValueError('No rows to summarize')
    counts={'PASS':0,'MISMATCH':0,'REVIEW':0}
    for row in rows:
        label=row['Status']
        counts[label]=counts.get(label,0)+1
    return {'rows':len(rows),**counts,
            'max_abs_diff':max(abs(float(r.get('Difference (tf/m)',r.get('Difference',0)))) for r in rows)}
