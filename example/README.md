# One example per format

Bio-Logic configuration examples in `data/biologic/` produce text and tables:

```sh
PYTHONPATH=src python3 example/read_configurations.py
```

The reports are written under [text/biologic/](text/biologic/). Each includes
the decoded source text, complete metadata, CSV tables, and a browsable HTML
view. MPS tables describe sequences and settings; MGR tables describe axes,
trace styles, and other graph properties. These configuration files are not
plotted. Their companion `.mpr` remains a measurement file; `.sta` is unsupported.

| Configuration example | Text and tables |
| --- | --- |
| [example.mps](data/biologic/example.mps) | [Protocol settings](text/biologic/example.mps/index.html) |
| [example.mgr](data/biologic/example.mgr) | [Graph properties](text/biologic/example.mgr/index.html) |

Open [index.html](index.html) for the offline plot gallery, or view the
[overview](plots/overview.png). There is one raw file and one full plot for
each of the twelve formats found in the two source checkouts:

| Format | Raw file |
| --- | --- |
| Bio-Logic MPR | [CV_C01.mpr](data/CV_C01.mpr) |
| Bio-Logic MPT | [example.mpt](data/example.mpt) |
| Gamry DTA | [eispot_data.dta](data/eispot_data.dta) |
| Neware NDA | [TestFile.nda](data/TestFile.nda) |
| Neware NDAX | [123456789012_Unit27_Example_Data_File.ndax](data/123456789012_Unit27_Example_Data_File.ndax) |
| Arbin RES | [arbin1.res](data/arbin1.res) |
| VersaStudio PAR | [example_CV_1.par](data/example_CV_1.par) |
| CorrWare COR | [rde_500rpm.cor](data/rde_500rpm.cor) |
| Plain CSV | [example_CV.csv](data/example_CV.csv) |
| Tab-delimited TXT | [example_CV.txt](data/example_CV.txt) |
| Battery workbook (OOXML with XLS suffix) | [example_battery.xls](data/example_battery.xls) |
| OpenDocument spreadsheet | [export_data1_cv_results.ods](data/export_data1_cv_results.ods) |

The first six samples come from `echem-data-viewer`; the remaining six come
from `PySimpleEChem`. No `.nox`, `.xlsx`, or `.tsv` sample with that extension
was present. XLSX and TSV layouts are additionally covered by synthetic tests.

The files were copied without modification. [manifest.json](manifest.json)
records their original relative paths, sizes, and SHA-256 checksums;
[results.json](results.json) records reading and plotting results.
Source licenses and applicable attribution notices are retained under
`licenses/` and alongside the raw files.

Plots show recorded potential, current, current versus potential, and capacity
where available. Impedance data uses Nyquist and Bode plots. Missing values
remain gaps. Neware plots use record indices because step time can reset.
Supplemental NDAX run/step tables remain accessible through the reader; the
plots show the primary measurement table.
CorrWare current density is plotted in mA/cm². Generic spreadsheets without
signal/unit headers are plotted using their native column labels; no units
are invented. Battery measurement blocks remain separate datasets.

To regenerate the plots, run from the repository root:

```sh
python3 -m pip install matplotlib
PYTHONPATH=src python3 example/plot_examples.py
```

To select one example per format from another checkout:

```sh
PYTHONPATH=src python3 example/plot_examples.py \
  --collect-from /path/to/echem-data-viewer
```

Run the same command with `--collect-from /path/to/PySimpleEChem` to merge the
new formats into the collection. There remains one selected file per format.
