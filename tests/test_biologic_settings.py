"""Text and table output for Bio-Logic configuration files."""

from io import BytesIO, StringIO
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

import numpy as np
from numpy.testing import assert_allclose, assert_array_equal

from pyelectrochemreader import Configuration, Experiment, ReadError, read


def load(text, extension, encoding="utf-8", **kwargs):
    return read(BytesIO(text.encode(encoding)), format=extension, **kwargs)


def fixed(*values):
    return "".join(str(value).ljust(20) for value in values) + "\n"


class MPSTests(unittest.TestCase):
    def test_sequence_table_preserves_blanks_units_and_text(self):
        text = ("BT-LAB SETTING FILE\nNumber of linked techniques : 1\nDevice : BCS-815\n"
                "Comments : example: pulsed current\nTechnique : 1\nModulo Bat\n" +
                fixed("Ns", 0, 1, 2) + fixed("ctrl_type", "CC", "Rest", "CC") +
                fixed("ctrl1_val", "5,000", "", "-1,25e-2") + fixed("ctrl1_val_unit", "mA", "", "µA") +
                fixed("ctrl2_val_unit", "", "", "") + fixed("ctrl_trigger", "Rising Edge", "Falling Edge", "Rising Edge"))
        config = load(text, "mps")
        self.assertIsInstance(config, Configuration)
        self.assertNotIsInstance(config, Experiment)
        self.assertFalse(hasattr(config, "datasets"))
        self.assertEqual(config.text, text)
        self.assertEqual(config.metadata["settings"]["Device"], "BCS-815")
        self.assertEqual(config.metadata["settings"]["Comments"], "example: pulsed current")
        self.assertEqual(config[0].name, "Technique 1: Modulo Bat")
        assert_array_equal(config[0]["Ns"], [0, 1, 2])
        assert_allclose(config[0]["ctrl1_val"], [5, np.nan, -.0125])
        assert_array_equal(config[0]["ctrl1_val_unit"], ["mA", "", "µA"])
        assert_array_equal(config[0]["ctrl2_val_unit"], ["", "", ""])
        self.assertEqual(config[0]["ctrl_trigger"][1], "Falling Edge")
        self.assertEqual(len(config[0].to_numpy()), 3)
        output = StringIO()
        with redirect_stdout(output):
            print(config)
        self.assertEqual(output.getvalue(), text + "\n")

    def test_tabbed_settings_and_multiple_techniques(self):
        text = ("EC-LAB SETTING FILE\r\nNumber of linked techniques : 2\r\n"
                "Technique : 1\r\nCA\r\nNs\t0\t1\r\nctrl_type\tCC\tRest\r\nunit\tmA\t\r\n"
                "Technique : 2\r\nLoop\r\ngoto Ne\t1\r\nntimes\t10\r\n")
        config = load(text, "mps")
        self.assertEqual(len(config), 2)
        self.assertEqual(config.text, text)
        self.assertEqual(config.metadata["techniques"], [{"number": 1, "name": "CA"}, {"number": 2, "name": "Loop"}])
        assert_array_equal(config[0]["unit"], ["mA", ""])
        assert_array_equal(config[1]["Parameter"], ["goto Ne", "ntimes"])
        assert_array_equal(config[1]["Value"], ["1", "10"])
        display = config.to_text()
        self.assertIn("Technique 2: Loop\n\nRow 1\n  Parameter: goto Ne\n  Value: 1", display)
        self.assertIn("Row 2\n  Parameter: ntimes\n  Value: 10", display)

    def test_readable_text_shows_all_sequences_and_rest_limit_fields(self):
        text = ("BT-LAB SETTING FILE\nDevice : BCS-815\nTechnique : 1\nModulo Bat\n" +
                fixed("Ns", 0, 12, 13) + fixed("ctrl_type", "CC", "Rest", "Loop") +
                fixed("ctrl1_val_unit", "mA", "", "") + fixed("lim_nb", 1, 1, 0) +
                fixed("lim1_type", "Ecell", "Time", "Time") + fixed("lim1_comp", ">", ">", ">") +
                fixed("lim1_value", ".9", "3,000", "5,000") + fixed("lim1_value_unit", "V", "mn", "s") +
                fixed("lim1_action", "Next sequence", "Next sequence", "Next sequence") +
                fixed("rec1_value", 10, 10, 10) + fixed("rec1_value_unit", "s", "s", "s"))
        config = load(text, "mps")
        with np.printoptions(threshold=1, linewidth=10):
            display = config.to_text()
        self.assertIn("Device : BCS-815", display)
        self.assertIn("Technique 1: Modulo Bat", display)
        self.assertIn("Sequence 0\n  Ns: 0\n  ctrl_type: CC", display)
        self.assertIn("Sequence 13\n  Ns: 13\n  ctrl_type: Loop", display)
        self.assertIn("Sequence 12\n"
                      "  Ns: 12\n"
                      "  ctrl_type: Rest\n"
                      "  ctrl1_val_unit: \n"
                      "  lim_nb: 1\n"
                      "  lim1_type: Time\n"
                      "  lim1_comp: >\n"
                      "  lim1_value: 3.0\n"
                      "  lim1_value_unit: mn\n"
                      "  lim1_action: Next sequence\n"
                      "  rec1_value: 10\n"
                      "  rec1_value_unit: s", display)
        self.assertNotIn("...", display)
        self.assertEqual(str(config), text)

    def test_duplicate_parameter_names_are_preserved(self):
        text = ("EC-LAB SETTING FILE\nTechnique : 1\nTest\n" + fixed("Ns", 0) +
                fixed("parameter", "first") + fixed("parameter", "second") + fixed("parameter 2", "third"))
        table = load(text, "mps")[0]
        self.assertEqual(len(table.column_names), len(set(table.column_names)))
        self.assertEqual(table.metadata["parameter_names"], ["Ns", "parameter", "parameter", "parameter 2"])
        self.assertEqual([table.columns[key][0] for key in table.column_names[1:]], ["first", "second", "third"])

    def test_metadata_only_settings_are_valid(self):
        text = "EC-LAB SETTING FILE\nNumber of linked techniques : 0\nDevice : VMP3\n"
        config = load(text, "mps")
        self.assertEqual(config.tables, ())
        self.assertEqual(len(config), 0)
        self.assertEqual(config.metadata["settings"]["Device"], "VMP3")
        self.assertEqual(config.to_text(), text)

    def test_repeated_sequence_blocks(self):
        text = ("BT-LAB SETTING FILE\nTechnique : 1\nModulo Bat\n" +
                fixed("Ns", 0, 1) + fixed("mode", "CC", "Rest") +
                fixed("Ns", 2, 3) + fixed("mode", "Rest", "CC"))
        config = load(text, "mps")
        self.assertEqual(len(config), 2)
        assert_array_equal(config[1]["Ns"], [2, 3])
        self.assertTrue(config[1].name.endswith("[2]"))

    def test_encoding_and_paths(self):
        text = "EC-LAB SETTING FILE\nElectrode surface area : 1 cm²\n"
        config = load(text, "mps", encoding="cp1252")
        self.assertEqual(config.metadata["settings"]["Electrode surface area"], "1 cm²")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.MPS"
            path.write_bytes(text.encode("cp1252"))
            self.assertEqual(read(path).source, str(path))
            with path.open("rb") as stream:
                self.assertEqual(read(stream).format, "mps")
                self.assertFalse(stream.closed)

    def test_malformed_settings(self):
        examples = ["wrong signature\n", "BT-LAB SETTING FILE\nNumber of linked techniques : 1\n",
                    "EC-LAB SETTING FILE\nTechnique : 1\n",
                    "BT-LAB SETTING FILE\nTechnique : 1\nCA\nNs\t0\tbad\nvalue\t1\t2\n",
                    "BT-LAB SETTING FILE\nTechnique : 1\nCA\nNs\t0\nvalue\t1\t2\n",
                    "BT-LAB SETTING FILE\nTechnique : 1\nCA\n" + fixed("Ns", 0) + fixed("value", 1, 2)]
        for text in examples:
            with self.subTest(text=text), self.assertRaises(ReadError):
                load(text, "mps")


