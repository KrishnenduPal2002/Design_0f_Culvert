"""Stage 3: workbook-reference vehicle load stations, impact and dispersion.

Source: `Design of Culvert 1x3x3 (Fill=0.3m).xlsx`, Input sheet:
E1/E3/E2, J5, T10, N11:O30, C15, J13, B18:J25, T12, T13.

This reproduces selected SPREADSHEET calculations for 70R(W), 40T Bogie,
70R tracked equivalent load stations, 385T special vehicle and Custom Pattern. It is *not* an independently validated IRC 6 loading model.
The wheel contributions are in tonne-force (tf), not kilograms, and
P / effective_width is the spreadsheet's LOAD PER UNIT WIDTH. These
components must NOT be treated as analysed moments, ULS reactions, or
member design forces without the structural engineer's approval.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import math

TF_TO_KN = 9.80665
# Source: Input!N11:O30. The last source ratio is 20 (not 2.0).
ALPHA_TABLE = (
    (.1, .4), (.2, .8), (.3, 1.16), (.4, 1.44), (.5, 1.68),
    (.6, 1.84), (.7, 1.96), (.8, 2.08), (.9, 2.16), (1., 2.24),
    (1.1, 2.28), (1.2, 2.36), (1.3, 2.40), (1.4, 2.48),
    (1.5, 2.48), (1.6, 2.52), (1.7, 2.56), (1.8, 2.60),
    (1.9, 2.60), (20., 2.60),
)
# Load/spacing pairs from workbook Input!C19:C25 and D19:D25;
# positions are offsets BEHIND the lead axle in metres.
REFERENCE_70RW_WHEELS_TF = (17., 17., 17., 17., 12., 12., 8.)
REFERENCE_70RW_SPACINGS_M = (1.37, 3.05, 1.37, 2.13, 1.52, 3.96)

# Additional patterns recovered from the source workbook Input worksheet:
# 40t bogie: Input!C590 and moving-load DIST (D591) = -1.22 m.
# The second 20 tf station is a repeated load in the moving-load pattern.
REFERENCE_40T_BOGIE_TF = (20.0, 20.0)
REFERENCE_40T_SPACINGS_M = (1.22,)
# 70R tracked: source uses five *equivalent* 14 tf load stations
# with 0.914m spacing (Input!D970:D973). It also has a 4.57m track
# length and 1.1425m listed elsewhere; use the actual moving-table steps.
# NOT five physical road wheels.
REFERENCE_70RT_EQUIVALENT_TF = (14.0,) * 5
REFERENCE_70RT_SPACINGS_M = (0.914,) * 4
# 385t SV: 20 trailer axles at 18 tf, plus prime mover axles
# at 9.5, 9.5 and 6 tf. From Input!1381 onward and IRC:6 Fig. 6.
REFERENCE_385T_AXLE_TF = (18.0,) * 20 + (9.5, 9.5, 6.0)
REFERENCE_385T_SPACINGS_M = (1.5,) * 19 + (5.389, 1.37, 3.2)
LOADING_TYPES = ('70rw', '40t', '70rt', 'sv385', 'custom')


def tracked_impact(span_m: float) -> float:
    """Workbook Input!J961 impact for 70R tracked equivalent loading."""
    if not math.isfinite(span_m) or span_m <= 0:
        raise ValueError('Span must be positive')
    if span_m < 5:
        return 1.25
    if span_m > 9:
        return 1.1
    return 1.1 + (1.25 - 1.1) * (9 - span_m) / 4


def lookup_alpha(width_to_span: float) -> float:
    """Linear interpolation as in Excel FORECAST on the bounding table rows."""
    if not math.isfinite(width_to_span) or width_to_span < ALPHA_TABLE[0][0] or width_to_span > ALPHA_TABLE[-1][0]:
        raise ValueError('b/lo falls outside the workbook alpha lookup table [0.1, 20]')
    for (x0,y0), (x1,y1) in zip(ALPHA_TABLE, ALPHA_TABLE[1:]):
        if x0 <= width_to_span <= x1:
            return y0 + (width_to_span-x0)*(y1-y0)/(x1-x0)
    return ALPHA_TABLE[-1][1]


def excel_impact(effective_span_m: float) -> float:
    """Workbook Input!J13: IF(E3<12,1.25,1+4.5/(6+E3))."""
    if effective_span_m <= 0 or not math.isfinite(effective_span_m):
        raise ValueError('Effective span must be positive')
    return 1.25 if effective_span_m < 12 else 1 + 4.5/(6+effective_span_m)


@dataclass(frozen=True)
class VehicleLoadingInputs:
    # Relative to the first axle; full reference vehicle will normally
    # exceed a 3.25m span, so only some wheels lie on the slab at one time.
    wheel_loads_tf: tuple[float, ...] = REFERENCE_70RW_WHEELS_TF
    axle_spacings_m: tuple[float, ...] = REFERENCE_70RW_SPACINGS_M
    lead_axle_position_m: float = 0.25
    tyre_transverse_width_m: float = 0.86
    wearing_coat_m: float = 0.05
    fill_spread_each_side_per_m: float = 1.0  # Source Input!C15: +2*(wearing + fill)
    # Edge availability in workbook Input!T12,T13. Fixed geometric reference,
    # not derived from the varying app geometry. Engineer must verify if edited.
    avail_edge_width_m: float = 2.13
    avail_centre_width_m: float = 1.93
    impact_override: float | None = None
    case_name: str = 'Class 70R(W) (workbook reference axle pattern)'
    loading_type: str = '70rw'
    # Special vehicle has four transverse tyre groups in the source worksheet;
    # the default availability terms are Input!U1375:U1376.
    sv_gap1_m: float = 0.75
    sv_gap2_m: float = 1.05

    def validate(self) -> None:
        if self.loading_type not in LOADING_TYPES:
            raise ValueError(f'Unknown loading_type: {self.loading_type}')
        if not math.isfinite(self.sv_gap1_m) or self.sv_gap1_m <= 0 or not math.isfinite(self.sv_gap2_m) or self.sv_gap2_m <= 0:
            raise ValueError('SV transverse gaps must be positive')
        if not self.wheel_loads_tf or len(self.wheel_loads_tf) != len(self.axle_spacings_m)+1:
            raise ValueError('Provide N wheel loads and N-1 positive axle spacings')
        if len(self.wheel_loads_tf) > 30:
            raise ValueError('Maximum 30 reference wheel positions')
        for v in (*self.wheel_loads_tf,*self.axle_spacings_m):
            if not isinstance(v,(int,float)) or not math.isfinite(v) or v <= 0:
                raise ValueError('Wheel loads and spacings must be positive finite numbers')
        for key in ('lead_axle_position_m','tyre_transverse_width_m','wearing_coat_m',
                    'fill_spread_each_side_per_m','avail_edge_width_m','avail_centre_width_m'):
            val=getattr(self,key)
            if not math.isfinite(val) or (val<0 if key in ('lead_axle_position_m','wearing_coat_m') else val<=0):
                raise ValueError(f'{key} is invalid')
        if self.impact_override is not None and (not math.isfinite(self.impact_override) or self.impact_override<1):
            raise ValueError('Manual impact factor must be >= 1')


def calculate_axle_dispersion(culvert, params: VehicleLoadingInputs) -> dict:
    """Reproduce the workbook Input effective-width arithmetic for one position.

    The leading axle position x=0 is the support reference used in workbook.
    Wheels before x=0 or after the effective span are excluded.
    No vehicle code checking or structural effects are performed.
    """
    culvert.validate(); params.validate()
    span = culvert.clear_width_m + culvert.wall_thickness_m  # Input!E3 one-cell rule
    ratio = culvert.length_m / span                    # Input!C14 = E2/E3
    alpha = lookup_alpha(ratio)                         # Input!J15
    b1 = params.tyre_transverse_width_m + 2*params.fill_spread_each_side_per_m*(
        params.wearing_coat_m + culvert.earth_cushion_m)  # Input!C15
    impact = (params.impact_override if params.impact_override is not None else
              (tracked_impact(span) if params.loading_type == '70rt'
               else 1.0 if params.loading_type == 'sv385'
               else excel_impact(span)))
    cumulative_offset=0.0
    rows=[]
    for i,p in enumerate(params.wheel_loads_tf):
        if i:
            cumulative_offset += params.axle_spacings_m[i-1]
        x = params.lead_axle_position_m - cumulative_offset
        active = (0 <= x <= span)
        row={'wheel': i+1, 'wheel_load_tf': p, 'distance_from_left_support_m': x,
             'on_span':active, 'effective_width_m':None, 'base_contact_width_m': b1,
             'effective_width_increment_m':None, 'final_effective_width_m':None,
             'impact_factor':impact, 'load_per_unit_width_tf_m':None,
             'load_per_unit_width_kn_m':None}
        if active:
            a=min(x, span-x)  # Input!E19: nearest support for one cell
            beff=alpha*a*(1-a/span) # Input!F19
            raw=b1+beff # Input!H19
            # Input!I19: exact 3-case availability logic. Width represents
            # the full effective axle-width (2 wheel strips in spreadsheet).
            if params.loading_type == 'sv385':
                # Excel Input!I1383: four transverse tyre-group width rule.
                edge, gap1, gap2 = params.avail_edge_width_m, params.sv_gap1_m, params.sv_gap2_m
                if raw / 2 < edge and raw < gap1:
                    final = raw * 4
                elif raw / 2 < edge and raw < gap2:
                    final = raw / 2 + gap1 + raw + gap1
                elif raw / 2 < edge and raw > gap2:
                    final = raw / 2 + gap1 + gap2 + gap1 + raw / 2
                else:
                    final = edge + gap1 + gap2 + gap1 + edge
            elif raw/2 < params.avail_edge_width_m and raw < params.avail_centre_width_m:
                final=raw*2
            elif raw/2 < params.avail_edge_width_m and raw >= params.avail_centre_width_m:
                final=params.avail_centre_width_m+raw
            else:
                final=params.avail_centre_width_m+params.avail_edge_width_m+raw/2
            strip_load=p*impact/final  # Input!J19
            row.update({'distance_to_nearest_support_m':a,
                'effective_width_increment_m':beff,'effective_width_m':raw,
                'final_effective_width_m':final,
                'load_per_unit_width_tf_m':strip_load,
                'load_per_unit_width_kn_m':strip_load*TF_TO_KN})
        rows.append(row)
    active_rows=[r for r in rows if r['on_span']]
    return {
        'parameters':asdict(params),
        'loading_type':params.loading_type,
        'vehicle_gross_pattern_load_tf':sum(params.wheel_loads_tf),
        'is_equivalent_track_discretisation':params.loading_type == '70rt',
        'frame_application_approved':False,
        'impact_source':('Explicit user override, requires approval' if params.impact_override is not None
                         else 'Excel J961: tracked impact' if params.loading_type == '70rt'
                         else 'IRC special vehicle: no dynamic impact applied' if params.loading_type == 'sv385'
                         else 'Excel wheel/bogie impact function'),
        'effective_span_m':span, 'width_span_ratio':ratio, 'alpha':alpha,
        'tyre_and_fill_spread_base_width_m':b1,'impact_factor':impact,
        'axles':rows, 'active_axle_count':len(active_rows),
        'sum_active_wheel_forces_tf':sum(r['wheel_load_tf'] for r in active_rows),
        'sum_active_strip_load_intensities_tf_m':sum(r['load_per_unit_width_tf_m'] for r in active_rows),
        'max_active_strip_load_intensity_tf_m':max((r['load_per_unit_width_tf_m'] for r in active_rows),default=0),
        'reference_checks':{
            'source':'Excel Input!C14,C15,J13,J15; moving load sections 70R(W):19+, 40T:590+, 70R(T):969+, 385T:1383+',
            'reference_values_apply_to_unchanged_geometry_only': True,
        },
        'status':('Reference Excel geometry/dispersion arithmetic for ' + params.case_name + '. '
                  + ('Five loads represent an equivalent TRACK discretisation, not physical axles. '
                     if params.loading_type == '70rt' else '')
                  + ('385t special vehicle: NO ordinary impact or braking; special transverse width rule. '
                     if params.loading_type == 'sv385' else '')
                  + 'Loads are NOT verified for real roadway placement, member analysis or structural design.'),
    }


def scan_lead_axle_positions(culvert, params: VehicleLoadingInputs, step_m: float = 0.25) -> dict:
    """Screen wheel configurations along the local reference span.

    This samples positions, in the style of the workbook's stepped Input
    load arrangements, but does NOT find a structural analysis envelope.
    The sum of strip-load intensities is only a SCREENING indicator.
    """
    if not math.isfinite(step_m) or step_m<=0:
        raise ValueError('Position step must be positive')
    params.validate()
    span=culvert.clear_width_m+culvert.wall_thickness_m
    travel=span+sum(params.axle_spacings_m)
    if travel/step_m>1500:
        raise ValueError('Too many sampling positions; increase step_m')
    samples=[]
    i=0
    while True:
        x=min(i*step_m,travel)
        adjusted=VehicleLoadingInputs(**{**asdict(params),'lead_axle_position_m':x})
        p=calculate_axle_dispersion(culvert,adjusted)
        samples.append({'lead_position_m':x,
                        'active_wheels':p['active_axle_count'],
                        'sum_strip_intensities_tf_m':p['sum_active_strip_load_intensities_tf_m'],
                        'largest_single_strip_intensity_tf_m':p['max_active_strip_load_intensity_tf_m']})
        if x>=travel:break
        i+=1
    largest=max(samples,key=lambda r:r['sum_strip_intensities_tf_m'])
    return {
        'position_step_m':step_m, 'positions_checked':len(samples),
        'samples':samples,
        'peak_sampled_position_m':largest['lead_position_m'],
        'peak_sampled_sum_strip_intensities_tf_m':largest['sum_strip_intensities_tf_m'],
        'status':'Numerical *screening* sweep; NOT moment/shear/reaction envelope or verified vehicle-placement search',
    }


def vehicle_preset(loading_type: str) -> VehicleLoadingInputs:
    """Return *editable* reference inputs based on the original Excel Input sheet.

    Custom remains a fully editable pattern. These are *reference* datasets,
    not proof of code compliance or safe carriageway placement.
    """
    if loading_type == '70rw':
        return VehicleLoadingInputs()
    if loading_type == '40t':
        return VehicleLoadingInputs(
            wheel_loads_tf=REFERENCE_40T_BOGIE_TF,
            axle_spacings_m=REFERENCE_40T_SPACINGS_M,
            tyre_transverse_width_m=.86,
            lead_axle_position_m=.25,
            case_name='Class 40T Bogie (Excel Input!B586)', loading_type='40t',
        )
    if loading_type == '70rt':
        return VehicleLoadingInputs(
            wheel_loads_tf=REFERENCE_70RT_EQUIVALENT_TF,
            axle_spacings_m=REFERENCE_70RT_SPACINGS_M,
            tyre_transverse_width_m=.84,
            avail_edge_width_m=2.12,
            avail_centre_width_m=1.95,
            lead_axle_position_m=.25,
            case_name='Class 70R Tracked (5 equivalent 14tf stations, Excel B961)',
            loading_type='70rt',
        )
    if loading_type == 'sv385':
        return VehicleLoadingInputs(
            wheel_loads_tf=REFERENCE_385T_AXLE_TF,
            axle_spacings_m=REFERENCE_385T_SPACINGS_M,
            lead_axle_position_m=1.0,
            tyre_transverse_width_m=.45,
            avail_edge_width_m=4.425,
            avail_centre_width_m=.75,
            impact_override=None,
            case_name='385T Special Vehicle (20-axle trailer + prime mover, Excel B1375)',
            loading_type='sv385',
        )
    if loading_type == 'custom':
        return VehicleLoadingInputs(case_name='Custom engineer-supplied load pattern', loading_type='custom')
    raise ValueError(f'Unsupported vehicle loading type: {loading_type}')
