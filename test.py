import struct
import numpy as np
import json

def sign_extend_24bit(val_bytes):
    val = val_bytes[0] + (val_bytes[1] << 8) + (val_bytes[2] << 16)
    if val_bytes[2] & 0x80:
        val -= 1 << 24
    return val

def parse_inertial_cal_params(header, sensor):
    offsets = {
        'WR_ACCEL': 76,
        'GYRO': 97,
        'MAG': 118,
        'LN_ACCEL': 139
    }
    start = offsets[sensor]
    cal_bytes = header[start:start+21]
    offset = struct.unpack('>hhh', bytes(cal_bytes[0:6]))
    gain = struct.unpack('>HHH', bytes(cal_bytes[6:12]))
    align = struct.unpack('bbb'*3, bytes(cal_bytes[12:21]))
    alignment = np.array(align).reshape((3,3)).T
    return np.array(offset), np.array(gain), alignment

def apply_inertial_calibration(raw, offset, gain, alignment):
    raw = np.array(raw, dtype=float)
    gain = np.where(gain == 0, 1, gain)
    data_no_offset = raw - offset
    data_scaled = data_no_offset / gain
    return (alignment/100.0 @ data_scaled.T).T

def time_calibration(timestamps, header):
    phone_rwc = struct.unpack('<I', header[52:56])[0]
    print(phone_rwc, "Phone_rwc")

    shimmer_rtc64 = int.from_bytes(header[44:52], 'little')
    print("Exact bytes header RTC", header[44:53])
    shimmer_rtc_lower40 = shimmer_rtc64 % (2**40)
    initial_rtc_ticks = header[251]*(2**32) + struct.unpack('<I', header[252:256])[0]
    raw = np.array(timestamps, dtype=np.uint32)
    diffs = np.diff(raw)
    rollover = np.where(diffs < -2**23)[0]
    corr = np.zeros_like(raw)
    if rollover.size > 0:
        corr[rollover+1] = 1
    corr = np.cumsum(corr) * 2**24
    unwrapped = initial_rtc_ticks + (raw - raw[0]) + corr
    temp_time = unwrapped - shimmer_rtc_lower40
    print(initial_rtc_ticks, shimmer_rtc_lower40, "RTC")
    print("Full shimemr RTC", shimmer_rtc64)
    temp_time = phone_rwc + temp_time/32768.0
    return temp_time, initial_rtc_ticks

