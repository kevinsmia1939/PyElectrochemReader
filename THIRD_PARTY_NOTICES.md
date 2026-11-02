# Source acknowledgments

This package was developed against the local `echem-data-viewer` reference
checkout. It does not import that application or its bundled packages.

* **Galvani**, Christopher Kerr and contributors, GPL-3.0-or-later:
  `_biologic_columns.py` retains its column layout maps. `_nrbf.py` retains the
  data-only NOVA serialization decoder; `nova.py` adapts its signal extraction
  helpers. The synthetic NOX test fixture is adapted from its tests.
  [Reference fork](https://github.com/kevinsmia1939/galvani).
* **NewareNDA**, copyright (c) 2022–2024 SES AI Corporation; author Daniel
  Cogswell; BSD-3-Clause: `_neware_columns.py` retains scale and state tables.
  `neware.py` rewrites its NDA/NDC layouts with NumPy batch decoding and standard
  library ZIP/XML support. Its full notice is included in
  `src/pyelectrochemreader/licenses/NewareNDA.txt`.
  [Project](https://github.com/Solid-Energy-Systems/NewareNDA).
* **MDBTools**, copyright (C) 2000 Brian Bruns and contributors,
  LGPL-2.0-or-later: the page, table, row, and compressed Unicode layouts in
  `arbin.py` follow its library source. This is a Python rewrite; no MDBTools
  binaries or shared libraries are required or distributed. Its library license
  is included in `src/pyelectrochemreader/licenses/MDBTools-LGPL-2.0.txt`.
  [Project](https://github.com/mdbtools/mdbtools).

The reference application's Gamry fixtures were used for validation. The new
text-table reader does not incorporate gamry-parser code.

Rewritten and adapted files are marked with the modification date 2026-11-02.
The complete package is distributed under GPL-3.0-or-later; its license is
included both at the repository root and inside the installed package.
