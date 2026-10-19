#!/usr/bin/env python3
"""Build the JobPilot landing page (one self-contained HTML file) from the repo.

    python3 site/build.py [--out site/dist/jobpilot.html]

It creates a fictional demo workspace, exports its dashboard (embedded as the page's "Try it live" tab), and inlines
the screenshots in docs/images (converted to JPEG with macOS `sips` when available, else kept as PNG). Nothing in it
comes from a real workspace.
"""
import argparse
import base64
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IMAGES = REPO / "docs" / "images"
SHOTS = {"{{IMG_TODAY}}": "dashboard-today.png", "{{IMG_TODAY_ZH}}": "dashboard-today-zh.png",
         "{{IMG_POOL}}": "dashboard-pool.png", "{{IMG_APPS}}": "dashboard-applications.png",
         "{{IMG_REFS}}": "dashboard-referrals.png"}


def image_uri(png, tmp):
    if shutil.which("sips"):
        jpg = tmp / (png.stem + ".jpg")
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "72", "--resampleWidth", "1400",
                        str(png), "--out", str(jpg)], capture_output=True, check=True)
        return "data:image/jpeg;base64," + base64.b64encode(jpg.read_bytes()).decode()
    return "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "site" / "dist" / "jobpilot.html"))
    args = ap.parse_args()
    page = (REPO / "site" / "landing.template.html").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="jobpilot-site-") as tmp:
        tmp = Path(tmp)
        demo = tmp / "demo.html"
        subprocess.run([str(REPO / "plugins" / "jobpilot" / "bin" / "jobpilot"), "demo", str(tmp / "ws"), "--force",
                        "--export", str(demo)], check=True, capture_output=True, env={"JOBPILOT_OFFLINE": "1",
                                                                                       "PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin"})
        for key, name in SHOTS.items():
            page = page.replace(key, image_uri(IMAGES / name, tmp))
        page = page.replace("{{DEMO_B64}}", base64.b64encode(demo.read_bytes()).decode())
    if re.search(r"\{\{[A-Z_]+\}\}", page):
        sys.exit("unfilled placeholder in the template")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print("%s (%.1f MB)" % (out, out.stat().st_size / 1e6))


if __name__ == "__main__":
    main()
