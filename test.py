# ============================================================
#  read_shimmer_data_file.py
#  Exact MATLAB port of readShimmerDataFile.m
#  Adds: calibrate flag + auto-save .mat outputs
# ============================================================

import struct
import math
from array import array
from dataclasses import dataclass, asdict
from typing import List, Dict, Any
try:
    from scipy.io import savemat
except ImportError:
    savemat = None


# ----------------------------
# Helper functions
# ----------------------------

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


@dataclass
class Channel:
    name: str
    dtype: str
    nbytes: int
    endian: str


def _add_channels(names, dtype, nbytes, endian) -> List[Channel]:
    if isinstance(names, str):
        names = [names]
    return [Channel(n, dtype, nbytes, endian) for n in names]


# ----------------------------
# Array math helper functions (replacing NumPy)
# ----------------------------

def array_subtract(arr1, arr2):
    """Subtract two arrays element-wise"""
    return [a - b for a, b in zip(arr1, arr2)]

def array_divide(arr1, arr2):
    """Divide two arrays element-wise, avoiding division by zero"""
    return [a / b if b != 0 else a for a, b in zip(arr1, arr2)]

def matrix_vector_multiply(matrix_3x3, vector_3):
    """Multiply 3x3 matrix by 3-element vector"""
    result = [0.0, 0.0, 0.0]
    for i in range(3):
        for j in range(3):
            result[i] += matrix_3x3[i][j] * vector_3[j]
    return result

def transpose_matrix_3x3(matrix):
    """Transpose a 3x3 matrix"""
    return [[matrix[j][i] for j in range(3)] for i in range(3)]

# ----------------------------
# Calibration parsing & apply
# ----------------------------

def parse_inertial_cal_params(header: bytes, sensor: str):
    offsets = {'WR_ACCEL': 76, 'GYRO': 97, 'MAG': 118, 'LN_ACCEL': 139}
    start = offsets[sensor]
    cal = header[start:start + 21]
    offset = list(struct.unpack('>hhh', cal[0:6]))
    gain = list(struct.unpack('>HHH', cal[6:12]))
    align = list(struct.unpack('bbb' * 3, cal[12:21]))
    # Reshape to 3x3 and transpose
    alignment = [[align[j*3 + i] for j in range(3)] for i in range(3)]
    return offset, gain, alignment


def apply_inertial_calibration(raw_xyz_list, offset, gain, alignment):
    """Apply calibration to list of [x,y,z] triplets"""
    # Replace zero gains with 1
    gain_safe = [g if g != 0 else 1.0 for g in gain]
    
    calibrated = []
    for xyz in raw_xyz_list:
        # Subtract offset
        no_offset = array_subtract(xyz, offset)
        # Divide by gain
        scaled = array_divide(no_offset, gain_safe)
        # Apply alignment matrix (divided by 100)
        align_scaled = [[a/100.0 for a in row] for row in alignment]
        result = matrix_vector_multiply(align_scaled, scaled)
        calibrated.append(result)
    
    return calibrated


# ----------------------------
# Time calibration
# ----------------------------

