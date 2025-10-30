import json
from datetime import datetime

for fname in ["test_files/left.json", "test_files/right.json"]:
    with open(fname) as f:
        data = json.load(f)
    ts = data["timestampCal"][0]
    iso = datetime.utcfromtimestamp(ts).isoformat() + "Z"
    print(f"{fname}: {ts} -> {iso}")