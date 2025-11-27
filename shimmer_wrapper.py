#!/usr/bin/env python3
"""
Wrapper for shimmerCalibrate.py
--------------------------------
Mimics test.py behavior but uses functions from shimmerCalibrate.py.

Usage:
    python shimmer_wrapper.py test_files/data/realActions/DC95_left.txt
Outputs:
    for_compare/DC95_left-Decoded.mat
    for_compare/DC95_left.json
"""

import os
import sys
import json
from scipy.io import savemat

# Import your decoder
from shimmerCalibrate import read_shimmer_dat


def save_outputs(sensor_data, input_path: str):
    """Save sensor_data to MAT + JSON files under 'for_compare' folder."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    compare_dir = os.path.join(base_dir, "for_compare")
    os.makedirs(compare_dir, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(input_path))[0]
    json_path = os.path.join(compare_dir, f"{base_name}.json")
    mat_path = os.path.join(compare_dir, f"{base_name}-Decoded.mat")

    # ---- JSON ----
    # Filter out any non-serializable fields (like bytes)
    json_safe = {}
    for k, v in sensor_data.items():
        if isinstance(v, (bytes, bytearray)):
            continue
        elif isinstance(v, list):
            # Limit excessive nested objects
            json_safe[k] = v
        elif isinstance(v, (int, float, str, bool, type(None))):
            json_safe[k] = v
        else:
            try:
                json_safe[k] = str(v)
            except Exception:
                json_safe[k] = "<unserializable>"

    with open(json_path, "w") as f:
        json.dump(json_safe, f, indent=2)
    print(f"✅ JSON saved to {json_path}")

    # ---- MAT ----
    mat_dict = {"rightSensorData": sensor_data}
    savemat(mat_path, mat_dict)
    print(f"✅ MAT saved to {mat_path}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python shimmer_wrapper.py <input_file.txt|input_file.dat>")
        sys.exit(1)

    input_path = sys.argv[1]
    if not os.path.exists(input_path):
        print(f"❌ File not found: {input_path}")
        sys.exit(1)

    print(f"🔍 Decoding with shimmerCalibrate: {input_path}")

    try:
        with open(input_path, "rb") as f:
            file_bytes = f.read()

        sensor_data = read_shimmer_dat(file_bytes)
        print(f"✅ Successfully decoded {len(sensor_data.get('timestamps', []))} samples")

        save_outputs(sensor_data, input_path)
        print("🎯 Wrapper complete — outputs stored under 'for_compare/'")

    except Exception as e:
        print(f"❌ Error during decoding: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