def time_calibration(sensorData: Dict[str, Any], header: bytes) -> Dict[str, Any]:
    sdhRtcDiff0, sdhRtcDiff7 = 44, 51
    sdhConfigTime0, sdhConfigTime3 = 52, 55
    sdhMyLocalTime5th, sdhMyLocalTimeStart, sdhMyLocalTimeEnd = 251, 252, 255

    phoneRwc = struct.unpack('<I', header[sdhConfigTime0:sdhConfigTime3 + 1])[0]
    shimmerRtc64 = int.from_bytes(header[sdhRtcDiff0:sdhRtcDiff7 + 1], 'little')
    shimmerRtcLower40 = float(shimmerRtc64 % (2 ** 40))
    initialRtcTicks = (header[sdhMyLocalTime5th] * (2 ** 32)) + struct.unpack('<I', header[sdhMyLocalTimeStart:sdhMyLocalTimeEnd + 1])[0]

    raw = sensorData['timestamps']
    if not raw:
        sensorData['timestampCal'] = []
        sensorData['initialTime'] = int(initialRtcTicks)
        sensorData['phoneRwc'] = int(phoneRwc)
        sensorData['shimmerRtcLower40'] = int(shimmerRtcLower40)
        return sensorData

    # Calculate differences and find rollovers
    diffs = [raw[i+1] - raw[i] for i in range(len(raw)-1)]
    rollover_indices = [i for i, d in enumerate(diffs) if d < -2**23]
    
    # Apply rollover corrections
    corr = [0] * len(raw)
    rollover_count = 0
    for i in range(len(raw)):
        if i-1 in rollover_indices:
            rollover_count += 1
        corr[i] = rollover_count * (2**24)

    # Calculate unwrapped timestamps
    unwrapped = [int(initialRtcTicks) + (raw[i] - raw[0]) + corr[i] for i in range(len(raw))]
    tempTime = [phoneRwc + (u - shimmerRtcLower40) / 32768.0 for u in unwrapped]

    # Smooth time differences
    if len(tempTime) > 1:
        dt = [tempTime[i+1] - tempTime[i] for i in range(len(tempTime)-1)]
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
    sensorData['initialTime'] = int(initialRtcTicks)
    sensorData['phoneRwc'] = int(phoneRwc)
    sensorData['shimmerRtcLower40'] = int(shimmerRtcLower40)
    return sensorData


# ----------------------------
# Main reader
# ----------------------------

