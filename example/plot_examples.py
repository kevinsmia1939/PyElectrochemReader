"""Copy one instrument example per format and render a Matplotlib gallery.

Run from the repository root with PYTHONPATH=src, or install the reader first.
Matplotlib is required only for this example script.
"""

import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import shutil
from urllib.parse import quote

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from pyelectrochemreader import ReadError, read, supported_formats

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PLOTS = ROOT / "plots"
COLORS = ["#176b91", "#d16a35", "#47805b", "#8656a4", "#b84567"]
PREFERRED = {".mpr": "CV_C01.mpr", ".mpt": "example.mpt", ".dta": "eispot_data.dta",
             ".nda": "TestFile.nda", ".ndax": "123456789012_Unit27_Example_Data_File.ndax",
             ".res": "arbin1.res"}


def collect(source):
    """Select one readable file per format and retain its source license notices."""
    source = source.resolve()
    files = sorted(p for p in source.rglob("*") if p.is_file()
                   and ".git" not in p.parts and p.suffix.lower() in supported_formats)
    if not files:
        raise SystemExit(f"No supported measurement files in {source}")
    selected = []
    for extension in supported_formats:
        candidates = sorted((p for p in files if p.suffix.lower() == extension),
                            key=lambda p: (p.name != PREFERRED.get(extension), p.stat().st_size))
        for candidate in candidates:
            try:
                read(candidate)
            except ReadError:
                continue
            selected.append(candidate)
            break
    manifest = []
    DATA.mkdir(parents=True, exist_ok=True)
    for path in selected:
        relative = path.relative_to(source)
        target = DATA / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        sidecar = path.with_name(path.name + ".license")
        if sidecar.is_file():
            shutil.copy2(sidecar, target.with_name(target.name + ".license"))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"Copy verification failed: {relative}")
        manifest.append({"file": path.name, "source": relative.as_posix(), "format": path.suffix.lower()[1:],
                         "bytes": path.stat().st_size, "sha256": digest})
    for project in ("galvani", "NewareNDA", "gamry-parser", "."):
        license_file = source / project / "LICENSE"
        label = project if project != "." else "echem-data-viewer"
        if license_file.is_file():
            target = ROOT / "licenses" / label / "LICENSE"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(license_file, target)
        licenses = source / project / "LICENSES"
        if licenses.is_dir():
            shutil.copytree(licenses, ROOT / "licenses" / label / "LICENSES", dirs_exist_ok=True)
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def first(dataset, candidates):
    for name in candidates:
        if name in dataset:
            return numeric(dataset[name])
    return None


def numeric(values):
    values = np.asarray(values)
    if values.dtype.kind in "fiu":
        return values
    result = np.full(len(values), np.nan)
    for index, value in enumerate(values):
        try:
            result[index] = float(value)
        except (TypeError, ValueError):
            pass
    return result


def x_values(dataset, length, file_format):
    if file_format not in ("nda", "ndax") and "time" in dataset:
        values = numeric(dataset["time"])
        finite = values[np.isfinite(values)]
        if len(values) == length and len(finite) and np.all(np.diff(finite) >= 0):
            return values, "Time (s)"
    for name in ("Index", "Data_Point", "Pt"):
        if name in dataset and len(dataset[name]) == length:
            return dataset[name], "Record index"
    return np.arange(length), "Sample index"


def impedance(dataset):
    real = first(dataset, ("Re(Z)/Ohm", "Zreal"))
    negative_imag = first(dataset, ("-Im(Z)/Ohm",))
    if negative_imag is None:
        imag = first(dataset, ("Zimag",))
        negative_imag = -imag if imag is not None else None
    magnitude = first(dataset, ("|Z|/Ohm", "Zmod"))
    phase = first(dataset, ("Phase(Z)/deg", "Zphz"))
    return real, negative_imag, magnitude, phase


