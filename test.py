import sys
from shimmer_decode import read_shimmer_dat_file_as_txt

def parse_custom_filename(fname):
    parts = fname.split("__")
    device = parts[0] if len(parts) > 0 else "none"
    timestamp = parts[1] if len(parts) > 1 else "none"
    experiment_name = parts[2] if len(parts) > 2 else "none"
    shimmer_field = parts[3] if len(parts) > 3 else "none"
    filename = parts[5] if len(parts) > 5 else "none"
    shimmer_device = shimmer_field
    shimmer_day = "none"
    if shimmer_field != "none" and "-" in shimmer_field:
        shimmer_device, shimmer_day = shimmer_field.rsplit("-", 1)
    ext = ""
    part = None
    if filename and "." in filename:
        ext = filename.split(".")[-1]
        part = filename.split(".")[0]
    elif filename:
        part = filename
    date = "none"
    time = "none"
    if timestamp and "_" in timestamp:
        ymd, hms = timestamp.split("_", 1)
        if len(ymd) == 8 and len(hms) == 6:
            date = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
            time = f"{hms[:2]}:{hms[2:4]}:{hms[4:6]}"
    return {
        "full_file_name": fname,
        "device": device,
        "timestamp": timestamp,
        "date": date,
        "time": time,
        "experiment_name": experiment_name,
        "shimmer_device": shimmer_device,
        "shimmer_day": shimmer_day,
        "filename": filename,
        "ext": ext,
        "part": part
    }


def main(bin_file_path, fname, patient, decoder):
    meta = parse_custom_filename(fname)
    meta["patient"] = patient
    with open(bin_file_path, "rb") as f:
        file_bytes = f.read()
    decoded = read_shimmer_dat_file_as_txt(file_bytes)
    # Check if lengths of timestamps and Accel_LN_X/Y/Z are equal
    keys = ["timestamps", "Accel_LN_X", "Accel_LN_Y", "Accel_LN_Z"]
    lens = {}
    for k in keys:
        if k in decoded:
            lens[k] = len(decoded[k])
    if len(lens) == 4:
        if len(set(lens.values())) != 1:
            print(f"WARNING: Lengths differ: {lens}")
        else:
            print(f"Lengths OK: {lens}")
    # ...existing code...
    # Convert all numpy arrays in decoded to lists for full JSON output
    for k, v in decoded.items():
        try:
            import numpy as np
            if isinstance(v, np.ndarray):
                decoded[k] = v.tolist()
        except Exception:
            pass
    record = {**meta, **decoded}
    import json
    print(json.dumps(record, indent=2, default=str))


if __name__ == "__main__":
    bin_file_path = "a9ae0f999916e210__20250924_223836__FullC_0719_1750859791__Shimmer_DCFF-000__000"
    fname = "a9ae0f999916e210__20250924_223836__FullC_0719_1750859791__Shimmer_DCFF-000__000.txt"
    patient = "John Doe"
    # Choose decoder: "legacy" for decode_shimmer_file, "matlab" for read_shimmer_dat_file_as_txt
    decoder = "matlab"
    main(bin_file_path, fname, patient, decoder)