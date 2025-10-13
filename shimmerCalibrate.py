# ============================================================
#  read_shimmer_data_file.py
#  Exact MATLAB port of readShimmerDataFile.m
#  Adds: calibrate flag + in-memory decode wrapper for FastAPI
# ============================================================

import struct
import math
import io
from dataclasses import dataclass, asdict
from typing import List, Dict, Any
from datetime import datetime


# ============================================================
# Helper structures and functions
# ============================================================

@dataclass
class Channel:
    name: str
    dtype: str
    nbytes: int
    endian: str


def _sign_extend_24_le(b: bytes) -> int:
    """Interpret 3 bytes (little-endian) as signed 24-bit int."""
    v = b[0] | (b[1] << 8) | (b[2] << 16)
    if b[2] & 0x80:
        v -= 1 << 24
    return v


def _sign_extend_24_be(b: bytes) -> int:
    """Interpret 3 bytes (big-endian) as signed 24-bit int."""
    v = (b[0] << 16) | (b[1] << 8) | b[2]
    if b[0] & 0x80:
        v -= 1 << 24
    return v


def _add_channels(names, dtype, nbytes, endian) -> List[Channel]:
    if isinstance(names, str):
        names = [names]
    return [Channel(n, dtype, nbytes, endian) for n in names]


def array_subtract(arr1, arr2):
    return [a - b for a, b in zip(arr1, arr2)]


def array_divide(arr1, arr2):
    return [a / b if b != 0 else a for a, b in zip(arr1, arr2)]


def matrix_vector_multiply(matrix_3x3, vector_3):
    result = [0.0, 0.0, 0.0]
    for i in range(3):
        for j in range(3):
            result[i] += matrix_3x3[i][j] * vector_3[j]
    return result


# ============================================================
# Calibration and Time Functions
# ============================================================

def parse_inertial_cal_params(header: bytes, sensor: str):
    offsets = {'WR_ACCEL': 76, 'GYRO': 97, 'MAG': 118, 'LN_ACCEL': 139}
    start = offsets[sensor]
    cal = header[start:start + 21]
    offset = list(struct.unpack('>hhh', cal[0:6]))
    gain = list(struct.unpack('>HHH', cal[6:12]))
    align = list(struct.unpack('bbb' * 3, cal[12:21]))
    alignment = [[align[j * 3 + i] for j in range(3)] for i in range(3)]
    return offset, gain, alignment


def apply_inertial_calibration(raw_xyz_list, offset, gain, alignment):
    gain_safe = [g if g != 0 else 1.0 for g in gain]
    align_scaled = [[a / 100.0 for a in row] for row in alignment]
    calibrated = []
    for xyz in raw_xyz_list:
        no_offset = array_subtract(xyz, offset)
        scaled = array_divide(no_offset, gain_safe)
        calibrated.append(matrix_vector_multiply(align_scaled, scaled))
    return calibrated


def time_calibration(sensorData: Dict[str, Any], header: bytes) -> Dict[str, Any]:
    sdhRtcDiff0, sdhRtcDiff7 = 44, 51
    sdhConfigTime0, sdhConfigTime3 = 52, 55
    sdhMyLocalTime5th, sdhMyLocalTimeStart, sdhMyLocalTimeEnd = 251, 252, 255

    phoneRwc = struct.unpack('<I', header[sdhConfigTime0:sdhConfigTime3 + 1])[0]
    shimmerRtc64 = int.from_bytes(header[sdhRtcDiff0:sdhRtcDiff7 + 1], 'little')
    shimmerRtcLower40 = float(shimmerRtc64 % (2 ** 40))
    initialRtcTicks = (header[sdhMyLocalTime5th] * (2 ** 32)) + struct.unpack('<I', header[sdhMyLocalTimeStart:sdhMyLocalTimeEnd + 1])[0]

    raw = sensorData.get('timestamps', [])
    if not raw:
        sensorData['timestampCal'] = []
        return sensorData

    diffs = [raw[i + 1] - raw[i] for i in range(len(raw) - 1)]
    rollover_indices = [i for i, d in enumerate(diffs) if d < -2 ** 23]
    corr = [0] * len(raw)
    rollover_count = 0
    for i in range(len(raw)):
        if i - 1 in rollover_indices:
            rollover_count += 1
        corr[i] = rollover_count * (2 ** 24)

    unwrapped = [int(initialRtcTicks) + (raw[i] - raw[0]) + corr[i] for i in range(len(raw))]
    tempTime = [phoneRwc + (u - shimmerRtcLower40) / 32768.0 for u in unwrapped]

    if len(tempTime) > 1:
        dt = [tempTime[i + 1] - tempTime[i] for i in range(len(tempTime) - 1)]
        if dt:
            meanDiff = sum(dt) / len(dt)
            threshold = 10.0 * abs(meanDiff) if meanDiff != 0 else 10.0
            dt_smoothed = [d if abs(d) <= threshold else meanDiff for d in dt]
            tempTime_updated = [tempTime[0]]
            for d in dt_smoothed:
                tempTime_updated.append(tempTime_updated[-1] + d)
        else:
            tempTime_updated = tempTime
    else:
        tempTime_updated = tempTime

    sensorData['timestampCal'] = tempTime_updated
    return sensorData


