"""Download every file of corkern VERSION from INDEX (pypi | testpypi) and compare its SHA-256
with the digest the index publishes. Prints one line per file; exits 1 on a mismatch or when
the release has no files."""
import hashlib
import json
import sys
import urllib.error
import urllib.request

version, index = sys.argv[1], sys.argv[2]
host = {"pypi": "https://pypi.org", "testpypi": "https://test.pypi.org"}[index]
try:
    with urllib.request.urlopen(f"{host}/pypi/corkern/{version}/json") as r:
        files = json.load(r)["urls"]
except urllib.error.HTTPError as e:
    sys.exit(f"corkern {version} is not on {index} ({e.code})")
bad = 0
for f in files:
    with urllib.request.urlopen(f["url"]) as r:
        got = hashlib.sha256(r.read()).hexdigest()
    want = f["digests"]["sha256"]
    print(f"{'ok  ' if got == want else 'BAD '} {got}  {f['filename']}")
    bad += got != want
sys.exit(1 if bad or not files else 0)
