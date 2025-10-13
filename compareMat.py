import numpy as np
from scipy.io import loadmat

def compare_sensorData_struct_vs_flat(ours_path, his_path):
    ours = loadmat(ours_path, squeeze_me=True, struct_as_record=False)
    his = loadmat(his_path, squeeze_me=True, struct_as_record=False)

    sensor = ours.get("sensorData")
    if sensor is None:
        raise ValueError(f"{ours_path} missing sensorData variable")

    his_vars = {k: v for k, v in his.items() if not k.startswith("__")}
    print(f"\nComparing your {ours_path}  ↔  {his_path}")
    print("=" * 120)

    matched = 0
    total_err = []

    for key in sensor._fieldnames:
        val_ours = getattr(sensor, key)
        # try to find corresponding variable in his file
        candidates = []
        for his_k in his_vars:
            k_low = key.lower()
            his_low = his_k.lower()
            if his_low.endswith(k_low) or his_low.endswith(k_low + "_cal") or his_low.endswith(k_low + "_uncal"):
                candidates.append(his_k)
            elif k_low.endswith("_cal") and "_cal" in his_low and k_low.replace("_cal", "") in his_low:
                candidates.append(his_k)
            elif k_low.endswith("_uncal") and "_uncal" in his_low and k_low.replace("_uncal", "") in his_low:
                candidates.append(his_k)

        if not candidates:
            continue

        # choose the closest match name-length-wise
        his_key = sorted(candidates, key=lambda c: abs(len(c) - len(key)))[0]
        val_his = his_vars[his_key]

        a = np.array(val_ours, dtype=float).ravel()
        b = np.array(val_his, dtype=float).ravel()
        if a.size == 0 or b.size == 0:
            continue

        n = min(a.size, b.size)
        a, b = a[:n], b[:n]

        diff = np.abs(a - b)
        mean_err = np.mean(diff)
        max_err = np.max(diff)
        rms_err = np.sqrt(np.mean(diff ** 2))
        rel_err = mean_err / (np.mean(np.abs(a)) + 1e-12)

        # STRICT equality check
        ok = np.array_equal(a, b)
        status = "✅ EXACT MATCH" if ok else "❌ MISMATCH"

        print(
            f"{key:25s} ↔ {his_key:40s} | "
            f"mean={mean_err:10.3e}, max={max_err:10.3e}, rms={rms_err:10.3e}, rel={rel_err:10.3e} → {status}"
        )

        total_err.append(rms_err)
        matched += 1

    print(f"\nMatched {matched} variables out of {len(sensor._fieldnames)} from your file.")
    if total_err:
        print(f"Overall RMS error across all matched variables: {np.mean(total_err):.3e}")

if __name__ == "__main__":
    # Only compare calibrated files (no uncalibrated)
    compare_sensorData_struct_vs_flat("CalibratedData.mat", "His_Calibrated_SD.mat")
    compare_sensorData_struct_vs_flat("UncalibratedData.mat", "His_Uncalibrated_SD.mat")
