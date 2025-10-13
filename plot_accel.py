import json
import matplotlib.pyplot as plt
import numpy as np

# Load the data
with open('output.json', 'r') as f:
    data = json.load(f)

# Get the data
timestampCal = np.array(data['timestampCal'])
accel_wr_abs = np.array(data['Accel_WR_Absolute'])

# Create the plot
plt.figure(figsize=(12, 6))
plt.plot(timestampCal, accel_wr_abs, linewidth=1, color='blue', alpha=0.7)
plt.xlabel('Timestamp (Unix time)', fontsize=12)
plt.ylabel('Accel_WR_Absolute (m/s²)', fontsize=12)
plt.title('Wide Range Accelerometer Magnitude vs Time', fontsize=14, fontweight='bold')
plt.grid(True, alpha=0.3)
plt.tight_layout()

# Add some stats to the plot
mean_accel = np.mean(accel_wr_abs)
max_accel = np.max(accel_wr_abs)
min_accel = np.min(accel_wr_abs)
plt.axhline(y=mean_accel, color='r', linestyle='--', label=f'Mean: {mean_accel:.2f}', alpha=0.5)

plt.legend()

# Save the plot
plt.savefig('accel_plot.png', dpi=150, bbox_inches='tight')
print(f'✓ Plot saved as: accel_plot.png')
print(f'  Mean: {mean_accel:.2f} m/s²')
print(f'  Max: {max_accel:.2f} m/s²')
print(f'  Min: {min_accel:.2f} m/s²')
print(f'  Variance (max-min): {max_accel - min_accel:.2f} m/s²')
print(f'  Number of samples: {len(accel_wr_abs)}')

plt.show()
