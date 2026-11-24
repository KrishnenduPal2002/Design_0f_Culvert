"""Parametric vehicle-only plane-frame FEM based on the four supplied STD files.

Original model input coordinates, section sizes, supports and moving-load
intensities are embedded below. NO saved member forces or workbook response
values are solver inputs. Reference case replay uses the source load commands;
changed spans, cover or vehicle settings regenerate dispersed moving loads.
The latter are independent sampled predictions, not engineering-approved.
All force units in this solver are tonne-force and metre, strip width 1 m.
"""
from __future__ import annotations
from dataclasses import replace
import math
import numpy as np
from .vehicle_loading import vehicle_preset, calculate_axle_dispersion

TF_TO_KN = 9.80665
VEHICLES = ('70rw', '40t', '70rt', 'sv385')
REFERENCE_SPAN = 3.275
REFERENCE_RISE = 3.263  # rounded source coordinate
REFERENCE_E_TF_M2 = 3230824.972
REFERENCE_E_GPA = REFERENCE_E_TF_M2 * TF_TO_KN / 1e6
REFERENCE_SOIL_K = 1000.0

STD_NODES = {1: (0.0, 0.0),
 2: (0.138, 0.0),
 3: (0.288, 0.0),
 4: (0.558, 0.0),
 5: (0.828, 0.0),
 6: (1.098, 0.0),
 7: (1.368, 0.0),
 8: (1.638, 0.0),
 9: (1.908, 0.0),
 10: (2.178, 0.0),
 11: (2.448, 0.0),
 12: (2.718, 0.0),
 13: (2.988, 0.0),
 14: (3.138, 0.0),
 15: (3.275, 0.0),
 16: (0.0, 3.263),
 17: (0.138, 3.263),
 18: (0.288, 3.263),
 19: (0.533, 3.263),
 20: (0.778, 3.263),
 21: (1.024, 3.263),
 22: (1.269, 3.263),
 23: (1.515, 3.263),
 24: (1.76, 3.263),
 25: (2.006, 3.263),
 26: (2.251, 3.263),
 27: (2.497, 3.263),
 28: (2.742, 3.263),
 29: (2.988, 3.263),
 30: (3.138, 3.263),
 31: (3.275, 3.263),
 32: (0.0, 0.1375),
 33: (0.0, 0.2875),
 34: (0.0, 1.631),
 35: (0.0, 2.988),
 36: (0.0, 3.138),
 37: (3.275, 0.1375),
 38: (3.275, 0.2875),
 39: (3.275, 1.631),
 40: (3.275, 2.988),
 41: (3.275, 3.138)}

STD_BEAMS = {101: (1, 2),
 102: (2, 3),
 103: (3, 4),
 104: (4, 5),
 105: (5, 6),
 106: (6, 7),
 107: (7, 8),
 108: (8, 9),
 109: (9, 10),
 110: (10, 11),
 111: (11, 12),
 112: (12, 13),
 113: (13, 14),
 114: (14, 15),
 201: (16, 17),
 202: (17, 18),
 203: (18, 19),
 204: (19, 20),
 205: (20, 21),
 206: (21, 22),
 207: (22, 23),
 208: (23, 24),
 209: (24, 25),
 210: (25, 26),
 211: (26, 27),
 212: (27, 28),
 213: (28, 29),
 214: (29, 30),
 215: (30, 31),
 1001: (1, 32),
 1002: (32, 33),
 1003: (33, 34),
 1004: (34, 35),
 1005: (35, 36),
 1006: (36, 16),
 2001: (15, 37),
 2002: (37, 38),
 2003: (38, 39),
 2004: (39, 40),
 2005: (40, 41),
 2006: (41, 31)}

STD_SPRINGS = {1: 68.75,
 15: 68.75,
 4: 270.0,
 5: 270.0,
 6: 270.0,
 7: 270.0,
 8: 270.0,
 9: 270.0,
 10: 270.0,
 11: 270.0,
 12: 270.0,
 2: 143.75,
 14: 143.75,
 3: 210.0,
 13: 210.0}

