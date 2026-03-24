#!/bin/bash
# Convert Markdown to PDF with Mermaid diagram support
# Usage: ./md_to_pdf.sh <markdown_file> [output_pdf]

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
MD_FILE="${1:?Missing input markdown file}"
PDF_FILE="${2:-${MD_FILE%.md}.pdf}"

if [ ! -f "$MD_FILE" ]; then
  echo "Error: File not found: $MD_FILE"
  exit 1
fi

echo "Converting Markdown to PDF with Mermaid support..."
echo "  Input:  $MD_FILE"
echo "  Output: $PDF_FILE"

# Get the directory of the markdown file
MD_DIR="$( cd "$( dirname "$MD_FILE" )" && pwd )"
MD_BASENAME="$( basename "$MD_FILE" )"
HTML_FILE="${MD_DIR}/${MD_BASENAME%.md}.html"
RENDERED_HTML="${MD_DIR}/${MD_BASENAME%.md}_rendered.html"

# Step 1: Generate HTML from Markdown using VS Code extension
echo ""
echo "Step 1: Generating HTML from Markdown..."
if [ -f "$HTML_FILE" ]; then
  echo "  HTML file already exists: $HTML_FILE"
  echo "  (Please manually export or regenerate if needed)"
else
  echo "  Warning: HTML file not found. Please export from VS Code:"
  echo "    1. Open $MD_FILE in VS Code"
  echo "    2. Right-click → Markdown Preview Enhanced → Export to HTML"
  exit 1
fi

# Step 2: Convert Mermaid diagrams to SVG
echo ""
echo "Step 2: Converting Mermaid diagrams to SVG..."
python3 "$SCRIPT_DIR/render_mermaid_html.py" "$HTML_FILE" "$RENDERED_HTML"

# Step 3: Convert HTML to PDF using Puppeteer
echo ""
echo "Step 3: Converting HTML to PDF..."
node "$SCRIPT_DIR/html_to_pdf.js" "$RENDERED_HTML" "$PDF_FILE"

echo ""
echo "✓ Successfully created: $PDF_FILE"
echo ""
echo "Summary:"
echo "  - Mermaid diagrams: Rendered to SVG"
echo "  - HTML to PDF: Using Puppeteer (chromium)"
echo "  - File size: $(du -h "$PDF_FILE" | cut -f1)"
