"""BBS and BOQ CSV templates and revised quantity exports.

CSV values are trial quantities, not approved fabrication schedules.
"""
import csv
import io
import math
from .bbs import reference_bars
from .boq import reference_boq_items

BBS_COLUMNS = ('id','mark','member','diameter_mm','spacing_mm','cut_length_mm','bar_count')
BOQ_COLUMNS = ('id','description','unit','reference_quantity','quantity')

def _csv_bytes(rows, fields):
    buff=io.StringIO(newline='')
    writer=csv.DictWriter(buff,fieldnames=list(fields),extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    return buff.getvalue().encode('utf-8-sig')

def bbs_template_csv() -> bytes:
    return _csv_bytes(reference_bars(),BBS_COLUMNS)

def boq_template_csv() -> bytes:
    return _csv_bytes([dict(r,quantity=r['reference_quantity']) for r in reference_boq_items()], BOQ_COLUMNS)

def bbs_result_csv(result: dict) -> bytes:
    return _csv_bytes(result['lines'], ('id','mark','member','diameter_mm','spacing_mm','cut_length_mm','bar_count','length_total_m','unit_weight_kg_m','weight_kg','status'))

def boq_result_csv(result: dict) -> bytes:
    return _csv_bytes(result['reference_items'],('id','description','unit','reference_quantity','quantity','quantity_source'))

def _parse(data,fields):
    if isinstance(data,bytes):s=data.decode('utf-8-sig')
    else:s=str(data)
    rows=list(csv.DictReader(io.StringIO(s)))
    if not rows or set(fields)-set(rows[0]):
        raise ValueError('CSV requires: '+', '.join(fields))
    return rows

def parse_bbs_csv(data:bytes|str)->list[dict]:
    reference={r['id']:r for r in reference_bars()}
    input_rows=_parse(data,BBS_COLUMNS)
    if len(input_rows)!=len(reference) or {r['id'] for r in input_rows}!=set(reference):
        raise ValueError('BBS CSV must contain exactly all reference mark ids; do not add/remove rows')
    out=[]
    for r in input_rows:
        item=dict(reference[r['id']])
        for field in ('diameter_mm','cut_length_mm'):
            item[field]=float(r[field])
        item['bar_count']=float(r['bar_count'])
        spacing=r['spacing_mm'].strip()
        item['spacing_mm']=(float(spacing) if spacing else None)
        out.append(item)
    return out

def parse_boq_csv(data:bytes|str)->dict[str,float]:
    reference={r['id']:r for r in reference_boq_items()}
    input_rows=_parse(data,BOQ_COLUMNS)
    if len(input_rows)!=len(reference) or {r['id'] for r in input_rows}!=set(reference):
        raise ValueError('BOQ CSV must contain exactly all reference item ids; do not add/remove rows')
    output={}
    for r in input_rows:
        q=float(r['quantity'])
        if not math.isfinite(q) or q<0:raise ValueError(f"Invalid BOQ quantity for {r['id']}")
        if not math.isclose(q,reference[r['id']]['reference_quantity'],rel_tol=0,abs_tol=1e-9):
            output[r['id']]=q
    return output