STD_SECTIONS = {101: (0.275, 1.0),
 102: (0.275, 1.0),
 103: (0.275, 1.0),
 104: (0.275, 1.0),
 105: (0.275, 1.0),
 106: (0.275, 1.0),
 107: (0.275, 1.0),
 108: (0.275, 1.0),
 109: (0.275, 1.0),
 110: (0.275, 1.0),
 111: (0.275, 1.0),
 112: (0.275, 1.0),
 113: (0.275, 1.0),
 114: (0.275, 1.0),
 201: (0.25, 1.0),
 202: (0.25, 1.0),
 203: (0.25, 1.0),
 204: (0.25, 1.0),
 205: (0.25, 1.0),
 206: (0.25, 1.0),
 207: (0.25, 1.0),
 208: (0.25, 1.0),
 209: (0.25, 1.0),
 210: (0.25, 1.0),
 211: (0.25, 1.0),
 212: (0.25, 1.0),
 213: (0.25, 1.0),
 214: (0.25, 1.0),
 215: (0.25, 1.0),
 1001: (0.275, 1.0),
 1002: (0.275, 1.0),
 1003: (0.275, 1.0),
 1004: (0.275, 1.0),
 1005: (0.275, 1.0),
 1006: (0.275, 1.0),
 2001: (0.275, 1.0),
 2002: (0.275, 1.0),
 2003: (0.275, 1.0),
 2004: (0.275, 1.0),
 2005: (0.275, 1.0),
 2006: (0.275, 1.0)}

STD_FIXED = ((1, 0),)

