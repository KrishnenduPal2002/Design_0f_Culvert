"""Combine the three current presentation DXFs into a vector A2 PDF."""
from io import StringIO


def build_drawing_pdf(sheet1: bytes, sheet2: bytes, sheet3: bytes) -> bytes:
    import ezdxf
    import fitz
    from ezdxf.addons.drawing import Frontend, RenderContext, config, layout, pymupdf

    configuration = config.Configuration(
        color_policy=config.ColorPolicy.BLACK,
        background_policy=config.BackgroundPolicy.WHITE,
    )
    with fitz.open() as combined:
        for index, data in enumerate((sheet1, sheet2, sheet3), 1):
            if not data:
                raise ValueError(f'Sheet {index} DXF unavailable')
            doc = ezdxf.read(StringIO(data.decode('utf-8-sig')))
            model = doc.modelspace()
            if not len(model):
                raise ValueError(f'Sheet {index} has no model-space drawing')
            backend = pymupdf.PyMuPdfBackend()
            Frontend(RenderContext(doc), backend, config=configuration).draw_layout(model)
            pdf = backend.get_pdf_bytes(layout.Page(594, 420, margins=layout.Margins(8, 8, 12, 8)), settings=layout.Settings(fixed_stroke_width=0.13, output_layers=False))
            with fitz.open(stream=pdf, filetype='pdf') as single:
                if len(single) != 1:
                    raise ValueError(f'Sheet {index} did not produce one PDF page')
                combined.insert_pdf(single)
            page = combined[-1]
            page.insert_text((24, page.rect.height-14), 'ENGINEERING REVIEW - NOT FOR CONSTRUCTION | Presentation drawing; do not scale. Reinforcement detailing and site geometry require approval.', fontsize=8, color=(0,0,0))
        combined.set_metadata({'title': 'RCC Box Culvert - Drawing Sheets 1 to 3', 'subject': 'Generated engineering review drawings'})
        return combined.tobytes(garbage=4, deflate=True)
