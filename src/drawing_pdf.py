"""Render live CAD sheets into a three-page vector A2 PDF."""
from io import StringIO


def build_drawing_pdf(sheet1: bytes, sheet2: bytes, sheet3: bytes) -> bytes:
    import ezdxf
    import fitz
    from ezdxf.addons.drawing import Frontend, RenderContext, config, layout, pymupdf

    configuration = config.Configuration(color_policy=config.ColorPolicy.BLACK,
        background_policy=config.BackgroundPolicy.WHITE)
    with fitz.open() as combined:
        for index, data in enumerate((sheet1, sheet2, sheet3), 1):
            if not data:
                raise ValueError(f'Sheet {index} DXF unavailable')
            doc = ezdxf.read(StringIO(data.decode('utf-8-sig'), newline=None))
            model = doc.modelspace()
            # A complete presentation may be in paper space instead of Model.
            names = {1: ('DETAILED A2', 'GAD A2'), 2: ('DETAILED A2', 'DETAILS A2'),
                     3: ('REINFORCEMENT A2',)}[index]
            candidates = [model] if len(model) else []
            for name in names:
                if name in doc.layouts and len(doc.layouts.get(name)):
                    candidates.append(doc.layouts.get(name))
            candidates.extend(p for p in doc.layouts if p.name != 'Model' and len(p) and p not in candidates)
            errors = []
            for drawing in candidates:
                try:
                    backend = pymupdf.PyMuPdfBackend()
                    Frontend(RenderContext(doc), backend, config=configuration).draw_layout(drawing)
                    pdf = backend.get_pdf_bytes(layout.Page(594,420,margins=layout.Margins(8,8,12,8)),
                        settings=layout.Settings(fixed_stroke_width=0.13,output_layers=False))
                    with fitz.open(stream=pdf,filetype='pdf') as single:
                        if len(single)!=1 or not (single[0].get_drawings() or single[0].get_text().strip()):
                            raise ValueError('No rendered drawing entities')
                        combined.insert_pdf(single)
                    break
                except Exception as exc:
                    errors.append(f'{drawing.name}: {exc}')
            else:
                raise ValueError(f'Sheet {index} has no renderable drawing in Model Space or paper layouts. '+ '; '.join(errors))
            page=combined[-1]
            page.insert_text((24,page.rect.height-14),'ENGINEERING REVIEW - NOT FOR CONSTRUCTION | Presentation drawing; do not scale. Reinforcement detailing and site geometry require approval.',fontsize=8,color=(0,0,0))
        combined.set_metadata({'title':'RCC Box Culvert - Drawing Sheets 1 to 3','subject':'Generated engineering review drawings'})
        return combined.tobytes(garbage=4,deflate=True)
