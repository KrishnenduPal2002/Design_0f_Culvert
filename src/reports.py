import csv
import io
import json


def to_json_bytes(result: dict) -> bytes:
    return json.dumps(result, indent=2, ensure_ascii=False).encode("utf-8")


def to_csv_bytes(result: dict) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Section", "Parameter", "Value"])
    for section in ("inputs", "geometry", "preliminary_self_weight"):
        for key, value in result[section].items():
            writer.writerow([section, key, value])
    writer.writerow(["status", "structural_design_status", result["structural_design_status"]])
    return buffer.getvalue().encode("utf-8-sig")
