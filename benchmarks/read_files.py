"""Report median end-to-end read times; requires an installed package."""

import argparse
from pathlib import Path
from statistics import median
from time import perf_counter

from pyelectrochemreader import Configuration, ReadError, read, supported_formats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    for path in args.paths:
        files = sorted(path.rglob("*")) if path.is_dir() else [path]
        for file in files:
            if file.suffix.lower() not in supported_formats:
                continue
            times = []
            try:
                for _ in range(args.repeat):
                    start = perf_counter()
                    experiment = read(file)
                    times.append(perf_counter() - start)
            except (ReadError, OSError) as exc:
                print(f"ERROR {file}: {exc}")
                continue
            rows = sum(len(table) for table in experiment) if isinstance(experiment, Configuration) else len(experiment[0])
            label = "configuration rows" if isinstance(experiment, Configuration) else "primary rows"
            print(f"{median(times):.6f} s  {rows:>10,} {label}  {file}")


if __name__ == "__main__":
    main()
