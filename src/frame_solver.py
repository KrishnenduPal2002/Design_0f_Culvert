"""EXPERIMENTAL 2D plane-frame FEM for a 1 m longitudinal RCC box-culvert strip.

This is an independently implemented educational/engineering *prototype*, NOT
STAAD, IRC-compliant verification, a certified structural model, or engineering
approval. Engineering reviewer must verify frame centreline conventions,
loads, direction, units, supports/subgrade springs and mesh convergence.

Coordinate system: x to the right, y upwards; kN, m, kN.m; 1m longitudinal
strip. Each 2-node Euler-Bernoulli frame element has 6 DOF: (ux,uy,rz) each.
A horizontal anchor at bottom left fixes rigid-body drift. Vertical Winkler
springs at bottom nodes give vertical foundation support. A bilateral spring
model resists tension unrealistically; optional compression-only active-set is
experimental and requires checking uplift/contact convergence independently.

All load cases are assembled in a single global vector using USER-SELECTED
factors; this solves combined member actions (rather than summing unrelated
pressures or magnitudes). Factored design limits/envelopes are NOT certified.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import math
import numpy as np

from .inputs import CulvertInputs
from .earth_pressure import EarthPressureInputs, TF_TO_KN
from .traffic_loads import BrakingInputs, braking_cases
from .vehicle_loading import VehicleLoadingInputs, calculate_axle_dispersion
from .load_combinations import CombinationInputs


@dataclass(frozen=True)
class FrameOptions:
    concrete_elastic_modulus_gpa: float = 32.0
    foundation_modulus_tf_m3: float = 1000.0
    mesh_divisions_per_member: int = 8
    compression_only_springs: bool = False
    braking_case: str = "Class 70R(W) + A"
    braking_direction: int = 1

    def validate(self):
        if not (math.isfinite(self.concrete_elastic_modulus_gpa) and 1 <= self.concrete_elastic_modulus_gpa <= 100):
            raise ValueError("Concrete elastic modulus should be 1–100 GPa for this prototype")
        if not (math.isfinite(self.foundation_modulus_tf_m3) and self.foundation_modulus_tf_m3 > 0):
            raise ValueError("Foundation modulus must be a positive tf/m³ value")
        if type(self.mesh_divisions_per_member) is not int or not (2 <= self.mesh_divisions_per_member <= 24):
            raise ValueError("Mesh divisions must be an integer between 2 and 24")
        if self.braking_case not in ("Class 70R(W) + A", "Class 40T + A"):
            raise ValueError("Unknown braking case")
        if self.braking_direction not in (-1, 1):
            raise ValueError("Braking direction must be +1 or -1")


# Signed values: for an element from start to end, (ux,uy,rotation) per node.
def _beam_stiffness(ea: float, ei: float, length: float) -> np.ndarray:
    L=length
    a=ea/L; b=12*ei/L**3; c=6*ei/L**2; d=4*ei/L; e=2*ei/L
    return np.array([
        [ a,0,0,-a,0,0],
        [ 0,b,c,0,-b,c],
        [ 0,c,d,0,-c,e],
        [-a,0,0,a,0,0],
        [ 0,-b,-c,0,b,-c],
        [ 0,c,e,0,-c,d],
    ], dtype=float)


def _transform(dx: float, dy: float) -> np.ndarray:
    length = math.hypot(dx,dy)
    c,s = dx/length, dy/length
    t = np.array([[c,s,0],[-s,c,0],[0,0,1]], dtype=float)
    out = np.zeros((6,6))
    out[:3,:3]=t; out[3:,3:]=t
    return out


def _shape(r: float, L: float) -> tuple[float, float, float, float]:
    return (1-3*r*r+2*r**3, L*(r-2*r*r+r**3),
            3*r*r-2*r**3, L*(-r*r+r**3))


def _distributed_local(L: float, load_at_r) -> np.ndarray:
    """Consistent equivalent nodal forces, exact for linear distribution (3-point Gauss)."""
    # fixed quadrature points/weights over [0,1].
    r_values = (0.5-math.sqrt(3/5)/2, 0.5, 0.5+math.sqrt(3/5)/2)
    weights = (5/18,8/18,5/18)
    f=np.zeros(6)
    for r,w in zip(r_values,weights):
        px,py=load_at_r(r)
        n1,n2,n3,n4=_shape(r,L)
        f+=w*L*np.array([px*(1-r),py*n1,py*n2,px*r,py*n3,py*n4])
    return f


def _point_local(L: float, r: float, px:float=0, py:float=0, mz:float=0) -> np.ndarray:
    n1,n2,n3,n4=_shape(r,L)
    # N' for applied external point moment, conjugate to element cross-section rotation
    dn1=(-6*r+6*r*r)/L
    dn2=1-4*r+3*r*r
    dn3=(6*r-6*r*r)/L
    dn4=-2*r+3*r*r
    return np.array([px*(1-r),py*n1+mz*dn1,py*n2+mz*dn2,
                     px*r,py*n3+mz*dn3,py*n4+mz*dn4])


def _build_mesh(inputs: CulvertInputs, options: FrameOptions):
    """Build a centreline mesh aligned to wall faces and haunch boundaries.

    For the reference 1x3x3 geometry at the default mesh resolution, the
    bottom-slab stations reproduce the 15 supports in the STAAD workbook.
    Other dimensions use the same geometric rules; this is not an override
    with fixed member coordinates or structural analysis results.
    """
    span = inputs.clear_width_m + inputs.wall_thickness_m
    rise = inputs.clear_height_m + 0.5 * (
        inputs.top_slab_thickness_m + inputs.bottom_slab_thickness_m
    )
    # The workbook's bottom and top slab have different internal subdivisions.
    # Further mesh refinement preserves each physical face/haunch station.
    refinement = max(1, math.ceil(options.mesh_divisions_per_member / 8))
    half_wall = inputs.wall_thickness_m / 2.0
    lower_haunch = half_wall + (inputs.haunch_width_m if inputs.haunch_count else 0.0)
    upper_haunch = lower_haunch

    def slab_stations(interior_count: int) -> list[float]:
        left, right = lower_haunch, span - upper_haunch
        segments = [0.0, half_wall, left]
        segments += [left + (right-left)*j / interior_count
                     for j in range(1, interior_count + 1)]
        segments += [span-half_wall, span]
        # Enforce a consistent mesh even when no haunch is modelled.
        return sorted(set(round(v, 12) for v in segments))

    bottom_stations = slab_stations(10 * refinement)
    top_stations = slab_stations(11 * refinement)
    lower = inputs.bottom_slab_thickness_m / 2.0
    upper = inputs.top_slab_thickness_m / 2.0
    haunch_h = inputs.haunch_height_m if inputs.haunch_count else 0.0
    wall_stations = [0.0, lower, lower+haunch_h, rise/2,
                     rise-upper-haunch_h, rise-upper, rise]
    wall_stations = sorted(set(round(v, 12) for v in wall_stations))
    if refinement > 1:
        wall_stations = sorted(set(round(x0+(x1-x0)*j/refinement, 12)
                                   for x0,x1 in zip(wall_stations,wall_stations[1:])
                                   for j in range(refinement+1)))

    nodes=[];lookup={};elements=[]
    groups={key:[] for key in ('Bottom slab','Right wall','Top slab','Left wall')}
    def node(x,y):
        key=(round(x,10),round(y,10))
        if key not in lookup:
            lookup[key]=len(nodes);nodes.append((x,y))
        return lookup[key]
    def member(name,stations,horizontal,constant,t):
        for lo,hi in zip(stations,stations[1:]):
            x0,y0=(lo,constant) if horizontal else (constant,lo)
            x1,y1=(hi,constant) if horizontal else (constant,hi)
            a=node(x0,y0); b=node(x1,y1)
            dx,dy=x1-x0,y1-y0
            item={'id':len(elements),'member':name,'a':a,'b':b,
                  'length':math.hypot(dx,dy),'thickness':t,
                  'dofs':[3*a,3*a+1,3*a+2,3*b,3*b+1,3*b+2],
                  'T':_transform(dx,dy)}
            elements.append(item);groups[name].append(item['id'])
    member('Bottom slab',bottom_stations,True,0.0,inputs.bottom_slab_thickness_m)
    member('Right wall',wall_stations,False,span,inputs.wall_thickness_m)
    member('Top slab',top_stations,True,rise,inputs.top_slab_thickness_m)
    member('Left wall',wall_stations,False,0.0,inputs.wall_thickness_m)
    bottom_nodes=sorted(((i,coords[0]) for i,coords in enumerate(nodes)
                         if abs(coords[1])<1e-9),key=lambda x:x[1])
    return np.array(nodes,dtype=float),elements,groups,bottom_nodes,span,rise


def recover_section_moment_knm(
    start_shear_kn: float, start_moment_knm: float,
    element_length_m: float, distance_from_start_m: float,
    profile: dict,
) -> float:
    """Recover the exact beam-section moment from FE end forces and applied loads.

    M(x)=-M_start+V_start*x+integrals of distributed transverse loading,
    transverse point loads and applied point couples. Unlike straight-line
    interpolation of the two end moments, this captures a wheel acting *inside*
    an element. Local transverse forces and moments must use the same local
    axes as the element recovery forces.
    """
    L=float(element_length_m)
    x=float(distance_from_start_m)
    if not (math.isfinite(x) and math.isfinite(L) and L > 0 and -1e-8 <= x <= L+1e-8):
        raise ValueError('Section station is outside its finite element')
    x=max(0.0,min(L,x))
    p0=float(profile['py_start_kn_per_m'])
    p1=float(profile['py_end_kn_per_m'])
    value=(-float(start_moment_knm)+float(start_shear_kn)*x +
           p0*x*x/2 + (p1-p0)*x**3/(6*L) +
           float(profile.get('py_bubble_kn_per_m', 0.0)) *
           (x**3/L-x**4/(2*L**2)))
    for load in profile.get('point_loads', []):
        xp=float(load['fraction'])*L
        if x + 1e-9 >= xp:
            value += float(load['py_kn'])*(x-xp)-float(load['mz_knm'])
    if not math.isfinite(value):
        raise ValueError('Non-finite recovered section moment')
    return value



def _element_section_extrema(
    start_shear_kn: float, start_moment_knm: float, length_m: float, profile: dict,
) -> dict:
    """Find the largest absolute section moment and shear *inside* an element.

    The moment is a piecewise cubic polynomial under linearly distributed
    transverse loads. Its extrema occur at endpoints, a force discontinuity,
    the two sides of a concentrated couple, or a zero of its derivative
    (shear). Solve the shear polynomial on each interval exactly; no station
    sampling or end-only approximation is used here.

    This is a numerical recovery utility, not a structural-design envelope.
    """
    L = float(length_m)
    if not math.isfinite(L) or L <= 0:
        raise ValueError('Element length must be positive and finite')
    v0 = float(start_shear_kn)
    p0 = float(profile['py_start_kn_per_m'])
    p1 = float(profile['py_end_kn_per_m'])
    pts = [(float(p['fraction'])*L, float(p['py_kn']), float(p['mz_knm']))
           for p in profile.get('point_loads', [])]
    if any(not (math.isfinite(x) and math.isfinite(py) and math.isfinite(mz))
           or x < -1e-9 or x > L+1e-9 for x,py,mz in pts):
        raise ValueError('Invalid concentrated load or section location')
    edges = sorted(set([0.0, L] + [min(L,max(0.,x)) for x,_,_ in pts]))
    # Derivative of section moment on each load-free interval:
    # V(x) = v0 + p0*x + ((p1-p0)/(2L))*x^2 + sum(P_i left of x).
    delta=float(profile.get('py_bubble_kn_per_m', 0.0))
    a=(p1-p0)/(2*L)
    candidates=set(edges)
    for xlo,xhi in zip(edges,edges[1:]):
        active_load=sum(py for xp,py,_ in pts if xp <= xlo+1e-10)
        coeffs=[-2*delta/L**2, a+3*delta/L, p0, v0+active_load]
        while len(coeffs)>1 and abs(coeffs[0])<1e-12:
            coeffs.pop(0)
        for root in np.roots(coeffs):
            if abs(root.imag)<1e-9 and xlo <= root.real <= xhi:
                candidates.add(float(root.real))

    largest_m = (-1., 0.)
    largest_v = (-1., 0.)
    for x in candidates:
        # recover_section_moment_knm includes applied point couples at x,
        # corresponding to the right-hand limit. A finite couple gives a
        # jump; test the left-hand limit as well.
        m_right = recover_section_moment_knm(v0,start_moment_knm,L,x,profile)
        coincident = [(py,mz) for xp,py,mz in pts if abs(xp-x)<=1e-9]
        m_left = m_right + sum(mz for _,mz in coincident)
        for value in (m_right,m_left):
            if abs(value)>largest_m[0]:largest_m=(abs(value),x)

        base_v=v0+p0*x+a*x*x+delta*(3*x*x/L-2*x**3/L**2)
        v_right=base_v+sum(py for xp,py,_ in pts if xp<=x+1e-9)
        v_left=v_right-sum(py for py,_ in coincident)
        for value in (v_left,v_right):
            if abs(value)>largest_v[0]:largest_v=(abs(value),x)
    return {
        'max_abs_moment_knm':largest_m[0],
        'max_abs_moment_station_local_m':largest_m[1],
        'max_abs_shear_kn':largest_v[0],
        'max_abs_shear_station_local_m':largest_v[1],
    }

def run_frame_analysis(
    inputs:CulvertInputs,
    earth:EarthPressureInputs|None=None,
    braking:BrakingInputs|None=None,
    vehicle:VehicleLoadingInputs|None=None,
    combo:CombinationInputs|None=None,
    options:FrameOptions|None=None,
) -> dict:
    """Solve a modelled 2D frame with mutually compatible kN/m-strip loads.

    Not a validated culvert design; design-note STAAD forces must be compared
    separately with actual approved frame supports and load cases.
    """
    inputs.validate()
    earth=earth or EarthPressureInputs(earth_cushion_m=inputs.earth_cushion_m)
    braking=braking or BrakingInputs(length_m=inputs.length_m)
    vehicle=vehicle or VehicleLoadingInputs()
    combo=combo or CombinationInputs()
    options=options or FrameOptions()
    earth.validate();braking.validate();vehicle.validate();combo.validate();options.validate()
    if not math.isclose(earth.earth_cushion_m,inputs.earth_cushion_m,abs_tol=1e-9):
        raise ValueError('Earth cushion must match geometry')
    if not math.isclose(braking.length_m, inputs.length_m,abs_tol=1e-9):
        raise ValueError('Braking length must match geometry')
    nodes,elements,groups,bottom_nodes,span,rise=_build_mesh(inputs,options)
    n_dof=len(nodes)*3
    K=np.zeros((n_dof,n_dof))
    E=options.concrete_elastic_modulus_gpa*1e6  # 1GPa = 10^6 kN/m2
    for e in elements:
        t=e['thickness'];b=1.0;L=e['length']
        kl=_beam_stiffness(E*b*t,E*b*t**3/12,L)
        e['kl']=kl
        kg=e['T'].T@kl@e['T']
        ids=e['dofs'];K[np.ix_(ids,ids)] += kg

    compnames=("concrete_dead","additional_sidl","wearing_course","soil","surcharge","cushion","vehicle","braking")
    nodal_by_component={key:np.zeros(n_dof) for key in compnames}
    element_fe_by_component={key:[np.zeros(6) for _ in elements] for key in compnames}
    # Recover actual section moments within elements (especially under axle
    # point loads), instead of interpolating between element-end moments.
    profile_by_component = {
        key: [{'py0': 0.0, 'py1': 0.0, 'py_bubble': 0.0, 'points': []} for _ in elements]
        for key in compnames
    }
    def add_element_load(component:str,e,fl:np.ndarray):
        element_fe_by_component[component][e['id']] += fl
        nodal_by_component[component][e['dofs']] += e['T'].T@fl
    def add_uniform_global(component:str,e,fx:float,fy:float):
        t=e['T'];px,py=(t[:2,:2]@np.array([fx,fy]))
        add_element_load(component,e,_distributed_local(e['length'],lambda r:(px,py)))
        profile_by_component[component][e['id']]['py0'] += float(py)
        profile_by_component[component][e['id']]['py1'] += float(py)
    def add_variable_top_gravity(component:str,e,kn_per_m_at_x):
        """Consistent nodal forces for a spatially varying top-slab load.

        Recover the endpoint intensity as the member-load profile for section
        actions. The stiffness quadrature evaluates the actual distribution.
        """
        L=e['length']; xa=float(nodes[e['a'],0]); xb=float(nodes[e['b'],0])
        def q(r):
            fx,fy=0.0,-kn_per_m_at_x(xa+(xb-xa)*r)
            return e['T'][:2,:2]@np.array([fx,fy])
        add_element_load(component,e,_distributed_local(L,q))
        p0 = float(q(0.0)[1]); p1 = float(q(1.0)[1])
        profile_by_component[component][e['id']]['py0'] += p0
        profile_by_component[component][e['id']]['py1'] += p1
        # The crash-barrier dispersion q(x) is nonlinear. End-point linear
        # interpolation loses part of its resultant, causing false shear
        # equilibrium failures. Preserve the 3-point Gauss resultant used by
        # the FEM load assembly with a zero-at-ends quadratic bubble:
        # q(r) = p0 + (p1-p0)r + 6*delta*r*(1-r).
        gauss = ((0.5-math.sqrt(3/5)/2,5/18),
                 (0.5,8/18),(0.5+math.sqrt(3/5)/2,5/18))
        average = sum(weight*float(q(r)[1]) for r,weight in gauss)
        profile_by_component[component][e['id']]['py_bubble'] += average - (p0+p1)/2

    def add_linear_pressure(component:str,e,fx_at_y):
        t=e['T'];L=e['length'];y0=nodes[e['a'],1];y1=nodes[e['b'],1]
        def q(r):
            val=fx_at_y(y0+(y1-y0)*r)
            return t[:2,:2]@np.array([val,0.0])
        add_element_load(component,e,_distributed_local(L,q))
        profile_by_component[component][e['id']]['py0'] += float(q(0.0)[1])
        profile_by_component[component][e['id']]['py1'] += float(q(1.0)[1])
    def add_global_point(component:str,e,r:float,fx:float=0,fy:float=0,mz:float=0):
        px,py=e['T'][:2,:2]@np.array([fx,fy])
        add_element_load(component,e,_point_local(e['length'],r,px,py,mz))
        profile_by_component[component][e['id']]['points'].append({
            'fraction': float(r), 'py_kn': float(py), 'mz_knm': float(mz),
        })
    # STAAD 501 SELFWEIGHT is applied to the *centreline frame members* at
    # their actual section thicknesses (UNIT METER MTON, SELFWEIGHT Y -1).
    # Do not scale to the outside footprint or add a separate haunch lump:
    # neither operation appears in the source STAAD 501 member load case.
    # The geometry/quantity schedule is separate from this FEM load model.
    top_w = inputs.concrete_density_tf_m3 * TF_TO_KN * inputs.top_slab_thickness_m
    bottom_w = inputs.concrete_density_tf_m3 * TF_TO_KN * inputs.bottom_slab_thickness_m
    wall_w = inputs.concrete_density_tf_m3 * TF_TO_KN * inputs.wall_thickness_m
    # STAAD 503 is a uniform top-member line load. For the reference geometry,
    # 0.050 m * 2.2 tf/m3 = 0.110 tf/m (per 1m longitudinal strip).
    wearing_w = (inputs.wearing_coat_thickness_m
                 * inputs.wearing_course_density_tf_m3 * TF_TO_KN)
    for eid in groups['Top slab']:
        add_uniform_global('concrete_dead', elements[eid], 0, -top_w)
        if wearing_w:
            add_uniform_global('wearing_course', elements[eid], 0, -wearing_w)
    # STAAD reference load case 502: barrier/parapet SIDL dispersed across
    # the top slab. Source: design-note p.6 "CB Loading Dispersion".
    # The design-note per-barrier line load is 1.375 tf/m, derived from the
    # same cross-sectional areas as the original Dead Load & SIDL schedule:
    # crash-barrier equivalent area 0.400 m2, parapet 0.5 m by 0.3 m.
    # The effective-width equation is beff = b1 + 0.5*alpha*a*(1-a/L).
    # b1=0.85m and alpha=2.6 are *reference design assumptions* not existing
    # Design Inputs. These require engineering approval for other geometries.
    barrier_line_tf_m=(0.400 + inputs.ms_railing_width_m*0.300)*inputs.concrete_density_tf_m3
    cb_b1_m=0.85
    cb_alpha=2.600
    def sidl_top_kn_per_m(x:float)->float:
        a=min(max(x,0.0),max(span-x,0.0))
        beff=cb_b1_m+0.5*cb_alpha*a*(1-a/span)
        return barrier_line_tf_m/beff*TF_TO_KN
    # STAAD 502 applies a uniform load to each top-slab member, evaluated
    # at a section-specific dispersion station (source: CB Dispersion table).
    # Reproduce those 15 member loads from the same input-driven formula,
    # rather than applying a continuously varying pressure to each element.
    # Refining the FEM mesh keeps the parent-member intensity unchanged.
    hw = inputs.wall_thickness_m / 2.0
    haunch_edge = hw + (inputs.haunch_width_m if inputs.haunch_count else 0.0)
    end_edge = span - haunch_edge
    parent_stations = sorted(set(round(x, 12) for x in (
        [0.0, hw, haunch_edge]
        + [haunch_edge + (end_edge-haunch_edge)*j/11 for j in range(1, 12)]
        + [span-hw, span]
    )))
    parent_segments = len(parent_stations) - 1
    for eid in groups['Top slab']:
        el = elements[eid]
        xm = (float(nodes[el['a'], 0]) + float(nodes[el['b'], 0]))/2.0
        parent = next((j for j in range(parent_segments)
                       if parent_stations[j]-1e-10 <= xm <= parent_stations[j+1]+1e-10), None)
        if parent is None:
            raise ValueError('SIDL dispersion could not map top-slab element')
        mirror = min(parent, parent_segments-1-parent)
        if mirror < 2 or mirror == parent_segments//2:
            station = (parent_stations[parent] + parent_stations[parent+1])/2.0
        elif parent < parent_segments//2:
            station = parent_stations[parent+1]
        else:
            station = parent_stations[parent]
        load = sidl_top_kn_per_m(station)
        # STAAD 502 assigns the central top member (208) twice, once from
        # each mirrored half of the CB-dispersion schedule. Reproduce that
        # documented input-model behavior for the reference comparison;
        # the engineer must confirm whether double loading is intended.
        if parent == parent_segments // 2:
            load *= 2.0
        add_uniform_global('additional_sidl', el, 0, -load)

    for eid in groups['Bottom slab']:
        add_uniform_global('concrete_dead', elements[eid], 0, -bottom_w)
    for member in ('Left wall', 'Right wall'):
        for eid in groups[member]:
            add_uniform_global('concrete_dead', elements[eid], 0, -wall_w)
    # Do not add additional haunch node loads in STAAD-equivalent SELFWEIGHT;
    # the idealised frame has constant-thickness members at the corners.
    # The earth pressure datum is the top slab upper surface, consistent with
    # the original pressure TABLE inference, not a verified STAAD application.
    # Top slab centreline is half its thickness below that surface.
    offset=inputs.top_slab_thickness_m/2
    for member,sign in (('Left wall',+1),('Right wall',-1)):
        for eid in groups[member]:
            e=elements[eid]
            # STAAD load case 504 (EARTH PRESSURE) includes BOTH the
            # depth-dependent lateral earth pressure and a uniform GX
            # contribution of +/-0.3 tf/m² for the reference 1x3x3 input.
            # K0 * gamma * cushion = 0.5 * 2.0 * 0.3 = 0.3 tf/m².
            # Assign them to the same component so both receive the 504/EP
            # combination factor (not the separate 505/EC factor).
            add_linear_pressure('soil',e, lambda y,s=sign:
                s*earth.k0*earth.backfill_density_t_m3
                *(offset+rise-y+earth.earth_cushion_m)*TF_TO_KN)
            # STAAD workbook 506/507 put live-load *lateral* surcharge
            # on LHS/RHS, respectively, not both walls in every ULS case.
            # The default both-wall application remains unchanged for the
            # existing Streamlit user-defined factor set.
            if getattr(combo, 'surcharge_side', 'both') in ('both', 'left' if member == 'Left wall' else 'right'):
                add_linear_pressure('surcharge',e, lambda y,s=sign:
                    s*earth.k0*earth.backfill_density_t_m3*earth.surcharge_equivalent_height_m*TF_TO_KN)
    # STAAD reference case 505 (Earth Cushion): the supplied design workbook
    # applies a 0.5 multiplier to the vertical cushion line-load calculation.
    # For gamma=2.0 tf/m3 and h=0.30m, the workbook reports 0.300 tf/m,
    # rather than the full 0.600 tf/m of a 1m-wide soil strip. Preserve the
    # *workbook's distribution convention* in the reference-equivalent model;
    # it remains a design assumption needing the engineer's confirmation.
    # Keep gamma and h input-driven -- no fixed 0.300 tf/m value is injected.
    cushion_vertical_distribution_factor = 0.5
    top_fill_kn_per_m=(cushion_vertical_distribution_factor
                       * earth.backfill_density_t_m3
                       * earth.earth_cushion_m * TF_TO_KN)
    for eid in groups['Top slab']:
        add_uniform_global('cushion',elements[eid],0,-top_fill_kn_per_m)

    # The workbook calculates a wheel force / effective spread width (tf/m).
    # This is modelled here as a transverse FRAME point load per 1m strip.
    # Longitudinal contact/patch geometry and actual axle distribution are
    # not recovered from spreadsheet and require engineer confirmation.
    wheel_results=calculate_axle_dispersion(inputs,vehicle)
    for row in wheel_results['axles']:
        if not row['on_span']:continue
        x=row['distance_from_left_support_m']
        eid=min(groups['Top slab'],key=lambda j:abs((nodes[elements[j]['a'],0]+nodes[elements[j]['b'],0])/2-x))
        el=elements[eid];xa=nodes[el['a'],0];r=(x-xa)/el['length']
        if r<-1e-7 or r>1+1e-7:
            # For exact joints use the immediately adjacent element.
            for j in groups['Top slab']:
                el=elements[j];xa=nodes[el['a'],0];r=(x-xa)/el['length']
                if -1e-9<=r<=1+1e-9:break
            else:
                raise ValueError('Axle position could not be mapped to top slab')
        add_global_point('vehicle',el,min(max(r,0),1),fy=-row['load_per_unit_width_kn_m'])

    bcase=next(row for row in braking_cases(braking) if row['Case']==options.braking_case)
    bf=bcase['Per-metre force (kN/m)']*options.braking_direction  # on a 1m culvert strip
    topmid=elements[groups['Top slab'][len(groups['Top slab'])//2]]
    add_global_point('braking',topmid,0.0 if len(groups['Top slab'])%2==0 else 0.5,
                     fx=bf,mz=-bf*braking.assumed_lever_arm_m)

    factors={
        'concrete_dead':combo.concrete_dead_factor,
        'additional_sidl':getattr(combo, 'effective_additional_sidl_factor', combo.concrete_dead_factor),
        'wearing_course':getattr(combo, 'effective_wearing_course_factor', combo.concrete_dead_factor),
        'soil':combo.soil_factor,
        'surcharge':combo.surcharge_factor,
        'cushion':combo.cushion_factor,
        'vehicle':combo.vehicle_factor,
        'braking':combo.braking_factor,
    }
    loads={key:float(factors[key])*nodal_by_component[key] for key in compnames}
    F=sum(loads.values(),np.zeros(n_dof))
    # Each distributed Winkler spring represents the effective tributary width
    # at a bottom slab node (subgrade modulus: tf/m³ => kN/m³).
    # Tributary widths are derived from the *actual* adjacent node distances.
    # The previous uniform dx gave an end spring of 203.125 tf/m for the
    # reference geometry, instead of the reference 62.5 tf/m.
    springs={}
    for k,(i,x) in enumerate(bottom_nodes):
        left = (x-bottom_nodes[k-1][1])/2 if k else 0.0
        right = (bottom_nodes[k+1][1]-x)/2 if k+1<len(bottom_nodes) else 0.0
        tributary_width=left+right
        springs[3*i+1]=options.foundation_modulus_tf_m3*TF_TO_KN*tributary_width
    fixed=3*bottom_nodes[0][0]  # one horizontal horizontal anchor (ux=0)
    free=np.array([j for j in range(n_dof) if j != fixed],dtype=int)
    active=set(springs)
    if options.compression_only_springs:
        # start all active and iterate spring contact. Unlike bilateral model,
        # individual load effects cannot be superimposed after this nonlinear solve.
        active=set(springs)
    converged=False
    for iteration in range(35):
        Kfull=K.copy()
        for dof in active:Kfull[dof,dof]+=springs[dof]
        try: Ufree=np.linalg.solve(Kfull[np.ix_(free,free)],F[free])
        except np.linalg.LinAlgError as err:
            raise ValueError('Frame stiffness matrix singular; review supports/contact assumptions') from err
        U=np.zeros(n_dof);U[free]=Ufree
        if not options.compression_only_springs:
            converged=True;break
        new_active={dof for dof in springs if U[dof] < -1e-10}
        if new_active==active:
            converged=True;break
        active=new_active
    if not converged:
        raise ValueError('Compression-only foundation springs did not converge; change supports/load case')
    foundation_reaction={dof:-springs[dof]*U[dof] for dof in active}
    anchor_reaction=(Kfull@U-F)[fixed]
    # At the fixed anchor Kfull@U - F = support force acting on the model.
    external_x=sum(float(F[3*j]) for j in range(len(nodes)))
    external_y=sum(float(F[3*j+1]) for j in range(len(nodes)))
    spring_sum=sum(foundation_reaction.values())
    equilibrium_x=external_x+float(anchor_reaction)
    equilibrium_y=external_y+spring_sum
    # Global moment equilibrium about the model origin (bottom-left). The
    # applied vector includes both equivalent nodal loads and actual nodal
    # couples, so the total must include the rz forces as well.
    applied_global_moment=sum(
        float(x*F[3*i+1]-y*F[3*i]+F[3*i+2])
        for i,(x,y) in enumerate(nodes)
    )
    support_global_moment=(
        sum(float(nodes[dof//3,0]*reaction)
            for dof,reaction in foundation_reaction.items())
        -float(nodes[bottom_nodes[0][0],1])*float(anchor_reaction)
    )
    equilibrium_moment=applied_global_moment+support_global_moment
    nodal_reactions=[{'node':i,'x_m':float(nodes[i,0]),'vertical_reaction_kn_per_m':
        float(foundation_reaction.get(3*i+1,0))} for i,_ in bottom_nodes]
    # Element fixed-end (equivalent nodal) actions must be subtracted to
    # recover member end actions; never use K*u alone when loads exist.
    end_actions=[];summary={}
    for e in elements:
        local_U=e['T']@U[e['dofs']]
        fe=sum((factors[key]*element_fe_by_component[key][e['id']] for key in compnames),np.zeros(6))
        q=e['kl']@local_U-fe
        a,b=nodes[e['a']],nodes[e['b']]
        for label,offset_end,xyz in [('start',0,a),('end',3,b)]:
            row={'member':e['member'],'element':e['id']+1,'end':label,
                 'x_m':float(xyz[0]),'y_m':float(xyz[1]),
                 'axial_kn':float(q[offset_end]),
                 'shear_kn':float(q[offset_end+1]),
                 'moment_knm':float(q[offset_end+2])}
            end_actions.append(row)
            s=summary.setdefault(e['member'],{'member':e['member'],'max_abs_axial_kn':0.0,
                     'max_abs_shear_kn':0.0,'max_abs_moment_knm':0.0,
                     'max_abs_moment_station_m':None})
            s['max_abs_axial_kn']=max(s['max_abs_axial_kn'],abs(float(q[offset_end])))
            s['max_abs_shear_kn']=max(s['max_abs_shear_kn'],abs(float(q[offset_end+1])))
            if abs(float(q[offset_end+2])) > s['max_abs_moment_knm'] or s['max_abs_moment_station_m'] is None:
                s['max_abs_moment_station_m']=float(xyz[0] if 'slab' in e['member'] else xyz[1])
            s['max_abs_moment_knm']=max(s['max_abs_moment_knm'],abs(float(q[offset_end+2])))
    member_load_profiles = []
    for e in elements:
        index=e['id']
        points=[]
        for component in compnames:
            weight=float(factors[component])
            for point in profile_by_component[component][index]['points']:
                points.append({
                    'fraction': point['fraction'],
                    'py_kn': point['py_kn']*weight,
                    'mz_knm': point['mz_knm']*weight,
                    'component': component,
                })
        member_load_profiles.append({
            'element': index+1, 'member': e['member'],
            'length_m': float(e['length']),
            'py_start_kn_per_m': sum(float(factors[c])*profile_by_component[c][index]['py0'] for c in compnames),
            'py_end_kn_per_m': sum(float(factors[c])*profile_by_component[c][index]['py1'] for c in compnames),
            'py_bubble_kn_per_m': sum(float(factors[c])*profile_by_component[c][index]['py_bubble'] for c in compnames),
            'point_loads': points,
        })
    # End actions alone do not contain maximum internal beam moments under
    # distributed or internal wheel loads. Recover section extrema analytically
    # on every finite element and update the already-built member summary.
    start_actions={int(row['element']):row for row in end_actions if row['end']=='start'}
    for e,profile in zip(elements,member_load_profiles):
        member_summary=summary[e['member']]
        start_action=start_actions[e['id']+1]
        peak=_element_section_extrema(
            start_action['shear_kn'],start_action['moment_knm'],
            e['length'],profile,
        )
        if peak['max_abs_moment_knm'] >= member_summary['max_abs_moment_knm']:
            member_summary['max_abs_moment_knm']=peak['max_abs_moment_knm']
            member_summary['max_abs_moment_station_m']=(
                float(nodes[e['a'],0] if 'slab' in e['member'] else nodes[e['a'],1])
                +peak['max_abs_moment_station_local_m']
            )
        if peak['max_abs_shear_kn'] > member_summary['max_abs_shear_kn']:
            member_summary['max_abs_shear_kn']=peak['max_abs_shear_kn']

    input_components=[]
    for key in compnames:
        fv=nodal_by_component[key]
        input_components.append({'component':key,'factor':factors[key],
                                 'unfactored_fx_kn':float(sum(fv[0::3])),
                                 'unfactored_fy_kn':float(sum(fv[1::3])),
                                 'applied_fx_kn':float(sum(loads[key][0::3])),
                                 'applied_fy_kn':float(sum(loads[key][1::3]))})
    # Workbook load-case audit. These are *diagnostics*, not extra structural
    # loads or substituted reference forces. No result from this block is used
    # in stiffness assembly, member action recovery, or design checks.
    # The reference STAAD sheet assigns load 502 as eight nonuniform pairs
    # of top-slab member loads; the model does not currently reproduce them.
    # Load 505 is a top-slab vertical intensity of -0.300 tf/m in the
    # original 1x3x3 worksheet (Staad Input(ULS), rows 332 onwards).
    workbook_reference_geometry = (
        abs(inputs.clear_width_m-3.0)<1e-9
        and abs(inputs.clear_height_m-3.0)<1e-9
        and abs(inputs.wall_thickness_m-0.25)<1e-9
        and abs(inputs.earth_cushion_m-0.30)<1e-9
        and abs(inputs.top_slab_thickness_m-0.25)<1e-9
        and abs(inputs.bottom_slab_thickness_m-0.275)<1e-9
    )
    reference_load_case_audit = {
        '502_additional_sidl': {
            'status': 'MODELLED FROM DISPERSION EQUATION; STAAD MEMBER MAPPING NOT VALIDATED',
            'source': 'Design Note p.6, CB Loading Dispersion; STAAD load 502',
            'component': 'additional_sidl',
            'barrier_parapet_tf_per_m': float(barrier_line_tf_m),
            'effective_width_base_m': cb_b1_m,
            'effective_width_alpha': cb_alpha,
            'sample_stations_m': (0.063,0.200,0.520,0.766,1.011,1.257,1.502,1.625),
            'calculated_loads_tf_per_m': [
                round(sidl_top_kn_per_m(x)/TF_TO_KN, 4)
                for x in (0.063,0.200,0.520,0.766,1.011,1.257,1.502,1.625)
            ],
            'use_for_design': False,
        },
        '505_vertical_earth_cushion': {
            'source': 'Staad Input(ULS), rows 332-347',
            'python_unfactored_intensity_tf_per_m': float(
                cushion_vertical_distribution_factor
                * earth.backfill_density_t_m3 * earth.earth_cushion_m),
            'reference_distribution_factor': cushion_vertical_distribution_factor,
            'workbook_reference_intensity_tf_per_m': (
                0.300 if workbook_reference_geometry else None),
            'reference_geometry_applicable': workbook_reference_geometry,
            'status': ('MISMATCH — workbook basis needs confirmation'
                       if workbook_reference_geometry and
                          not math.isclose(cushion_vertical_distribution_factor
                                           * earth.backfill_density_t_m3
                                           * earth.earth_cushion_m,0.300,abs_tol=1e-9)
                       else ('Matches workbook reference intensity (distribution factor is provisional)'
                             if workbook_reference_geometry
                             else 'Reference comparison not established')),
        },
        'use_for_design': False,
    }
    max_nodal_displacement_mm=max(float(np.hypot(U[3*j],U[3*j+1]))*1000 for j in range(len(nodes)))
    return {
        'mode':'EXPERIMENTAL Python 2D Euler-Bernoulli frame FEM; not STAAD / not validated',
        'options':asdict(options),'load_factors':factors,
        'frame_width_m':span,'frame_height_m':rise,
        'strip_width_m':1.0,'node_count':len(nodes),'element_count':len(elements),
        'nodes':[{'node':i,'x_m':float(p[0]),'y_m':float(p[1]),
                  'ux_mm':float(U[3*i]*1000),'uy_mm':float(U[3*i+1]*1000),
                  'rz_rad':float(U[3*i+2])} for i,p in enumerate(nodes)],
        'elements':[{'element':e['id']+1,'member':e['member'],'node_start':e['a'],
                     'node_end':e['b']} for e in elements],
        'load_components':input_components,
        'reference_load_case_audit':reference_load_case_audit,
        'member_end_actions':end_actions,'member_action_summary':list(summary.values()),
        'member_load_profiles':member_load_profiles,
        'foundation_spring_reactions':nodal_reactions,
        'bottom_left_horizontal_anchor_reaction_kn_per_m':float(anchor_reaction),
        'total_applied_fx_kn_per_m':float(external_x),
        'total_applied_fy_kn_per_m':float(external_y),
        'total_foundation_reaction_kn_per_m':float(spring_sum),
        'equilibrium_residual_fx_kn_per_m':float(equilibrium_x),
        'equilibrium_residual_fy_kn_per_m':float(equilibrium_y),
        'equilibrium_residual_moment_knm_per_m':float(equilibrium_moment),
        'max_nodal_displacement_mm':max_nodal_displacement_mm,
        'foundation_active_springs':len(active),
        'converged':converged,
        'status':'EXPERIMENTAL model solved with member load application and combined FEM effects; not externally verified.',
        'limitations':[
            'Not a STAAD connection, validated frame solver, or approved IRC structural design',
            '1m longitudinal strip with approximate centreline frame, element stiffness and corner weights',
            'Horizontal anchor + Winkler vertical springs; foundation/contact behaviour requires engineering review',
            'Earth-pressure datum, opposing wall loading, fill application, braking lever arm/direction are assumptions',
            'STAAD 502 barrier/parapet SIDL uses the workbook member-wise dispersion, including a duplicated centre-member load that requires engineer confirmation',
            'STAAD 504 uniform lateral cushion pressure is grouped with soil/EP; STAAD 505 vertical cushion uses workbook 0.5 load-distribution convention requiring engineer validation',
            'Wheel point loads use spreadsheet effective-width intensities; full IRC axle/patch cases are not verified',
            'The user-configured factors are NOT certified ULS/SLS design combinations',
            'Wearing course now applies as separate gravity load; factor 1.0 is provisional and project-specific load factors require approval',
            'Haunch shape affects self-weight but not frame member EI; design note haunch area still differs',
            'Mesh sensitivity should be checked separately; no code-approved capacity, crack-width, shear reinforcement, BBS/BOQ or approval',
        ],
    }
