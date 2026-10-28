from dataclasses import dataclass, asdict
from .inputs import CulvertInputs

@dataclass(frozen=True)
class GeometryResult:
    external_width_m: float
    external_height_m: float
    opening_area_m2: float
    concrete_cross_section_area_m2: float
    concrete_volume_m3: float
    top_slab_volume_m3: float
    bottom_slab_volume_m3: float
    walls_volume_m3: float
    haunch_area_each_m2: float
    haunch_volume_m3: float
    box_volume_without_haunches_m3: float

    def to_dict(self):
        return asdict(self)


def calculate_geometry(inputs: CulvertInputs) -> GeometryResult:
    """Simplified rectangular box + four internal triangular haunches.

    Clear width/height are the dimensions of the un-haunched rectangular opening.
    Haunches extend the full culvert length. No barriers or other features.
    """
    inputs.validate()
    w, h, length = inputs.clear_width_m, inputs.clear_height_m, inputs.length_m
    wall, top, bottom = inputs.wall_thickness_m, inputs.top_slab_thickness_m, inputs.bottom_slab_thickness_m
    ow, oh = w + 2 * wall, h + top + bottom
    top_vol = ow * top * length
    bottom_vol = ow * bottom * length
    walls_vol = 2 * wall * h * length
    base_area = ow * oh - w * h
    haunch_area_each = 0.5 * inputs.haunch_width_m * inputs.haunch_height_m
    haunch_volume = haunch_area_each * inputs.haunch_count * length
    total_area = base_area + haunch_area_each * inputs.haunch_count
    return GeometryResult(
        external_width_m=ow, external_height_m=oh,
        opening_area_m2=w*h-haunch_area_each*inputs.haunch_count,
        concrete_cross_section_area_m2=total_area,
        concrete_volume_m3=total_area*length,
        top_slab_volume_m3=top_vol, bottom_slab_volume_m3=bottom_vol,
        walls_volume_m3=walls_vol,
        haunch_area_each_m2=haunch_area_each,
        haunch_volume_m3=haunch_volume,
        box_volume_without_haunches_m3=base_area*length,
    )
