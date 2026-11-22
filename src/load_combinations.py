"""User-configured *component-wise* load factor sets.

No factor in this file is asserted to be an IRC-approved load combination.
Different dimensional/structural effects must NOT be added. Instead each
component is reported separately after multiplication by its own factor.
No response of a structural frame/member is computed here.
"""
from dataclasses import dataclass, asdict
import math

@dataclass(frozen=True)
class CombinationInputs:
    name: str = 'User-configured illustration (NOT approved)'
    concrete_dead_factor: float = 1.0
    # Optional separate STAAD 502 factor. None preserves the existing UI setting.
    additional_sidl_factor: float | None = None
    # None: use the user-selected permanent dead factor (the existing Streamlit
    # form has no separate wearing-course factor field). Explicit numbers
    # override this, as needed for STAAD 1001–1006 where WC = 1.75 and DL = 1.35.
    wearing_course_factor: float | None = None
    # "both" preserves previous behavior; STAAD 506/507 act on one wall only.
    surcharge_side: str = 'both' 
    soil_factor: float = 1.0
    surcharge_factor: float = 1.0
    cushion_factor: float = 1.0
    vehicle_factor: float = 1.0
    braking_factor: float = 1.0
    @property
    def effective_additional_sidl_factor(self) -> float:
        return (self.concrete_dead_factor if self.additional_sidl_factor is None
                else self.additional_sidl_factor)

    @property
    def effective_wearing_course_factor(self) -> float:
        return (self.concrete_dead_factor if self.wearing_course_factor is None
                else self.wearing_course_factor)

    def validate(self):
        if self.surcharge_side not in ('none', 'left', 'right', 'both'):
            raise ValueError('surcharge_side must be none, left, right, or both')
        for key,val in asdict(self).items():
            if key in ('name', 'surcharge_side'):
                continue
            if key in ('wearing_course_factor', 'additional_sidl_factor') and val is None:
                continue
            if isinstance(val,bool) or not isinstance(val,(int,float)) or not math.isfinite(val) or val<0:
                raise ValueError(f'{key} must be a nonnegative finite number')


def calculate_component_factors(result:dict, combo:CombinationInputs) -> dict:
    combo.validate()
    earth=result['earth_pressure']['pressure_at_bottom_slab_centreline']
    axle=result['vehicle_axle_loading']
    brake=result['braking_forces']['cases']
    dead=result['preliminary_self_weight']['concrete_self_weight_kn']
    length=result['inputs']['length_m']
    # Separate vertical/pressure/braking components deliberately.
    return {
        'name':combo.name,'factors':asdict(combo),
        'components':[
            {'component':'Box concrete self-weight', 'original':dead/length,'factor':combo.concrete_dead_factor,'factored':dead/length*combo.concrete_dead_factor,'unit':'kN/m over culvert length'},
            {'component':'Earth pressure, reference bottom', 'original':earth['earth_pressure_t_m2'],'factor':combo.soil_factor,'factored':earth['earth_pressure_t_m2']*combo.soil_factor,'unit':'tf/m² lateral'},
            {'component':'Live-load surcharge pressure', 'original':earth['surcharge_pressure_t_m2'],'factor':combo.surcharge_factor,'factored':earth['surcharge_pressure_t_m2']*combo.surcharge_factor,'unit':'tf/m² lateral'},
            {'component':'Cushion pressure (separate)', 'original':earth['cushion_pressure_t_m2'],'factor':combo.cushion_factor,'factored':earth['cushion_pressure_t_m2']*combo.cushion_factor,'unit':'tf/m² lateral'},
            {'component':'Maximum active axle strip-load intensity', 'original':axle['max_active_strip_load_intensity_tf_m'],'factor':combo.vehicle_factor,'factored':axle['max_active_strip_load_intensity_tf_m']*combo.vehicle_factor,'unit':'tf/m reference strip'},
            *({'component':'Braking force per metre — '+row['Case'], 'original':row['Per-metre force (tf/m)'], 'factor':combo.braking_factor,'factored':row['Per-metre force (tf/m)']*combo.braking_factor,'unit':'tf/m horizontal'} for row in brake),
        ],
        'status':('Component-wise arithmetic with USER-SUPPLIED factors. Not a code-approved ULS/SLS '
                  'combination; cannot combine unlike units or infer member moments, shears, safety.'),
    }


