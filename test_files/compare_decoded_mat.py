#!/usr/bin/env python3
"""
Compare decoded MAT files to verify decoder accuracy.
Useful for validating Python decoder against MATLAB reference.
"""

import os
import sys
import numpy as np
from scipy.io import loadmat
import matplotlib.pyplot as plt

def load_mat_safely(filepath):
    """Load MAT file with error handling."""
    try:
        data = loadmat(filepath, squeeze_me=True, struct_as_record=False)
        # Remove MATLAB metadata keys
        return {k: v for k, v in data.items() if not k.startswith('__')}
    except Exception as e:
        print(f"❌ Error loading {filepath}: {e}")
        return None

def compare_arrays(arr1, arr2, name, tolerance=1e-6):
    """Compare two arrays with tolerance."""
    if arr1 is None or arr2 is None:
        return f"⚠️  {name}: One array is None"
    
    # Convert to numpy arrays if needed
    if not isinstance(arr1, np.ndarray):
        arr1 = np.array(arr1)
    if not isinstance(arr2, np.ndarray):
        arr2 = np.array(arr2)
    
    # Shape and size info
    shape_info = f"shapes: {arr1.shape} vs {arr2.shape}"
    dtype_info = f"dtypes: {arr1.dtype} vs {arr2.dtype}"
    
    # EXACT shape matching required
    if arr1.shape != arr2.shape:
        return f"❌ {name}: Shape mismatch - EXACT shapes required ({shape_info}, sizes: {arr1.size} vs {arr2.size})"
    
    # Same shape comparison
    if arr1.size == 0:
        return f"✅ {name}: Both empty ({shape_info}, {dtype_info})"
    
    # Adjust tolerance based on field name
    if name.endswith('_cal'):
        # Calibrated fields can have up to 1e-10 tolerance
        cal_tolerance = 1e-10
        return f"✅ {name}: Same shape ({shape_info}) - " + compare_flat_arrays(arr1, arr2, cal_tolerance, dtype_info, is_cal=True)
    else:
        # Non-calibrated fields must match exactly
        return f"✅ {name}: Same shape ({shape_info}) - " + compare_flat_arrays(arr1, arr2, 0, dtype_info, is_cal=False)

def compare_flat_arrays(arr1, arr2, tolerance, dtype_info, is_cal=False):
    """Compare arrays assuming they have compatible shapes."""
    # Handle different data types
    if np.issubdtype(arr1.dtype, np.floating) or np.issubdtype(arr2.dtype, np.floating):
        # Floating point comparison
        if is_cal:
            # Calibrated fields: use tolerance up to 1e-10
            if np.allclose(arr1, arr2, rtol=tolerance, atol=tolerance, equal_nan=True):
                max_diff = np.max(np.abs(arr1 - arr2))
                return f"Match within cal tolerance (max diff: {max_diff:.2e}, tol: {tolerance:.0e}) ({dtype_info})"
            else:
                diff = np.abs(arr1 - arr2)
                max_diff = np.max(diff)
                mean_diff = np.mean(diff)
                return f"❌ Cal field differs (max: {max_diff:.2e}, mean: {mean_diff:.2e}, tol: {tolerance:.0e}) ({dtype_info})"
        else:
            # Non-calibrated fields: must be exact
            if np.array_equal(arr1, arr2):
                return f"Exact floating match ({dtype_info})"
            else:
                diff = np.abs(arr1 - arr2)
                max_diff = np.max(diff)
                mean_diff = np.mean(diff)
                return f"❌ Must be exact (max: {max_diff:.2e}, mean: {mean_diff:.2e}) ({dtype_info})"
    else:
        # Integer comparison - always exact
        if np.array_equal(arr1, arr2):
            return f"Exact integer match ({dtype_info})"
        else:
            diff_count = np.sum(arr1 != arr2)
            diff_pct = (diff_count / arr1.size) * 100
            return f"❌ {diff_count}/{arr1.size} integers differ ({diff_pct:.1f}%) ({dtype_info})"

