#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""沪上错题 · 报告 PDF 渲染器（仅标准库）
用法: python3 render_pdf.py <input.html> <output.pdf> [--page a4]
Chrome/Chromium/Edge headless 自动发现（跨平台候选路径+PATH），--no-sandbox 兜底容器环境。
退出码 0=成功(stdout 输出 PDF 路径)，非 0=失败(stderr 原因，调用方走降级链)。
"""
import argparse, os, shutil, subprocess, sys
from pathlib import Path

CANDS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
BROWSER = "chrome"

def find_browser():
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        p = shutil.which(name)
        if p: return p
    for c in CANDS:
        if Path(c).exists(): return c
    return None

def render(html, pdf, page="a4"):
    b = find_browser()
    if not b:
        sys.exit("NO_BROWSER: 未找到 Chrome/Chromium/Edge —— 降级：宿主 pdf_generator 工具或直发 HTML")
    src = Path(html).expanduser().resolve()
    dst = Path(pdf).expanduser().resolve()
    if not src.exists(): sys.exit(f"NO_FILE: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    size = {"a4": "A4", "letter": "Letter", "long": "A4"}[page if page in ("a4", "letter", "long") else "a4"]
    cmd = [b, "--headless=new", "--disable-gpu", "--no-sandbox",
           "--no-pdf-header-footer", "--virtual-time-budget=10000",
           f"--print-to-pdf={dst}", src.as_uri()]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if not dst.exists() or dst.stat().st_size < 1000:
        sys.exit(f"RENDER_FAIL: {r.stderr[-300:] or r.stdout[-300:]}")
    print(str(dst))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("html"); ap.add_argument("pdf"); ap.add_argument("--page", default="a4")
    a = ap.parse_args()
    render(a.html, a.pdf, a.page)
