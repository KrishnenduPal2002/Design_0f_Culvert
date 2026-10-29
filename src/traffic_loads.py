"""Stage 3: reproduce design-note *braking-force summary* only.

Source: Design Note Of Box Culvert 1x3x3, p.2 (IRC 6:2017 reference).
The note gives total horizontal forces 7.94 tf and 9.14 tf and their
rounded per-metre values 0.66 and 0.76 tf/m for a 12 m structure.

This module does NOT calculate axle/vehicle load dispersion, wheel loads,
impact factors, structural actions or ULS/SLS load combinations.
"""
from __future__ import annotations
from dataclasses import dataclass
import math

TF_TO_KN = 9.80665

@dataclass(frozen=True)
class BrakingInputs:
    length_m: float = 12.0
    class_70rw_force_tf: float = 6.8
    class_40t_force_tf: float = 8.0
    class_a_force_tf: float = 1.14
    # Optional lever arm for an ILLUSTRATIVE moment, not a verified design rule.
    assumed_lever_arm_m: float = 1.325

    def validate(self) -> None:
        for name, value in vars(self).items():
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        if self.length_m <= 0 or self.assumed_lever_arm_m < 0:
            raise ValueError("Length must be positive and lever arm nonnegative")
        if any(v < 0 for v in (self.class_70rw_force_tf, self.class_40t_force_tf, self.class_a_force_tf)):
            raise ValueError("Horizontal force contributions must be nonnegative")


def braking_cases(p: BrakingInputs) -> list[dict]:
    p.validate()
    cases = [
        ("Class 70R(W) + A", p.class_70rw_force_tf, 7.94, 0.66, 0.877),
        ("Class 40T + A", p.class_40t_force_tf, 9.14, 0.76, 1.009),
    ]
    output = []
    for label, main, reference_total, reference_per_m, reference_moment in cases:
        total = main + p.class_a_force_tf
        per_m = total / p.length_m
        output.append({
            "Case": label,
            "Main force (tf)": main,
            "Class A force (tf)": p.class_a_force_tf,
            "Total force (tf)": total,
            "Per-metre force (tf/m)": per_m,
            "Per-metre force (kN/m)": per_m * TF_TO_KN,
            "Illustrative moment (tf·m/m)": per_m * p.assumed_lever_arm_m,
            "Note total (tf)": reference_total,
            "Note per metre (tf/m)": reference_per_m,
            "Note moment (tf·m/m)": reference_moment,
            "Difference total (tf)": total - reference_total,
        })
    return output
