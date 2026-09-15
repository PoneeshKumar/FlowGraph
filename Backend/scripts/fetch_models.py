"""Download the trained GNN artifacts (checkpoints + feature cache).

`ml/runs/` and `ml/cache/` are gitignored — they are regenerable, and the feature
cache alone is 37 MB — so they ship as a GitHub Release asset instead:

    python3 scripts/fetch_models.py                 # ~52 MB into ml/runs and ml/cache

Without them the GNN stage of the pipeline is skipped (marks fall back to the
cycle and community signals) and `/viz` degrades gracefully. To rebuild them from
scratch instead, see "Reproducing the champion" in the README.
"""
import argparse
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPO = "PoneeshKumar/FlowGraph"
TAG = "models-v1"
ASSET = "flowgraph-models-v1.tar.gz"
URL = f"https://github.com/{REPO}/releases/download/{TAG}/{ASSET}"

BACKEND = Path(__file__).resolve().parent.parent
# What the archive is expected to contain, and what "already present" means.
EXPECTED = ["ml/runs/v10_L3", "ml/runs/v10_L3_s1", "ml/runs/v10_L3_s7", "ml/cache/featureset_v4.npz"]


def _present() -> bool:
    return all((BACKEND / p).exists() for p in EXPECTED)


def _report(blocks: int, block_size: int, total: int) -> None:
    if total <= 0:
        return
    done = min(blocks * block_size, total)
    pct = 100 * done / total
    sys.stdout.write(f"\r  {done / 1e6:5.1f} / {total / 1e6:.1f} MB ({pct:3.0f}%)")
    sys.stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=URL, help="override the release asset URL")
    ap.add_argument("--force", action="store_true", help="re-download even if the files exist")
    args = ap.parse_args()

    if _present() and not args.force:
        print("Model artifacts already present:")
        for p in EXPECTED:
            print(f"  {p}")
        print("Re-download with --force.")
        return 0

    print(f"Downloading {args.url}")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / ASSET
        try:
            urllib.request.urlretrieve(args.url, archive, _report)
            print()
        except urllib.error.HTTPError as exc:
            print(f"\nDownload failed ({exc.code} {exc.reason}).")
            print(f"If the release does not exist yet, train the champion instead — see")
            print('"Reproducing the champion" in the README (~15 min on CPU once the graph is ingested).')
            return 1
        except urllib.error.URLError as exc:
            print(f"\nDownload failed ({exc.reason}). Check your network and retry.")
            return 1

        print("Extracting…")
        with tarfile.open(archive) as tar:
            # Never write outside Backend/: a crafted archive could otherwise
            # traverse with ../ or a symlink.
            safe = []
            for member in tar.getmembers():
                target = (BACKEND / member.name).resolve()
                if not str(target).startswith(str(BACKEND.resolve())):
                    print(f"  refusing suspicious path: {member.name}")
                    return 1
                if member.issym() or member.islnk():
                    print(f"  refusing link member: {member.name}")
                    return 1
                safe.append(member)
            tar.extractall(BACKEND, members=safe)

    missing = [p for p in EXPECTED if not (BACKEND / p).exists()]
    if missing:
        print("Extracted, but these are still missing:")
        for p in missing:
            print(f"  {p}")
        return 1
    print("Done:")
    for p in EXPECTED:
        size = sum(f.stat().st_size for f in (BACKEND / p).rglob("*") if f.is_file()) \
            if (BACKEND / p).is_dir() else (BACKEND / p).stat().st_size
        print(f"  {p}  ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
