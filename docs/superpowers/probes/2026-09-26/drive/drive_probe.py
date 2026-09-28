"""Probe: parse Drive folder links + build rclone argv + parse rclone JSON logs.

Nothing here touches the network or any rclone config; tests run against
local paths only.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

_ID = r"[A-Za-z0-9_-]{10,}"
_BARE_ID = re.compile(rf"^{_ID}$")
_FOLDER_PATH = re.compile(rf"/folders/({_ID})")
_FILE_PATH = re.compile(rf"/file/d/({_ID})")


class DriveLinkError(ValueError):
    pass


def parse_drive_folder(link: str) -> tuple[str, str | None]:
    """Return (folder_id, resource_key_or_None) from a Drive folder link or bare ID."""
    s = link.strip()
    if _BARE_ID.match(s):
        return s, None
    u = urlparse(s if "://" in s else "https://" + s)
    host = (u.hostname or "").lower()
    if host not in {"drive.google.com", "docs.google.com"}:
        raise DriveLinkError(f"not a Google Drive link: {host!r}")
    q = parse_qs(u.query)
    rk = (q.get("resourcekey") or [None])[0]
    if _FILE_PATH.search(u.path):
        raise DriveLinkError("this is a FILE link (/file/d/...), not a folder")
    m = _FOLDER_PATH.search(u.path)
    if m:
        return m.group(1), rk
    ids = q.get("id")
    if ids and _BARE_ID.match(ids[0]):
        return ids[0], rk
    raise DriveLinkError("no folder id found in link")


BASE_FLAGS = [
    "--use-json-log",
    "--stats", "1s",
    "--stats-log-level", "NOTICE",
    "-v",
    "--retries", "3",
    "--retries-sleep", "10s",
    "--low-level-retries", "10",
    "--drive-chunk-size", "64M",
    "--transfers", "1",
]


def upload_argv(rclone: str, remote: str, local_file: str, dest_name: str) -> list[str]:
    return [rclone, "copyto", local_file, f"{remote}:{dest_name}", *BASE_FLAGS]


def stat_argv(rclone: str, remote: str, dest_name: str) -> list[str]:
    return [rclone, "lsjson", f"{remote}:{dest_name}", "--stat", "--hash",
            "--hash-type", "md5", "--original"]


def run_with_progress(argv: list[str]) -> int:
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert p.stderr is not None
    for line in p.stderr:
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            print("TEXT", line.rstrip()[:100])  # pre-init lines (e.g. with -vv) are plain text
            continue
        st = d.get("stats")
        if st:
            tb = st.get("totalBytes") or 0
            pct = 100 * st.get("bytes", 0) / tb if tb else 100.0
            print(f"PROGRESS {pct:5.1f}% transfers={st.get('transfers')} errors={st.get('errors')}")
        elif d.get("level") in ("error", "critical"):
            print("ERROR", d.get("msg"))
        elif "object" in d:
            print("EVENT", d.get("msg"), d.get("object"), d.get("size"))
    return p.wait()


if __name__ == "__main__":
    cases = {
        "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345?usp=sharing": ("1AbCdEfGhIjKlMnOpQrStUvWxYz012345", None),
        "https://drive.google.com/drive/u/0/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345": ("1AbCdEfGhIjKlMnOpQrStUvWxYz012345", None),
        "https://drive.google.com/drive/u/1/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345?usp=drive_link": ("1AbCdEfGhIjKlMnOpQrStUvWxYz012345", None),
        "https://drive.google.com/drive/mobile/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345?usp=sharing": ("1AbCdEfGhIjKlMnOpQrStUvWxYz012345", None),
        "https://drive.google.com/drive/folders/0B1234abcdEFGHijklMNOPqrstu?resourcekey=0-ABCDEFGHIXJQpIGqBJq3MC&usp=sharing": ("0B1234abcdEFGHijklMNOPqrstu", "0-ABCDEFGHIXJQpIGqBJq3MC"),
        "https://drive.google.com/open?id=1AbCdEfGhIjKlMnOpQrStUvWxYz012345": ("1AbCdEfGhIjKlMnOpQrStUvWxYz012345", None),
        "https://drive.google.com/folderview?id=1AbCdEfGhIjKlMnOpQrStUvWxYz012345": ("1AbCdEfGhIjKlMnOpQrStUvWxYz012345", None),
        "drive.google.com/drive/folders/0AEeXXXXXXXXUk9PVA": ("0AEeXXXXXXXXUk9PVA", None),
        "1AbCdEfGhIjKlMnOpQrStUvWxYz012345": ("1AbCdEfGhIjKlMnOpQrStUvWxYz012345", None),
    }
    bad = [
        "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view?usp=sharing",
        "https://evil.example.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345",
        "https://drive.google.com/drive/shared-with-me",
    ]
    ok = 0
    for url, want in cases.items():
        got = parse_drive_folder(url)
        assert got == want, (url, got, want)
        ok += 1
    for url in bad:
        try:
            parse_drive_folder(url)
        except DriveLinkError as e:
            ok += 1
            print("rejected:", url[:60], "->", e)
        else:
            raise AssertionError(f"accepted bad url {url}")
    print(f"parser: {ok}/{len(cases) + len(bad)} cases OK")

    if len(sys.argv) == 3:  # local smoke test: python drive_probe.py <rclone> <file>
        rc = run_with_progress([sys.argv[1], "copyto", sys.argv[2], "t/dst3/out.mp4",
                                "--use-json-log", "--stats", "1s", "--stats-log-level", "NOTICE",
                                "-v", "--bwlimit", "40M"])
        print("exit", rc)
