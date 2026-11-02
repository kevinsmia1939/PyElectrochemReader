"""Regression tests for text tables and dependency-free spreadsheet readers."""

import csv
from io import BytesIO
import os
from pathlib import Path
import re
import unittest
from xml.sax.saxutils import escape
from zipfile import ZipFile

import numpy as np
from numpy.testing import assert_allclose, assert_array_equal

from pyelectrochemreader import ReadError, UnsupportedFormatError, read


def load(raw, file_format, **kwargs):
    return read(BytesIO(raw), format=file_format, **kwargs)


def xlsx(sheets, *, shared=(), styles=""):
    result = BytesIO()
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with ZipFile(result, "w") as archive:
        definitions, relationships = [], []
        for i, (name, xml) in enumerate(sheets, 1):
            definitions.append(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>')
            relationships.append(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>')
            archive.writestr(f"xl/worksheets/sheet{i}.xml", f'<worksheet xmlns="{namespace}"><sheetData>{xml}</sheetData></worksheet>')
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{namespace}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{"".join(definitions)}</sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", f'<Relationships>{"".join(relationships)}</Relationships>')
        if shared:
            archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{namespace}">'+"".join(f'<si><t>{escape(s)}</t></si>' for s in shared)+'</sst>')
        if styles:
            archive.writestr("xl/styles.xml", styles)
    return result.getvalue()


def ods(content):
    result = BytesIO()
    with ZipFile(result, "w") as archive:
        archive.writestr("content.xml", '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:spreadsheet>'+content+'</office:spreadsheet></office:body></office:document-content>')
    return result.getvalue()


class VersaStudioTests(unittest.TestCase):
    def test_all_columns_and_multiple_segments(self):
        raw = b'''<Application>
Name=VersaStudio
</Application>
Scan Rate (V/s)=3e-3
<Segment1>
Type=2
Version=3
Definition=Segment #,Point #,E(V),I(A),Elapsed Time(s),Status,0
0,0,.1,.002,0,3
0,1,.2,-.003,1,4
</Segment1>
<Segment2>
Definition=E(V),I(A),Elapsed Time(s)
.3,.004,2
</Segment2>
'''
        experiment = load(raw, "par")
        self.assertEqual(len(experiment), 2)
        self.assertEqual(experiment.metadata["scan_rate"], .003)
        self.assertEqual(experiment[0].name, "Segment1")
        self.assertEqual(experiment[0].column_names, ("Segment #", "Point #", "E(V)", "I(A)", "Elapsed Time(s)", "Status"))
        assert_allclose(experiment[0]["current"], [.002, -.003])
        assert_array_equal(experiment[0]["Status"], [3, 4])
        assert_allclose(experiment[1]["voltage"], [.3])
        self.assertEqual(experiment[0].unit("time"), "s")

    def test_bad_segments(self):
        for raw in (b'<Segment1>\nDefinition=E(V),I(A)\n.1,.2\n',
                    b'<Segment1>\nVersion=3\n</Segment1>',
                    b'<Segment1>\nDefinition=E(V),I(A)\n.1,.2,.3\n</Segment1>',
                    b'<Segment1>\nDefinition=E(V),I(A)\n.1,bad\n</Segment1>'):
            with self.subTest(raw=raw), self.assertRaises(ReadError):
                load(raw, "par")


class CorrWareTests(unittest.TestCase):
    def test_density_is_not_absolute_current(self):
        raw = b'CORRW ASCII\nScan Rate: 20\nData Points: 100\n E(Volts)\tI(A/cm2)\tT(Seconds)\nEnd Comments\n0,1\t-0,002\t0\n0,2\t0,003\t1\n'
        experiment = load(raw, "cor")
        dataset = experiment[0]
        assert_allclose(dataset["voltage"], [.1, .2])
        assert_allclose(dataset["current_density"], [-.002, .003])
        self.assertNotIn("current", dataset)
        self.assertEqual(dataset.unit("current_density"), "A/cm²")
        self.assertEqual(dataset.unit("I(A/cm2)"), "A/cm2")
        self.assertEqual(experiment.metadata["scan_rate"], .02)
        self.assertEqual(experiment.metadata["declared_points"], 100)
        self.assertEqual(len(dataset), 2)

    def test_absolute_current_and_invalid_rows(self):
        raw = b'CORRW ASCII\nE(Volts)\tI(A)\nEnd Comments\n.1\t.2\n'
        assert_allclose(load(raw, "cor")[0]["current"], [.2])
        with self.assertRaises(ReadError):
            load(raw.replace(b'.1\t.2', b'.1\t.2\t.3'), "cor")
        with self.assertRaises(ReadError):
            load(raw.replace(b'End Comments', b'Comments'), "cor")


class DelimitedTests(unittest.TestCase):
    def test_headerless_first_row_is_preserved(self):
        for file_format, raw in (("csv", b'.1,.002\n.2,-.003\n'),
                                 ("txt", b'.1\t.002\t\n.2\t-.003\t\n'),
                                 ("tsv", b'.1\t.002\n.2\t-.003\n'),
                                 ("txt", b'.1 .002\n.2 -.003\n')):
            with self.subTest(format=file_format):
                dataset = load(raw, file_format)[0]
                assert_allclose(dataset["voltage"], [.1, .2])
                assert_allclose(dataset["current"], [.002, -.003])
                self.assertEqual(dataset.column_names, ("Voltage", "Current"))

    def test_named_columns_units_quotes_and_missing_values(self):
        raw = b'"Potential (mV)","Current (uA)","Notes, instrument"\n100,20,"first, point"\n200,,second\n'
        dataset = load(raw, "csv")[0]
        assert_allclose(dataset["voltage"], [.1, .2])
        assert_allclose(dataset["current"], [20e-6, np.nan])
        assert_allclose(dataset["Potential (mV)"], [100, 200])
        assert_array_equal(dataset["Notes, instrument"], ["first, point", "second"])
        self.assertEqual(dataset.unit("Current (uA)"), "uA")
        self.assertEqual(dataset.unit("current"), "A")

    def test_semicolon_decimal_commas_bom_and_encoding(self):
        raw = '\ufeffPotential/V;Current/mA\n0,1;2,5\n0,2;-3,5\n'.encode()
        dataset = load(raw, "csv")[0]
        assert_allclose(dataset["voltage"], [.1, .2])
        assert_allclose(dataset["current"], [.0025, -.0035])
        dataset = load('E(V)\tI(µA)\n.1\t2\n'.encode('cp1252'), "txt")[0]
        assert_allclose(dataset["current"], [2e-6])

    def test_delimiter_and_header_overrides(self):
        dataset = load(b'0|1\n2|3\n', "txt", delimiter='|', header=True)[0]
        self.assertEqual(dataset.column_names, ('0', '1'))
        assert_allclose(dataset['0'], [2])
        dataset = load(b'0|1\n2|3\n', "txt", delimiter='|', header=False)[0]
        assert_allclose(dataset['voltage'], [0, 2])
        with self.assertRaises(TypeError):
            load(b'hello', "par", delimiter='|')
        with self.assertRaises(TypeError):
            load(b'1,2', "csv", header="yes")

    def test_mixed_rows_are_not_lost_as_headers(self):
        dataset = load(b'first.par,VersaSTAT,3\nsecond.par,VersaSTAT,5\n', "txt")[0]
        self.assertEqual(len(dataset), 2)
        assert_array_equal(dataset['column_1'], ['first.par', 'second.par'])
        self.assertNotIn('voltage', dataset)

    def test_duplicate_headers_and_time_durations(self):
        dataset = load(b'TestTime,Voltage/V,Current/mA,extra,extra\n0,3,100,1,2\n1-00:00:17,4,-200,3,4\n', "csv")[0]
        assert_allclose(dataset['time'], [0, 86417])
        assert_allclose(dataset['current'], [.1, -.2])
        assert_array_equal(dataset['extra 2'], [2, 4])

    def test_ec_lab_ascii_named_txt(self):
        dataset = load(b'EC-Lab ASCII FILE\nNb header lines : 3\ntime/s\tEwe/V\tI/mA\n0\t3\t100\n', "txt")[0]
        assert_allclose(dataset['current'], [.1])

    def test_invalid_numeric_and_extra_columns(self):
        for raw in (b'Potential/V,Current/A\n1,bad\n', b'Potential/V,Current/A\n1,2,3\n'):
            with self.subTest(raw=raw), self.assertRaises(ReadError):
                load(raw, "csv")


class SpreadsheetTests(unittest.TestCase):
    def test_shared_strings_cached_formulas_and_missing_cells(self):
        raw = xlsx([('CV', '''<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="inlineStr"><is><t>Note</t></is></c></row>
<row r="2"><c r="A2"><v>.1</v></c><c r="B2"><f>1+1</f><v>2</v></c><c r="C2" t="inlineStr"><is><t>recorded</t></is></c></row>
<row r="3"><c r="A3"><v>.2</v></c><c r="B3"><f>1+2</f></c></row>''')], shared=('WE(1).Potential (V)', 'WE(1).Current (A)'))
        experiment = load(raw, "xlsx")
        self.assertEqual(experiment[0].name, 'CV')
        self.assertEqual(experiment.metadata['formula_policy'], 'cached values only')
        assert_allclose(experiment[0]['voltage'], [.1, .2])
        assert_allclose(experiment[0]['current'], [2, np.nan])
        assert_array_equal(experiment[0]['Note'], ['recorded', ''])

    def test_sheets_and_misnamed_xls_ooxml(self):
        raw = xlsx([('first', '<row><c><v>1</v></c><c><v>2</v></c></row>'),
                    ('second', '<row><c><v>3</v></c><c><v>4</v></c></row>')])
        experiment = load(raw, "xls")
        self.assertEqual(experiment.format, 'xls')
        self.assertEqual(experiment.metadata['container'], 'xlsx')
        self.assertEqual([d.name for d in experiment], ['first', 'second'])
        assert_allclose(experiment[1]['current'], [4])
        with self.assertRaisesRegex(UnsupportedFormatError, 'BIFF'):
            load(b'\xd0\xcf\x11\xe0' + bytes(100), "xls")

    def test_repeated_battery_tables_and_time_formats(self):
        raw = xlsx([('battery', '''<row><c t="s"><v>0</v></c><c t="s"><v>1</v></c><c t="s"><v>2</v></c></row>
<row><c><v>1</v></c><c s="1"><v>0.5</v></c><c><v>2</v></c></row>
<row><c t="s"><v>0</v></c><c t="s"><v>3</v></c><c t="s"><v>4</v></c></row>
<row><c><v>1</v></c><c s="1"><v>0.0006944444444444445</v></c><c><v>3</v></c></row>
<row><c t="s"><v>0</v></c><c t="s"><v>1</v></c><c t="s"><v>2</v></c></row>
<row><c><v>2</v></c><c t="s"><v>5</v></c><c><v>4</v></c></row>''')],
            shared=('Voltage/V', 'TestTime', 'Current/mA', 'Mode', 'Period', '1-00:00:17'),
            styles='<styleSheet><cellXfs><xf numFmtId="0"/><xf numFmtId="21"/></cellXfs></styleSheet>')
        experiment = load(raw, "xlsx")
        self.assertEqual(len(experiment), 2)
        assert_allclose(experiment[0]['time'], [43200])
        assert_allclose(experiment[1]['time'], [86417])

    def test_invalid_spreadsheet_cells(self):
        for cell in ('<c r="A1" t="s"><v>-1</v></c>', '<c r="A1" t="e"><v>#DIV/0!</v></c>', '<c r="bad"><v>1</v></c>'):
            with self.subTest(cell=cell), self.assertRaises(ReadError):
                load(xlsx([('sheet', '<row>'+cell+'</row>')], shared=('header',)), "xlsx")

    def test_ods_repeats_time_and_empty_padding(self):
        raw = ods('''<table:table table:name="Data">
<table:table-row><table:table-cell office:value-type="string"><text:p>Time/s</text:p></table:table-cell><table:table-cell office:value-type="string"><text:p>Voltage/V</text:p></table:table-cell><table:table-cell office:value-type="string"><text:p>Current/mA</text:p></table:table-cell></table:table-row>
<table:table-row table:number-rows-repeated="2"><table:table-cell office:value-type="time" office:time-value="PT1H2M3S"/><table:table-cell office:value-type="float" office:value="3"/><table:table-cell office:value-type="float" office:value="100"/><table:table-cell table:number-columns-repeated="16381"/></table:table-row>
<table:table-row table:number-rows-repeated="1000000"><table:table-cell table:number-columns-repeated="16384"/></table:table-row>
</table:table>''')
        dataset = load(raw, 'ods')[0]
        self.assertEqual(len(dataset), 2)
        self.assertEqual(len(dataset.columns), 3)
        assert_allclose(dataset['time'], [3723, 3723])
        assert_allclose(dataset['current'], [.1, .1])


@unittest.skipUnless(os.environ.get('PYSIMPLEECHEM_REFERENCE'), 'Set PYSIMPLEECHEM_REFERENCE to the PySimpleEChem checkout')
class PySimpleEChemReferenceTests(unittest.TestCase):
    def test_native_par_cor_and_plain_cv_values(self):
        root = Path(os.environ['PYSIMPLEECHEM_REFERENCE']) / 'example_data'
        for path in sorted(root.iterdir()):
            if path.suffix not in ('.par', '.cor') and path.name not in ('example_CV.csv', 'example_CV.txt'):
                continue
            with self.subTest(file=path.name):
                experiment = read(path)
                dataset = experiment[0]
                text = path.read_text()
                if path.suffix == '.par':
                    block = re.search(r'Definition=Segment[^\n]*\n(.*?)</Segment\d+>', text, re.S)[1]
                    rows = list(csv.reader(block.strip().splitlines()))
                    expected = np.array(rows, dtype=float)
                    assert_allclose(dataset['voltage'], expected[:, 2])
                    assert_allclose(dataset['current'], expected[:, 3])
                    assert_allclose(dataset['time'], expected[:, 4])
                    self.assertEqual(len(dataset.columns), expected.shape[1])
                else:
                    body = text.split('End Comments\n', 1)[1] if path.suffix == '.cor' else text
                    separator = ',' if path.suffix == '.csv' else '\t'
                    expected = np.loadtxt(BytesIO(body.encode()), delimiter=separator, usecols=(0, 1), ndmin=2)
                    assert_allclose(dataset['voltage'], expected[:, 0])
                    key = 'current_density' if path.suffix == '.cor' else 'current'
                    assert_allclose(dataset[key], expected[:, 1])

    def test_battery_and_ods_exports(self):
        root = Path(os.environ['PYSIMPLEECHEM_REFERENCE']) / 'example_data'
        battery = read(root / 'example_battery.xls')
        self.assertEqual(battery.metadata['container'], 'xlsx')
        self.assertEqual(len(battery), 84)
        self.assertEqual(sum(len(d) for d in battery), 1620)
        assert_allclose(battery[0]['voltage'][:3], [1.2946, 1.348, 1.3566])
        assert_allclose(battery[0]['current'][:3], [.6497, .6497, .6499])
        for path in root.glob('*.ods'):
            with self.subTest(file=path.name):
                self.assertGreater(len(read(path)[0]), 0)


if __name__ == '__main__':
    unittest.main()
