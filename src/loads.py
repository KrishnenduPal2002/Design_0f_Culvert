from dataclasses import dataclass, asdict
from .inputs import CulvertInputs
from .geometry import GeometryResult

@dataclass(frozen=True)
class PreliminarySelfWeight:
    concrete_self_weight_kn: float
    top_slab_self_weight_kn: float
    bottom_slab_self_weight_kn: float
    wall_self_weight_kn: float

    def to_dict(self):
        return asdict(self)


def calculate_preliminary_self_weight(inputs: CulvertInputs, geometry: GeometryResult) -> PreliminarySelfWeight:
    """Geometry-based concrete dead load only. NOT a design load combination."""
    gamma = inputs.concrete_unit_weight_kn_m3
    return PreliminarySelfWeight(
        concrete_self_weight_kn=geometry.concrete_volume_m3 * gamma,
        top_slab_self_weight_kn=geometry.top_slab_volume_m3 * gamma,
        bottom_slab_self_weight_kn=geometry.bottom_slab_volume_m3 * gamma,
        wall_self_weight_kn=geometry.walls_volume_m3 * gamma,
    )
