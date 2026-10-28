from src.inputs import CulvertInputs
from src.calculations import calculate_preview
import json

if __name__ == "__main__":
    print(json.dumps(calculate_preview(CulvertInputs()), indent=2))
