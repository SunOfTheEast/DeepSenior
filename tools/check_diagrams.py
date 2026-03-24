#!/usr/bin/env python3
"""
Verify that Mermaid diagrams are present in the HTML before PDF conversion
"""

import sys
import re
from pathlib import Path

def check_svg_in_html(html_file):
    """Check if SVG elements are present in the HTML file"""
    with open(html_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Count SVG tags
    svg_count = len(re.findall(r'<svg', content))
    print(f"SVG elements found: {svg_count}")
    
    # Count Mermaid rendered divs
    mermaid_count = len(re.findall(r'class="mermaid-rendered"', content))
    print(f"Mermaid rendered divs: {mermaid_count}")
    
    # Check for flowchart content
    flowchart_count = len(re.findall(r'class="flowchart"', content))
    print(f"Flowchart SVG elements: {flowchart_count}")
    
    # Check for SVG text elements (diagram content)
    text_count = len(re.findall(r'<text[^>]*>', content))
    print(f"SVG text elements: {text_count}")
    
    if svg_count > 0 and text_count > 0:
        print("\n✓ Diagrams appear to be properly embedded!")
        return True
    else:
        print("\n✗ Warning: Diagrams may not be properly embedded")
        return False

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python check_diagrams.py <html_file>")
        sys.exit(1)
    
    html_file = sys.argv[1]
    if not Path(html_file).exists():
        print(f"Error: File not found: {html_file}")
        sys.exit(1)
    
    check_svg_in_html(html_file)
