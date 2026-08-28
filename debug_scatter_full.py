import zipfile
from pathlib import Path

excel_file = Path("C:/Users/EddyS/AppData/Local/Temp/campbell_sci/bm_2011_01_18_10_30_09.xlsx")

with zipfile.ZipFile(excel_file, 'r') as z:
    # Read chart3.xml (first scatter chart)
    content = z.read('xl/charts/chart3.xml').decode('utf-8')
    
    # Find and print the full series element
    start = content.find('<ser>')
    end = content.find('</ser>') + len('</ser>')
    
    if start > -1:
        ser_xml = content[start:end]
        # Pretty print with indentation
        import xml.dom.minidom as minidom
        try:
            dom = minidom.parseString(ser_xml)
            print(dom.toprettyxml(indent="  "))
        except:
            print(ser_xml)
