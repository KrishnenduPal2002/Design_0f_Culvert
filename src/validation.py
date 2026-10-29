from .inputs import CulvertInputs
from .geometry import calculate_geometry


def validate_basic_geometry(inputs: CulvertInputs, tolerance: float = 1e-8) -> None:
    """Internal arithmetic consistency; not engineering design approval."""
    g = calculate_geometry(inputs)
    assert abs(g.concrete_volume_m3 - (
        g.top_slab_volume_m3 + g.bottom_slab_volume_m3 + g.walls_volume_m3
    )) <= tolerance * max(1.0, g.concrete_volume_m3)


def compare_to_reference(actual: float, expected: float, absolute_tolerance: float = 1e-6) -> dict:
    difference = actual - expected
    return {"actual": actual, "reference": expected,
            "difference": difference, "matches": abs(difference) <= absolute_tolerance}