STD_LOAD_CASES = {'70rw': ((1, 0.0, (6.811,), ()),
          (2, 0.25, (5.195,), ()),
          (3, 0.5, (4.628,), ()),
          (4, 0.75, (4.256,), ()),
          (5, 1.0, (4.012,), ()),
          (6, 1.25, (3.864,), ()),
          (7, 1.5, (3.792, 5.638), (-1.37,)),
          (8, 1.75, (3.789, 4.87), (-1.37,)),
          (9, 2.0, (3.854, 4.415), (-1.37,)),
          (10, 2.25, (3.994, 4.116), (-1.37,)),
          (11, 2.5, (4.226, 3.925), (-1.37,)),
          (12, 2.75, (4.584, 3.818), (-1.37,)),
          (13, 3.0, (5.127, 3.782), (-1.37,)),
          (14, 3.25, (6.54, 3.814), (-1.37,)),
          (15, 2.13, (3.916,), ()),
          (16, 2.38, (4.101,), ()),
          (17, 2.63, (4.393,), ()),
          (18, 2.88, (4.837,), ()),
          (19, 3.13, (5.533, 6.027), (-3.05,)),
          (20, 0.33, (4.986,), ()),
          (21, 0.58, (4.492,), ()),
          (22, 0.83, (4.166,), ()),
          (23, 1.08, (3.956,), ()),
          (24, 1.33, (3.833,), ()),
          (25, 1.58, (3.784, 5.311), (-1.37,)),
          (26, 1.83, (3.802, 4.703), (-1.37,)),
          (27, 2.08, (3.89, 4.305), (-1.37,)),
          (28, 2.33, (4.057, 4.044), (-1.37,)),
          (29, 2.58, (4.325, 3.882), (-1.37,)),
          (30, 2.83, (4.733, 3.799), (-1.37,)),
          (31, 3.08, (5.357, 3.785), (-1.37,)),
          (32, 1.96, (3.838,), ()),
          (33, 2.21, (3.966, 4.254), (-2.13,)),
          (34, 2.46, (4.182, 3.52), (-2.13,)),
          (35, 2.71, (4.516, 3.171), (-2.13,)),
          (36, 2.96, (5.023, 2.941), (-2.13,)),
          (37, 3.21, (6.157, 2.792), (-2.13,)),
          (38, 1.33, (2.706,), ()),
          (39, 1.58, (2.671, 4.378), (-1.52,)),
          (40, 1.83, (2.684, 3.555), (-1.52,)),
          (41, 2.08, (2.746, 3.194), (-1.52,)),
          (42, 2.33, (2.864, 2.956), (-1.52,)),
          (43, 2.58, (3.053, 2.802), (-1.52,)),
          (44, 2.83, (3.341, 2.711), (-1.52,)),
          (45, 3.08, (3.781, 2.672), (-1.52,)),
          (46, 1.81, (2.681,), ()),
          (47, 2.06, (2.739,), ()),
          (48, 2.31, (2.852,), ()),
          (49, 2.56, (3.035,), ()),
          (50, 2.81, (3.313,), ()),
          (51, 3.06, (3.739,), ()),
          (55, 0.1, (2.759,), ()),
          (56, 0.35, (2.324,), ()),
          (57, 0.6, (2.099,), ()),
          (58, 0.85, (1.951,), ()),
          (59, 1.1, (1.855,), ()),
          (60, 1.35, (1.801,), ()),
          (61, 1.6, (1.78,), ()),
          (62, 1.85, (1.791,), ()),
          (63, 2.1, (1.835,), ())),
 '40t': ((101, 0.0, (8.013,), ()),
         (102, 0.25, (6.112,), ()),
         (103, 0.5, (5.445,), ()),
         (104, 0.75, (5.007,), ()),
         (105, 1.0, (4.72,), ()),
         (106, 1.25, (4.546, 7.635), (-1.22,)),
         (107, 1.5, (4.461, 6.016), (-1.22,)),
         (108, 1.75, (4.457, 5.382), (-1.22,)),
         (109, 2.0, (4.534, 4.965), (-1.22,)),
         (110, 2.25, (4.698, 4.694), (-1.22,)),
         (111, 2.5, (4.972, 4.531), (-1.22,)),
         (112, 2.75, (5.392, 4.457), (-1.22,)),
         (113, 3.0, (6.031, 4.462), (-1.22,)),
         (114, 3.25, (7.695, 4.548), (-1.22,)),
         (115, 2.28, (4.725,), ()),
         (116, 2.53, (5.014,), ()),
         (117, 2.78, (5.456,), ()),
         (118, 3.03, (6.128,), ())),
 '70rt': ((201, 0.0, (5.682,), ()),
          (202, 0.25, (4.278,), ()),
          (203, 0.5, (3.811,), ()),
          (204, 0.75, (3.505,), ()),
          (205, 1.0, (3.304, 4.978), (-1.143,)),
          (206, 1.25, (3.182, 4.095), (-1.143,)),
          (207, 1.5, (3.123, 3.691), (-1.143,)),
          (208, 1.75, (3.12, 3.425), (-1.143,)),
          (209, 2.0, (3.173, 3.254, 4.471), (-1.143, -1.143)),
          (210, 2.25, (3.289, 3.155, 3.936), (-1.143, -1.143)),
          (211, 2.5, (3.48, 3.116, 3.587), (-1.143, -1.143)),
          (212, 2.75, (3.775, 3.132, 3.357, 5.606), (-1.143, -1.143, -1.143)),
          (213, 3.0, (4.222, 3.206, 3.213, 4.26), (-1.143, -1.143, -1.143)),
          (214, 3.25, (5.453, 3.345, 3.135, 3.799), (-1.143, -1.143, -1.143)),
          (215, 2.3575, (3.568, 3.115, 3.497), (-1.143, -1.143)),
          (216, 2.6075, (3.908, 3.151, 3.299, 4.923), (-1.143, -1.143, -1.143)),
          (217, 2.8575, (4.427, 3.246, 3.179, 4.079), (-1.143, -1.143, -1.143)),
          (218, 3.1075, (3.411, 3.122, 3.681), (-1.143, -1.143)),
          (219, 2.215, (3.669, 3.121, 3.419), (-1.143, -1.143)),
          (220, 2.465, (4.061, 3.176, 3.25), (-1.143, -1.143)),
          (221, 2.715, (4.863, 3.294, 3.153), (-1.143, -1.143)),
          (222, 2.965, (3.488, 3.115), (-1.143,)),
          (223, 3.215, (3.786, 3.133), (-1.143,)),
          (224, 2.3225, (4.24, 3.209), (-1.143,)),
          (225, 2.5725, (5.524, 3.351), (-1.143,)),
          (226, 2.8225, (3.577,), ()),
          (227, 3.0725, (3.921,), ()),
          (228, 3.3225, (4.448,), ())),
 'sv385': ((401, 1.0, (3.269,), ()),
           (402, 1.25, (3.153,), ()),
           (403, 1.5, (3.096, 4.865), (-1.5,)),
           (404, 1.75, (3.093, 4.186), (-1.5,)),
           (405, 2.0, (3.144, 3.749), (-1.5,)),
           (406, 2.25, (3.254, 3.459), (-1.5,)),
           (407, 2.5, (3.436, 3.269), (-1.5,)),
           (408, 2.75, (3.714, 3.153), (-1.5,)),
           (409, 3.0, (4.133, 3.096, 4.865), (-1.5, -1.5)),
           (410, 3.25, (4.782, 3.093, 4.186), (-1.5, -1.5)),
           (411, 2.0, (3.144, 3.749), (-1.5,)),
           (412, 2.25, (3.254, 3.459), (-1.5,)),
           (413, 2.5, (3.436, 3.269), (-1.5,)),
           (414, 2.75, (3.714, 3.153), (-1.5,)),
           (415, 3.0, (4.133, 3.096, 4.865), (-1.5, -1.5)))}


