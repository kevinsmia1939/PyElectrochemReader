# One example per format

Open [index.html](index.html) for the offline plot gallery, or view the
[overview](plots/overview.png). There is one raw file and one full plot for
each of the six formats found in the source checkout:

| Format | Raw file |
| --- | --- |
| Bio-Logic MPR | [CV_C01.mpr](data/CV_C01.mpr) |
| Bio-Logic MPT | [example.mpt](data/example.mpt) |
| Gamry DTA | [eispot_data.dta](data/eispot_data.dta) |
| Neware NDA | [TestFile.nda](data/TestFile.nda) |
| Neware NDAX | [123456789012_Unit27_Example_Data_File.ndax](data/123456789012_Unit27_Example_Data_File.ndax) |
| Arbin RES | [arbin1.res](data/arbin1.res) |

No `.nox` example was present in `echem-data-viewer`.

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