def numeric_plot(axis, x, y, *, color, label=None, scatter=False):
    x, y = numeric(x), numeric(y)
    if len(x) != len(y):
        return False
    # Keep missing samples as gaps in lines. No interpolation or smoothing.
    valid = np.isfinite(x) & np.isfinite(y)
    if not np.any(valid):
        return False
    if scatter:
        axis.scatter(x[valid], y[valid], s=9, color=color, label=label, alpha=.8)
    else:
        axis.plot(np.where(valid, x, np.nan), np.where(valid, y, np.nan),
                  color=color, label=label, linewidth=1)
    return True


def render_file(experiment, relative, destination):
    datasets = [d for d in experiment if d.name not in ("run_info", "steps") and not d.name.startswith("aux_")]
    eis = any("frequency" in d and impedance(d)[2] is not None for d in datasets)
    fig, axes = plt.subplots(2, 2, figsize=(11, 7), layout="constrained")
    axes = axes.ravel()
    used = set()
    legends = set()
    for index, dataset in enumerate(datasets):
        color = COLORS[index % len(COLORS)]
        label = dataset.name or f"Dataset {index + 1}"
        if eis and "frequency" in dataset:
            frequency = dataset["frequency"]
            real, negative_imag, magnitude, phase = impedance(dataset)
            if real is not None and negative_imag is not None:
                valid = frequency > 0
                if len(real) == len(valid) and numeric_plot(axes[0], np.where(valid, real, np.nan), negative_imag,
                                                          color=color, label=label, scatter=True):
                    axes[0].set(xlabel="Re(Z) (Ω)", ylabel="−Im(Z) (Ω)", title="Nyquist")
                    used.add(0)
            for panel, values, ylabel in ((1, magnitude, "|Z| (Ω)"), (2, phase, "Phase (°)")):
                if values is not None and numeric_plot(axes[panel], np.where(frequency > 0, frequency, np.nan), values,
                                                       color=color, label=label, scatter=True):
                    axes[panel].set(xlabel="Frequency (Hz)", ylabel=ylabel, xscale="log", title="Bode")
                    if panel == 1 and np.any(values > 0):
                        axes[panel].set_yscale("log")
                    used.add(panel)
            if "voltage" in dataset:
                values = numeric(dataset["voltage"])
                x, xlabel = x_values(dataset, len(values), experiment.format)
                if numeric_plot(axes[3], x, values, color=color, label=label):
                    axes[3].set(xlabel=xlabel, ylabel="Voltage (V)", title="Recorded potential")
                    used.add(3)
        else:
            voltage = first(dataset, ("voltage", "Vstep", "Vfwd"))
            currents = [("current", "Current"), ("Ifwd", "Forward current"),
                        ("Irev", "Reverse current"), ("Idif", "Difference current")]
            if voltage is not None:
                x, xlabel = x_values(dataset, len(voltage), experiment.format)
                if numeric_plot(axes[0], x, voltage, color=color, label=label):
                    axes[0].set(xlabel=xlabel, ylabel="Voltage (V)", title="Recorded potential")
                    used.add(0)
            for current_index, (name, description) in enumerate(currents):
                if name not in dataset:
                    continue
                current = dataset[name] * 1000
                current_color = COLORS[(index + current_index) % len(COLORS)]
                curve_label = f"{label}: {description}" if name != "current" else label
                x, xlabel = x_values(dataset, len(current), experiment.format)
                if numeric_plot(axes[1], x, current, color=current_color, label=curve_label):
                    axes[1].set(xlabel=xlabel, ylabel="Current (mA)", title="Recorded current")
                    used.add(1)
                if voltage is not None and numeric_plot(axes[2], voltage, current, color=current_color, label=curve_label):
                    axes[2].set(xlabel="Voltage (V)", ylabel="Current (mA)", title="Current versus potential")
                    used.add(2)
                if name != "current":
                    legends.update((1, 2))
            for name, description, counter_color in (("charge_capacity", "Charge", COLORS[2]),
                                                       ("discharge_capacity", "Discharge", COLORS[1])):
                if name in dataset:
                    values = dataset[name] * 1000
                    x, xlabel = x_values(dataset, len(values), experiment.format)
                    if numeric_plot(axes[3], x, values, color=counter_color, label=description):
                        axes[3].set(xlabel=xlabel, ylabel="Capacity (mAh)", title="Recorded capacity counters")
                        used.add(3)
                        legends.add(3)
    for index, axis in enumerate(axes):
        if index in used:
            axis.grid(alpha=.2)
            axis.spines[["top", "right"]].set_visible(False)
            axis.ticklabel_format(style="sci", axis="both", scilimits=(-4, 5)) if not eis else None
            if index in legends or len(datasets) > 1:
                axis.legend(fontsize=7, loc="best")
        else:
            axis.set_axis_off()
            axis.text(.5, .5, "Signal not recorded", ha="center", va="center", color="#7b8490", transform=axis.transAxes)
    fig.suptitle(relative.name, fontsize=11, fontweight="bold")
    rows = len(experiment[0])
    fig.supxlabel(f"{rows:,} primary records • {len(experiment)} dataset(s) • recorded values; gaps remain missing", fontsize=8, color="#536171")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=130)
    plt.close(fig)
    if not used:
        raise ValueError("No plottable recorded signals")