def plot_comparison(arr1, arr2, name, output_dir="comparison_plots"):
    """Plot arrays for visual comparison."""
    os.makedirs(output_dir, exist_ok=True)
    
    if arr1 is None or arr2 is None:
        return
    
    # Convert to numpy
    if not isinstance(arr1, np.ndarray):
        arr1 = np.array(arr1)
    if not isinstance(arr2, np.ndarray):
        arr2 = np.array(arr2)
    
    if arr1.shape != arr2.shape or arr1.size == 0:
        return
    
    # Limit plot size for large arrays
    if arr1.size > 10000:
        step = arr1.size // 5000
        arr1_plot = arr1[::step]
        arr2_plot = arr2[::step]
        indices = np.arange(0, arr1.size, step)
    else:
        arr1_plot = arr1
        arr2_plot = arr2
        indices = np.arange(arr1.size)
    
    plt.figure(figsize=(12, 8))
    
    # Plot both arrays
    plt.subplot(3, 1, 1)
    plt.plot(indices, arr1_plot, 'b-', label='Array 1', alpha=0.7)
    plt.plot(indices, arr2_plot, 'r--', label='Array 2', alpha=0.7)
    plt.title(f'{name} - Overlay Comparison')
    plt.legend()
    plt.grid(True)
    
    # Plot difference
    plt.subplot(3, 1, 2)
    diff = arr1_plot - arr2_plot
    plt.plot(indices, diff, 'g-', label='Difference')
    plt.title(f'{name} - Difference (Array1 - Array2)')
    plt.legend()
    plt.grid(True)
    
    # Statistics
    plt.subplot(3, 1, 3)
    plt.text(0.1, 0.7, f"Shape: {arr1.shape}", transform=plt.gca().transAxes)
    plt.text(0.1, 0.6, f"Array 1 range: [{np.min(arr1):.3e}, {np.max(arr1):.3e}]", transform=plt.gca().transAxes)
    plt.text(0.1, 0.5, f"Array 2 range: [{np.min(arr2):.3e}, {np.max(arr2):.3e}]", transform=plt.gca().transAxes)
    plt.text(0.1, 0.4, f"Max diff: {np.max(np.abs(diff)):.3e}", transform=plt.gca().transAxes)
    plt.text(0.1, 0.3, f"Mean diff: {np.mean(np.abs(diff)):.3e}", transform=plt.gca().transAxes)
    plt.text(0.1, 0.2, f"RMS diff: {np.sqrt(np.mean(diff**2)):.3e}", transform=plt.gca().transAxes)
    plt.axis('off')
    plt.title(f'{name} - Statistics')
    
    plt.tight_layout()
    plot_path = os.path.join(output_dir, f"{name.replace('/', '_').replace(' ', '_')}.png")
    plt.savefig(plot_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"📊 Saved comparison plot: {plot_path}")

