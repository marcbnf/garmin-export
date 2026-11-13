#!/usr/bin/env python3
"""Turn part of a real export cache into an anonymized test fixture.

    python tools/anonymize_cache.py <export folder> tests/fixtures/private-<name> [--days 3] [--activities 2]

Copies the most recent cached days and activities from <export folder>/.cache
and replaces what identifies you: names, emails, ids, GPS coordinates,
free-text fields, device serials. Measurements (heart rate, sleep, stress...)
are kept, so tests run against the real shapes Garmin returns.

The values are still your health measurements: review the files before
sharing them. Folders named tests/fixtures/private* are git-ignored; the
tests pick up every folder under tests/fixtures/.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

# Keys whose value is replaced (matched case-insensitively, on the full key)
TEXT_KEYS = {
    "displayname", "fullname", "username", "firstname", "lastname", "name", "email",
    "emailaddress", "ownerdisplayname", "ownerfullname", "ownerprofileimageurlsmall",
    "ownerprofileimageurlmedium", "ownerprofileimageurllarge", "profileimageurlsmall",
    "profileimageurlmedium", "profileimageurllarge", "description", "locationname",
    "activityname", "comments", "notes", "serialnumber", "uuid", "location", "city",
    "address", "polyline", "encodedpolyline", "phonenumber",
}
ID_KEY = re.compile(r"(id|pk)$", re.I)                       # userProfileId, ownerId, deviceId...
COORD_KEY = re.compile(r"(lat|lon|latitude|longitude)$", re.I)
EMAIL = re.compile(r"[^@\s\"]+@[^@\s\"]+\.[a-z]{2,}", re.I)
KEEP_IDS = {"typeid", "parenttypeid", "sorttypeid", "eventtypeid", "sporttypeid", "zonenumber"}


def fake_id(value: Any) -> int:
    """Stable replacement id (same input -> same output, so links between files hold)."""
    return int(hashlib.sha256(str(value).encode()).hexdigest()[:8], 16)


def anonymize(data: Any, key: str = "") -> Any:
    k = key.lower()
    if isinstance(data, dict):
        return {kk: anonymize(v, kk) for kk, v in data.items()}
    if isinstance(data, list):
        return [anonymize(v, key) for v in data]
    if k in TEXT_KEYS and isinstance(data, str):
        return "redacted"
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        if COORD_KEY.search(k):
            return 0.0
        if ID_KEY.search(k) and k not in KEEP_IDS:
            return fake_id(data)
    if isinstance(data, str):
        return EMAIL.sub("user@example.com", data)
    return data


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("export_dir", type=Path)
    p.add_argument("dest", type=Path)
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--activities", type=int, default=2)
    a = p.parse_args()

    cache = a.export_dir.expanduser() / ".cache"
    days = sorted((cache / "daily").glob("[0-9]*.json"))[-a.days:]
    acts = sorted((cache / "activities").glob("*.json"), key=lambda f: f.stat().st_mtime)[-a.activities:]
    if not days:
        sys.exit(f"No cached days in {cache / 'daily'}: run an export first.")

    for sub, files in (("daily", days), ("activities", acts)):
        out = a.dest / sub
        out.mkdir(parents=True, exist_ok=True)
        for f in files:
            data = anonymize(json.loads(f.read_text(encoding="utf-8")))
            name = f.name if sub == "daily" else f"{fake_id(f.stem)}.json"
            if sub == "activities" and isinstance(data.get("summary"), dict):
                data["summary"]["activityId"] = int(Path(name).stem)
            (out / name).write_text(json.dumps(data), encoding="utf-8")
    print(f"Wrote {len(days)} days and {len(acts)} activities to {a.dest}. "
          "Review them before sharing or committing.")


if __name__ == "__main__":
    main()
