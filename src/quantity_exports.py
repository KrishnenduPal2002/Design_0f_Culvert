"""BBS and BOQ CSV templates and revised quantity exports.

CSV values are trial quantities, not approved fabrication schedules.
"""
import csv
import io
import math
from .bbs import reference_bars, bbs_shape_name
from .boq import reference_boq_items

BBS_COLUMNS = ('id','mark','member','diameter_mm','spacing_mm','cut_length_mm','bar_count')
BOQ_COLUMNS = ('id','description','unit','reference_quantity','quantity')

def _csv_bytes(rows, fields):
    buff=io.StringIO(newline='')
    writer=csv.DictWriter(buff,fieldnames=list(fields),extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    return buff.getvalue().encode('utf-8-sig')

def bbs_template_csv(rows=None) -> bytes:
    rows=reference_bars() if rows is None else rows
    return _csv_bytes([r for r in rows if r['mark']!='w1a'],BBS_COLUMNS)

def boq_template_csv() -> bytes:
    return _csv_bytes([dict(r,quantity=r['reference_quantity']) for r in reference_boq_items()], BOQ_COLUMNS)

def bbs_result_csv(result: dict) -> bytes:
    rows=[dict(r, serial_no=i, shape=bbs_shape_name(r['mark'])) for i,r in enumerate(result['lines'],1)]
    return _csv_bytes(rows, ('serial_no','id','mark','shape','diameter_mm','spacing_mm','m1_mm','m2_mm','m3_mm','cut_length_mm','bar_count','length_total_m','unit_weight_kg_m','weight_kg','member','status'))

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
    reference={r['id']:r for r in reference_bars() if r['mark']!='w1a'}
    input_rows=_parse(data,BBS_COLUMNS)
    legacy=[r for r in input_rows if r['id'] not in reference and r['mark']=='w1a']
    if legacy:
        if len(legacy)!=1 or float(legacy[0]['diameter_mm'])!=0:
            raise ValueError('Active or duplicate w1a is incompatible with the 25-mark schedule')
        input_rows=[r for r in input_rows if r not in legacy]
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