def _close(a,b):
    return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-10)


def _reference_geometry(inputs):
    return all(_close(getattr(inputs,k),v) for k,v in {
        'clear_width_m':3., 'clear_height_m':3., 'wall_thickness_m':.275,
        'top_slab_thickness_m':.25, 'bottom_slab_thickness_m':.275,
        'haunch_width_m':.15, 'haunch_height_m':.15, 'haunch_count':4,
    }.items())


def _model(inputs, elastic_modulus_gpa, mesh_divisions):
    inputs.validate()
    if int(inputs.number_of_cells)!=1 or not _close(inputs.internal_wall_thickness_m,0.):
        raise ValueError('Vehicle model supports single-cell culverts only')
    if int(inputs.haunch_count)!=4:
        raise ValueError('The original 26/16-row selectors require four haunches; zero-haunch mapping is not validated')
    if not math.isfinite(elastic_modulus_gpa) or elastic_modulus_gpa<=0:
        raise ValueError('Concrete modulus must be positive')
    if isinstance(mesh_divisions,bool) or int(mesh_divisions)!=mesh_divisions or not 2<=int(mesh_divisions)<=24:
        raise ValueError('FEM mesh divisions must be 2–24')
    span=float(inputs.clear_width_m+inputs.wall_thickness_m)
    rise=float(inputs.clear_height_m+(inputs.top_slab_thickness_m+inputs.bottom_slab_thickness_m)/2)
    half=float(inputs.wall_thickness_m/2)
    face=half+float(inputs.haunch_width_m)
    low=float(inputs.bottom_slab_thickness_m/2)
    high=float(inputs.top_slab_thickness_m/2)
    h=float(inputs.haunch_height_m)
    def slab(n):
        return [0.,half,face]+[face+(span-2*face)*j/n for j in range(1,n+1)]+[span-half,span]
    bx,tx=slab(10),slab(11)
    ys=[0.,low,low+h,rise/2,rise-high-h,rise-high,rise]
    if any(b-a<=1e-8 for stations in (bx,tx,ys) for a,b in zip(stations,stations[1:])):
        raise ValueError('Haunch/member stations overlap for this geometry')
    nodes={**{i+1:(x,0.) for i,x in enumerate(bx)},
           **{i+16:(x,rise) for i,x in enumerate(tx)},
           **{i+32:(0.,y) for i,y in enumerate(ys[1:-1])},
           **{i+37:(span,y) for i,y in enumerate(ys[1:-1])}}
    exact_geometry=_reference_geometry(inputs)
    # The STD reference was saved with rounded node coordinates. Preserve
    # those actual source coordinates at that exact geometry, not at 250 mm walls.
    if exact_geometry:
        nodes=dict(STD_NODES)
    beams=dict(STD_BEAMS)
    sections={m:(float(inputs.bottom_slab_thickness_m if m<200 else
                         inputs.top_slab_thickness_m if m<1000 else inputs.wall_thickness_m),1.) for m in beams}
    # Original tributary springs use analytical (unrounded) station lengths.
    # Refinement adds springs at new bottom nodes, preserving total k_s * span.
    refinement=max(1,math.ceil(int(mesh_divisions)/8))
    source_map={}; fine_beams={}; fine_sections={}; next_node=max(nodes)+1; next_beam=10000
    bottom_x={i+1:x for i,x in enumerate(bx)}
    for m,(a,b) in beams.items():
        chain=[a]
        for j in range(1,refinement):
            t=j/refinement
            nodes[next_node]=tuple((1-t)*u+t*v for u,v in zip(nodes[a],nodes[b]))
            if m<200: bottom_x[next_node]=(1-t)*bottom_x[a]+t*bottom_x[b]
            chain.append(next_node);next_node+=1
        chain.append(b); segment_ids=[]
        for j,(i,k) in enumerate(zip(chain,chain[1:])):
            ident=m if refinement==1 else next_beam
            if refinement>1:next_beam+=1
            fine_beams[ident]=(i,k);fine_sections[ident]=sections[m];segment_ids.append(ident)
        source_map[m]=(segment_ids[0],segment_ids[-1])
    ordered=sorted(bottom_x,key=bottom_x.get); springs={}
    ks=float(inputs.subgrade_reaction_modulus_tf_m3)
    for j,n in enumerate(ordered):
        width=(bottom_x[n]-bottom_x[ordered[j-1]])/2 if j else 0.
        if j+1<len(ordered):width+=(bottom_x[ordered[j+1]]-bottom_x[n])/2
        springs[n]=ks*width
    if exact_geometry and refinement==1:
        springs={n:k*ks/REFERENCE_SOIL_K for n,k in STD_SPRINGS.items()}
    if not _close(sum(springs.values()),ks*span):
        raise AssertionError('Tributary spring widths do not sum to the slab span')
    top=[seg for m in beams if 201<=m<=215 for seg in (
        range(source_map[m][0],source_map[m][1]+1) if refinement>1 else (m,))]
    return nodes,fine_beams,springs,fine_sections,STD_FIXED,float(elastic_modulus_gpa)*1e6/TF_TO_KN,source_map,top,refinement


