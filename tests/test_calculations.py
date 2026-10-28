import pytest
from src.inputs import CulvertInputs
from src.geometry import calculate_geometry
from src.calculations import calculate_preview
from src.validation import validate_basic_geometry, compare_to_reference


def test_example_geometry():
    g = calculate_geometry(CulvertInputs())
    assert g.external_width_m == pytest.approx(3.5)
    assert g.external_height_m == pytest.approx(3.525)
    assert g.concrete_volume_m3 == pytest.approx(3.5 * 3.525 * 12 - 3 * 3 * 12)
    validate_basic_geometry(CulvertInputs())


def test_reject_invalid_dimension():
    with pytest.raises(ValueError):
        calculate_geometry(CulvertInputs(clear_width_m=-1))


def test_load_sum():
    result = calculate_preview(CulvertInputs())
    loads = result["preliminary_self_weight"]
    assert loads["concrete_self_weight_kn"] == pytest.approx(
        loads["top_slab_self_weight_kn"] + loads["bottom_slab_self_weight_kn"] + loads["wall_self_weight_kn"]
    )


def test_reference_comparison():
    assert compare_to_reference(3.5, 3.5)["matches"]
    assert not compare_to_reference(3.5, 3.6)["matches"]
