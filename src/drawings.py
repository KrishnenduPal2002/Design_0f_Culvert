import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Polygon
from .inputs import CulvertInputs
from .geometry import calculate_geometry


def haunch_triangles(inputs: CulvertInputs):
    """Four right-triangular internal corner haunches in metre coordinates."""
    if inputs.haunch_count == 0:
        return []
    x0 = inputs.wall_thickness_m
    x1 = x0 + inputs.clear_width_m
    y0 = inputs.bottom_slab_thickness_m
    y1 = y0 + inputs.clear_height_m
    a, b = inputs.haunch_width_m, inputs.haunch_height_m
    return [
        [(x0,y0), (x0+a,y0), (x0,y0+b)],
        [(x1,y0), (x1-a,y0), (x1,y0+b)],
        [(x0,y1), (x0+a,y1), (x0,y1-b)],
        [(x1,y1), (x1-a,y1), (x1,y1-b)],
    ]


def draw_culvert(inputs: CulvertInputs):
    """Schematic cross-section only; not a construction drawing."""
    g = calculate_geometry(inputs)
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.add_patch(Rectangle((0, 0), g.external_width_m, g.external_height_m,
                           facecolor="#b9c2cb", edgecolor="black", linewidth=1.8))
    ax.add_patch(Rectangle((inputs.wall_thickness_m, inputs.bottom_slab_thickness_m),
                           inputs.clear_width_m, inputs.clear_height_m,
                           facecolor="white", edgecolor="black", linewidth=1.5))
    for triangle in haunch_triangles(inputs):
        ax.add_patch(Polygon(triangle, closed=True, facecolor="#b9c2cb",
                             edgecolor="black", linewidth=1.2))
    ax.set_xlim(-0.5, g.external_width_m + 0.5)
    ax.set_ylim(-0.5, g.external_height_m + 0.5)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("Width (m)")
    ax.set_ylabel("Height (m)")
    ax.set_title("RCC Box Culvert — With Internal Haunches (NOT FOR CONSTRUCTION)")
    ax.grid(alpha=0.15)
    fig.tight_layout()
    return fig


def export_schematic_dxf(inputs: CulvertInputs, path):
    """DXF schematic outline and four haunch triangles; no rebar detailing."""
    import ezdxf
    g = calculate_geometry(inputs)
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    def rect(x0, y0, w, h):
        msp.add_lwpolyline([(x0,y0),(x0+w,y0),(x0+w,y0+h),(x0,y0+h)],close=True)
    rect(0, 0, g.external_width_m, g.external_height_m)
    rect(inputs.wall_thickness_m, inputs.bottom_slab_thickness_m,
         inputs.clear_width_m, inputs.clear_height_m)
    for triangle in haunch_triangles(inputs):
        msp.add_lwpolyline(triangle, close=True)
    doc.saveas(str(path))
