"""Stage 3: reproducible axle pattern, impact and effective-width DISPERSION.

Source: `Design of Culvert 1x3x3 (Fill=0.3m).xlsx`, Input sheet:
E1/E3/E2, J5, T10, N11:O30, C15, J13, B18:J25, T12, T13.

This reproduces specific SPREADSHEET calculations for an editable 70R(W)
wheel pattern. It is *not* an independently validated IRC 6 loading model.
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

    def validate(self) -> None:
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
    impact = params.impact_override if params.impact_override is not None else excel_impact(span)
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
            if raw/2 < params.avail_edge_width_m and raw < params.avail_centre_width_m:
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
        'effective_span_m':span, 'width_span_ratio':ratio, 'alpha':alpha,
        'tyre_and_fill_spread_base_width_m':b1,'impact_factor':impact,
        'axles':rows, 'active_axle_count':len(active_rows),
        'sum_active_wheel_forces_tf':sum(r['wheel_load_tf'] for r in active_rows),
        'sum_active_strip_load_intensities_tf_m':sum(r['load_per_unit_width_tf_m'] for r in active_rows),
        'max_active_strip_load_intensity_tf_m':max((r['load_per_unit_width_tf_m'] for r in active_rows),default=0),
        'reference_checks':{
            'source':'Excel Input!C14,C15,J13,J15,I19,J19 and wheel positions C19:D25',
            'lead_at_0m_expected_tf_m':17*1.25/3.12,
            'lead_at_0_25m_expected_tf_m':17*1.25/4.09,
        },
        'status':('Workbook-derived, reference-only effective-width calculation. '
                  'Not a complete code-verified loading model; does not compute actions or design safety.'),
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