def read_shimmer_data_file_as_txt(filepath):
    headerLength = 256
    timestampBytes = 3
    twoByteChannelSize = 2
    bmpx80PacketSize = 5
    exg16bitPacketSize = 5
    exg24bitPacketSize = 7
    macAddressLength = 6
    # Sensor masks
    sensorAAccelMask = 0x80
    sensorMpu9x50Icm20948GyroMask = 0x40
    sensorLsm303xxxxMagMask = 0x20
    sensorExg124bitMask = 0x10
    sensorExg224bitMask = 0x08
    sensorGsrMask = 0x04
    sensorExtA7Mask = 0x02
    sensorExtA6Mask = 0x01
    sensorStrainMask = 0x80
    sensorVbattMask = 0x20
    sensorLsm303xxxxAccelMask = 0x10
    sensorExtA15Mask = 0x08
    sensorIntA1Mask = 0x04
    sensorIntA12Mask = 0x02
    sensorIntA13Mask = 0x01
    sensorIntA14Mask = 0x80
    sensorMpu9x50Icm20948AccelMask = 0x40
    sensorMpu9x50Icm20948MagMask = 0x20
    sensorExg116bitMask = 0x10
    sensorExg216bitMask = 0x08
    sensorBmpx80PressureMask = 0x04
    with open(filepath, 'rb') as f:
        header = f.read(headerLength)
        sensors0 = header[3]
        sensors1 = header[4]
        sensors2 = header[5]
        sensors3 = header[6]
        sensors4 = header[7]
        configByte3 = header[11]
        sampleRateTicks = struct.unpack('<H', header[0:2])[0]
        sampleRate = 32768 / sampleRateTicks if sampleRateTicks != 0 else float('nan')
        macBytes = header[24:24+macAddressLength]
        macAddressStr = ':'.join(f'{b:02X}' for b in macBytes)
        packetLengthBytes = timestampBytes
        channelNames = []
        channelTypes = []
        channelByteCounts = []
        # SENSORS0
        if sensors0 & sensorAAccelMask:
            packetLengthBytes += 6
            channelNames += ['Accel_LN_X', 'Accel_LN_Y', 'Accel_LN_Z']
            channelTypes += ['int16', 'int16', 'int16']
            channelByteCounts += [2,2,2]
        if sensors1 & sensorVbattMask:
            packetLengthBytes += 2
            channelNames += ['VSenseBatt']
            channelTypes += ['uint16']
            channelByteCounts += [2]
        if sensors0 & sensorExtA7Mask:
            packetLengthBytes += 2
            channelNames += ['EXT_A7']
            channelTypes += ['uint16']
            channelByteCounts += [2]
        if sensors0 & sensorExtA6Mask:
            packetLengthBytes += 2
            channelNames += ['EXT_A6']
            channelTypes += ['uint16']
            channelByteCounts += [2]
        if sensors1 & sensorExtA15Mask:
            packetLengthBytes += 2
            channelNames += ['EXT_A15']
            channelTypes += ['uint16']
            channelByteCounts += [2]
        if sensors1 & sensorIntA12Mask:
            packetLengthBytes += 2
            channelNames += ['INT_A12']
            channelTypes += ['uint16']
            channelByteCounts += [2]
        if sensors1 & sensorStrainMask:
            packetLengthBytes += 4
            channelNames += ['Strain_High', 'Strain_Low']
            channelTypes += ['uint16', 'uint16']
            channelByteCounts += [2,2]
        if (sensors1 & sensorIntA13Mask) and not (sensors1 & sensorStrainMask):
            packetLengthBytes += 2
            channelNames += ['INT_A13']
            channelTypes += ['uint16']
            channelByteCounts += [2]
        if (sensors2 & sensorIntA14Mask) and not (sensors1 & sensorStrainMask):
            packetLengthBytes += 2
            channelNames += ['INT_A14']
            channelTypes += ['uint16']
            channelByteCounts += [2]
        if sensors0 & sensorGsrMask:
            packetLengthBytes += 2
            channelNames += ['GSR_Raw']
            channelTypes += ['uint16']
            channelByteCounts += [2]
        if (sensors1 & sensorIntA1Mask) and not (sensors0 & sensorGsrMask):
            packetLengthBytes += 2
            channelNames += ['INT_A1']
            channelTypes += ['uint16']
            channelByteCounts += [2]
        if sensors0 & sensorMpu9x50Icm20948GyroMask:
            packetLengthBytes += 6
            channelNames += ['Gyro_X', 'Gyro_Y', 'Gyro_Z']
            channelTypes += ['int16', 'int16', 'int16']
            channelByteCounts += [2,2,2]
        if sensors1 & sensorLsm303xxxxAccelMask:
            packetLengthBytes += 6
            channelNames += ['Accel_WR_X', 'Accel_WR_Y', 'Accel_WR_Z']
            channelTypes += ['int16', 'int16', 'int16']
            channelByteCounts += [2,2,2]
        if sensors0 & sensorLsm303xxxxMagMask:
            packetLengthBytes += 6
            channelNames += ['Mag_X', 'Mag_Y', 'Mag_Z']
            channelTypes += ['int16', 'int16', 'int16']
            channelByteCounts += [2,2,2]
        if sensors2 & sensorMpu9x50Icm20948AccelMask:
            packetLengthBytes += 6
            channelNames += ['Accel_MPU_X', 'Accel_MPU_Y', 'Accel_MPU_Z']
            channelTypes += ['int16', 'int16', 'int16']
            channelByteCounts += [2,2,2]
        if sensors2 & sensorMpu9x50Icm20948MagMask:
            packetLengthBytes += 6
            channelNames += ['Mag_MPU_X', 'Mag_MPU_Y', 'Mag_MPU_Z']
            channelTypes += ['int16', 'int16', 'int16']
            channelByteCounts += [2,2,2]
        if sensors2 & sensorBmpx80PressureMask:
            packetLengthBytes += bmpx80PacketSize
            channelNames += ['BMP_Temperature', 'BMP_Pressure']
            channelTypes += ['int16', 'int24']
            channelByteCounts += [2,3]
        if sensors0 & sensorExg124bitMask:
            packetLengthBytes += exg24bitPacketSize
            channelNames += ['EXG1_Status', 'EXG1_CH1_24bit', 'EXG1_CH2_24bit']
            channelTypes += ['uint8', 'int24', 'int24']
            channelByteCounts += [1,3,3]
        elif sensors2 & sensorExg116bitMask:
            packetLengthBytes += exg16bitPacketSize
            channelNames += ['EXG1_Status', 'EXG1_CH1_16bit', 'EXG1_CH2_16bit']
            channelTypes += ['uint8', 'int16', 'int16']
            channelByteCounts += [1,2,2]
        if sensors0 & sensorExg224bitMask:
            packetLengthBytes += exg24bitPacketSize
            channelNames += ['EXG2_Status', 'EXG2_CH1_24bit', 'EXG2_CH2_24bit']
            channelTypes += ['uint8', 'int24', 'int24']
            channelByteCounts += [1,3,3]
        elif sensors2 & sensorExg216bitMask:
            packetLengthBytes += exg16bitPacketSize
            channelNames += ['EXG2_Status', 'EXG2_CH1_16bit', 'EXG2_CH2_16bit']
            channelTypes += ['uint8', 'int16', 'int16']
            channelByteCounts += [1,2,2]
        f.seek(0, 2)
        fileSize = f.tell()
        f.seek(headerLength, 0)
        numSamplesEstimate = (fileSize - headerLength) // packetLengthBytes
        data = {name: np.zeros(numSamplesEstimate, dtype=np.int32) for name in channelNames}
        timestamps = np.zeros(numSamplesEstimate, dtype=np.uint32)
        for sampleCount in range(numSamplesEstimate):
            packet = f.read(packetLengthBytes)
            if len(packet) < packetLengthBytes:
                break
            currentByte = 0
            tsVal = packet[currentByte] + (packet[currentByte+1] << 8) + (packet[currentByte+2] << 16)
            timestamps[sampleCount] = tsVal
            currentByte += timestampBytes
            for i, (name, typ, nbytes) in enumerate(zip(channelNames, channelTypes, channelByteCounts)):
                channelBytes = packet[currentByte:currentByte+nbytes]
                currentByte += nbytes
                if typ == 'uint8':
                    val = channelBytes[0]
                elif typ == 'int16':
                    val = struct.unpack('<h', channelBytes)[0]
                elif typ == 'uint16':
                    val = struct.unpack('<H', channelBytes)[0]
                elif typ == 'int24':
                    val = sign_extend_24bit(channelBytes)
                else:
                    val = 0
                data[name][sampleCount] = val
        for name in data:
            data[name] = data[name][:sampleCount]
        timestamps = timestamps[:sampleCount]
        output = {
            'sampleRate': sampleRate,
            'headerBytes': list(header),
            'channelNames': channelNames,
            'timestamps': timestamps.tolist(),
            'macAddress': macAddressStr,
        }
        for name in data:
            output[name] = data[name].tolist()
        # Calibration for inertial sensors
        inertial_sensors = [
            ('Accel_LN', 'LN_ACCEL', 'm/s^2'),
            ('Accel_WR', 'WR_ACCEL', 'm/s^2'),
            ('Gyro', 'GYRO', 'deg/s'),
            ('Mag', 'MAG', 'Gauss'),
        ]
        for prefix, calName, unit in inertial_sensors:
            if all(f'{prefix}_{axis}' in output for axis in ['X','Y','Z']):
                offset, gain, align = parse_inertial_cal_params(header, calName)
                raw = np.column_stack([output[f'{prefix}_X'], output[f'{prefix}_Y'], output[f'{prefix}_Z']])
                cal = apply_inertial_calibration(raw, offset, gain, align)
                output[f'{prefix}_X_cal'] = cal[:,0].tolist()
                output[f'{prefix}_Y_cal'] = cal[:,1].tolist()
                output[f'{prefix}_Z_cal'] = cal[:,2].tolist()
        # Calculate Accel_WR_Absolute from calibrated wide-range accel
        if all(f'Accel_WR_{axis}_cal' in output for axis in ['X','Y','Z']):
            x_cal = np.array(output['Accel_WR_X_cal'])
            y_cal = np.array(output['Accel_WR_Y_cal'])
            z_cal = np.array(output['Accel_WR_Z_cal'])
            accel_wr_abs = np.sqrt(x_cal**2 + y_cal**2 + z_cal**2)
            output['Accel_WR_Absolute'] = accel_wr_abs.tolist()
            # Calculate variance (max - min)
            output['Accel_WR_VAR'] = float(accel_wr_abs.max() - accel_wr_abs.min())
        
        # Time calibration
        timestampCal, initialTime = time_calibration(timestamps, header)
        output['timestampCal'] = timestampCal.tolist()
        output['initialTime'] = initialTime
        return output

result = read_shimmer_data_file_as_txt('000')
with open('output.json', 'w') as f:
    json.dump(result, f)


# Save only calibrated accel values to separate file
accel_only = {
    'Accel_WR_X_cal': result.get('Accel_LN_X_cal', []),
    'Accel_WR_Y_cal': result.get('Accel_LN_Y_cal', []),
    'Accel_WR_Z_cal': result.get('Accel_LN_Z_cal', []),
    'Accel_WR_Absolute': result.get('Accel_WR_Absolute', []),
    'Accel_WR_VAR': result.get('Accel_WR_VAR', []),
    'timestampCal': result.get('timestampCal', [])
}
with open('accel_output.json', 'w') as f:
    json.dump(accel_only, f, indent=2)