def read_shimmer_data_file(filepath: str, calibrate: bool = True) -> Dict[str, Any]:
    sensorData: Dict[str, Any] = {}
    headerLength = 256
    timestampBytes = 3

    with open(filepath, 'rb') as f:
        header = f.read(headerLength)
        if len(header) < headerLength:
            raise IOError("Could not read full 256-byte header.")

        sensors0, sensors1, sensors2 = header[3], header[4], header[5]
        sampleRateTicks = struct.unpack('<H', header[0:2])[0]
        sensorData['sampleRate'] = (32768.0 / sampleRateTicks) if sampleRateTicks != 0 else float('nan')

        mac = header[24:30]
        macAddressStr = ':'.join(f'{b:02X}' for b in mac)

        channelInfo: List[Channel] = []

        # -------- MATLAB-equivalent sensor parsing --------
        if sensors0 & 0x80:
            channelInfo += _add_channels(['Accel_LN_X', 'Accel_LN_Y', 'Accel_LN_Z'], 'int16', 2, 'little')
        if sensors1 & 0x20:
            channelInfo += _add_channels('VSenseBatt', 'uint16', 2, 'little')
        if sensors0 & 0x02:
            channelInfo += _add_channels('EXT_A7', 'uint16', 2, 'little')
        if sensors0 & 0x01:
            channelInfo += _add_channels('EXT_A6', 'uint16', 2, 'little')
        if sensors1 & 0x08:
            channelInfo += _add_channels('EXT_A15', 'uint16', 2, 'little')
        if sensors1 & 0x02:
            channelInfo += _add_channels('INT_A12', 'uint16', 2, 'little')
        if sensors1 & 0x80:
            channelInfo += _add_channels(['Strain_High', 'Strain_Low'], 'uint16', 2, 'little')
        if (sensors1 & 0x01) and not (sensors1 & 0x80):
            channelInfo += _add_channels('INT_A13', 'uint16', 2, 'little')
        if (sensors2 & 0x80) and not (sensors1 & 0x80):
            channelInfo += _add_channels('INT_A14', 'uint16', 2, 'little')
        if sensors0 & 0x04:
            channelInfo += _add_channels('GSR_Raw', 'uint16', 2, 'little')
        if (sensors1 & 0x04) and not (sensors0 & 0x04):
            channelInfo += _add_channels('INT_A1', 'uint16', 2, 'little')
        if sensors0 & 0x40:
            channelInfo += _add_channels(['Gyro_X', 'Gyro_Y', 'Gyro_Z'], 'int16', 2, 'big')
        if sensors1 & 0x10:
            channelInfo += _add_channels(['Accel_WR_X', 'Accel_WR_Y', 'Accel_WR_Z'], 'int16', 2, 'little')
        if sensors0 & 0x20:
            channelInfo += _add_channels(['Mag_X', 'Mag_Z', 'Mag_Y'], 'int16', 2, 'big')
        if sensors2 & 0x40:
            channelInfo += _add_channels(['Accel_MPU_X', 'Accel_MPU_Y', 'Accel_MPU_Z'], 'int16', 2, 'big')
        if sensors2 & 0x20:
            channelInfo += _add_channels(['Mag_MPU_X', 'Mag_MPU_Y', 'Mag_MPU_Z'], 'int16', 2, 'little')
        if sensors2 & 0x04:
            channelInfo += _add_channels('BMP_Temperature', 'int16', 2, 'big')
            channelInfo += _add_channels('BMP_Pressure', 'int24', 3, 'big')
        if sensors0 & 0x10:
            channelInfo += _add_channels('EXG1_Status', 'uint8', 1, 'big')
            channelInfo += _add_channels(['EXG1_CH1_24bit', 'EXG1_CH2_24bit'], 'int24', 3, 'big')
        elif sensors2 & 0x10:
            channelInfo += _add_channels('EXG1_Status', 'uint8', 1, 'big')
            channelInfo += _add_channels(['EXG1_CH1_16bit', 'EXG1_CH2_16bit'], 'int16', 3, 'big')  # typo preserved
        if sensors0 & 0x08:
            channelInfo += _add_channels('EXG2_Status', 'uint8', 1, 'big')
            channelInfo += _add_channels(['EXG2_CH1_24bit', 'EXG2_CH2_24bit'], 'int24', 3, 'big')
        elif sensors2 & 0x08:
            channelInfo += _add_channels('EXG2_Status', 'uint8', 1, 'big')
            channelInfo += _add_channels(['EXG2_CH1_16bit', 'EXG2_CH2_16bit'], 'int16', 2, 'big')

        packetLengthBytes = timestampBytes + sum(ch.nbytes for ch in channelInfo)
        sensorData['channelInfo'] = [asdict(ch) for ch in channelInfo]
        sensorData['packetLengthBytes'] = packetLengthBytes

        # --- read all packets ---
        f.seek(0, 2)
        fileSize = f.tell()
        f.seek(headerLength)
        numSamples = (fileSize - headerLength) // packetLengthBytes
        timestamps = []
        arrays = {ch.name: [] for ch in channelInfo}

        for i in range(numSamples):
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
        sensorData['headerBytes'] = list(header)

    # ---- skip calibration if requested ----
    if not calibrate:
        return sensorData

    # ----------------------------
    # convertShimmerData (MATLAB parity)
    # ----------------------------
    inertials = [
        ('Accel_LN', 'LN_ACCEL', 'm/s^2'),
        ('Accel_WR', 'WR_ACCEL', 'm/s^2'),
        ('Gyro', 'GYRO', 'deg/s'),
        ('Mag', 'MAG', 'Gauss'),
    ]
    for prefix, calName, _unit in inertials:
        xk, yk, zk = f'{prefix}_X', f'{prefix}_Y', f'{prefix}_Z'
        if all(k in sensorData for k in (xk, yk, zk)):
            # Convert to list of [x,y,z] triplets
            raw_xyz_list = [[float(sensorData[xk][i]), float(sensorData[yk][i]), float(sensorData[zk][i])] 
                           for i in range(len(sensorData[xk]))]
            
            offset, gain, align = parse_inertial_cal_params(bytes(sensorData['headerBytes']), calName)
            cal = apply_inertial_calibration(raw_xyz_list, offset, gain, align)
            
            # Extract calibrated components
            sensorData[f'{prefix}_X_cal'] = [xyz[0] for xyz in cal]
            sensorData[f'{prefix}_Y_cal'] = [xyz[1] for xyz in cal]
            sensorData[f'{prefix}_Z_cal'] = [xyz[2] for xyz in cal]
            
            # Calculate absolute value for Accel_WR
            if prefix == 'Accel_WR':
                accel_wr_abs = [math.sqrt(xyz[0]**2 + xyz[1]**2 + xyz[2]**2) for xyz in cal]
                sensorData['Accel_WR_Absolute'] = accel_wr_abs
                if accel_wr_abs:
                    sensorData['Accel_WR_VAR'] = max(accel_wr_abs) - min(accel_wr_abs)

    if 'INT_A13' in sensorData:
        sensorData['uwbDis'] = [float(v) for v in sensorData['INT_A13']]
    if 'INT_A14' in sensorData:
        sensorData['tagId'] = sensorData['INT_A14']
    if 'VSenseBatt' in sensorData:
        vs = sensorData['VSenseBatt']
        sensorData['VSenseBatt_cal'] = [1.4652 * v - 0.004 for v in vs]
    for axis in ('X', 'Y', 'Z'):
        k = f'Gyro_{axis}_cal'
        if k in sensorData:
            sensorData[k] = [v * 100.0 for v in sensorData[k]]

    sensorData = time_calibration(sensorData, bytes(sensorData['headerBytes']))
    
    # Convert Unix timestamps to readable format
    from datetime import datetime
    if 'timestampCal' in sensorData:
        def convert_unix_to_readable(unix_timestamp):
            try:
                # Check if timestamp is in milliseconds and convert to seconds
                if unix_timestamp > 2000000000:  # If > year 2033, likely in milliseconds
                    unix_timestamp = unix_timestamp / 1000.0
                dt = datetime.fromtimestamp(unix_timestamp)
                return dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            except (ValueError, OSError):
                return "Invalid timestamp"
        
        sensorData['timestampReadable'] = [convert_unix_to_readable(ts) for ts in sensorData['timestampCal']]
    
    return sensorData