def _same_vehicle_loading(vehicle,code):
    p=vehicle_preset(code)
    fields=('wheel_loads_tf','axle_spacings_m','tyre_transverse_width_m','wearing_coat_m',
            'fill_spread_each_side_per_m','avail_edge_width_m','avail_centre_width_m',
            'impact_override','sv_gap1_m','sv_gap2_m')
    def same(a,b):
        if a is None or b is None:return a is b
        if isinstance(a,(tuple,list)) and isinstance(b,(tuple,list)):
            return len(a)==len(b) and all(_close(x,y) for x,y in zip(a,b))
        return _close(a,b)
    if not all(same(getattr(vehicle,f),getattr(p,f)) for f in fields):return False
    # Older app builds omitted this field even when the preset was unchanged.
    g=getattr(vehicle,'generated_axle_spacings_m',None)
    pg=getattr(p,'generated_axle_spacings_m',None)
    return g is None or same(g,pg)


def _source_loads_applicable(inputs, vehicle, code, step):
    # The STD files contain dispersed load intensities, not the full road
    # cross-section/cover parameters. The supplied workbook reference is
    # nominally 12 m long with 0.30 m fill. Use source loads ONLY under those
    # reference loading assumptions; actual stiffness may still be edited.
    return (_close(inputs.clear_width_m+inputs.wall_thickness_m,REFERENCE_SPAN)
            and _close(inputs.length_m,12.) and _close(inputs.earth_cushion_m,.30)
            and _close(step,.25) and _same_vehicle_loading(vehicle,code))


