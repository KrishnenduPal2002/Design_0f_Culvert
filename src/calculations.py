from dataclasses import asdict
from .inputs import CulvertInputs
from .geometry import calculate_geometry
from .loads import calculate_preliminary_self_weight


def calculate_preview(inputs: CulvertInputs) -> dict:
    """Preview calculations only; excludes all structural design checks."""
    inputs.validate()
    geom = calculate_geometry(inputs)
    loads = calculate_preliminary_self_weight(inputs, geom)
    return {
        "inputs": asdict(inputs),
        "geometry": geom.to_dict(),
        "preliminary_self_weight": loads.to_dict(),
        "structural_design_status": "NOT IMPLEMENTED OR VERIFIED",
        "limitations": [
            "No STAAD structural analysis or equivalent frame solver",
            "No vehicle loading, earth pressure or load combinations",
            "No ULS/SLS, crack width, shear or reinforcement design checks",
            "No BBS, BOQ or construction-ready reinforcement details",
            "No approval or certification of structural safety",
            "Volumes include four simplified internal triangular haunches; omit barriers and other special geometry",
        ],
    }
