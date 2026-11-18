"""Numerical diagnostics for the experimental 1x3x3 culvert frame model.

Run from project root:
    python run_solver_audit.py
    python run_solver_audit.py --skip-envelope

Writes CSVs and a Markdown report under solver_audit_output/.
This tool is a SOFTWARE BENCHMARK, not engineering-design verification.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from src.inputs import CulvertInputs
from src.frame_solver import run_frame_analysis, FrameOptions
from src.calculations import (
    calculate_live_frame_flexural_demands,
    calculate_moving_vehicle_uls_envelope,
    calculate_uls_flexural_strength,
)
from src.load_combinations import CombinationInputs


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer=csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run_audit(outdir: Path, *, include_envelope: bool=True) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    inputs=CulvertInputs()  # original reference dimensions; modify here for a custom study
    combo=CombinationInputs()  # all 1.0, NOT an approved ULS factor set
    mesh_rows=[]
    for mesh in (4,8,16):
        options=FrameOptions(mesh_divisions_per_member=mesh)
        result=run_frame_analysis(inputs,combo=combo,options=options)
        moments=calculate_live_frame_flexural_demands(inputs,result)
        mesh_rows.append({
            'Mesh divisions / member':mesh,
            'Node count':result['node_count'],
            'Max nodal translation (mm)':round(result['max_nodal_displacement_mm'],8),
            'Fx residual (kN/1m strip)':round(result['equilibrium_residual_fx_kn_per_m'],10),
            'Fy residual (kN/1m strip)':round(result['equilibrium_residual_fy_kn_per_m'],10),
            'Moment residual (kN.m/1m strip)':round(result['equilibrium_residual_moment_knm_per_m'],10),
            'Top slab midspan face demand (tf.m/m)':round(moments[2]['moment_tf_m_per_m'],8),
            'Bottom slab midspan face demand (tf.m/m)':round(moments[5]['moment_tf_m_per_m'],8),
        })
    _write_csv(outdir/'mesh_convergence.csv',mesh_rows)

    comparison=[]
    envelope_count=0
    if include_envelope:
        options=FrameOptions(mesh_divisions_per_member=8)
        envelope=calculate_moving_vehicle_uls_envelope(
            inputs,combo=combo,options=options,step_m=0.15)
        envelope_count=envelope['scenario_count']
        reference=calculate_uls_flexural_strength(inputs)['rows']
        live=calculate_uls_flexural_strength(inputs,live_demands=envelope['rows'])['rows']
        for ref,calc in zip(reference,live):
            comparison.append({
                'Component':ref['Component'],
                'Section':ref['Section'],
                'Face':ref['Face'],
                'Excel moment (tf.m/m)':ref['Design Moment (t-m)'],
                'Live experimental moment (tf.m/m)':calc['Design Moment (t-m)'],
                'Excel d min (mm)':ref['d min (mm)'],
                'Live experimental d min (mm)':calc['d min (mm)'],
                'Excel Ast required (mm2/m)':ref['Ast Reqd. (mm²/m)'],
                'Live experimental Ast required (mm2/m)':calc['Ast Reqd. (mm²/m)'],
                'Live effective depth (mm)':calc['d provided (mm)'],
                'Governing wheel lead position (m)':calc['Governing axle position (m)'],
            })
        _write_csv(outdir/'excel_vs_live_fem.csv',comparison)

    max_residual=max(max(abs(row['Fx residual (kN/1m strip)']),
                         abs(row['Fy residual (kN/1m strip)']),
                         abs(row['Moment residual (kN.m/1m strip)'])) for row in mesh_rows)
    lines=[
        '# Structural solver numerical audit — single-cell RCC box culvert',
        '',
        'This audit checks code arithmetic and selected numerical properties only.',
        '**It does not certify the structural model, IRC loading, ULS combinations, or reinforcement safety.**',
        '',
        '## Reference case',
        '- Opening: 3.0 m clear width × 3.0 m clear height; one cell',
        '- 1 m longitudinal analytical strip; 12 m overall length',
        '- Single horizontal anchor at bottom-left; distributed Winkler vertical base springs',
        '- 32 GPa beam elasticity, 1000 tf/m³ soil-spring modulus',
        '- All component factors are 1.0 (illustrative only; NOT approved ULS factors)',
        '- Default Class 70R(W) vehicle station pattern and one braking direction',
        '',
        '## Numerical checks',
        f'- Maximum measured global Fx/Fy/moment residual over meshes: {max_residual:.3g} in the displayed force/moment units',
        '- Analytical simply supported beam checks (uniform/point loads) are automated in tests/test_solver_stage1_verification.py',
        '- Mesh sensitivity results: `mesh_convergence.csv` (4, 8, and 16 elements per member)',
    ]
    if include_envelope:
        lines += [
            f'- Moving vehicle position cases evaluated: {envelope_count}',
            '- Sixteen-row comparison against original Excel reference: `excel_vs_live_fem.csv`',
            '',
            '### Example — top slab midspan',
            f'- Excel moment: {comparison[2]["Excel moment (tf.m/m)"]:.6g} tf·m/m',
            f'- Live experimental moment: {comparison[2]["Live experimental moment (tf.m/m)"]:.6g} tf·m/m',
            f'- Excel dmin: {comparison[2]["Excel d min (mm)"]:.6g} mm',
            f'- Live experimental dmin: {comparison[2]["Live experimental d min (mm)"]:.6g} mm',
        ]
    lines += [
        '',
        '## Outstanding ENGINEERING issues — not solved by this patch',
        '1. Default user-configured 1.0 factors are not verified ULS/SLS combinations.',
        '2. Wearing course, crash barriers, parapets and temperature are not all represented as loads in frame_solver.py.',
        '3. Haunch weight is approximated at corners; haunch stiffening is absent from element EI.',
        '4. Effective-width wheel intensities are approximated as 2D frame point loads, not verified contact-patch loading.',
        '5. Earth pressure datum, braking application and foundation boundary conditions require engineering justification.',
        '6. Compression-only soil springs are nonlinear and cannot be verified using the linear superposition check.',
        '7. Section/face mapping, approved design factors and detailing require independent engineering validation.',
        '',
        'Do not publish the displayed ULS checks as verified structural design results until these items are resolved.',
    ]
    (outdir/'audit_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return {'output_dir':str(outdir),'mesh_rows':mesh_rows,
            'envelope_scenarios':envelope_count,'comparison_rows':len(comparison)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-envelope',action='store_true',help='Only run equilibrium and mesh checks')
    parser.add_argument('--output',default='solver_audit_output',help='Directory for audit results')
    args=parser.parse_args()
    summary=run_audit(Path(args.output),include_envelope=not args.skip_envelope)
    print('Audit files saved under:',summary['output_dir'])
    print('Mesh cases:',len(summary['mesh_rows']))
    print('Moving vehicle scenarios:',summary['envelope_scenarios'])
    print('Excel/FEM comparison rows:',summary['comparison_rows'])
    print('NOTE: Numerical checks only; this is NOT validated structural design.')
