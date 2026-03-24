#!/usr/bin/env node

/**
 * Convert HTML file to PDF using Puppeteer
 * This script ensures proper rendering of SVG and complex HTML content
 */

const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

async function htmlToPdf(inputPath, outputPath) {
  let browser;
  try {
    console.log(`Converting: ${inputPath}`);
    
    browser = await puppeteer.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
    
    const page = await browser.newPage();
    
    // Set a larger viewport to ensure good rendering
    await page.setViewport({ width: 1920, height: 1080 });
    
    // Load the HTML file
    const fileUrl = `file://${path.resolve(inputPath)}`;
    console.log(`Loading: ${fileUrl}`);
    
    await page.goto(fileUrl, {
      waitUntil: 'networkidle0',
      timeout: 120000
    });
    
    // Wait for rendering
    await new Promise(resolve => setTimeout(resolve, 3000));
    
    console.log(`Generating PDF: ${outputPath}`);
    
    // Get page height
    const pageMetrics = await page.metrics();
    const contentHeight = await page.evaluate(() => {
      return Math.max(
        document.body.scrollHeight,
        document.documentElement.scrollHeight
      );
    });
    
    console.log(`Page height: ${contentHeight}px`);
    
    await page.pdf({
      path: outputPath,
      format: 'A4',
      margin: {
        top: '20mm',
        right: '20mm',
        bottom: '20mm',
        left: '20mm'
      },
      printBackground: true,
      scale: 1,
      displayHeaderFooter: false,
      preferCSSPageSize: true
    });
    
    console.log(`✓ PDF generated successfully: ${outputPath}`);
    
  } catch (error) {
    console.error('Error:', error.message);
    process.exit(1);
  } finally {
    if (browser) {
      await browser.close();
    }
  }
}

// Parse command line arguments
const args = process.argv.slice(2);
if (args.length < 2) {
  console.log('Usage: node html_to_pdf.js <input.html> <output.pdf>');
  process.exit(1);
}

const [inputFile, outputFile] = args;

if (!fs.existsSync(inputFile)) {
  console.error(`Error: File not found: ${inputFile}`);
  process.exit(1);
}

htmlToPdf(inputFile, outputFile);
