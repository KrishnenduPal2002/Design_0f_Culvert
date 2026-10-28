from src.excel_audit import audit_workbook

if __name__ == "__main__":
    print(audit_workbook("data/original_design.xlsx", "outputs/formula_inventory.csv"))