def workbook_uls_basic_combinations() -> tuple[CombinationInputs, ...]:
    """Six explicit factor sets from source workbook `Staad Input(ULS)`.

    Cell A376:A387 / STAAD 1001..1006 (worksheet rows 376–387):
      501 SELFWEIGHT: 1.35; 502 SIDL: 1.35; 503 wearing coat: 1.75;
      504 earth pressure: 1.50; 505 earth cushion: 1.35;
      506 LHS surcharge / 507 RHS surcharge: 1.20.

    These are *reference load-case definitions*, NOT a certified reproduction:
    the current frame model still omits load case 502's separately distributed
    SIDL, and its geometry, supports and member stiffness differ from STAAD.
    Workbook LLS is lateral surcharge, NOT vertical moving wheel/axle traffic.
    Temperature loads 508–511 are in later reference cases, not 1001..1006.
    """
    cases = (
        (1001, 0.0, 0.0, 0.0, 'none'),
        (1002, 1.5, 0.0, 0.0, 'none'),
        (1003, 1.5, 1.35, 0.0, 'none'),
        (1004, 1.5, 1.35, 1.2, 'left'),
        (1005, 1.5, 1.35, 1.2, 'right'),
        (1006, 1.5, 1.35, 1.2, 'both'),
    )
    return tuple(CombinationInputs(
        name=f'Workbook ULS {number} (PARTIAL model; missing SIDL)',
        concrete_dead_factor=1.35,
        wearing_course_factor=1.75,
        soil_factor=soil,
        cushion_factor=cushion,
        surcharge_factor=surcharge,
        surcharge_side=side,
        vehicle_factor=0.0,
        braking_factor=0.0,
    ) for number, soil, cushion, surcharge, side in cases)


def workbook_uls_combination_definitions() -> tuple[dict, ...]:
    """Exact STAAD 1001–1030 *load-case definitions* from the supplied workbook.

    Source: ``Staad Input(ULS)``, rows 376–444. Entries 1007–1030
    add one temperature load (508–511) at factor 0.9.

    The supplied spreadsheet's load cases 508 and 509 have ``TEMP 0``;
    load cases 510 and 511 have ``FX 0, MZ 0`` at both joints. Therefore
    these 24 additional combinations contribute *zero thermal demand in the
    given reference workbook*. Do not generalize this to other designs.

    This returns source definitions, not an approved combination envelope.
    It deliberately does NOT pass temperature effects to the existing FEM:
    that model has no verified temperature loading implementation.
    """
    base = (
        (0.0, 0.0, ()),
        (1.5, 0.0, ()),
        (1.5, 1.35, ()),
        (1.5, 1.35, (506,)),
        (1.5, 1.35, (507,)),
        (1.5, 1.35, (506, 507)),
    )
    records = []
    for thermal_case in (None, 508, 509, 510, 511):
        for offset, (earth, cushion, surcharges) in enumerate(base):
            number = 1001 + offset + (0 if thermal_case is None
                                      else (thermal_case - 507) * 6)
            factors = {501: 1.35, 502: 1.35, 503: 1.75}
            if earth:
                factors[504] = earth
            if cushion:
                factors[505] = cushion
            for case in surcharges:
                factors[case] = 1.2
            if thermal_case is not None:
                factors[thermal_case] = 0.9
            records.append({
                'combination_number': number,
                'load_case_factors': factors,
                'temperature_load_case': thermal_case,
                'temperature_load_zero_in_reference': thermal_case is not None,
            })
    assert len(records) == 30
    return tuple(records)
