# Renders SPEC.md to SPEC.pdf (A4, 10.5 pt) and prints the page count -- the
# "two pages" check. Needs: pip install markdown pypdf, and Google Chrome.
# Author: Hemang (Roll No. 24bcs10209).   Usage: python3 tools/render_spec.py SPEC.md SPEC.pdf

import markdown, subprocess, sys, pypdf
src, out = sys.argv[1], sys.argv[2]
body = markdown.markdown(open(src).read(), extensions=["tables", "fenced_code"])
css = """
@page { size: A4; margin: 16mm 17mm; }
body { font-family: -apple-system, Helvetica, Arial, sans-serif; font-size: 10.5pt; line-height: 1.3; color: #111; }
h1 { font-size: 15pt; margin: 0 0 4px; } h2 { font-size: 11pt; margin: 7px 0 2px; border-bottom: 1px solid #ccc; }
p, ul, ol { margin: 3px 0; } li { margin: 1px 0; } ul, ol { padding-left: 18px; }
pre { font-size: 9pt; line-height: 1.2; background: #f4f4f4; padding: 4px 6px; margin: 4px 0; white-space: pre; }
code { font-family: Menlo, monospace; font-size: 9pt; }
table { border-collapse: collapse; margin: 4px 0; font-size: 9.5pt; } td, th { border: 1px solid #bbb; padding: 1px 5px; vertical-align: top; }
blockquote { margin: 4px 0; padding: 3px 8px; border-left: 3px solid #d33; background: #fff4f4; }
"""
import os, tempfile
html = os.path.join(tempfile.mkdtemp(), "spec.html")   # intermediate, not kept
open(html, "w").write(f"<!doctype html><meta charset=utf-8><title>BHP/1 spec</title><style>{css}</style>{body}")
subprocess.run(["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--headless", "--disable-gpu",
                "--no-pdf-header-footer", f"--print-to-pdf={out}", html], capture_output=True, check=True)
print("pages:", len(pypdf.PdfReader(out).pages))