# ============================================================
# In-memory Decoder (for FastAPI)
# ============================================================

def read_shimmer_dat(file_bytes: bytes) -> Dict[str, Any]:
    """Full in-memory version with calibration + timestamp alignment."""
    f = io.BytesIO(file_bytes)
    headerLength = 256
    timestampBytes = 3
    sensorData: Dict[str, Any] = {}

    header = f.read(headerLength)
    if len(header) < headerLength:
        raise IOError("Header too short")

    sensors0, sensors1, sensors2 = header[3], header[4], header[5]
    sampleRateTicks = struct.unpack('<H', header[0:2])[0]
    sensorData['sampleRate'] = (32768.0 / sampleRateTicks) if sampleRateTicks != 0 else float('nan')
    mac = header[24:30]
    sensorData['macAddress'] = ':'.join(f'{b:02X}' for b in mac)
    sensorData['headerBytes'] = list(header)

    channelInfo: List[Channel] = []
    if sensors0 & 0x80:
        channelInfo += _add_channels(['Accel_LN_X', 'Accel_LN_Y', 'Accel_LN_Z'], 'int16', 2, 'little')
    if sensors1 & 0x10:
        channelInfo += _add_channels(['Accel_WR_X', 'Accel_WR_Y', 'Accel_WR_Z'], 'int16', 2, 'little')
    if sensors0 & 0x40:
        channelInfo += _add_channels(['Gyro_X', 'Gyro_Y', 'Gyro_Z'], 'int16', 2, 'big')
    if sensors0 & 0x20:
        channelInfo += _add_channels(['Mag_X', 'Mag_Z', 'Mag_Y'], 'int16', 2, 'big')

    packetLengthBytes = timestampBytes + sum(ch.nbytes for ch in channelInfo)
    f.seek(0, 2)
    fileSize = f.tell()
    f.seek(headerLength)
    numSamples = (fileSize - headerLength) // packetLengthBytes

    timestamps = []
    arrays = {ch.name: [] for ch in channelInfo}

    for _ in range(numSamples):
        packet = f.read(packetLengthBytes)
        if len(packet) < packetLengthBytes:
            break
        pos = 0
        ts = packet[pos] | (packet[pos + 1] << 8) | (packet[pos + 2] << 16)
        timestamps.append(ts)
        pos += timestampBytes
        for ch in channelInfo:
            b = packet[pos:pos + ch.nbytes]
            pos += ch.nbytes
            if ch.endian == 'big' and ch.nbytes > 1:
                b_eff = b[::-1]
            else:
                b_eff = b
            if ch.dtype == 'uint8':
                val = b_eff[0]
            elif ch.dtype == 'int16':
                val = struct.unpack('<h', b_eff)[0]
            elif ch.dtype == 'uint16':
                val = struct.unpack('<H', b_eff)[0]
            elif ch.dtype == 'int24':
                val = _sign_extend_24_be(b) if ch.endian == 'big' else _sign_extend_24_le(b)
            else:
                val = 0
            arrays[ch.name].append(val)

    sensorData['timestamps'] = timestamps
    for k, v in arrays.items():
        sensorData[k] = v

    # === Calibration ===
    sensorData = time_calibration(sensorData, header)

    inertials = [
        ('Accel_LN', 'LN_ACCEL'),
        ('Accel_WR', 'WR_ACCEL'),
        ('Gyro', 'GYRO'),
        ('Mag', 'MAG'),
    ]
    for prefix, calName in inertials:
        xk, yk, zk = f'{prefix}_X', f'{prefix}_Y', f'{prefix}_Z'
        if all(k in sensorData for k in (xk, yk, zk)):
            raw_xyz = [[float(sensorData[xk][i]), float(sensorData[yk][i]), float(sensorData[zk][i])] for i in range(len(sensorData[xk]))]
            offset, gain, align = parse_inertial_cal_params(header, calName)
            cal = apply_inertial_calibration(raw_xyz, offset, gain, align)
            sensorData[f'{prefix}_X_cal'] = [xyz[0] for xyz in cal]
            sensorData[f'{prefix}_Y_cal'] = [xyz[1] for xyz in cal]
            sensorData[f'{prefix}_Z_cal'] = [xyz[2] for xyz in cal]
            if prefix == 'Accel_WR':
                abs_vals = [math.sqrt(xyz[0] ** 2 + xyz[1] ** 2 + xyz[2] ** 2) for xyz in cal]
                sensorData['Accel_WR_Absolute'] = abs_vals
                if abs_vals:
                    sensorData['Accel_WR_VAR'] = max(abs_vals) - min(abs_vals)

    if 'timestampCal' in sensorData:
        def convert_unix_to_readable(ts):
            try:
                if ts > 2000000000:
                    ts = ts / 1000.0
                return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            except (OSError, ValueError):
                return "Invalid timestamp"
        sensorData['timestampReadable'] = [convert_unix_to_readable(t) for t in sensorData['timestampCal']]

    return sensorData