def _source_cases(code):
    cases=[]
    for typ,x,loads,dists in STD_LOAD_CASES[code]:
        points=[]
        for i,p in enumerate(loads):
            if i:x+=dists[i-1]
            points.append((x,p))
        cases.append((typ,points))
    return cases


def _regenerated_cases(inputs, vehicle, code, step):
    # New geometry/loads require new intensities. Use the existing workbook
    # dispersion equations, with the actual generated station spacing for
    # force application. These independent sampled cases are not the original
    # saved STAAD cases. The tracked vehicle is a 5-station equivalent model.
    preset=vehicle_preset(code)
    spacing=getattr(vehicle,'generated_axle_spacings_m',None)
    if spacing is None and tuple(vehicle.axle_spacings_m)==tuple(preset.axle_spacings_m):
        spacing=getattr(preset,'generated_axle_spacings_m',None)
    spacing=tuple(spacing or vehicle.axle_spacings_m)
    args={'axle_spacings_m':spacing}
    if hasattr(vehicle,'generated_axle_spacings_m'):args['generated_axle_spacings_m']=spacing
    v=replace(vehicle,**args)
    v.validate()
    span=float(inputs.clear_width_m+inputs.wall_thickness_m)
    travel=span+sum(spacing)
    n=int(math.ceil(travel/step))
    if n>2000:raise ValueError('Too many vehicle positions; increase the LL scan spacing')
    places=[min(i*step,travel) for i in range(n+1)]
    cases=[]
    for x in places:
        calculated=calculate_axle_dispersion(inputs,replace(v,lead_axle_position_m=x))
        points=[(float(r['distance_from_left_support_m']),float(r['load_per_unit_width_tf_m']))
                for r in calculated['axles'] if r['on_span']]
        cases.append((x,points))
    return cases


def _solve_cases(model,cases):
    nodes,beams,springs,sections,fixed,E,source_map,top,refinement=model
    nd=3*len(nodes);K=np.zeros((nd,nd));elements={};index={n:i*3 for i,n in enumerate(nodes)}
    for m,(a,b) in beams.items():
        x,y=nodes[a];dx,dy=np.array(nodes[b])-np.array(nodes[a]);L=math.hypot(dx,dy);c,s=dx/L,dy/L
        T=np.zeros((6,6));T[:3,:3]=T[3:,3:]=[[c,s,0],[-s,c,0],[0,0,1]]
        h,w=sections[m];ea=E*h*w;ei=E*w*h**3/12;A=ea/L;B=12*ei/L**3;C=6*ei/L**2;D=4*ei/L;F=2*ei/L
        k=np.array([[A,0,0,-A,0,0],[0,B,C,0,-B,C],[0,C,D,0,-C,F],[-A,0,0,A,0,0],[0,-B,-C,0,B,-C],[0,C,F,0,-C,D]])
        ids=np.r_[index[a]:index[a]+3,index[b]:index[b]+3];K[np.ix_(ids,ids)]+=T.T@k@T
        elements[m]=(ids,T,k,L,x,y,dx,dy)
    for n,k in springs.items():K[index[n]+1,index[n]+1]+=k
    free=np.array(sorted(set(range(nd))-{index[n]+d for n,d in fixed}))
    loads=np.zeros((nd,len(cases)));local={m:np.zeros((6,len(cases))) for m in beams};ignored=0
    for col,(_,points) in enumerate(cases):
        for x,p in points:
            if not math.isfinite(x) or not math.isfinite(p) or p<0:raise ValueError('Invalid vehicle point load')
            candidates=[m for m in top if elements[m][4]-1e-9<=x<=elements[m][4]+elements[m][6]+1e-9]
            if not candidates:ignored+=1;continue
            m=candidates[0];ids,T,k,L,x0,y0,dx,dy=elements[m];r=min(1.,max(0.,(x-x0)/dx))
            q=-p*np.array([0,1-3*r*r+2*r**3,L*(r-2*r*r+r**3),0,3*r*r-2*r**3,L*(-r*r+r**3)])
            local[m][:,col]+=q;loads[ids,col]+=T.T@q
    u=np.zeros_like(loads);u[free,:]=np.linalg.solve(K[np.ix_(free,free)],loads[free,:])
    residual=float(np.max(np.abs((K@u-loads)[free,:])))
    if not math.isfinite(residual) or residual>1e-5:raise ValueError(f'Vehicle equilibrium failed: {residual:g} tf')
    # Source member-end selectors always refer to the original 41 members.
    # When refined, recover actions at the original endpoints, not at the
    # new internal subdivisions. Signs follow the verified source replay.
    out={}
    for m,(first,last) in source_map.items():
        for end,seg,off,sign in [('start',first,0,1.),('end',last,3,-1.)]:
            ids,T,k,*_=elements[seg];q=k@T@u[ids,:]-local[seg]
            vals={}
            for name,values,unit in [('Fy',sign*q[off+1,:],'tf'),('Mz',sign*q[off+2,:],'tfm')]:
                vals[f'{name}_min_{unit}']=float(min(0.,np.min(values)))
                vals[f'{name}_max_{unit}']=float(max(0.,np.max(values)))
            if not all(math.isfinite(v) for v in vals.values()):raise ValueError('Nonfinite member action')
            out[(m,end)]=vals
    return out,{'sample_count':len(cases),'nodes':len(nodes),'members':len(beams),
                'spring_count':len(springs),'max_equilibrium_residual_tf':residual,
                'off_span_loads':ignored,'mesh_refinement':refinement}


