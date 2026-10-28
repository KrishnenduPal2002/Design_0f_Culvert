# RCC Box Culvert Python Prototype

This is a **working software prototype** for geometry, simplified concrete quantities, preliminary concrete self-weight, schematic PNG/DXF output, and workbook formula auditing.

**It is not a conversion of all Excel formulas, not a structural analysis engine, not an approved structural design, and must not be used for construction or safety decisions.**

## Setup (Windows / VS Code)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

If Python 3.11 is not installed, use a supported Python version and adjust the launcher.

## Command-line tools

```powershell
.\.venv\Scripts\python.exe run_preview.py
.\.venv\Scripts\python.exe run_audit.py
.\.venv\Scripts\python.exe -m pytest -q
```

The workbook provided with the assignment is included at `data/original_design.xlsx` for private reference. Do not publish it without permission. The audit writes a formula inventory to `outputs/formula_inventory.csv`. It does not execute or validate formulas.

## Files

- `app.py`: Streamlit UI
- `src/inputs.py`: input model and validation
- `src/geometry.py`: simplified rectangular geometry and volumes
- `src/loads.py`: preliminary concrete self-weight only
- `src/calculations.py`: preview orchestration and limitations
- `src/drawings.py`: schematic PNG and DXF outline
- `src/reports.py`: JSON and CSV export
- `src/excel_audit.py`: Excel formula/reference inventory
- `src/validation.py`: arithmetic consistency and comparison helpers
- `tests/test_calculations.py`: basic automated tests

## Required next steps for a real engineering application

1. Engineer identifies editable cells and expected outputs in the source workbook.
2. Resolve broken Excel references and obtain any missing linked workbooks.
3. Determine how STAAD or other structural analysis results are produced.
4. Translate each approved formula with units, rounding and dependency tracking.
5. Implement load cases, ULS/SLS, reinforcement, BBS and BOQ where required.
6. Compare multiple cases to independently verified engineer-approved results.
7. Have a licensed/responsible engineer review all engineering outputs and drawings.

Assumptions: one rectangular cell, uniform wall and slab thickness, no haunches or other special concrete volumes. Concrete unit weight defaults to 25 kN/m³ **as a placeholder requiring confirmation**. The earth cushion is recorded but not used in load calculations.