class MGRTests(unittest.TestCase):
    def test_axes_styles_and_parameters_are_separate_tables(self):
        text = '''<?xml version="1.0" encoding="UTF-8"?>
<Configuration><Configuration Num="1"><Info Soft="BT-Lab" Version="179"/>
<Graph Num="1"><AxesStyle>
<AxisStyle Axis="x-axis" Title="Capacity/mA.h" Min="-15,5" Max="800.25" AxisEnabled="true"/>
<AxisStyle Axis="y1-axis" Title="Ecell/V" Min="0.2" Max="1"/>
</AxesStyle><TracesStyle><Trace Num="1" LineColor="16711680" DrawLines="true"/></TracesStyle>
<TracesParams><Trace Num="1" Variable="Ecell"/></TracesParams>
<UnknownSetting Version="next"><Note>Keep &amp; preserve</Note></UnknownSetting>
</Graph></Configuration></Configuration>'''
        config = load(text, "mgr")
        self.assertIsInstance(config, Configuration)
        self.assertEqual(str(config), text)
        tables = {table.name: table for table in config}
        axes = tables["AxesStyle/AxisStyle"]
        assert_array_equal(axes["Title"], ["Capacity/mA.h", "Ecell/V"])
        assert_allclose(axes["Min"], [-15.5, .2])
        assert_allclose(axes["Max"], [800.25, 1])
        self.assertIs(axes["AxisEnabled"][0], True)
        self.assertIsNone(axes["AxisEnabled"][1])
        self.assertEqual(tables["TracesStyle/Trace"]["LineColor"][0], 16711680)
        self.assertEqual(tables["TracesParams/Trace"]["Variable"][0], "Ecell")
        self.assertEqual(tables["UnknownSetting/Note"]["xml_text"][0], "Keep & preserve")
        self.assertEqual(config.metadata["tree"]["tag"], "Configuration")
        display = config.to_text()
        self.assertIn("AxesStyle/AxisStyle\n\nRow 1", display)
        self.assertIn("  Title: Capacity/mA.h", display)
        self.assertIn("  Title: Ecell/V", display)
        self.assertIn("  AxisEnabled: True", display)
        self.assertIn("  AxisEnabled: ", display)
        self.assertIn("  xml_text: Keep & preserve", display)

    def test_graph_paths_and_attribute_collisions(self):
        text = '''<Configuration><Graph Num="1"><AxisStyle Axis="x"/></Graph>
<Graph Num="2"><AxisStyle Axis="y" xml_path="native" xml_text="attribute">content</AxisStyle></Graph></Configuration>'''
        table = next(table for table in load(text, "mgr") if table.name == "Graph/AxisStyle")
        self.assertEqual(len(table), 2)
        path_name = table.metadata["path_column"]
        self.assertNotEqual(path_name, "xml_path")
        self.assertIn("Graph[2]", table[path_name][1])
        self.assertEqual(table["xml_path"][1], "native")
        self.assertEqual(table["xml_text"][1], "attribute")
        self.assertEqual(table["_xml_text"][1], "content")

    def test_metadata_only_and_namespaces(self):
        config = load('<Configuration xmlns="urn:biologic"/>', "mgr")
        self.assertEqual(config.tables, ())
        self.assertEqual(config.metadata["tree"]["tag"], "{urn:biologic}Configuration")
        self.assertEqual(config.to_text(), config.text)

    def test_xml_encodings_and_override(self):
        for encoding in ("utf-8", "cp1252", "utf-16", "utf-32"):
            text = f'<?xml version="1.0" encoding="{encoding}"?><Configuration><Info Name="café"/></Configuration>'
            with self.subTest(encoding=encoding):
                config = load(text, "mgr", encoding=encoding)
                self.assertEqual(config.text, text)
                self.assertEqual(config[0]["Name"][0], "café")
        wrong_declaration = '<?xml version="1.0" encoding="UTF-8"?><Configuration><Info Name="café"/></Configuration>'
        config = read(BytesIO(wrong_declaration.encode("cp1252")), format="mgr", encoding="cp1252")
        self.assertEqual(config[0]["Name"][0], "café")

    def test_malformed_xml_and_wrong_root(self):
        for text in ('<Configuration><Info></Configuration>', '<wrong/>', 'not XML',
                     '<?xml version="1.0" encoding="unknown-codec"?><Configuration/>'):
            with self.subTest(text=text), self.assertRaises(ReadError):
                load(text, "mgr")


