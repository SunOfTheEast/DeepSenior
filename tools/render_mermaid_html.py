#!/usr/bin/env python3
"""
Convert Mermaid diagrams in HTML to SVG before PDF export.
This script processes HTML files and replaces Mermaid code blocks with rendered SVG.
"""

import os
import re
import sys
import json
import html
import tempfile
import subprocess
from pathlib import Path


def extract_mermaid_blocks(html_content):
    """
    Extract all mermaid code blocks from HTML.
    Returns list of (full_match, mermaid_code, match_object)
    """
    # Pattern to match: <div class="mermaid">...code...</div>
    pattern = r'<div class="mermaid">(.*?)</div>'
    matches = []
    
    for match in re.finditer(pattern, html_content, re.DOTALL):
        # Decode HTML entities (e.g., --&gt; to ->, &lt; to <, etc.)
        mermaid_code = html.unescape(match.group(1).strip())
        matches.append((match.group(0), mermaid_code, match))
    
    return matches


def render_mermaid_to_svg(mermaid_code, diagram_id):
    """
    Render mermaid code to SVG using mmdc command.
    Returns SVG string or None if rendering fails.
    """
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', 
            suffix='.mmd', 
            delete=False, 
            encoding='utf-8'
        ) as f:
            f.write(mermaid_code)
            temp_mmd = f.name
        
        temp_svg = temp_mmd.replace('.mmd', '.svg')
        
        # Run mmdc command
        result = subprocess.run(
            ['mmdc', '-i', temp_mmd, '-o', temp_svg, '-t', 'default'],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            print(f"Warning: Failed to render diagram {diagram_id}")
            print(f"  stderr: {result.stderr}")
            return None
        
        # Read rendered SVG
        with open(temp_svg, 'r', encoding='utf-8') as f:
            svg_content = f.read()
        
        # Remove problematic styles and animations for PDF compatibility
        # Remove keyframes (animations don't work in PDF)
        svg_content = re.sub(r'<style>.*?@keyframes.*?</style>', '<style></style>', svg_content, flags=re.DOTALL)
        
        # Remove animate elements
        svg_content = re.sub(r'<animate[^>]*>', '', svg_content)
        svg_content = re.sub(r'<animateTransform[^>]*>', '', svg_content)
        
        # Clean up empty style tags
        svg_content = re.sub(r'<style>\s*</style>', '', svg_content)
        
        # Add viewBox attributes to ensure proper sizing in PDF
        if 'viewBox=' not in svg_content and 'width=' in svg_content and 'height=' in svg_content:
            # Try to extract dimensions
            width_match = re.search(r'width="([^"]*)"', svg_content)
            height_match = re.search(r'height="([^"]*)"', svg_content)
            if width_match and height_match:
                width = width_match.group(1)
                height = height_match.group(1)
                svg_content = svg_content.replace(
                    '<svg',
                    f'<svg viewBox="0 0 {width} {height}"',
                    1
                )
        
        # Clean up temp files
        os.unlink(temp_mmd)
        if os.path.exists(temp_svg):
            os.unlink(temp_svg)
        
        return svg_content
    
    except Exception as e:
        print(f"Error rendering diagram {diagram_id}: {e}")
        return None


def process_html_file(input_path, output_path=None):
    """
    Process HTML file and convert Mermaid diagrams to SVG.
    """
    if output_path is None:
        output_path = input_path
    
    print(f"Processing: {input_path}")
    
    # Read HTML
    with open(input_path, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    # Extract and process mermaid blocks
    mermaid_blocks = extract_mermaid_blocks(html_content)
    
    if not mermaid_blocks:
        print("  No Mermaid diagrams found.")
        return
    
    print(f"  Found {len(mermaid_blocks)} Mermaid diagram(s)")
    
    # Replace mermaid blocks with SVG
    modified_html = html_content
    for i, (full_match, mermaid_code, match) in enumerate(mermaid_blocks):
        diagram_id = f"diagram_{i+1}"
        print(f"  Rendering {diagram_id}...")
        
        svg_content = render_mermaid_to_svg(mermaid_code, diagram_id)
        
        if svg_content:
            # Wrap SVG in a div for styling with page-break handling
            replacement = f'<div class="mermaid-rendered" style="page-break-inside: avoid; margin: 20px 0; text-align: center;">{svg_content}</div>'
            modified_html = modified_html.replace(full_match, replacement, 1)
            print(f"    ✓ {diagram_id} rendered successfully")
        else:
            print(f"    ✗ {diagram_id} failed, keeping original")
    
    # Add CSS for better PDF rendering
    css_injection = '''
    <style>
    .mermaid-rendered svg {
        max-width: 100%;
        height: auto;
        display: block;
        margin: 0 auto;
        background: white;
    }
    .mermaid-rendered {
        page-break-inside: avoid;
        margin: 20px 0;
    }
    </style>
    '''
    
    # Inject CSS before closing head tag
    if '</head>' in modified_html:
        modified_html = modified_html.replace('</head>', css_injection + '</head>')
    
    # Write modified HTML
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(modified_html)
    
    print(f"  Output saved to: {output_path}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python render_mermaid_html.py <html_file> [output_file]")
        print("\nThis script converts Mermaid diagrams in HTML to SVG before PDF export.")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    if not os.path.exists(input_file):
        print(f"Error: File not found: {input_file}")
        sys.exit(1)
    
    process_html_file(input_file, output_file)
    print("\n✓ Processing complete!")


if __name__ == '__main__':
    main()