def predict_std_based_vehicle_envelopes(inputs, *, elastic_modulus_gpa=32.,
                                        mesh_divisions_per_member=8,step_m=.25,
                                        vehicle_overrides=None):
    """Return vehicle envelopes with explicit source-vs-regenerated case provenance."""
    step=float(step_m)
    if not math.isfinite(step) or not .05<=step<=2.:raise ValueError('LL scan step must be 0.05–2 m')
    overrides=vehicle_overrides or {}
    if set(overrides)-set(VEHICLES):raise ValueError('Unsupported vehicle override')
    model=_model(inputs,float(elastic_modulus_gpa),mesh_divisions_per_member)
    envelopes={};meta={}
    for code in VEHICLES:
        v=overrides.get(code) or vehicle_preset(code)
        v.validate()
        source=_source_loads_applicable(inputs,v,code,step)
        cases=_source_cases(code) if source else _regenerated_cases(inputs,v,code,step)
        envelopes[code],meta[code]=_solve_cases(model,cases)
        meta[code].update({'load_basis':'Original STD generated cases' if source else 'Regenerated sampled cases',
                           'source_cases':source,'step_m':step})
    reference_geometry=_reference_geometry(inputs)
    reference_support=_close(inputs.subgrade_reaction_modulus_tf_m3,1000.)
    reference_mesh=model[-1]==1
    exact_E=_close(elastic_modulus_gpa,REFERENCE_E_GPA)
    # Saved output comparisons are meaningful at the source model only.
    # Allow the UI's rounded 32 GPa input to be reported as a near-source
    # benchmark, but never claim exact model identity with it.
    reference_loads=all(x['source_cases'] for x in meta.values())
    return {'envelopes':envelopes,'scan_info':meta,
            'reference_geometry':reference_geometry,'reference_support':reference_support,
            'reference_mesh':reference_mesh,'reference_modulus':exact_E,
            'reference_loads':reference_loads,
            'reference_modulus_gpa':REFERENCE_E_GPA,
            'reference_case':reference_geometry and reference_support and reference_mesh and reference_loads and exact_E,
            'near_reference_case':reference_geometry and reference_support and reference_mesh and reference_loads and abs(float(elastic_modulus_gpa)-REFERENCE_E_GPA)<.5}
