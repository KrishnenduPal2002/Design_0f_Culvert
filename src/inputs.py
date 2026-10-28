from dataclasses import dataclass, asdict
import math

@dataclass(frozen=True)
class CulvertInputs:
    clear_width_m: float = 3.0
    clear_height_m: float = 3.0
    length_m: float = 12.0
    wall_thickness_m: float = 0.25
    top_slab_thickness_m: float = 0.25
    bottom_slab_thickness_m: float = 0.275
    earth_cushion_m: float = 0.30
    concrete_unit_weight_kn_m3: float = 25.0  # Confirm with engineer
    concrete_grade: str = "M35"
    steel_grade: str = "Fe500"
    haunch_width_m: float = 0.15
    haunch_height_m: float = 0.15
    haunch_count: int = 4

    def validate(self):
        for key, value in asdict(self).items():
            if key.endswith(("_m", "_kn_m3")):
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"{key} must be a finite number")
                if value <= 0 and key != "earth_cushion_m":
                    raise ValueError(f"{key} must be positive")
                if key == "earth_cushion_m" and value < 0:
                    raise ValueError("Earth cushion cannot be negative")
        if type(self.haunch_count) is not int or self.haunch_count not in (0, 4):
            raise ValueError("This prototype supports either 0 or 4 internal haunches")
        if self.haunch_count == 4 and (self.haunch_width_m * 2 >= self.clear_width_m or
                                       self.haunch_height_m * 2 >= self.clear_height_m):
            raise ValueError("Haunch dimensions must fit within the clear opening")
        return self