class LocalBioLogicConfigurationTests(unittest.TestCase):
    def test_user_supplied_examples(self):
        root = Path(__file__).resolve().parents[1] / "example" / "data" / "biologic"
        mps, mgr = list(root.glob("*.mps")), list(root.glob("*.mgr"))
        if not mps or not mgr:
            self.skipTest("User-supplied BioLogic configuration examples are not available")
        settings = read(mps[0])
        self.assertEqual(settings.metadata["settings"]["Device"], "BCS-815")
        self.assertEqual(settings[0].columns["ctrl_type"][2], "CC")
        self.assertEqual(len(settings[0]), 14)
        self.assertEqual(len(settings[0].columns), 51)
        assert_array_equal(settings[0]["Ns"], np.arange(14))
        assert_array_equal(settings[0]["ctrl1_val_unit"][[2, 5, 10]], ["mA"] * 3)
        block = settings.to_text().split("\nSequence 12\n", 1)[1].split("\nSequence 13\n", 1)[0]
        fields = dict(line[2:].split(": ", 1) for line in block.splitlines() if line.startswith("  "))
        self.assertEqual(len(fields), 51)
        self.assertEqual(fields["ctrl_type"], "Rest")
        self.assertEqual(fields["lim_nb"], "1")
        self.assertEqual(fields["lim1_type"], "Time")
        self.assertEqual(fields["lim1_comp"], ">")
        self.assertEqual(fields["lim1_value"], "3.0")
        self.assertEqual(fields["lim1_value_unit"], "mn")
        self.assertEqual(fields["lim1_action"], "Next sequence")
        self.assertEqual(fields["lim1_seq"], "13")
        graph = read(mgr[0])
        tables = {table.name: table for table in graph}
        self.assertEqual(len(tables["AxesStyle/AxisStyle"]), 4)
        self.assertEqual(len(tables["TracesStyle/Trace"]), 128)
        self.assertEqual(len(tables["TracesParams/Trace"]), 128)
        source_axes = ET.parse(mgr[0]).getroot().findall(".//AxisStyle")
        assert_array_equal(tables["AxesStyle/AxisStyle"]["Title"], [node.get("Title") for node in source_axes])


if __name__ == "__main__":
    unittest.main()