def render_overview(results):
    selected = [r for r in results if r["status"] == "plotted"]
    rows = (len(selected) + 2) // 3
    fig, axes = plt.subplots(rows, 3, figsize=(14, rows * 4), layout="constrained", squeeze=False)
    for axis, entry in zip(axes.ravel(), selected):
        file_format, filename = entry["format"], entry["file"]
        experiment = read(DATA / entry["file"])
        dataset = experiment[0]
        real, negative_imag, _, _ = impedance(dataset)
        if real is not None and negative_imag is not None and "frequency" in dataset:
            numeric_plot(axis, real, negative_imag, color=COLORS[0], scatter=True)
            axis.set(xlabel="Re(Z) (Ω)", ylabel="−Im(Z) (Ω)")
        elif file_format == "mpr" and "voltage" in dataset and "current" in dataset:
            numeric_plot(axis, dataset["voltage"], dataset["current"] * 1000, color=COLORS[0])
            axis.set(xlabel="Voltage (V)", ylabel="Current (mA)")
        else:
            values = numeric(dataset["voltage"])
            x, xlabel = x_values(dataset, len(values), file_format)
            numeric_plot(axis, x, values, color=COLORS[0])
            axis.set(xlabel=xlabel, ylabel="Voltage (V)")
        axis.set_title(f".{file_format.upper()} • {filename}", fontsize=8, loc="left", pad=10)
        axis.grid(alpha=.2)
        axis.spines[["top", "right"]].set_visible(False)
    for axis in axes.ravel()[len(selected):]:
        axis.set_axis_off()
    fig.suptitle("Electrochemical examples • one file per format", fontsize=20, fontweight="bold")
    fig.supxlabel("Recorded values • full plots and raw files in index.html", fontsize=11)
    fig.savefig(PLOTS / "overview.png", dpi=140)
    plt.close(fig)


def gallery(results):
    cards = []
    for entry in results:
        relative = entry["file"]
        filename = html.escape(Path(relative).name)
        source = html.escape(entry.get("source", relative))
        raw_url = quote("data/" + relative)
        if entry["status"] == "plotted":
            plot_url = quote(entry["plot"])
            preview = f'<a href="{plot_url}"><img loading="lazy" src="{plot_url}" alt="Plots for {filename}"></a>'
            details = f'{entry["primary_records"]:,} primary records · {entry["datasets"]} datasets'
        else:
            preview = f'<div class="error">{html.escape(entry["error"])}</div>'
            details = "No measurement records to plot"
        cards.append(f'<article data-search="{source.lower()}" data-format="{entry["format"]}"><h2>{filename}</h2>'
                     f'<p class="path">{source}</p>{preview}<footer><span>{details}</span><a href="{raw_url}">Raw file ↗</a></footer></article>')
    counts = Counter(r["format"] for r in results)
    options = ''.join(f'<option value="{key}">.{key} ({value})</option>' for key, value in sorted(counts.items()))
    document = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Electrochemical example plots</title><style>
