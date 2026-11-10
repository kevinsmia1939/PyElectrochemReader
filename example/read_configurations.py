"""Export MPS/MGR configuration to UTF-8 text, CSV tables, and an HTML report.

Uses only Python's standard library and the NumPy-only reader; no plotting.
"""

import argparse
import csv
import html
import json
from pathlib import Path
import re
from urllib.parse import quote

from pyelectrochemreader import Configuration, read

ROOT = Path(__file__).resolve().parent


def write_configuration(config, directory):
    if not isinstance(config, Configuration):
        raise TypeError("Expected an MPS/MGR Configuration")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "source.txt").write_text(config.text, encoding="utf-8")
    # The original document tree/header is useful alongside the typed tables.
    (directory / "metadata.json").write_text(json.dumps(config.metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sections = []
    for index, table in enumerate(config.tables, 1):
        stem = re.sub(r"[^\w.-]+", "_", table.name).strip("_") or "table"
        filename = f"{index:02d}_{stem}.csv"
        with (directory / filename).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(table.column_names)
            writer.writerows(zip(*(table.columns[name] for name in table.column_names)))
        headings = "".join(f"<th>{html.escape(name)}</th>" for name in table.column_names)
        rows = []
        for values in zip(*(table.columns[name] for name in table.column_names)):
            cells = "".join(f"<td>{html.escape('' if value is None else str(value))}</td>" for value in values)
            rows.append(f"<tr>{cells}</tr>")
        sections.append(f'<section><h2>{html.escape(table.name)}</h2><p>{len(table)} rows · '
                        f'<a href="{quote(filename)}">CSV table</a></p><div class="scroll"><table>'
                        f'<thead><tr>{headings}</tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>')
    title = f"{config.format.upper()} configuration"
    document = f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{font:15px system-ui,sans-serif;color:#162b40;background:#f5f7fa;margin:30px}}
section{{background:white;padding:20px;margin:24px 0;border:1px solid #dce5ec;border-radius:10px}}
.scroll{{overflow:auto;max-height:600px}}table{{border-collapse:collapse;font-size:12px}}th,td{{border:1px solid #dce5ec;padding:7px;white-space:pre}}
th{{background:#edf3f7;position:sticky;top:0}}a{{color:#176b91}}pre{{overflow:auto;max-height:500px;font-size:12px}}</style>
<h1>{title}</h1><p>Configuration text and tables · {len(config.tables)} tables · no measurement plots</p>
<p><a href="source.txt">Original decoded text</a> · <a href="metadata.json">Complete metadata</a></p>
{"".join(sections)}<section><details><summary>Source text</summary><pre>{html.escape(config.text)}</pre></details></section></html>'''
    report = directory / "index.html"
    report.write_text(document, encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", type=Path, help="MPS/MGR paths (defaults to example/data/biologic)")
    parser.add_argument("--output", type=Path, default=ROOT / "text" / "biologic")
    args = parser.parse_args()
    files = args.files or sorted(p for p in (ROOT / "data" / "biologic").iterdir()
                                if p.suffix.lower() in (".mps", ".mgr"))
    for path in files:
        config = read(path)
        if not isinstance(config, Configuration):
            parser.error(f"{path} is not an MPS/MGR configuration")
        report = write_configuration(config, args.output / path.name)
        print(f"{path.name}: {len(config.tables)} tables; {sum(len(t) for t in config)} table rows")
        print(f"Report: {report}")


if __name__ == "__main__":
    main()