def compare_mat_files(file1, file2, tolerance=1e-6, create_plots=True):
    """Compare two MAT files comprehensively."""
    print(f"🔍 Comparing MAT files:")
    print(f"   File 1: {file1}")
    print(f"   File 2: {file2}")
    print(f"   Tolerance: {tolerance}")
    print("=" * 80)
    
    # Load files
    data1 = load_mat_safely(file1)
    data2 = load_mat_safely(file2)
    
    if data1 is None or data2 is None:
        return
    
    # Get all field names
    fields1 = set(data1.keys())
    fields2 = set(data2.keys())
    
    print(f"\n📊 Field Summary:")
    print(f"   File 1 fields: {len(fields1)}")
    print(f"   File 2 fields: {len(fields2)}")
    print(f"   Common fields: {len(fields1 & fields2)}")
    print(f"   File 1 only: {fields1 - fields2}")
    print(f"   File 2 only: {fields2 - fields1}")
    
    # Extract first level contents from both files
    def extract_first_level_fields(data, file_name):
        """Extract fields from the first struct found in data."""
        extracted_fields = {}
        
        for key, value in data.items():
            if hasattr(value, '_fieldnames'):  # Found a struct
                print(f"\n📁 Extracting from {file_name} -> {key}:")
                for field in value._fieldnames:
                    try:
                        extracted_fields[field] = getattr(value, field)
                        field_val = getattr(value, field)
                        if hasattr(field_val, 'shape'):
                            print(f"   {field}: shape={field_val.shape}, dtype={field_val.dtype}")
                        else:
                            print(f"   {field}: {type(field_val).__name__}")
                    except Exception as e:
                        print(f"   {field}: <Error: {e}>")
                break  # Use the first struct found
        
        return extracted_fields
    
    # Extract fields from both files
    file1_fields = extract_first_level_fields(data1, "File 1")
    file2_fields = extract_first_level_fields(data2, "File 2")
    
    if not file1_fields or not file2_fields:
        print("⚠️  Could not extract struct fields from one or both files")
        return
    
    # Compare extracted fields
    fields1_set = set(file1_fields.keys())
    fields2_set = set(file2_fields.keys())
    
    print(f"\n🔍 Comparing extracted struct fields:")
    print(f"   File 1 struct fields: {len(fields1_set)}")
    print(f"   File 2 struct fields: {len(fields2_set)}")
    print(f"   Matching fields: {len(fields1_set & fields2_set)}")
    print(f"   File 1 only: {fields1_set - fields2_set}")
    print(f"   File 2 only: {fields2_set - fields1_set}")
    
    # Compare matching fields
    results = []
    matching_fields = sorted(fields1_set & fields2_set)
    print(f"\n🔍 Detailed Field Comparisons:")
    
    for field in matching_fields:
        try:
            file1_value = file1_fields[field]
            file2_value = file2_fields[field]
            result = compare_arrays(file1_value, file2_value, field, tolerance)
            results.append(result)
            print(f"   {result}")
            
            # Create plots for mismatches
            if create_plots and '❌' in result:
                plot_comparison(file1_value, file2_value, f"comparison_{field}")
        except Exception as e:
            print(f"   ❌ Error comparing {field}: {e}")
    
    # If no matching fields found, show what we have
    if not results:
        print("\n⚠️  No matching fields found in struct comparison, trying top-level comparison:")
        common_fields = sorted(fields1 & fields2)
        
        print(f"\n🔍 Field Comparisons:")
        for field in common_fields:
            result = compare_arrays(data1[field], data2[field], field, tolerance)
            results.append(result)
            print(f"   {result}")
            
            # Create plots for important fields - skip struct objects
            try:
                if create_plots and ('❌' in result or field in ['timestamps', 'Accel_WR_X', 'timestampCal']):
                    # Check if it's a numeric array before plotting
                    val1, val2 = data1[field], data2[field]
                    if hasattr(val1, 'shape') and hasattr(val2, 'shape') and not hasattr(val1, '_fieldnames'):
                        plot_comparison(val1, val2, field)
            except Exception as e:
                print(f"   📊 Skipped plot for {field}: {e}")
    
    # Summary
    matches = sum('✅' in r for r in results)
    total = len(results)
    print(f"\n📈 Summary:")
    print(f"   {matches}/{total} fields match ({matches/total*100:.1f}%)")
    
    if matches == total:
        print("🎉 All fields match! Decoder validation successful.")
    else:
        print("⚠️  Some fields differ. Check results above.")

def main():
    """Main comparison script."""
    if len(sys.argv) < 3:
        print("Usage: python compare_decoded_mat.py <file1.mat> <file2.mat> [tolerance]")
        print("\nExample:")
        print("  python compare_decoded_mat.py matlab_output.mat python_output.mat 1e-6")
        print("  python compare_decoded_mat.py test_files/data/testData.mat test_files/data/realActions/testData.mat")
        return
    
    file1 = sys.argv[1]
    file2 = sys.argv[2]
    tolerance = float(sys.argv[3]) if len(sys.argv) > 3 else 1e-6
    
    # Check files exist
    for f in [file1, file2]:
        if not os.path.exists(f):
            print(f"❌ File not found: {f}")
            return
    
    compare_mat_files(file1, file2, tolerance, create_plots=True)

if __name__ == "__main__":
    main()