*{box-sizing:border-box}body{margin:0;background:#f1f5f8;color:#162b40;font:15px system-ui,sans-serif}main{max-width:1500px;margin:auto;padding:38px 24px}
h1{font-size:36px;margin:0 0 10px}header p{color:#536171;line-height:1.7}a{color:#176b91}nav{display:flex;gap:12px;margin:24px 0;flex-wrap:wrap}
input,select{padding:12px;border:1px solid #c9d5df;border-radius:8px;background:white;font:inherit}input{flex:1;min-width:240px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,580px),1fr));gap:24px}article{padding:18px;background:white;border:1px solid #dce5ec;border-radius:12px;min-width:0}
h2{font-size:17px;margin:0;overflow-wrap:anywhere}.path{font-size:11px;color:#687888;overflow-wrap:anywhere}img{width:100%;height:auto;display:block}
footer{display:flex;justify-content:space-between;gap:12px;margin-top:16px;font-size:12px;color:#536171}.error{padding:50px 20px;background:#fff5e8;color:#8b4b09;border-radius:8px}
[hidden]{display:none!important}</style><main><header><h1>Electrochemical example plots</h1>
<p>One example file per available format. Click a plot for full resolution or download its raw file.<br>
Missing signals are marked; recorded gaps remain missing. Neware plots use record indices because recorded step time can reset.
No .nox file was found in the source checkout.</p>
<p><a href="plots/overview.png">Six-format overview</a> · <a href="manifest.json">File hashes and provenance</a> · <a href="results.json">Read results</a></p></header>
<nav><input id="search" aria-label="Filter files" placeholder="Find a file or source folder…"><select id="format" aria-label="Filter by format"><option value="">All formats</option>OPTIONS</select></nav>
<div class="grid">CARDS</div></main><script>
const search=document.querySelector('#search'), format=document.querySelector('#format');
function filter(){document.querySelectorAll('article').forEach(card=>{card.hidden=!card.dataset.search.includes(search.value.toLowerCase())||(format.value&&card.dataset.format!==format.value)})}
search.addEventListener('input',filter);format.addEventListener('change',filter);
</script></html>'''.replace("OPTIONS", options).replace("CARDS", "\n".join(cards))
    (ROOT / "index.html").write_text(document)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect-from", type=Path, help="Copy native examples from this checkout before plotting")
    args = parser.parse_args()
    if args.collect_from:
        manifest = collect(args.collect_from)
    else:
        manifest = json.loads((ROOT / "manifest.json").read_text())
    PLOTS.mkdir(exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.titlesize": 10, "axes.labelsize": 9,
                         "xtick.labelsize": 8, "ytick.labelsize": 8, "agg.path.chunksize": 10000})
    results = []
    for entry in manifest:
        result = dict(entry)
        relative = Path(entry["file"])
        try:
            experiment = read(DATA / relative)
            destination = PLOTS / relative.with_name(relative.name + ".png")
            render_file(experiment, relative, destination)
            result.update(status="plotted", primary_records=len(experiment[0]), datasets=len(experiment),
                          plot=destination.relative_to(ROOT).as_posix())
            print(f'PLOTTED {relative}', flush=True)
        except (ReadError, ValueError) as exc:
            result.update(status="unreadable", error=str(exc))
            print(f'NO PLOT {relative}: {exc}', flush=True)
        results.append(result)
    (ROOT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    render_overview(results)
    gallery(results)
    print(f'Gallery: {ROOT / "index.html"}', flush=True)
    print(dict(Counter(r["status"] for r in results)), flush=True)


if __name__ == "__main__":
    main()
