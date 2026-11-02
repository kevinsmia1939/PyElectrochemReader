from io import BytesIO
from pathlib import Path
import ast
import os
import struct
import tempfile
import unittest
from zipfile import ZipFile

import numpy as np
from numpy.testing import assert_allclose, assert_array_equal

from pyelectrochemreader import Dataset, ReadError, UnsupportedFormatError, read, supported_formats
from .fixtures import mpr, nda29, nda130, ndax, ndax_legacy, res
from .nox_fixture import _minimal_nox


def load(raw, format):
    return read(BytesIO(raw), format=format)


class CoreTests(unittest.TestCase):
    def test_aliases_and_native_values(self):
        values = np.array([100., -200.])
        dataset = Dataset({"I/mA": values, "time/s": np.array([0., 1.])})
        assert_allclose(dataset["current"], [.1, -.2])
        self.assertIs(dataset["I/mA"], values)
        self.assertEqual(dataset.unit("I/mA"), "mA")
        self.assertEqual(dataset.unit("current"), "A")
        self.assertIn("current", dataset)
        self.assertNotIn("voltage", dataset)
        with self.assertRaises(KeyError):
            dataset["voltage"]
        self.assertEqual(len(dataset), 2)

    def test_structured_export(self):
        dataset = Dataset({"I/mA": np.array([100., 200.])})
        table = dataset.to_numpy(["current"])
        assert_allclose(table["current"], [.1, .2])
        table["current"][0] = 9
        self.assertEqual(dataset["I/mA"][0], 100)

    def test_unequal_signal_lengths(self):
        dataset = Dataset({"a": np.zeros(2), "b": np.zeros(3)})
        with self.assertRaisesRegex(ValueError, "different lengths"):
            dataset.to_numpy()
        self.assertEqual(len(dataset.to_numpy(["a"])), 2)
        with self.assertRaises(ValueError):
            Dataset({"matrix": np.zeros((2, 2))})

    def test_paths_case_and_stream_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.MPR"
            path.write_bytes(mpr())
            result = read(path)
            self.assertEqual(result.source, str(path))
            self.assertEqual(result.format, "mpr")
            self.assertEqual(len(list(result)), 1)
            with path.open("rb") as stream:
                self.assertEqual(read(stream)[0]["time"][1], 1)
                self.assertFalse(stream.closed)

    def test_invalid_inputs(self):
        with self.assertRaises(UnsupportedFormatError):
            read(BytesIO(b"a"))
        with self.assertRaises(TypeError):
            read(__import__("io").StringIO("EXPLAIN"), format="dta")
        for extension in supported_formats:
            with self.subTest(extension=extension):
                with self.assertRaises(ReadError):
                    load(b"", extension)
                with self.assertRaises(ReadError):
                    load(b"not an electrochemical file", extension)
        with self.assertRaises(FileNotFoundError):
            read("/nonexistent-measurement-987123.mpr")

    def test_numpy_is_only_external_import(self):
        import sys
        root = Path(__file__).resolve().parents[1] / "src" / "pyelectrochemreader"
        for path in root.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    modules = [n.name.split(".")[0] for n in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    modules = [(node.module or "").split(".")[0]]
                else:
                    continue
                for module in modules:
                    self.assertIn(module, sys.stdlib_module_names | {"numpy"}, f"{path.name}: {module}")


class TextTests(unittest.TestCase):
    def test_mpt_decimal_commas_and_single_row(self):
        raw = b"EC-Lab ASCII FILE\nNb header lines : 3\ntime/s\t<I>/mA\tEwe/V\n1,5\t200\t3,2\n"
        dataset = load(raw, "mpt")[0]
        assert_allclose(dataset["time"], [1.5])
        assert_allclose(dataset["current"], [.2])
        assert_allclose(dataset["voltage"], [3.2])

    def test_mpt_non_ascii_unknown_columns_and_empty_cells(self):
        text = "BT-Lab ASCII FILE\r\nNb header lines : 3\r\nTemperature/°C\tnew column\tnew column\r\n25\t7\t\r\n"
        dataset = load(text.encode("cp1252"), "mpt")[0]
        self.assertEqual(dataset.unit("Temperature/°C"), "°C")
        self.assertEqual(dataset["new column"][0], 7)
        self.assertTrue(np.isnan(dataset["new column 2"][0]))

    def test_mpt_header_length_and_ragged_rows(self):
        for raw in [b"EC-Lab ASCII FILE\nmissing\n", b"EC-Lab ASCII FILE\nNb header lines : 7\n",
                    b"EC-Lab ASCII FILE\nNb header lines : 3\na\tb\n1\t2\t3\n"]:
            with self.subTest(raw=raw), self.assertRaises(ReadError):
                load(raw, "mpt")

    def test_gamry_multiple_curves_and_text_flags(self):
        raw = b"EXPLAIN\nTAG\tCV\nCURVE1\tTABLE\t99\n\tPt\tT\tVf\tIm\tOver\n\t#\ts\tV\tA\t#\n\t0\t1\t3.1\t0.2\tOVER\nCURVE2\tTABLE\n\tPt\tT\tVf\tIm\n\t#\ts\tV\tA\n\t0\t2\t3.2\t-0.3\nEXPERIMENTABORTED\tTOGGLE\tT\n"
        experiment = load(raw, "DTA")
        self.assertEqual(len(experiment), 2)
        self.assertEqual(experiment[0].name, "CURVE1")
        self.assertEqual(experiment.metadata["TAG"], "CV")
        self.assertEqual(experiment[0]["Over"][0], "OVER")
        assert_allclose(experiment[1]["current"], [-.3])

    def test_gamry_vfp600(self):
        dataset = load(b"VFP600\nVFPCURVE\tTABLE\n\tVoltage\tCurrent\n\tV\tA\n\t.1\t.2\n\t-0.1\t-.2\n", "dta")[0]
        assert_allclose(dataset["voltage"], [.1, -.1])
        assert_allclose(dataset["current"], [.2, -.2])
        self.assertNotIn("time", dataset)


class BioLogicTests(unittest.TestCase):
    def test_module_versions(self):
        for version, new_header in [(0, False), (0, True), (2, False), (3, False), (3, True)]:
            with self.subTest(version=version, new_header=new_header):
                dataset = load(mpr(version, new_header), "mpr")[0]
                assert_allclose(dataset["time"], [0, 1])
                assert_allclose(dataset["current"], [.1, -.2])
                assert_array_equal(dataset["ox/red"], [True, False])

    def test_unknown_columns_are_not_guessed(self):
        with self.assertRaisesRegex(UnsupportedFormatError, "column ID 999"):
            load(mpr(column_id=999), "mpr")

    def test_truncated_module(self):
        for count in (1, 30, 70, 150):
            with self.subTest(count=count), self.assertRaises(ReadError):
                load(mpr()[:-count], "mpr")


class NovaTests(unittest.TestCase):
    def test_nrbf_signals_and_metadata(self):
        experiment = load(_minimal_nox(), "nox")
        dataset = experiment[0]
        self.assertEqual(experiment.metadata["name"], "test")
        self.assertEqual(experiment.metadata["instrument"], "AUT123")
        assert_allclose(dataset["time"], [0, 1, 2])
        assert_allclose(dataset["voltage"], [3, 3.1, 3.2])
        assert_allclose(dataset["current"], [.1, .2, .3])

    def test_consecutive_graphs(self):
        experiment = load(_minimal_nox() * 2, "nox")
        self.assertEqual(experiment.metadata["stream_count"], 2)
        self.assertEqual(len(experiment), 1)

    def test_truncated_graph(self):
        for count in (1, 10, 80):
            with self.subTest(count=count), self.assertRaises(ReadError):
                load(_minimal_nox()[:-count], "nox")


class NewareTests(unittest.TestCase):
    def test_nda29_scales_counters_dates_and_order(self):
        dataset = load(nda29(), "nda")[0]
        assert_array_equal(dataset["Index"], [1, 2])
        assert_array_equal(dataset["Step"], [7, 7])
        assert_array_equal(dataset["Cycle"], [3, 3])
        assert_allclose(dataset["voltage"], [3.1, 3.2])
        assert_allclose(dataset["current"], [.1, -.1])
        assert_allclose(dataset["charge_capacity"], [.001, .001])
        self.assertEqual(dataset["Timestamp"][0], np.datetime64("2025-06-07T08:09:10"))

    def test_nda130_variants(self):
        for bts91 in (True, False):
            with self.subTest(bts91=bts91):
                dataset = load(nda130(bts91), "nda")[0]
                assert_allclose(dataset["current"], [.1, .1])
                assert_array_equal(dataset["Step"], [7, 7])
                self.assertNotIn("Cycle", dataset)
                if bts91:
                    assert_allclose(dataset["time"], [1.5, 2.5])
                    assert_allclose(dataset["Discharge_Capacity(mAh)"], [1, 1])
                    assert_allclose(dataset["T1"], [25, 25])
                else:
                    assert_allclose(dataset["time"], [1, 2])
                    assert_allclose(dataset["Charge_Capacity(mAh)"], [1, 1])

    def test_single_nda130_record(self):
        dataset = load(nda130()[:1080], "nda")[0]
        self.assertEqual(len(dataset), 1)
        self.assertEqual(dataset["T1"][0], 25)
        self.assertEqual(dataset.unit("T1"), "°C")

    def test_ndax_sparse_records_remain_raw(self):
        for version in (11, 14):
            with self.subTest(version=version):
                experiment = load(ndax(version), "ndax")
                dataset = experiment[0]
                self.assertEqual(len(dataset), 3)
                assert_allclose(dataset["voltage"], [3, 0, 3.2], rtol=1e-6)
                assert_allclose(dataset["current"], [.1, .2, -.3], rtol=1e-6)
                assert_allclose(dataset["time"], [0, np.nan, 2])
                self.assertTrue(np.isnat(dataset["Timestamp"][1]))
                # The two recorded observations at index 1 are both retained.
                assert_array_equal(experiment[1]["Index"], [1, 1, 3])
                assert_allclose(experiment[1]["Time"], [0, .1, 2])
                assert_array_equal(experiment[2]["Step_Index"], [7, 8])

    def test_legacy_ndax_versions(self):
        for version in (2, 5):
            with self.subTest(version=version):
                dataset = load(ndax_legacy(version), "ndax")[0]
                assert_allclose(dataset["voltage"], [3.1, 3.1])
                assert_allclose(dataset["current"], [.1, .1])
                assert_allclose(dataset["charge_capacity"], [.001, .001])

    def test_unsupported_nda_variant(self):
        raw = bytearray(nda29())
        raw[14] = 42
        with self.assertRaisesRegex(UnsupportedFormatError, "version 42"):
            load(bytes(raw), "nda")

    def test_truncated_nda_records(self):
        for raw in (nda29(), nda130(), nda130(False)):
            with self.subTest(size=len(raw)), self.assertRaisesRegex(ReadError, "Truncated NDA"):
                load(raw[:-1], "nda")

    def test_invalid_neware_range(self):
        raw = bytearray(nda29())
        struct.pack_into("<i", raw, 1024 + 78, 12345)
        with self.assertRaisesRegex(UnsupportedFormatError, "range 12345"):
            load(bytes(raw), "nda")

    def test_zip_paths_are_never_extracted(self):
        stream = BytesIO(ndax())
        with ZipFile(stream, "a") as archive:
            archive.writestr("../../outside.txt", "untrusted archive member")
        self.assertEqual(len(load(stream.getvalue(), "ndax")[0]), 3)


class ArbinTests(unittest.TestCase):
    def test_jet_versions_without_external_tools(self):
        for jet3 in (False, True):
            with self.subTest(jet3=jet3):
                experiment = load(res(jet3), "res")
                dataset = experiment[0]
                assert_array_equal(dataset["Data_Point"], [1, 2])
                assert_allclose(dataset["voltage"], [3.1, 3.2])
                assert_allclose(dataset["current"], [.1, -.2])
                assert_allclose(dataset["time"], [1, 2])
                self.assertEqual(dataset.unit("current"), "A")

    def test_null_measurements_remain_missing(self):
        dataset = load(res(null_current=True), "res")[0]
        self.assertTrue(np.isnan(dataset["current"]).all())

    def test_truncated_page(self):
        with self.assertRaisesRegex(ReadError, "Truncated"):
            load(res()[:-1], "res")

    def test_unsupported_database_version(self):
        raw = bytearray(res())
        raw[20] = 99
        with self.assertRaises(UnsupportedFormatError):
            load(bytes(raw), "res")

    def test_relocated_database_row(self):
        raw = bytearray(res())
        size = 4096
        pointer = struct.unpack_from("<H", raw, 5 * size + 14)[0]
        original = bytes(raw[5 * size + pointer:6 * size])
        struct.pack_into("<H", raw, 5 * size + 14, pointer | 0x8000)
        struct.pack_into("<I", raw, 5 * size + pointer, 6 << 8)
        page = bytearray(size)
        page[0] = 1
        struct.pack_into("<HH", page, 12, 1, size - len(original))
        page[size - len(original):] = original
        raw.extend(page)
        dataset = load(bytes(raw), "res")[0]
        assert_array_equal(dataset["Data_Point"], [1, 2])
        assert_allclose(dataset["current"], [.1, -.2])


@unittest.skipUnless(os.environ.get("ECHEM_REFERENCE"), "Set ECHEM_REFERENCE to the echem-data-viewer checkout")
class ReferenceCorpusTests(unittest.TestCase):
    def test_reference_files(self):
        root = Path(os.environ["ECHEM_REFERENCE"])
        files = []
        for directory in ("galvani/tests/testdata", "gamry-parser/tests", "NewareNDA/tests/nda"):
            files.extend(p for p in (root / directory).rglob("*") if p.suffix.lower() in supported_formats)
        self.assertGreater(len(files), 40)
        for path in files:
            with self.subTest(path=str(path.relative_to(root))):
                if path.name == "cv_data_incompleteheader.dta":
                    with self.assertRaises(ReadError):
                        read(path)
                    continue
                experiment = read(path)
                self.assertGreater(len(experiment[0]), 0)
                for dataset in experiment:
                    self.assertTrue(all(array.ndim == 1 for array in dataset.columns.values()))


if __name__ == "__main__":
    unittest.main()
