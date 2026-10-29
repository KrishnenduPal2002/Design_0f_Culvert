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
    soil_factor: float = 1.0
    surcharge_factor: float = 1.0
    cushion_factor: float = 1.0
    vehicle_factor: float = 1.0
    braking_factor: float = 1.0
    def validate(self):
        for key,val in asdict(self).items():
            if key=='name':continue
            if not isinstance(val,(int,float)) or not math.isfinite(val) or val<0:
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
