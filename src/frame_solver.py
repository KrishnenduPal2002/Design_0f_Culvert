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
    span=inputs.clear_width_m+inputs.wall_thickness_m  # model centreline, NOT outer clear width
    rise=inputs.clear_height_m+0.5*(inputs.top_slab_thickness_m+inputs.bottom_slab_thickness_m)
    n=options.mesh_divisions_per_member
    nodes=[]; lookup={}; elements=[]; groups={key:[] for key in ("Bottom slab","Right wall","Top slab","Left wall")}
    def node(x,y):
        key=(round(x,10),round(y,10))
        if key not in lookup:
            lookup[key]=len(nodes);nodes.append((x,y))
        return lookup[key]
    def member(name,p0,p1,t):
        for i in range(n):
            r0,r1=i/n,(i+1)/n
            x0=p0[0]*(1-r0)+p1[0]*r0;y0=p0[1]*(1-r0)+p1[1]*r0
            x1=p0[0]*(1-r1)+p1[0]*r1;y1=p0[1]*(1-r1)+p1[1]*r1
            a=node(x0,y0);b=node(x1,y1)
            dx=x1-x0;dy=y1-y0
            item={"id":len(elements),"member":name,"a":a,"b":b,"length":math.hypot(dx,dy),
                  "thickness":t,"dofs":[3*a,3*a+1,3*a+2,3*b,3*b+1,3*b+2],
                  "T":_transform(dx,dy)}
            elements.append(item);groups[name].append(item['id'])
    member("Bottom slab",(0,0),(span,0),inputs.bottom_slab_thickness_m)
    member("Right wall",(span,0),(span,rise),inputs.wall_thickness_m)
    member("Top slab",(0,rise),(span,rise),inputs.top_slab_thickness_m)
    member("Left wall",(0,0),(0,rise),inputs.wall_thickness_m)
    bottom_nodes=[(i,coords[0]) for i,coords in enumerate(nodes) if abs(coords[1])<1e-9]
    bottom_nodes.sort(key=lambda v:v[1])
    return np.array(nodes,dtype=float),elements,groups,bottom_nodes,span,rise


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

    compnames=("concrete_dead","soil","surcharge","cushion","vehicle","braking")
    nodal_by_component={key:np.zeros(n_dof) for key in compnames}
    element_fe_by_component={key:[np.zeros(6) for _ in elements] for key in compnames}
    def add_element_load(component:str,e,fl:np.ndarray):
        element_fe_by_component[component][e['id']] += fl
        nodal_by_component[component][e['dofs']] += e['T'].T@fl
    def add_uniform_global(component:str,e,fx:float,fy:float):
        t=e['T'];px,py=(t[:2,:2]@np.array([fx,fy]))
        add_element_load(component,e,_distributed_local(e['length'],lambda r:(px,py)))
    def add_linear_pressure(component:str,e,fx_at_y):
        t=e['T'];L=e['length'];y0=nodes[e['a'],1];y1=nodes[e['b'],1]
        def q(r):
            val=fx_at_y(y0+(y1-y0)*r)
            return t[:2,:2]@np.array([val,0.0])
        add_element_load(component,e,_distributed_local(L,q))
    def add_global_point(component:str,e,r:float,fx:float=0,fy:float=0,mz:float=0):
        px,py=e['T'][:2,:2]@np.array([fx,fy])
        add_element_load(component,e,_point_local(e['length'],r,px,py,mz))
    # Allocate preliminary rectangle/haunch volumes to equivalent modelled
    # centreline members. All values are per metre longitudinal culvert width.
    top_w=inputs.concrete_unit_weight_kn_m3*(inputs.clear_width_m+2*inputs.wall_thickness_m)*inputs.top_slab_thickness_m/span
    bottom_w=inputs.concrete_unit_weight_kn_m3*(inputs.clear_width_m+2*inputs.wall_thickness_m)*inputs.bottom_slab_thickness_m/span
    wall_w=inputs.concrete_unit_weight_kn_m3*inputs.wall_thickness_m*inputs.clear_height_m/rise
    for eid in groups['Top slab']:
        add_uniform_global('concrete_dead',elements[eid],0,-top_w)
    for eid in groups['Bottom slab']:
        add_uniform_global('concrete_dead',elements[eid],0,-bottom_w)
    for member in ('Left wall','Right wall'):
        for eid in groups[member]:
            add_uniform_global('concrete_dead',elements[eid],0,-wall_w)
    # The simplified haunch weights are applied as concentrated gravity loads
    # at the four corner frame nodes; their increased corner stiffness is NOT
    # represented by this constant-section beam model.
    for corner in ((0,0),(span,0),(0,rise),(span,rise)):
        for i,p in enumerate(nodes):
            if np.allclose(p,corner,atol=1e-10):
                nodal_by_component['concrete_dead'][3*i+1] -= (
                    0.5*inputs.haunch_width_m*inputs.haunch_height_m
                    *inputs.concrete_unit_weight_kn_m3*(inputs.haunch_count/4)
                )
                break
    # The earth pressure datum is the top slab upper surface, consistent with
    # the original pressure TABLE inference, not a verified STAAD application.
    # Top slab centreline is half its thickness below that surface.
    offset=inputs.top_slab_thickness_m/2
    for member,sign in (('Left wall',+1),('Right wall',-1)):
        for eid in groups[member]:
            e=elements[eid]
            add_linear_pressure('soil',e, lambda y,s=sign:
                s*earth.k0*earth.backfill_density_t_m3*(offset+rise-y)*TF_TO_KN)
            add_linear_pressure('surcharge',e, lambda y,s=sign:
                s*earth.k0*earth.backfill_density_t_m3*earth.surcharge_equivalent_height_m*TF_TO_KN)
            add_linear_pressure('cushion',e, lambda y,s=sign:
                s*earth.k0*earth.backfill_density_t_m3*earth.earth_cushion_m*TF_TO_KN)
    # Fill directly above slab is a distinct vertical pressure component,
    # included with the lateral cushion effect in the *same* model load case.
    top_fill_kn_per_m=earth.backfill_density_t_m3*earth.earth_cushion_m*TF_TO_KN
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
    dx=span/options.mesh_divisions_per_member
    base_k=options.foundation_modulus_tf_m3*TF_TO_KN*dx
    springs={3*i+1:base_k*(0.5 if k in (0,len(bottom_nodes)-1) else 1.0)
             for k,(i,_) in enumerate(bottom_nodes)}
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
                     'max_abs_shear_kn':0.0,'max_abs_moment_knm':0.0})
            s['max_abs_axial_kn']=max(s['max_abs_axial_kn'],abs(float(q[offset_end])))
            s['max_abs_shear_kn']=max(s['max_abs_shear_kn'],abs(float(q[offset_end+1])))
            s['max_abs_moment_knm']=max(s['max_abs_moment_knm'],abs(float(q[offset_end+2])))
    input_components=[]
    for key in compnames:
        fv=nodal_by_component[key]
        input_components.append({'component':key,'factor':factors[key],
                                 'unfactored_fx_kn':float(sum(fv[0::3])),
                                 'unfactored_fy_kn':float(sum(fv[1::3])),
                                 'applied_fx_kn':float(sum(loads[key][0::3])),
                                 'applied_fy_kn':float(sum(loads[key][1::3]))})
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
        'member_end_actions':end_actions,'member_action_summary':list(summary.values()),
        'foundation_spring_reactions':nodal_reactions,
        'bottom_left_horizontal_anchor_reaction_kn_per_m':float(anchor_reaction),
        'total_applied_fx_kn_per_m':float(external_x),
        'total_applied_fy_kn_per_m':float(external_y),
        'total_foundation_reaction_kn_per_m':float(spring_sum),
        'equilibrium_residual_fx_kn_per_m':float(equilibrium_x),
        'equilibrium_residual_fy_kn_per_m':float(equilibrium_y),
        'max_nodal_displacement_mm':max_nodal_displacement_mm,
        'foundation_active_springs':len(active),
        'converged':converged,
        'status':'EXPERIMENTAL model solved with member load application and combined FEM effects; not externally verified.',
        'limitations':[
            'Not a STAAD connection, validated frame solver, or approved IRC structural design',
            '1m longitudinal strip with approximate centreline frame, element stiffness and corner weights',
            'Horizontal anchor + Winkler vertical springs; foundation/contact behaviour requires engineering review',
            'Earth-pressure datum, opposing wall loading, fill application, braking lever arm/direction are assumptions',
            'Wheel point loads use spreadsheet effective-width intensities; full IRC axle/patch cases are not verified',
            'The user-configured factors are NOT certified ULS/SLS design combinations',
            'Haunch shape affects self-weight but not frame member EI; design note haunch area still differs',
            'No mesh convergence, code-based capacity, crack-width, shear reinforcement, BBS/BOQ or approval',
        ],
    }
