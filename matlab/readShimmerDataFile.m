function sensorData = readShimmerDataFile(filepath)
% This version is MODIFIED to correctly handle mixed-endian data formats
% present in the Shimmer3 firmware, where some sensors use little-endian
% and others use big-endian byte order.
%
% Args:
%   filepath (string): The full path to the .dat or equivalent binary file.
%
% Returns:
%   sensorData (struct): A structure containing parsed data, including
%                        timestamps and individual sensor channels.
%                        Returns an empty array if an error occurs.
%
% Notes:
%   - This function now correctly parses common sensors with mixed endianness
%     by using a channel property table and the `swapbytes` function.
%   - It is based on the Shimmer3 LogAndStream firmware data format.
%   - UWB data overlay is NOT handled in this version for simplicity.

    sensorData = []; % Initialize output
    
    % --- Define Constants for SD Header and Sensor Masks ---
    % (Constants remain the same as the original code)
    sdhSampleRate0 = 0; sdhSampleRate1 = 1; sdhBufferSize = 2; sdhSensors0 = 3;
    sdhSensors1 = 4; sdhSensors2 = 5; sdhSensors3 = 6; sdhSensors4 = 7;
    sdhConfigSetupByte0 = 8; sdhConfigSetupByte1 = 9; sdhConfigSetupByte2 = 10;
    sdhConfigSetupByte3 = 11; sdhConfigSetupByte4 = 12; sdhConfigSetupByte5 = 13;
    sdhConfigSetupByte6 = 14; sdhTrialConfig0 = 16; sdhTrialConfig1 = 17;
    sdhBroadcastInterval = 18; sdhBtCommsBaudRate = 19; sdhEstExpLenMsb = 20;
    sdhEstExpLenLsb = 21; sdhMaxExpLenMsb = 22; sdhMaxExpLenLsb = 23;
    sdhMacAddrCOffset = 24; macAddressLength = 6; sdhShimmerVersionByte0 = 30;
    sdhShimmerVersionByte1 = 31; sdhMyTrialId = 32; sdhNShimmer = 33;
    sdhFwVersionType0 = 34; sdhFwVersionType1 = 35; sdhFwVersionMajor0 = 36;
    sdhFwVersionMajor1 = 37; sdhFwVersionMinor = 38; sdhFwVersionInternal = 39;
    sdhDerivedChannels0 = 40; sdhDerivedChannels1 = 41; sdhDerivedChannels2 = 42;
    sdhRtcDiff0 = 44; sdhRtcDiff7 = 51; sdhConfigTime0 = 52; sdhConfigTime3 = 55;
    sdhExgAds1292r1Config1 = 56; sdhExgAds1292r1Resp2 = 65;
    sdhExgAds1292r2Config1 = 66; sdhExgAds1292r2Resp2 = 75;
    sdhLsm303dlhcAccelCalibration = 76; sdhMpu9150GyroCalibration = 97;
    sdhLsm303dlhcMagCalibration = 118; sdhAAccelCalibration = 139;
    sdhTempPresCalibration = 160; sdhLsm303dlhcAccelCalibTs = 182;
    sdhMpu9150GyroCalibTs = 190; sdhLsm303dlhcMagCalibTs = 198;
    sdhAAccelCalibTs = 206; sdhDaughterCardIdByte0 = 214; sdhDerivedChannels3 = 217;
    sdhDerivedChannels7 = 221; bmp280XtraCalibBytesStart = 222;
    sdhMyLocalTime5th = 251; sdhMyLocalTimeStart = 252; sdhMyLocalTimeEnd = 255;
    headerLength = 256; % Bytes
    
    % Sensor Masks
    sensorAAccelMask                = hex2dec('80'); sensorMpu9x50Icm20948GyroMask   = hex2dec('40');
    sensorLsm303xxxxMagMask         = hex2dec('20'); sensorExg124bitMask             = hex2dec('10');
    sensorExg224bitMask             = hex2dec('08'); sensorGsrMask                   = hex2dec('04');
    sensorExtA7Mask                 = hex2dec('02'); sensorExtA6Mask                 = hex2dec('01');
    sensorStrainMask                = hex2dec('80'); sensorVbattMask                 = hex2dec('20');
    sensorLsm303xxxxAccelMask       = hex2dec('10'); sensorExtA15Mask                = hex2dec('08');
    sensorIntA1Mask                 = hex2dec('04'); sensorIntA12Mask                = hex2dec('02');
    sensorIntA13Mask                = hex2dec('01'); sensorIntA14Mask                = hex2dec('80');
    sensorMpu9x50Icm20948AccelMask  = hex2dec('40'); sensorMpu9x50Icm20948MagMask    = hex2dec('20');
    sensorExg116bitMask             = hex2dec('10'); sensorExg216bitMask             = hex2dec('08');
    sensorBmpx80PressureMask        = hex2dec('04');
    
    % Packet component sizes
    timestampBytes = 3;
    
    % --- Open File ---
    % Open for binary read ('rb'). Crucially, specify 'ieee-le' (Little-Endian)
    % as the default format. We will manually handle Big-Endian channels later.
    fid = fopen(filepath, 'rb', 'ieee-le'); 
    if fid == -1
        error('Cannot open file: %s', filepath);
    end
    
    % --- Read and Parse Header ---
    headerBytes = fread(fid, headerLength, 'uint8=>uint8');
    if length(headerBytes) < headerLength
        error('Could not read full header from file.');
        fclose(fid);
        return;
    end
    
    % Extract sensor configuration bytes from the header
    sensors0 = headerBytes(sdhSensors0 + 1);
    sensors1 = headerBytes(sdhSensors1 + 1);
    sensors2 = headerBytes(sdhSensors2 + 1);
    configByte3 = headerBytes(sdhConfigSetupByte3 + 1); % For GSR Range
    
    % Extract and display MAC Address
    macBytes = headerBytes(sdhMacAddrCOffset + 1 : sdhMacAddrCOffset + macAddressLength);
    macAddressStr = strjoin(cellstr(dec2hex(macBytes, 2))', ':');
    disp(['MAC Address: ', macAddressStr]);
    
    % Extract and calculate Sample Rate
    sampleRateTicks = double(typecast(headerBytes(sdhSampleRate0+1:sdhSampleRate1+1), 'uint16'));
    if sampleRateTicks == 0
        warning('Sample rate ticks from header is 0. Cannot calculate sample rate.');
        sensorData.sampleRate = NaN;
    else
        sensorData.sampleRate = 32768 / sampleRateTicks; % Actual sampling rate in Hz
    end
    
    % --- REFACTORED: Define Channel Info Based on Header ---
    % We now use a struct array to hold all properties for each channel.
    % This is cleaner and less error-prone than parallel arrays.
    channelInfo = struct('name', {}, 'type', {}, 'bytes', {}, 'endian', {});
    
    % Build the channel list based on enabled sensors, in the correct order.
    % The 'endian' field is CRITICAL for correct parsing.
    if bitand(sensors0, sensorAAccelMask),       channelInfo = [channelInfo; addChan_V2({'Accel_LN_X', 'Accel_LN_Y', 'Accel_LN_Z'}, 'int16', 2, 'little')]; end
    if bitand(sensors1, sensorVbattMask),        channelInfo = [channelInfo; addChan_V2('VSenseBatt', 'uint16', 2, 'little')]; end
    if bitand(sensors0, sensorExtA7Mask),        channelInfo = [channelInfo; addChan_V2('EXT_A7', 'uint16', 2, 'little')]; end
    if bitand(sensors0, sensorExtA6Mask),        channelInfo = [channelInfo; addChan_V2('EXT_A6', 'uint16', 2, 'little')]; end
    if bitand(sensors1, sensorExtA15Mask),       channelInfo = [channelInfo; addChan_V2('EXT_A15', 'uint16', 2, 'little')]; end
    if bitand(sensors1, sensorIntA12Mask),       channelInfo = [channelInfo; addChan_V2('INT_A12', 'uint16', 2, 'little')]; end
    if bitand(sensors1, sensorStrainMask),       channelInfo = [channelInfo; addChan_V2({'Strain_High', 'Strain_Low'}, 'uint16', 2, 'little')]; end
    if bitand(sensors1, sensorIntA13Mask) && ~bitand(sensors1, sensorStrainMask), channelInfo = [channelInfo; addChan_V2('INT_A13', 'uint16', 2, 'little')]; end
    if bitand(sensors2, sensorIntA14Mask) && ~bitand(sensors1, sensorStrainMask), channelInfo = [channelInfo; addChan_V2('INT_A14', 'uint16', 2, 'little')]; end
    if bitand(sensors0, sensorGsrMask),          channelInfo = [channelInfo; addChan_V2('GSR_Raw', 'uint16', 2, 'little')]; end
    if bitand(sensors1, sensorIntA1Mask) && ~bitand(sensors0, sensorGsrMask), channelInfo = [channelInfo; addChan_V2('INT_A1', 'uint16', 2, 'little')]; end
    if bitand(sensors0, sensorMpu9x50Icm20948GyroMask), channelInfo = [channelInfo; addChan_V2({'Gyro_X', 'Gyro_Y', 'Gyro_Z'}, 'int16', 2, 'big')]; end % BIG ENDIAN
    if bitand(sensors1, sensorLsm303xxxxAccelMask), channelInfo = [channelInfo; addChan_V2({'Accel_WR_X', 'Accel_WR_Y', 'Accel_WR_Z'}, 'int16', 2, 'little')]; end
    if bitand(sensors0, sensorLsm303xxxxMagMask), channelInfo = [channelInfo; addChan_V2({'Mag_X', 'Mag_Z', 'Mag_Y'}, 'int16', 2, 'big')]; end % BIG ENDIAN
    if bitand(sensors2, sensorMpu9x50Icm20948AccelMask), channelInfo = [channelInfo; addChan_V2({'Accel_MPU_X', 'Accel_MPU_Y', 'Accel_MPU_Z'}, 'int16', 2, 'big')]; end % BIG ENDIAN
    if bitand(sensors2, sensorMpu9x50Icm20948MagMask), channelInfo = [channelInfo; addChan_V2({'Mag_MPU_X', 'Mag_MPU_Y', 'Mag_MPU_Z'}, 'int16', 2, 'little')]; end
    if bitand(sensors2, sensorBmpx80PressureMask)
        channelInfo = [channelInfo; addChan_V2('BMP_Temperature', 'int16', 2, 'big')]; % BIG ENDIAN
        channelInfo = [channelInfo; addChan_V2('BMP_Pressure', 'int24', 3, 'big')];    % BIG ENDIAN
    end
    if bitand(sensors0, sensorExg124bitMask)
        channelInfo = [channelInfo; addChan_V2('EXG1_Status', 'uint8', 1, 'big')];     % BIG ENDIAN (though 1 byte has no endianness)
        channelInfo = [channelInfo; addChan_V2({'EXG1_CH1_24bit', 'EXG1_CH2_24bit'}, 'int24', 3, 'big')]; % BIG ENDIAN
    elseif bitand(sensors2, sensorExg116bitMask)
        channelInfo = [channelInfo; addChan_V2('EXG1_Status', 'uint8', 1, 'big')];
        channelInfo = [channelInfo; addChan_V2({'EXG1_CH1_16bit', 'EXG1_CH2_16bit'}, 'int16', 3, 'big')]; % BIG ENDIAN
    end
    if bitand(sensors0, sensorExg224bitMask)
        channelInfo = [channelInfo; addChan_V2('EXG2_Status', 'uint8', 1, 'big')];
        channelInfo = [channelInfo; addChan_V2({'EXG2_CH1_24bit', 'EXG2_CH2_24bit'}, 'int24', 3, 'big')]; % BIG ENDIAN
    elseif bitand(sensors2, sensorExg216bitMask)
        channelInfo = [channelInfo; addChan_V2('EXG2_Status', 'uint8', 1, 'big')];
        channelInfo = [channelInfo; addChan_V2({'EXG2_CH1_16bit', 'EXG2_CH2_16bit'}, 'int16', 2, 'big')]; % BIG ENDIAN
    end
    
    % Calculate total packet length in bytes
    packetLengthBytes = timestampBytes + sum([channelInfo.bytes]);
    sensorData.channelInfo = channelInfo;
    sensorData.packetLengthBytes = packetLengthBytes;
    fprintf('Calculated packet length: %d bytes\n', packetLengthBytes);
    fprintf('Channels to be parsed: %s\n', strjoin({channelInfo.name}, ', '));
    
    % --- Preallocate Data Arrays ---
    fseek(fid, headerLength, 'bof'); % Seek to start of data
    fseek(fid, 0, 'eof');
    fileSize = ftell(fid);
    fseek(fid, headerLength, 'bof'); 
    
    if packetLengthBytes <= timestampBytes
        warning('No channels seem to be enabled. Aborting read.');
        fclose(fid);
        return;
    end
    
    numSamplesEstimate = floor((fileSize - headerLength) / packetLengthBytes);
    if numSamplesEstimate <= 0
        warning('No data packets found in file.');
        fclose(fid);
        return;
    end
    
    sensorData.timestamps = zeros(numSamplesEstimate, 1, 'uint32');
    for i = 1:length(channelInfo)
        fieldName = matlab.lang.makeValidName(channelInfo(i).name);
        switch channelInfo(i).type
            case 'uint8',  sensorData.(fieldName) = zeros(numSamplesEstimate, 1, 'uint8');
            case 'int16',  sensorData.(fieldName) = zeros(numSamplesEstimate, 1, 'int16');
            case 'uint16', sensorData.(fieldName) = zeros(numSamplesEstimate, 1, 'uint16');
            case 'int24',  sensorData.(fieldName) = zeros(numSamplesEstimate, 1, 'int32');
            otherwise,     sensorData.(fieldName) = zeros(numSamplesEstimate, 1, 'double');
        end
    end
    
    % --- Read Data Packets in a Loop ---
    sampleCount = 0;
    while ~feof(fid) && sampleCount < numSamplesEstimate
        % Read one full packet (one sample) from the binary file
        packet = fread(fid, packetLengthBytes, 'uint8=>uint8');
        if length(packet) < packetLengthBytes
            if ~isempty(packet), warning('Partial packet read at end of file. Ignored.'); end
            break;
        end
        
        sampleCount = sampleCount + 1;
        currentByte = 1;
        
        % Timestamp (3 bytes, always little-endian)
        tsVal = uint32(packet(1)) + bitshift(uint32(packet(2)), 8) + bitshift(uint32(packet(3)), 16);
        sensorData.timestamps(sampleCount) = tsVal;
        currentByte = currentByte + timestampBytes;
        
        % Sensor Channels
        for i = 1:length(channelInfo)
            fieldName = matlab.lang.makeValidName(channelInfo(i).name);
            numBytes = channelInfo(i).bytes;
            
            % Get the raw bytes for the current channel
            channelBytes = packet(currentByte : currentByte + numBytes - 1);
            currentByte = currentByte + numBytes;
            
            % --- CRITICAL FIX: Handle Endianness ---
            % If the channel is Big-Endian, swap the byte order before typecasting.
            if strcmp(channelInfo(i).endian, 'big') && numBytes > 1
                channelBytes = channelBytes(end:-1:1);
            end
            
            % Typecast the (now correctly ordered) bytes into a numeric value
            switch channelInfo(i).type
                case 'uint8'
                    val = channelBytes(1);
                case 'int16'
                    val = typecast(channelBytes, 'int16');
                case 'uint16'
                    val = typecast(channelBytes, 'uint16');
                case 'int24'
                    % Handle 24-bit signed conversion with sign extension
                    if channelBytes(3) >= 128 % Check MSB of the 24-bit value
                        val = double(typecast(uint8([channelBytes; 255]), 'int32')); % Sign extend with 0xFF
                    else
                        val = double(typecast(uint8([channelBytes; 0]), 'int32'));   % Sign extend with 0x00
                    end
                otherwise
                    val = NaN;
            end
            sensorData.(fieldName)(sampleCount) = val;
        end
    end
    
    % --- Finalize and Clean Up ---
    % Trim unused preallocated rows from all data fields
    sensorData.timestamps = sensorData.timestamps(1:sampleCount);
    for i = 1:length(channelInfo)
        fieldName = matlab.lang.makeValidName(channelInfo(i).name);
        sensorData.(fieldName) = sensorData.(fieldName)(1:sampleCount);
    end
    
    fclose(fid);
    fprintf('Successfully read %d samples.\n', sampleCount);
    
    % Placeholder for further data processing (e.g., calibration, unit conversion)
    sensorData.headerBytes = headerBytes;
    sensorData = convertShimmerData(sensorData);
    sensorData = timeCalibration(sensorData, headerBytes);

end

function newChannels = addChan_V2(names, type, bytes, endian)
    % Uniformly handle input by converting a single string to a cell
    if ~iscell(names)
        names = {names}; 
    end
    
    numChans = numel(names);
    % Pre-allocate a correctly shaped (Nx1) struct array
    newChannels(numChans, 1) = struct('name', [], 'type', [], 'bytes', [], 'endian', []);
    
    % Populate the struct array
    for i = 1:numChans
        newChannels(i).name = names{i};
        newChannels(i).type = type;
        newChannels(i).bytes = bytes;
        newChannels(i).endian = endian;
    end
end

function [offset, gain, alignment] = parseInertialCalParams(headerBytes, sensorName)
% parseInertialCalParams Extracts calibration parameters for a specific
% inertial sensor from the 256-byte file header.
%
% Args:
%   headerBytes (uint8 vector): The 256-byte header from the data file.
%   sensorName (string): Sensor to parse. Valid options:
%                        'WR_ACCEL', 'GYRO', 'MAG', 'LN_ACCEL'.
%
% Returns:
%   offset (1x3 double): The offset vector.
%   gain (1x3 double): The gain (sensitivity) vector.
%   alignment (3x3 double): The alignment matrix.
% Start bytes for each sensor's calibration data are defined in the
% SD data file header layout. See Appendix 9.4.
    switch upper(sensorName)
        case 'WR_ACCEL'
            startByte = 76; % Wide Range Accelerometer Calibration 
        case 'GYRO'
            startByte = 97; % Gyroscope Calibration 
        case 'MAG'
            startByte = 118; % Magnetometer calibration 
        case 'LN_ACCEL'
            startByte = 139; % Analog (Low Noise) Accelerometer Calibration 
        otherwise
            error("Invalid sensorName. Use 'WR_ACCEL', 'GYRO', 'MAG', or 'LN_ACCEL'.");
    end
    % The calibration block is 21 bytes, as stored in InfoMem and written to the header.
    % Format is described in Appendix 9.1. 
    calBytes = headerBytes(startByte+1 : startByte+21);
    % Offset: 3x 16-bit signed integers, big-endian (Bytes 0-5 of block) 
    offsetUint16 = typecast(calBytes(1:6), 'uint16');
    offset = double(typecast(swapbytes(offsetUint16), 'int16'));
    % Gain/Sensitivity: 3x 16-bit signed integers, big-endian (Bytes 6-11 of block) 
    gainUint16 = typecast(calBytes(7:12), 'uint16');
    gain = double(typecast(swapbytes(gainUint16), 'uint16'));
    % Alignment: 9x 8-bit signed integers (Bytes 12-20 of block) 
    alignVec = double(typecast(calBytes(13:21), 'int8'));
    alignment = reshape(alignVec, 3, 3)'; % Reshape and transpose to get correct matrix
end

function calibratedData = applyInertialCalibration(rawData, offset, gain, alignment)
% applyInertialCalibration Applies calibration parameters to raw inertial data.
%
% Args:
%   rawData (Nx3 double): Matrix of raw data [X, Y, Z].
%   offset (1x3 double): The offset vector.
%   gain (1x3 double): The gain (sensitivity) vector.
%   alignment (3x3 double): The alignment matrix.
%
% Returns:
%   calibratedData (Nx3 double): Matrix of calibrated data in real-world units.
% The formula is: Calibrated = Alignment * ( (Raw - Offset) / Gain )
% Division by gain is necessary because the default sensitivity values in the
% manual are given in units of LSB per real-world unit (e.g., LSB/(m/s^2)).
% 
    if size(rawData, 2) ~= 3
        error('Input rawData must be an N-by-3 matrix.');
    end
    numSamples = size(rawData, 1);
    rawData = double(rawData);
    % Prevent division by zero if a gain value is 0.
    gain(gain == 0) = 1;
    % 1. Subtract offset vector from each row of raw data
    dataNoOffset = rawData - repmat(offset', numSamples, 1);
    % 2. Apply sensitivity/gain (element-wise division)
    dataScaled = dataNoOffset ./ repmat(gain', numSamples, 1);
    % 3. Apply alignment matrix to each sample
    calibratedData = ((alignment./100) * dataScaled')';
end

function sensorData = convertShimmerData(sensorData)
% convertShimmerData Converts raw data in a parsed Shimmer data struct
% to calibrated, real-world units.
%
% This function orchestrates the parsing of calibration parameters and
% the application of the calibration formula for all supported inertial
% sensors found within the data.
%
% Args:
%   sensorData (struct): A struct from your initial parser containing raw
%                        data fields (e.g., 'Gyro_X') and 'headerBytes'.
%
% Returns:
%   sensorData (struct): The input struct with new calibrated fields
%                        appended (e.g., 'Gyro_X_cal').
    fprintf('Starting Shimmer data conversion to real-world units...\n');
    % Define the inertial sensors to check for and convert
    sensorsToConvert = {
        % {Prefix for raw fields, Name for cal parser, Suffix for cal fields}
        {'Accel_LN', 'LN_ACCEL', 'm/s^2'};
        {'Accel_WR', 'WR_ACCEL', 'm/s^2'};
        {'Gyro', 'GYRO', 'deg/s'};
        {'Mag', 'MAG', 'Gauss'};
        
    };
    for i = 1:length(sensorsToConvert)
        prefix = sensorsToConvert{i}{1};
        calName = sensorsToConvert{i}{2};
        unit = sensorsToConvert{i}{3};
        
        % Check if the first channel for this sensor exists (e.g., 'Gyro_X')
        if isfield(sensorData, [prefix '_X'])
            % fprintf('Found %s data. Calibrating to %s...\n', prefix, unit);
            
            % 1. Get calibration parameters from the header
            [offset, gain, align] = parseInertialCalParams(sensorData.headerBytes, calName);
            
            % 2. Get the raw data as an Nx3 matrix
            rawData = [
                sensorData.([prefix '_X']), ...
                sensorData.([prefix '_Y']), ...
                sensorData.([prefix '_Z'])
            ];
            
            % 3. Apply the calibration
            calibratedData = applyInertialCalibration(rawData, offset, gain, align);
            
            % 4. Add new, calibrated fields to the output struct
            sensorData.([prefix '_X_cal']) = calibratedData(:, 1);
            sensorData.([prefix '_Y_cal']) = calibratedData(:, 2);
            sensorData.([prefix '_Z_cal']) = calibratedData(:, 3);
            
            % fprintf('  -> Calibration complete.\n');
        end
    end
    otherToConvert = {
        {'INT_A13', 'UWB_distance','cm'};
        {'VSenseBatt', 'Battery_level','mv'};
        {'timestamps', 'Time', ''};
        {'INT_A14', 'Tag_ID', ''}
    };
    sensorData.uwbDis = double(sensorData.INT_A13);
    sensorData.tagId = sensorData.INT_A14;
    sensorData.VSenseBatt_cal = 1.4652 * double(sensorData.VSenseBatt) -0.004;
    sensorData.Gyro_X_cal = sensorData.Gyro_X_cal * 100;
    sensorData.Gyro_Y_cal = sensorData.Gyro_Y_cal * 100;
    sensorData.Gyro_Z_cal = sensorData.Gyro_Z_cal * 100;
    % timestamp = double(sensorData.timestamps)/32.768e3;
    % timestamp = timestamp - timestamp(1);
    % cutoff = (max(timestamp)-min(timestamp))/2;
    % sensorData.timestamp_cal = unwrap(timestamp, cutoff);
    fprintf('Data conversion finished.\n');
end

function sensorData = timeCalibration(sensorData, fileHeader)
    % Calibrates raw 24-bit wrapping timestamps to a synchronized 64-bit 
    % Unix millisecond timestamp using synchronization info from the file header.
    %
    % Inputs:
    %   sensorData - A struct or table containing the field '.timestamps'.
    %   fileHeader - A uint8 array containing the 256-byte file header.
    %
    % Output:
    %   sensorData - The input struct with an added '.time_cal_ms' field.
    sdhRtcDiff0 = 44; % LSB of RTC Difference
    sdhRtcDiff7 = 51; % MSB of RTC Difference
    sdhConfigTime0 = 52; % Start of Config Time
    sdhConfigTime3 = 55; % End of Config Time
    sdhMyLocalTime5th = 251;
    sdhMyLocalTimeStart = 252; % Start of Local Time (lower 4 bytes)
    sdhMyLocalTimeEnd = 255;   % End of Local Time
    phoneRwc = double(typecast(fileHeader(sdhConfigTime0+1:sdhConfigTime3+1), 'uint32')); % a RWC timestamp generated on phone when got dock response
    shimmerRtc64 = (typecast(fileHeader(sdhRtcDiff0+1:sdhRtcDiff7+1), 'uint64')); % a RTC64 timestamp inside the dock response (generated when recieved dock request)
    shimmerRtcLower40 = double(mod(shimmerRtc64, 2^40));
    % Reconstruct the 40-bit RTC timestamp of the first sample (in ticks).
    initialRtcTicks = double(fileHeader(sdhMyLocalTime5th+1)) * (2^32) + double(typecast(fileHeader(sdhMyLocalTimeStart+1 : sdhMyLocalTimeEnd+1), 'uint32'));
    % Convert raw timestamps to int64 for safe differential calculations.
    % phoneRwc = 0;
    % shimmerRtcLower40 = 0;
    rawTimestamps = double(sensorData.timestamps);
    
     % Calculate the difference between consecutive timestamps.
    timestampDiffs = diff(rawTimestamps);
    
    % Find rollover points, which occur when the diff is a large negative number.
    rolloverIndices = find(timestampDiffs < -2^23);
    
    % Create a correction vector that adds 2^24 after each rollover.
    correctionVector = zeros(size(rawTimestamps));
    correctionVector(rolloverIndices + 1) = 1;
    correctionVector = cumsum(correctionVector) * 2^24;
    
    % Apply the correction to create a continuous, unwrapped tick count.
    % The timeline is based on the initial timestamp from the header.
    unwrappedTicks = (initialRtcTicks) + (rawTimestamps - rawTimestamps(1)) + correctionVector;
    tempTime =  double(unwrappedTicks) - shimmerRtcLower40;
    tempTime = phoneRwc + tempTime/32.768e3;
    % sensorData.timestampCal = tempTime; 
    
    % timestamp = double(sensorData.timestamps)/32.768e3;
    % timestamp = timestamp - timestamp(1);
    % cutoff = (max(timestamp)-min(timestamp))/2;
    % timestampCal = unwrap(timestamp, cutoff);
    timeDiff = diff(tempTime);
    meanDiff = mean(timeDiff);
    index = abs(timeDiff) > 10*meanDiff;
    timeDiff(index) = meanDiff;
    tempTime_updated = [tempTime(1); tempTime(1) + cumsum(timeDiff)];

    sensorData.timestampCal = tempTime_updated ;
    
    sensorData.initialTime = initialRtcTicks;
    sensorData.phoneRwc = phoneRwc;
    sensorData.shimmerRtcLower40 = shimmerRtcLower40;
end
