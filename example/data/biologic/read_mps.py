"""Print every example.mps setting and sequence field, without plotting.

From the repository root:
    PYTHONPATH=src python3 example/data/biologic/read_mps.py
"""

from pathlib import Path

from pyelectrochemreader import read


def main():
    settings = read(Path(__file__).with_name("example.mps"))
    print(settings.to_text())


if __name__ == "__main__":
    main()
