"""Validated Stage 1 culvert design inputs.

The reference-design values are kept alongside the primary geometry so that
Streamlit input values can be exported and audited as one consistent record.
Not every reference value is yet used by a calculation. In particular, a
single-cell box is the only supported geometry, and source Es/Ec units have
not been resolved against the engineering design note.
"""
from dataclasses import dataclass, asdict
import math


@dataclass(frozen=True)
class CulvertInputs:
    # Stage 1 — Geometric parameters (SI metres unless labelled otherwise)
    clear_width_m: float = 3.0
    clear_height_m: float = 3.0
    length_m: float = 12.0
    wall_thickness_m: float = 0.25
    top_slab_thickness_m: float = 0.25
    bottom_slab_thickness_m: float = 0.275
    earth_cushion_m: float = 0.30
    concrete_unit_weight_kn_m3: float = 25.0
    concrete_grade: str = "M35"
    steel_grade: str = "Fe500"
    haunch_width_m: float = 0.15
    haunch_height_m: float = 0.15
    haunch_count: int = 4

    # Extra geometry inputs. Only the single-cell model is implemented for
    # the structural analysis and drawing modules. Multi-cell inputs can be
    # inspected but must NOT be analysed as one cell.
    number_of_cells: int = 1
    internal_wall_thickness_m: float = 0.0
    wearing_coat_thickness_m: float = 0.05
    ms_railing_width_m: float = 0.50

    # Stage 1 — Basic parameters from the design note
    soil_friction_angle_deg: float = 30.0
    earth_pressure_coefficient_k0: float = 0.50
    backfill_density_tf_m3: float = 2.0
    wearing_course_density_tf_m3: float = 2.2
    concrete_density_tf_m3: float = 2.5
    surcharge_equivalent_height_m: float = 1.2
    safe_bearing_capacity_tf_m2: float = 10.0
    subgrade_reaction_modulus_tf_m3: float = 1000.0
    # Exact *numbers* from the design note; source units are not verified.
    steel_modulus_source_value: float = 200000.0
    concrete_modulus_source_value: float = 32000.0

    @property
    def effective_single_span_m(self) -> float:
        """Reference centre-to-centre width of a representative cell.

        For a single cell, both boundary walls have the side-wall thickness.
        For multiple cells, the displayed first-cell span uses one boundary
        wall and one internal wall. This is *reference geometry*, not a
        verified multi-cell structural span.
        """
        if self.number_of_cells == 1:
            return self.clear_width_m + self.wall_thickness_m
        return self.clear_width_m + 0.5 * (self.wall_thickness_m + self.internal_wall_thickness_m)

    @property
    def centreline_height_m(self) -> float:
        """Mid-slab-centreline separation shown in the design note."""
        return self.clear_height_m + (self.top_slab_thickness_m + self.bottom_slab_thickness_m) / 2.0

    @property
    def total_width_m(self) -> float:
        """Reference overall width, including all clear bays and walls."""
        return (
            self.number_of_cells * self.clear_width_m
            + 2.0 * self.wall_thickness_m
            + max(0, self.number_of_cells - 1) * self.internal_wall_thickness_m
        )

    @property
    def supports_single_cell_model(self) -> bool:
        """Whether the existing analysis/drawing modules may be run."""
        return self.number_of_cells == 1 and self.internal_wall_thickness_m == 0.0

    def validate(self):
        zero_allowed = {
            "earth_cushion_m", "internal_wall_thickness_m", "wearing_coat_thickness_m",
            "ms_railing_width_m", "soil_friction_angle_deg", "earth_pressure_coefficient_k0",
        }
        for key, value in asdict(self).items():
            if key in ("concrete_grade", "steel_grade", "haunch_count", "number_of_cells"):
                continue
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number")
            if key in zero_allowed:
                if value < 0:
                    raise ValueError(f"{key} cannot be negative")
            elif value <= 0:
                raise ValueError(f"{key} must be positive")
        if type(self.haunch_count) is not int or self.haunch_count not in (0, 4):
            raise ValueError("This prototype supports either 0 or 4 internal haunches")
        if type(self.number_of_cells) is not int or self.number_of_cells < 1:
            raise ValueError("Number of cells must be a positive integer")
        if not self.supports_single_cell_model:
            raise ValueError(
                "The current ULS/SLS, loads, frame and drawings modules support only "
                "one cell with zero internal-wall thickness. The multi-cell "
                "inputs are editable for reference geometry only."
            )
        if not 0 <= self.earth_pressure_coefficient_k0 <= 1:
            raise ValueError("Earth pressure coefficient K0 must be between 0 and 1")
        if not 0 <= self.soil_friction_angle_deg <= 90:
            raise ValueError("Soil friction angle must be between 0 and 90 degrees")
        if self.haunch_count == 4 and (
            self.haunch_width_m * 2 >= self.clear_width_m or
            self.haunch_height_m * 2 >= self.clear_height_m
        ):
            raise ValueError("Haunch dimensions must fit within the clear opening")
        return self