def read_shimmer_data_file_as_txt(filepath):
    """Wrapper function for compatibility"""
    return read_shimmer_data_file(filepath, calibrate=True)


# ---------------------------------------------------------
# Produce MATLAB-compatible .mat files automatically
# ---------------------------------------------------------
if __name__ == '__main__':
    import json
    
    # Use the actual test file
    fname = 'a9ae0f999916e210__20250925_232338__TEST0916_1752967227__Shimmer_DDD6-000__000.txt'
    
    try:
        result = read_shimmer_data_file_as_txt(fname)
        
        # Print summary
        print(f"✅ Successfully decoded {len(result.get('timestamps', []))} samples")
        print(f"Sample rate: {result.get('sampleRate', 'N/A')} Hz")
        print(f"Available channels: {len([k for k in result.keys() if isinstance(result[k], list) and k != 'headerBytes'])}")
        
        # Save full output
        with open('output.json', 'w') as f:
            json.dump(result, f, indent=2)
        print("✅ Full data saved to output.json")
        
        # Save accelerometer data only
        accel_only = {}
        for key in ['Accel_WR_X_cal', 'Accel_WR_Y_cal', 'Accel_WR_Z_cal', 'Accel_WR_Absolute', 'Accel_WR_VAR', 'timestampCal', 'timestampReadable']:
            if key in result:
                accel_only[key] = result[key]
        
        with open('accel_output.json', 'w') as f:
            json.dump(accel_only, f, indent=2)
        print("✅ Accelerometer data saved to accel_output.json")
        
        # Print some stats
        if 'Accel_WR_Absolute' in result:
            abs_vals = result['Accel_WR_Absolute']
            print(f"Accel_WR_Absolute: mean={sum(abs_vals)/len(abs_vals):.2f}, min={min(abs_vals):.2f}, max={max(abs_vals):.2f}")
        
        # Plot if matplotlib available
        try:
            import matplotlib.pyplot as plt
            
            if 'Accel_WR_Absolute' in result and 'timestampCal' in result:
                plt.figure(figsize=(12, 6))
                plt.plot(result['timestampCal'], result['Accel_WR_Absolute'])
                plt.xlabel('Timestamp (Unix)')
                plt.ylabel('Accel_WR_Absolute (m/s²)')
                plt.title('Wide-Range Accelerometer Magnitude vs Time')
                plt.grid(True)
                plt.savefig('accel_plot.png', dpi=150, bbox_inches='tight')
                print("✅ Plot saved as accel_plot.png")
                
        except ImportError:
            print("⚠️ matplotlib not available - skipping plot")
            
    except Exception as e:
        print(f"❌ Error: {e}")
