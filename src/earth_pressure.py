"""Stage 2: unfactored lateral earth pressure and equivalent surcharge.

Design-note reference: page 2, 'Earth Pressure per meter width'.
The note uses K0=0.5, gamma_soil=2.0 t/m^3 and surcharge height=1.2 m.
Pressure is in metric tonne-force/m^2 (tf/m^2); conversion to kPa uses
1 tf = 9.80665 kN. Depth z is measured downward from the reference datum
used in the design note (top slab upper surface), NOT from ground level.

This module is a transparent pressure calculator, not a structural solver.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

TF_TO_KN = 9.80665


@dataclass(frozen=True)
class EarthPressureInputs:
    k0: float = 0.5
    backfill_density_t_m3: float = 2.0
    surcharge_equivalent_height_m: float = 1.2
    earth_cushion_m: float = 0.3

    def validate(self) -> None:
        for name, val in vars(self).items():
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise ValueError(f"{name} must be finite")
        if not (0 <= self.k0 <= 1):
            raise ValueError("K0 must be between 0 and 1 for this model")
        if self.backfill_density_t_m3 <= 0:
            raise ValueError("Backfill density must be positive")
        if self.surcharge_equivalent_height_m < 0 or self.earth_cushion_m < 0:
            raise ValueError("Surcharge height and earth cushion cannot be negative")


def pressure_at_depth(depth_m: float, params: EarthPressureInputs) -> dict:
    """Lateral pressures, referenced to a depth below top slab upper surface.

    p_soil = K0 * gamma * z; p_surcharge = K0 * gamma * h_surcharge;
    p_cushion = K0 * gamma * h_cushion. Cushion is reported separately
    because the design note does not include it in its depth-based soil
    pressure column; do not add it to that column without confirming datum.
    """
    params.validate()
    if not math.isfinite(depth_m) or depth_m < 0:
        raise ValueError("Depth must be finite and non-negative")
    soil = params.k0 * params.backfill_density_t_m3 * depth_m
    surcharge = params.k0 * params.backfill_density_t_m3 * params.surcharge_equivalent_height_m
    cushion = params.k0 * params.backfill_density_t_m3 * params.earth_cushion_m
    return {
        "depth_m": depth_m,
        "earth_pressure_t_m2": soil,
        "earth_pressure_kpa": soil * TF_TO_KN,
        "surcharge_pressure_t_m2": surcharge,
        "surcharge_pressure_kpa": surcharge * TF_TO_KN,
        "cushion_pressure_t_m2": cushion,
        "cushion_pressure_kpa": cushion * TF_TO_KN,
        "soil_plus_surcharge_t_m2": soil + surcharge,
        "soil_plus_surcharge_kpa": (soil + surcharge) * TF_TO_KN,
    }


def design_note_reference_rows(params: EarthPressureInputs) -> list[dict]:
    """Check against the depth/pressure pairs printed on design note page 2."""
    positions = [
        ("Top slab centreline", 0.125, 0.125),
        ("End of top haunch", 0.250, 0.250),
        ("Start of top haunch", 0.400, 0.400),
        ("Side wall mid-height", 1.750, 1.750),
        ("Start of bottom haunch", 3.100, 3.100),
        ("End of bottom haunch", 3.250, 3.250),
        ("Bottom slab centreline", 3.3875, 3.3875),
    ]
    rows = []
    for label, depth, reference in positions:
        p = pressure_at_depth(depth, params)
        rows.append({
            "Location": label,
            "Depth (m)": depth,
            "Design note (tf/m²)": reference,
            "Python (tf/m²)": round(p["earth_pressure_t_m2"], 5),
            "Difference (tf/m²)": round(p["earth_pressure_t_m2"] - reference, 5),
        })
    return rows
