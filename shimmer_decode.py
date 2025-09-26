import struct
import numpy as np


def read_shimmer_dat_file_as_txt(file_bytes):
    HEADER_LENGTH = 256
    MAC_ADDRESS_LENGTH = 6
    TIMESTAMP_BYTES = 3
    TWO_BYTE_CHANNEL_SIZE = 2
    BMPX80_PACKET_SIZE = 5
    EXG_16BIT_PACKET_SIZE = 5
    EXG_24BIT_PACKET_SIZE = 7

    # SD Header Offsets
    SDH_SENSORS0 = 3
    SDH_SENSORS1 = 4
    SDH_SENSORS2 = 5
    SDH_CONFIG_SETUP_BYTE3 = 11
    SDH_MAC_ADDR_C_OFFSET = 24
    SDH_SAMPLE_RATE_0 = 0
    SDH_SAMPLE_RATE_1 = 1

    # Sensor Masks
    SENSOR_A_ACCEL_MASK = 0x80
    SENSOR_MPU9X50_ICM20948_GYRO_MASK = 0x40
    SENSOR_LSM303XXXX_MAG_MASK = 0x20
    SENSOR_EXG1_24BIT_MASK = 0x10
    SENSOR_EXG2_24BIT_MASK = 0x08
    SENSOR_GSR_MASK = 0x04
    SENSOR_EXT_A7_MASK = 0x02
    SENSOR_EXT_A6_MASK = 0x01
    SENSOR_STRAIN_MASK = 0x80
    SENSOR_VBATT_MASK = 0x20
    SENSOR_LSM303XXXX_ACCEL_MASK = 0x10
    SENSOR_EXT_A15_MASK = 0x08
    SENSOR_INT_A1_MASK = 0x04
    SENSOR_INT_A12_MASK = 0x02
    SENSOR_INT_A13_MASK = 0x01
    SENSOR_INT_A14_MASK = 0x80
    SENSOR_MPU9X50_ICM20948_ACCEL_MASK = 0x40
    SENSOR_MPU9X50_ICM20948_MAG_MASK = 0x20
    SENSOR_EXG1_16BIT_MASK = 0x10
    SENSOR_EXG2_16BIT_MASK = 0x08
    SENSOR_BMPX80_PRESSURE_MASK = 0x04

    sensorData = {}

    if len(file_bytes) < HEADER_LENGTH:
        raise ValueError("Could not read full header from file.")

    headerBytes = file_bytes[:HEADER_LENGTH]
    sensors0 = headerBytes[SDH_SENSORS0]
    sensors1 = headerBytes[SDH_SENSORS1]
    sensors2 = headerBytes[SDH_SENSORS2]
    configByte3 = headerBytes[SDH_CONFIG_SETUP_BYTE3]
    mac_bytes = headerBytes[SDH_MAC_ADDR_C_OFFSET:SDH_MAC_ADDR_C_OFFSET+MAC_ADDRESS_LENGTH]
    mac_address = ':'.join(f'{b:02X}' for b in mac_bytes)
    sampleRateTicks = struct.unpack('<H', headerBytes[SDH_SAMPLE_RATE_0:SDH_SAMPLE_RATE_1+1])[0]
    sampleRate = 32768 / sampleRateTicks if sampleRateTicks else float('nan')

    sensorData['headerInfo'] = {
        'sensors0': sensors0,
        'sensors1': sensors1,
        'sensors2': sensors2,
        'configByte3': configByte3
    }
    sensorData['headerBytes'] = headerBytes
    sensorData['mac_address'] = mac_address
    sensorData['sampleRate'] = sampleRate

    # Dynamic channel detection
    packetLengthBytes = TIMESTAMP_BYTES
    channelNames = []
    channelTypes = []
    channelByteCounts = []

    if sensors0 & SENSOR_A_ACCEL_MASK:
        packetLengthBytes += 3 * TWO_BYTE_CHANNEL_SIZE
        channelNames += ['Accel_LN_X', 'Accel_LN_Y', 'Accel_LN_Z']
        channelTypes += ['int16', 'int16', 'int16']
        channelByteCounts += [2, 2, 2]
    if sensors1 & SENSOR_VBATT_MASK:
        packetLengthBytes += 2
        channelNames.append('VSenseBatt')
        channelTypes.append('uint16')
        channelByteCounts.append(2)
    if sensors0 & SENSOR_EXT_A7_MASK:
        packetLengthBytes += 2
        channelNames.append('EXT_A7')
        channelTypes.append('uint16')
        channelByteCounts.append(2)
    if sensors0 & SENSOR_EXT_A6_MASK:
        packetLengthBytes += 2
        channelNames.append('EXT_A6')
        channelTypes.append('uint16')
        channelByteCounts.append(2)
    if sensors1 & SENSOR_EXT_A15_MASK:
        packetLengthBytes += 2
        channelNames.append('EXT_A15')
        channelTypes.append('uint16')
        channelByteCounts.append(2)
    if sensors1 & SENSOR_INT_A12_MASK:
        packetLengthBytes += 2
        channelNames.append('INT_A12')
        channelTypes.append('uint16')
        channelByteCounts.append(2)
    if sensors1 & SENSOR_STRAIN_MASK:
        packetLengthBytes += 4
        channelNames += ['Strain_High', 'Strain_Low']
        channelTypes += ['uint16', 'uint16']
        channelByteCounts += [2, 2]
    if sensors1 & SENSOR_INT_A13_MASK and not (sensors1 & SENSOR_STRAIN_MASK):
        packetLengthBytes += 2
        channelNames.append('INT_A13')
        channelTypes.append('uint16')
        channelByteCounts.append(2)
    if sensors2 & SENSOR_INT_A14_MASK and not (sensors1 & SENSOR_STRAIN_MASK):
        packetLengthBytes += 2
        channelNames.append('INT_A14')
        channelTypes.append('uint16')
        channelByteCounts.append(2)
    if sensors0 & SENSOR_GSR_MASK:
        packetLengthBytes += 2
        channelNames.append('GSR_Raw')
        channelTypes.append('uint16')
        channelByteCounts.append(2)
    if sensors1 & SENSOR_INT_A1_MASK and not (sensors0 & SENSOR_GSR_MASK):
        packetLengthBytes += 2
        channelNames.append('INT_A1')
        channelTypes.append('uint16')
        channelByteCounts.append(2)
    if sensors0 & SENSOR_MPU9X50_ICM20948_GYRO_MASK:
        packetLengthBytes += 6
        channelNames += ['Gyro_X', 'Gyro_Y', 'Gyro_Z']
        channelTypes += ['int16', 'int16', 'int16']
        channelByteCounts += [2, 2, 2]
    if sensors1 & SENSOR_LSM303XXXX_ACCEL_MASK:
        packetLengthBytes += 6
        channelNames += ['Accel_WR_X', 'Accel_WR_Y', 'Accel_WR_Z']
        channelTypes += ['int16', 'int16', 'int16']
        channelByteCounts += [2, 2, 2]
    if sensors0 & SENSOR_LSM303XXXX_MAG_MASK:
        packetLengthBytes += 6
        channelNames += ['Mag_X', 'Mag_Y', 'Mag_Z']
        channelTypes += ['int16', 'int16', 'int16']
        channelByteCounts += [2, 2, 2]
    if sensors2 & SENSOR_MPU9X50_ICM20948_ACCEL_MASK:
        packetLengthBytes += 6
        channelNames += ['Accel_MPU_X', 'Accel_MPU_Y', 'Accel_MPU_Z']
        channelTypes += ['int16', 'int16', 'int16']
        channelByteCounts += [2, 2, 2]
    if sensors2 & SENSOR_MPU9X50_ICM20948_MAG_MASK:
        packetLengthBytes += 6
        channelNames += ['Mag_MPU_X', 'Mag_MPU_Y', 'Mag_MPU_Z']
        channelTypes += ['int16', 'int16', 'int16']
        channelByteCounts += [2, 2, 2]
    if sensors2 & SENSOR_BMPX80_PRESSURE_MASK:
        packetLengthBytes += BMPX80_PACKET_SIZE
        channelNames += ['BMP_Temperature', 'BMP_Pressure']
        channelTypes += ['int16', 'int24']
        channelByteCounts += [2, 3]
    # Add EXG logic if needed

    sensorData['channelNames'] = channelNames
    sensorData['packetLengthBytes'] = packetLengthBytes

    # Estimate number of samples
    fileSize = len(file_bytes)
    numSamplesEstimate = (fileSize - HEADER_LENGTH) // packetLengthBytes

    if numSamplesEstimate <= 0:
        sensorData['timestamps'] = []
        for name in channelNames:
            sensorData[name] = []
        return sensorData

    # Preallocate arrays
    sensorData['timestamps'] = np.zeros(numSamplesEstimate, dtype=np.uint32)
    for name, typ in zip(channelNames, channelTypes):
        if typ == 'int16':
            sensorData[name] = np.zeros(numSamplesEstimate, dtype=np.int16)
        elif typ == 'uint16':
            sensorData[name] = np.zeros(numSamplesEstimate, dtype=np.uint16)
        elif typ == 'int24':
            sensorData[name] = np.zeros(numSamplesEstimate, dtype=np.int32)
        else:
            sensorData[name] = np.zeros(numSamplesEstimate, dtype=np.float64)

    # Read data packets
    sampleCount = 0
    offset = HEADER_LENGTH
    while sampleCount < numSamplesEstimate:
        packet = file_bytes[offset:offset+packetLengthBytes]
        if len(packet) < packetLengthBytes:
            break
        idx = 0
        ts_val = packet[idx] + (packet[idx+1] << 8) + (packet[idx+2] << 16)
        sensorData['timestamps'][sampleCount] = ts_val
        idx += TIMESTAMP_BYTES
        for i, (name, typ, nbytes) in enumerate(zip(channelNames, channelTypes, channelByteCounts)):
            ch_bytes = packet[idx:idx+nbytes]
            if len(ch_bytes) < nbytes:
                break
            if typ == 'int16':
                val = struct.unpack('<h', ch_bytes)[0]
            elif typ == 'uint16':
                val = struct.unpack('<H', ch_bytes)[0]
            elif typ == 'int24':
                val = int.from_bytes(ch_bytes, 'little', signed=True)
            else:
                val = float(int.from_bytes(ch_bytes, 'little'))
            sensorData[name][sampleCount] = val
            idx += nbytes
        sampleCount += 1
        offset += packetLengthBytes

    # Trim unused preallocated rows
    sensorData['timestamps'] = sensorData['timestamps'][:sampleCount]
    for name in channelNames:
        sensorData[name] = sensorData[name][:sampleCount]

    # Calculate accel_ln_abs and accel_var if Accel_LN_X/Y/Z are present
    if all(name in sensorData for name in ["Accel_LN_X", "Accel_LN_Y", "Accel_LN_Z"]):
        x = np.array(sensorData["Accel_LN_X"], dtype=np.float64)
        y = np.array(sensorData["Accel_LN_Y"], dtype=np.float64)
        z = np.array(sensorData["Accel_LN_Z"], dtype=np.float64)
        lens = {"Accel_LN_X": len(x), "Accel_LN_Y": len(y), "Accel_LN_Z": len(z)}
        print(f"Accel_LN_X/Y/Z lengths: {lens}")
        if len(set(lens.values())) != 1:
            print(f"WARNING: Accel_LN_X/Y/Z lengths differ: {lens}")
        else:
            print(f"Accel_LN_X/Y/Z lengths OK: {lens}")
        # Check for invalid values
        invalid_mask = ~np.isfinite(x) | ~np.isfinite(y) | ~np.isfinite(z)
        num_invalid = np.sum(invalid_mask)
        if num_invalid > 0:
            print(f"WARNING: Found {num_invalid} invalid (NaN or inf) values in Accel_LN_X/Y/Z.")
        else:
            print("No invalid (NaN or inf) values in Accel_LN_X/Y/Z.")
        sum_sq = x**2 + y**2 + z**2
        num_negative = np.sum(sum_sq < 0)
        if num_negative > 0:
            print(f"WARNING: {num_negative} negative values found in x**2 + y**2 + z**2 before sqrt.")
        else:
            print("No negative values in x**2 + y**2 + z**2 before sqrt.")
        accel_ln_abs = np.sqrt(sum_sq)
        accel_ln_abs_rounded = np.round(accel_ln_abs, 2)
        sensorData["accel_ln_abs"] = accel_ln_abs_rounded.tolist()
        if accel_ln_abs_rounded.size > 0:
            min_val = np.min(accel_ln_abs_rounded)
            max_val = np.max(accel_ln_abs_rounded)
            print(f"accel_ln_abs_rounded min: {min_val}, max: {max_val}")
            sensorData["accel_var"] = float(max_val - min_val)
    print(f"Successfully read {sampleCount} samples.")
    return sensorData