"""
Download the training datasets from Kaggle.

Everything here is gitignored (`data/`, `*.csv`, `benchmarks/data/`), so datasets
have to be fetched per-machine. HI-Small is already present; this fetches the
rest.

SETUP (once)
------------
    pip install kaggle
    # Kaggle -> Settings -> API -> "Create New Token", saves kaggle.json
    mkdir -p ~/.kaggle && mv ~/Downloads/kaggle.json ~/.kaggle/
    chmod 600 ~/.kaggle/kaggle.json

USAGE
-----
    python3 -m ml.datasets.fetch --list
    python3 -m ml.datasets.fetch elliptic
    python3 -m ml.datasets.fetch ibm-aml-medium

Some Kaggle datasets require accepting their terms in the browser first; the
CLI returns 403 until you do.
"""

import argparse
import logging
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)


@dataclass
class Dataset:
    """A fetchable dataset."""

    slug: str                                   # Kaggle dataset identifier
    destination: str                            # relative to Backend/
    expected_files: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)   # specific files, or all
    note: str = ""
    approx_size: str = ""


DATASETS: Dict[str, Dataset] = {
    "elliptic": Dataset(
        slug="ellipticco/elliptic-data-set",
        destination="benchmarks/data/elliptic",
        expected_files=[
            "elliptic_txs_features.csv",
            "elliptic_txs_classes.csv",
            "elliptic_txs_edgelist.csv",
        ],
        approx_size="~400MB zipped",
        note=(
            "203,769 nodes / 234,355 edges, 4,545 illicit. Pre-computed "
            "anonymized features, so it loads straight to a FeatureSet and "
            "bypasses Neo4j/Redis — validates the model, not the feature "
            "pipeline. See ml/datasets/elliptic.py."
        ),
    ),
    "ibm-aml-medium": Dataset(
        slug="ealtman2019/ibm-transactions-for-anti-money-laundering-aml",
        destination="benchmarks/data",
        files=["HI-Medium_Trans.csv", "HI-Medium_Patterns.txt"],
        expected_files=["HI-Medium_Trans.csv", "HI-Medium_Patterns.txt"],
        approx_size="~5GB",
        note=(
            "Same schema as HI-Small, so patterns.py and ingest_for_training "
            "work unchanged. Roughly 6x the transactions."
        ),
    ),
    "ibm-aml-li-small": Dataset(
        slug="ealtman2019/ibm-transactions-for-anti-money-laundering-aml",
        destination="benchmarks/data",
        files=["LI-Small_Trans.csv", "LI-Small_Patterns.txt"],
        expected_files=["LI-Small_Trans.csv", "LI-Small_Patterns.txt"],
        approx_size="~500MB",
        note=(
            "Lower illicit ratio — a more realistic prevalence than HI. Useful "
            "as a held-out test set: train on HI-Small, evaluate here, and the "
            "score is a genuine generalization number rather than a re-run."
        ),
    ),
}


def _kaggle_available() -> bool:
    if shutil.which("kaggle") is None:
        return False
    credentials = Path.home() / ".kaggle" / "kaggle.json"
    import os

    if not credentials.exists() and not os.environ.get("KAGGLE_KEY"):
        logger.error(
            "kaggle CLI found but no credentials. Create a token at "
            "kaggle.com -> Settings -> API and save it to ~/.kaggle/kaggle.json"
        )
        return False
    return True


def fetch(name: str, backend_root: Path, force: bool = False) -> Path:
    """Download and extract one dataset. Returns its destination directory."""
    if name not in DATASETS:
        raise KeyError(
            f"unknown dataset {name!r}; choose from {', '.join(sorted(DATASETS))}"
        )

    dataset = DATASETS[name]
    destination = backend_root / dataset.destination
    destination.mkdir(parents=True, exist_ok=True)

    present = [f for f in dataset.expected_files if (destination / f).exists()]
    if present and not force:
        if len(present) == len(dataset.expected_files):
            logger.info("%s already present in %s — skipping", name, destination)
            return destination
        logger.warning(
            "%s partially present (%d/%d files). Re-run with --force to refetch.",
            name, len(present), len(dataset.expected_files),
        )

    if not _kaggle_available():
        raise RuntimeError(
            "kaggle CLI unavailable. Run `pip install kaggle` and configure "
            "credentials — see this module's docstring."
        )

    commands: List[List[str]] = []
    if dataset.files:
        # Pull only the variants we want: the IBM archive holds every size, and
        # fetching all of them is tens of GB.
        for filename in dataset.files:
            commands.append([
                "kaggle", "datasets", "download",
                "-d", dataset.slug, "-f", filename,
                "-p", str(destination),
            ])
    else:
        commands.append([
            "kaggle", "datasets", "download",
            "-d", dataset.slug, "-p", str(destination),
        ])

    for command in commands:
        logger.info("Running: %s", " ".join(command))
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"kaggle download failed ({result.returncode}).\n"
                f"stdout: {result.stdout.strip()}\n"
                f"stderr: {result.stderr.strip()}\n"
                f"If this is a 403, accept the dataset's terms at "
                f"https://www.kaggle.com/datasets/{dataset.slug}"
            )

    for archive in sorted(destination.glob("*.zip")):
        logger.info("Extracting %s", archive.name)
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(destination)
        archive.unlink()

    missing = [f for f in dataset.expected_files if not (destination / f).exists()]
    if missing:
        raise RuntimeError(
            f"download finished but these are missing from {destination}: "
            f"{', '.join(missing)}. Check the archive layout on Kaggle — file "
            f"names occasionally change."
        )

    logger.info("%s ready in %s", name, destination)
    return destination


def _describe(backend_root: Path) -> None:
    print("Available datasets:\n")
    for name, dataset in sorted(DATASETS.items()):
        destination = backend_root / dataset.destination
        have = all((destination / f).exists() for f in dataset.expected_files)
        status = "PRESENT" if have else "missing"
        print(f"  {name:20s} [{status}]  {dataset.approx_size}")
        print(f"    kaggle: {dataset.slug}")
        print(f"    into:   {dataset.destination}")
        if dataset.note:
            print(f"    {dataset.note}")
        print()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(
        description="Download training datasets from Kaggle."
    )
    parser.add_argument(
        "datasets", nargs="*", help="dataset names, or none with --list"
    )
    parser.add_argument("--list", action="store_true", help="show status and exit")
    parser.add_argument(
        "--force", action="store_true", help="refetch even if files are present"
    )
    args = parser.parse_args()

    backend_root = Path(__file__).resolve().parents[2]

    if args.list or not args.datasets:
        _describe(backend_root)
        return 0

    for name in args.datasets:
        try:
            fetch(name, backend_root, force=args.force)
        except (KeyError, RuntimeError) as exc:
            logger.error("%s: %s", name, exc)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
