#!/usr/bin/env python3
"""
Compare Decoded Shimmer Outputs (MAT + JSON)
============================================

Compares the outputs from `test.py` and `shimmer_wrapper.py`:
- MAT files: deep numeric comparison (within tolerance)
- JSON files: structural + numeric comparison

Usage:
    python compare_decoded_all.py <ref_mat> <cmp_mat>

Example:
    python compare_decoded_all.py \
        test_files/data/realActions/DC95_left-Decoded.mat \
        for_compare/DC95_left-Decoded.mat
"""

import os
import sys
import json
import numpy as np
from scipy.io import loadmat, matlab

# ---------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------

def matlab_to_dict(obj):
    """Recursively convert MATLAB structs (mat_struct) to Python dicts."""
    if isinstance(obj, matlab.mio5_params.mat_struct):
        out = {}
        for field in obj._fieldnames:
            val = getattr(obj, field)
            out[field] = matlab_to_dict(val)
        return out
    elif isinstance(obj, np.ndarray):
        if obj.size == 1:
            return matlab_to_dict(obj.item())
        else:
            return [matlab_to_dict(x) for x in obj]
    else:
        return obj


def load_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error loading JSON {path}: {e}")
        return None


def load_mat(path):
    try:
        data = loadmat(path, squeeze_me=True, struct_as_record=False)
        return {k: matlab_to_dict(v) for k, v in data.items() if not k.startswith("__")}
    except Exception as e:
        print(f"❌ Error loading MAT {path}: {e}")
        return None


def compare_numeric(a, b, tol=1e-6):
    """Compare numeric arrays/lists within tolerance."""
    try:
        a_arr = np.array(a)
        b_arr = np.array(b)
        if a_arr.shape != b_arr.shape:
            return f"❌ shape mismatch {a_arr.shape} vs {b_arr.shape}"

        if np.issubdtype(a_arr.dtype, np.number):
            if np.allclose(a_arr, b_arr, atol=tol, rtol=tol, equal_nan=True):
                return f"✅ match (max diff {np.max(np.abs(a_arr - b_arr)):.2e})"
            else:
                diff = np.abs(a_arr - b_arr)
                return f"❌ values differ (max {np.max(diff):.2e}, mean {np.mean(diff):.2e})"
        else:
            if np.array_equal(a_arr, b_arr):
                return "✅ exact match"
            else:
                return "❌ mismatch (non-numeric)"
    except Exception as e:
        return f"❌ error comparing: {e}"


def compare_dicts(ref_dict, cmp_dict, tol=1e-6, max_keys=30, label=""):
    """Compare two dictionaries key by key."""
    ref_keys = set(ref_dict.keys())
    cmp_keys = set(cmp_dict.keys())
    common = ref_keys & cmp_keys
    only_ref = ref_keys - cmp_keys
    only_cmp = cmp_keys - ref_keys

    print(f"\n📦 Comparing {label} files:")
    print(f"  Shared keys: {len(common)}, Ref-only: {len(only_ref)}, Cmp-only: {len(only_cmp)}")

    if only_ref:
        print(f"  ⚠️ Keys only in reference: {sorted(list(only_ref))[:max_keys]}")
    if only_cmp:
        print(f"  ⚠️ Keys only in compare:   {sorted(list(only_cmp))[:max_keys]}")

    results = []
    for key in sorted(common):
        a = ref_dict[key]
        b = cmp_dict[key]
        if isinstance(a, (list, np.ndarray)) and isinstance(b, (list, np.ndarray)):
            result = compare_numeric(a, b, tol)
        elif isinstance(a, dict) and isinstance(b, dict):
            sub_keys = set(a.keys()) & set(b.keys())
            result = f"{len(sub_keys)} subkeys compared"
        else:
            result = "✅ identical" if a == b else f"❌ differ ({a} vs {b})"
        results.append((key, result))

    print("\n🔍 Detailed comparison (first few keys):")
    for k, r in results[:max_keys]:
        print(f"   {k:<25} {r}")

    diffs = [r for _, r in results if r.startswith("❌")]
    print(f"\n📊 Summary for {label}: {len(common)-len(diffs)}/{len(common)} keys matched ({(len(common)-len(diffs))/max(1,len(common))*100:.1f}%)")
    return len(diffs) == 0


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    if len(sys.argv) < 3:
        print("Usage: python compare_decoded_all.py <ref_mat> <cmp_mat>")
        sys.exit(1)

    ref_mat = sys.argv[1]
    cmp_mat = sys.argv[2]

    if not os.path.exists(ref_mat) or not os.path.exists(cmp_mat):
        print("❌ One or both MAT files not found.")
        sys.exit(1)

    ref_base = os.path.splitext(os.path.basename(ref_mat))[0].replace("-Decoded", "")
    cmp_base = os.path.splitext(os.path.basename(cmp_mat))[0].replace("-Decoded", "")

    # --- Compare MAT ---
    ref_data = load_mat(ref_mat)
    cmp_data = load_mat(cmp_mat)
    if not ref_data or not cmp_data:
        print("❌ Could not load MAT files properly.")
        sys.exit(1)

    # Extract top-level struct (rightSensorData/leftSensorData)
    ref_struct = list(ref_data.values())[0]
    cmp_struct = list(cmp_data.values())[0]
    mat_match = compare_dicts(ref_struct, cmp_struct, label="MAT")

    # --- Compare JSON ---
    ref_json = ref_mat.replace("-Decoded.mat", "-AllChannels.json")
    cmp_json = os.path.join(os.path.dirname(cmp_mat), f"{cmp_base}.json")

    if os.path.exists(ref_json) and os.path.exists(cmp_json):
        ref_json_data = load_json(ref_json)
        cmp_json_data = load_json(cmp_json)
        json_match = compare_dicts(ref_json_data, cmp_json_data, label="JSON")
    else:
        print("⚠️ JSON files not found for comparison.")
        json_match = False

    print("\n==============================")
    print("✅ Overall result:")
    print(f"MAT match:  {'✅' if mat_match else '❌'}")
    print(f"JSON match: {'✅' if json_match else '❌'}")
    print("==============================")

    if mat_match and json_match:
        print("🎉 All outputs match!")
    else:
        print("⚠️ Some differences detected.")


if __name__ == "__main__":
    main()
