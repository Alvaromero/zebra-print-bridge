import unittest

from zebra_print_bridge import zpl

ICON = "~DGR:BOX.GRF,00004,001,\nFF00FF00\n"


class StatusFlagsTest(unittest.TestCase):
    def test_flags_hex_matches_web_decoder_positions(self):
        self.assertEqual(zpl.flags_hex(["media_out"], zpl.ERROR_FLAGS), "00000000 00000001")
        self.assertEqual(zpl.flags_hex(["head_open", "media_out"], zpl.ERROR_FLAGS), "00000000 00000005")
        self.assertEqual(zpl.flags_hex(["paused"], zpl.ERROR_FLAGS), "00000000 00010000")
        self.assertEqual(zpl.flags_hex([], zpl.ERROR_FLAGS), "00000000 00000000")

    def test_hqes_response(self):
        text = zpl.hqes_response(["ribbon_out"], ["need_calibrate"]).decode()
        self.assertIn("ERRORS:         1 00000000 00000002", text)
        self.assertIn("WARNINGS:       1 00000000 00000001", text)
        self.assertTrue(text.startswith("\x02") and "\x03" in text)


class ParsingTest(unittest.TestCase):
    def test_labels_skip_setup_blocks(self):
        data = "^XA^JSO^MNY~JC^XZ^XA^FO10,10^FDone^FS^XZ^XA^FO1,1^XGR:BOX.GRF,1,1^FS^XZ"
        self.assertEqual(len(zpl.labels(data)), 2)
        self.assertEqual(zpl.classify(data), "label")
        self.assertEqual(zpl.classify("^XA^JSO^MNY~JC^XZ"), "calibrate")
        self.assertEqual(zpl.classify("~HQES"), "status")
        self.assertEqual(zpl.classify(ICON), "graphics")
        self.assertEqual(zpl.classify("~WC"), "config")

    def test_graphics_and_references(self):
        self.assertEqual(zpl.extract_graphics(ICON), {"BOX": ICON})
        self.assertEqual(zpl.referenced_graphics("^XA^XGR:BOX.GRF,1,1^FS^XGlogo^FS^XZ"), {"BOX", "LOGO"})

    def test_label_size(self):
        self.assertEqual(zpl.label_size_inches("^XA^PW812^LL1218^FDx^XZ", 203), (4.0, 6.0))
        self.assertEqual(zpl.label_size_inches("^XA^PW600^LL300^FDx^XZ", 300), (2.0, 1.0))
        self.assertIsNone(zpl.label_size_inches("^XA^FDx^XZ", 203))

    def test_last_charset(self):
        self.assertEqual(zpl.last_charset("^XA^CI27^FDa^FS^XZ^XA^CI28^XZ"), 28)
        self.assertIsNone(zpl.last_charset("^XA^FDa^FS^XZ"))


class LintTest(unittest.TestCase):
    def codes(self, text, charset=None):
        return {w["code"]: w for w in zpl.lint(text, charset)}

    def test_ascii_only_is_clean(self):
        self.assertEqual(zpl.lint("^XA^FDHello^FS^XZ"), [])

    def test_accents_with_ci28_are_clean(self):
        self.assertEqual(zpl.lint("^XA^CI28^FO1,1^FDSão Paulo, Ñandú, Müller^FS^XZ"), [])

    def test_accents_without_ci28(self):
        found = self.codes("^XA^FDSão Paulo^FS^FDÑandú^FS^XZ")
        self.assertEqual(list(found), ["not_utf8"])
        self.assertEqual(found["not_utf8"]["chars"], ["ã", "Ñ", "ú"])
        self.assertIsNone(found["not_utf8"]["charset"])

    def test_ci28_set_by_an_earlier_job_on_the_printer(self):
        self.assertEqual(zpl.lint("^XA^FDSão^FS^XZ", charset=28), [])

    def test_other_charset_active(self):
        found = self.codes("^XA^CI27^FDcafé^FS^XZ")
        self.assertEqual(found["not_utf8"]["charset"], 27)

    def test_ci_only_applies_after_it(self):
        found = self.codes("^XA^FDcafé^FS^CI28^FDcafé^FS^XZ")
        self.assertEqual(found["not_utf8"]["chars"], ["é"])

    def test_non_latin_script_needs_a_downloaded_font(self):
        found = self.codes("^XA^CI28^FD深圳 Москва^FS^XZ")
        self.assertEqual(list(found), ["font_coverage"])
        self.assertEqual(found["font_coverage"]["chars"], ["深", "圳", "М", "о", "с", "к", "в", "а"])

    def test_non_latin_with_downloaded_font_is_clean(self):
        self.assertEqual(zpl.lint("^XA^CI28^CWJ,E:ANMDJ.TTF^AJN,40,40^FD深圳^FS^XZ"), [])

    def test_euro_and_typographic_quotes_are_latin(self):
        self.assertEqual(zpl.lint("^XA^CI28^FD€ 10 – “ok”^FS^XZ"), [])

    def test_non_utf8_encoding_skips_ci28_check(self):
        self.assertEqual(zpl.lint("^XA^FDcafé^FS^XZ", encoding="cp1252"), [])


class PrinterResponsesTest(unittest.TestCase):
    def test_parse_hqes_decodes_bitwise(self):
        text = zpl.hqes_response(["head_open", "media_out"], ["need_calibrate"]).decode()
        self.assertEqual(zpl.parse_hqes(text), {"errors": ["media_out", "head_open"], "warnings": ["need_calibrate"]})
        self.assertEqual(zpl.parse_hqes(zpl.hqes_response().decode()), {"errors": [], "warnings": []})
        self.assertIsNone(zpl.parse_hqes(""))

    def test_parse_hi(self):
        self.assertEqual(zpl.parse_hi("\x02ZD421-300dpi,V93.21.15Z,12,8192KB\x03\r\n"),
                         {"model": "ZD421-300dpi", "firmware": "V93.21.15Z", "memory": "8192KB", "dpi": 300})
        self.assertIsNone(zpl.parse_hi(""))

    def test_test_label_scales_and_is_safe(self):
        label = zpl.test_label("Dock ^1~", "usb:ABC", 300, "2026-01-01 10:00")
        self.assertTrue(label.startswith("^XA^CI28") and label.endswith("^XZ"))
        self.assertIn("^FDDock  1 ^FS", label)  # ^ and ~ cannot start commands
        self.assertIn("^A0N,44,44", label)  # 30 dots at 203 dpi -> 44 at 300 dpi
        self.assertEqual(zpl.lint(label), [])
        self.assertEqual(len(zpl.labels(label)), 1)


if __name__ == "__main__":
    unittest.main()
