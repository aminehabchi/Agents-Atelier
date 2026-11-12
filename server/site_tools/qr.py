"""QR codes for the QR code agent. Agents can't run code, and a QR encoder written by a model would rarely scan, so:

  1. The QR code agent writes qr.json at the site's root: [{"file": "assets/qr-whatsapp.svg", "data": "https://wa.me/…",
     "label": "Scan to chat on WhatsApp"}, …] and places <img src="assets/qr-whatsapp.svg"> where each code goes.
  2. When it finishes, the server reads qr.json, makes each SVG with segno (error correction M, quiet zone of 4 modules,
     black on white so every phone can read it) and deletes qr.json, so it is never part of the site.

What a code may contain is limited to links and contact formats; anything else is skipped and reported.
"""

import json
import re
from pathlib import Path

import segno

SPEC = "qr.json"
MAX_CODES = 6
MAX_DATA = 900
FILE_RE = re.compile(r"^assets/qr-[a-z0-9][a-z0-9-]{0,40}\.svg$")
DATA_RE = re.compile(r"^(https?://|tel:|mailto:|sms:|smsto:|WIFI:|BEGIN:VCARD)", re.I)


def build(site: Path) -> tuple[list[str], list[str]]:
    """Make the codes listed in site/qr.json, then remove it. Returns (files made, problems)."""
    spec = Path(site) / SPEC
    if not spec.is_file():
        return [], []
    made, problems = [], []
    try:
        items = json.loads(spec.read_text(errors="replace"))
    except ValueError:
        items, problems = [], ["qr.json isn't valid JSON"]
    finally:
        spec.unlink(missing_ok=True)
    if not isinstance(items, list):
        return [], problems or ["qr.json must be a list of codes"]
    for item in items[:MAX_CODES]:
        if not isinstance(item, dict):
            continue
        file, data = str(item.get("file") or ""), str(item.get("data") or "").strip()
        label = " ".join(str(item.get("label") or "QR code").split())[:120]
        if not FILE_RE.match(file):
            problems.append(f"{file or '(no file)'}: the file must be assets/qr-<name>.svg")
        elif not data or len(data) > MAX_DATA or not DATA_RE.match(data):
            problems.append(f"{file}: only links (https:, tel:, mailto:, sms:), Wi-Fi or a vCard can go in a QR code")
        else:
            (Path(site) / file).parent.mkdir(parents=True, exist_ok=True)
            segno.make(data, error="m", micro=False).save(Path(site) / file, kind="svg", scale=8, border=4,
                                                          dark="#000000", light="#ffffff", xmldecl=False, title=label)
            made.append(file)
    if len(items) > MAX_CODES:
        problems.append(f"only the first {MAX_CODES} codes were made")
    return made, problems
