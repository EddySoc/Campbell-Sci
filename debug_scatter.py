from zipfile import ZipFile
from pathlib import Path

# Find the latest Excel file (skip temp files starting with ~$)
temp_dir = Path(r"C:\Users\EddyS\AppData\Local\Temp\campbell_sci")
xlsx_files = sorted(
    [f for f in temp_dir.glob("*.xlsx") if not f.name.startswith('~')],
    key=lambda x: x.stat().st_mtime,
    reverse=True
)

if not xlsx_files:
    print("No Excel files found")
    exit(1)

latest_file = xlsx_files[0]
print(f"Analyzing: {latest_file.name}\n")

with ZipFile(latest_file, 'r') as zf:
    # List chart files
    print("=== Chart files ===")
    all_files = zf.namelist()
    chart_files = [f for f in all_files if 'chart' in f.lower() and f.endswith('.xml')]
    for cf in sorted(chart_files):
        print(cf)
    
    # Analyze each scatter chart
    for chart_file in chart_files:
        try:
            content = zf.read(chart_file).decode('utf-8')
            
            if 'scatterChart' in content:
                print(f"\n=== {chart_file} (SCATTER CHART) ===")
                
                # Check for series
                if '<c:ser>' in content:
                    print("✓ Found series")
                    ser_start = content.find('<c:ser>')
                    ser_end = content.find('</c:ser>') + len('</c:ser>')
                    ser_xml = content[ser_start:ser_end]
                    
                    # Look for xVal and val
                    if '<c:xVal>' in ser_xml:
                        print("  ✓ Has xVal (X-axis)")
                    else:
                        print("  ✗ NO xVal (X-axis) - PROBLEM!")
                    
                    if '<c:val>' in ser_xml:
                        print("  ✓ Has val (Y-axis)")
                    else:
                        print("  ✗ NO val (Y-axis) - PROBLEM!")
                    
                    # Print excerpt
                    print("\nFirst 1000 chars of series:")
                    print(ser_xml[:1000])
                else:
                    print("✗ NO series found - CHART IS EMPTY!")
        except Exception as e:
            print(f"Error: {e}")
