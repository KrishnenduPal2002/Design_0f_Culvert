"""Calculate output-worksheet summary cells from independent vehicle member-end FEM.

No workbook values are read or used as prediction inputs. Lookup metadata
contains only source beam/end selectors and summary MAX/MIN conventions.
The independent vehicle solver currently remains unvalidated against workbook
output, so its predictions must not silently enter approved design tables.
"""
from __future__ import annotations
import json,math
from pathlib import Path


def load_summary_selectors(path):
    doc=json.loads(Path(path).read_text(encoding='utf-8'))
    if doc.get('fixture_type') != 'vehicle-summary-selector-metadata-only' or set(doc.get('vehicles',{})) != {'70rw','40t','70rt','sv385'}:
        raise ValueError('Unexpected vehicle selector metadata')
    for vehicle,cells in doc['vehicles'].items():
      for cell,record in cells.items():
        if record['operation'] not in ('single','min','max') or not record['lookups']:
            raise ValueError('Unsupported summary selector '+cell)
        if record['operation']=='single' and len(record['lookups'])!=1:
            raise ValueError('Invalid single-cell selection '+cell)
        for row in record['lookups']:
            if row['end'] not in ('start','end') or row['quantity'] not in ('Fy','Mz') or row['sign'] not in ('min','max'):
                raise ValueError('Invalid summary lookup '+cell)
    return doc


def calculate_vehicle_summary_cells(envelopes, selectors, vehicle):
    """Input: keyed vehicle-end envelope from actual independent frame solver."""
    values={}
    for cell,record in selectors['vehicles'][vehicle].items():
        candidates=[]
        for lookup in record['lookups']:
            key=(int(lookup['beam']),lookup['end'])
            if key not in envelopes:raise ValueError(f'Unsolved member-end {key} for {cell}')
            field=f"{lookup['quantity']}_{lookup['sign']}_{'tfm' if lookup['quantity']=='Mz' else 'tf'}"
            v=float(envelopes[key][field])
            if not math.isfinite(v):raise ValueError('Nonfinite computed response')
            candidates.append(v)
        op=record['operation']
        values[cell]=candidates[0] if op=='single' else max(candidates) if op=='max' else min(candidates)
    return values
