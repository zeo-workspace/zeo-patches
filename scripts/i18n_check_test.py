"""Unit tests for scripts/i18n-check.py, on temporary trees only.

python3 -I scripts/i18n_check_test.py
"""

import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "i18n_check", pathlib.Path(__file__).with_name("i18n-check.py")
)
i18n_check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(i18n_check)

CATALOG_RS = """
pub static SHIPPED: &[LocaleSource] = &[
    LocaleSource { language: "en-US", files: &[("en-US/zeo.ftl", include_str!("../locales/en-US/zeo.ftl"))] },
    LocaleSource { language: "pt-BR", files: &[("pt-BR/zeo.ftl", include_str!("../locales/pt-BR/zeo.ftl"))] },
];
"""

EN = """\
### header
agent-sandbox-hide-host = Hide the Host
    .tooltip = The agent sees a made-up host
agent-sandbox-jails = { $count ->
    [one] One jail
   *[other] { $count } jails
}
agent-sandbox-folder = Writable: { $folder_name }
"""

PT = """\
agent-sandbox-hide-host = Ocultar o host
    .tooltip = O agente vê um host inventado
agent-sandbox-jails = { $count ->
    [one] Uma jail
   *[other] { $count } jails
}
"""

CODE_OK = """\
fn render() {
    Label::new(t!("agent-sandbox-hide-host"));
    Tooltip::text(t!("agent-sandbox-hide-host.tooltip"));
    Label::new(t!("agent-sandbox-jails", count = jails.len()));
    Label::new(zeo_i18n::t!(
        "agent-sandbox-folder",
        folder_name = path.file_name().map(|n| n == x).unwrap_or_default(),
    ));
    // t!("in-a-comment") is not a use
}
"""


class TreeCase(unittest.TestCase):
    def tree(self, code=CODE_OK, en=EN, pt=PT, extra_ftl=None, catalog=CATALOG_RS):
        root = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        crate = root / "crates/zeo_i18n"
        (crate / "src").mkdir(parents=True)
        (crate / "src/catalog.rs").write_text(catalog)
        for language, text in (("en-US", en), ("pt-BR", pt)):
            (crate / "locales" / language).mkdir(parents=True)
            (crate / "locales" / language / "zeo.ftl").write_text(text)
        if extra_ftl:
            (crate / "locales/pt-BR/more.ftl").write_text(extra_ftl)
        (crate / "src/tests.rs").write_text('t!("only-in-its-own-tests");')
        ui = root / "crates/agent_ui/src"
        ui.mkdir(parents=True)
        (ui / "sandbox.rs").write_text(textwrap.dedent(code))
        return root

    def kinds(self, root):
        return [problem.split()[0] for problem in i18n_check.check(root)]


class I18nCheckTest(TreeCase):
    def test_a_consistent_tree_is_clean(self):
        self.assertEqual(i18n_check.check(self.tree()), [])

    def test_an_id_missing_from_english_is_reported_with_its_place(self):
        problems = i18n_check.check(
            self.tree(code=CODE_OK + 't!("agent-sandbox-nope");\n')
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("missing", problems[0])
        self.assertIn("crates/agent_ui/src/sandbox.rs:11", problems[0])

    def test_a_missing_attribute_is_missing(self):
        code = CODE_OK + 't!("agent-sandbox-hide-host.note");\n'
        self.assertEqual(self.kinds(self.tree(code=code)), ["missing"])

    def test_an_english_message_nobody_uses_is_unused(self):
        en = EN + "agent-sandbox-orphan = Nobody shows this\n"
        self.assertEqual(self.kinds(self.tree(en=en)), ["unused"])

    def test_a_missing_or_extra_variable_is_reported(self):
        missing = CODE_OK.replace("count = jails.len()", "")
        self.assertEqual(self.kinds(self.tree(code=missing)), ["variables"])
        extra = CODE_OK.replace("count = jails.len()", "count = 1, total = 2")
        self.assertEqual(self.kinds(self.tree(code=extra)), ["variables"])

    def test_variable_names_are_compared_as_written(self):
        en = EN.replace("$folder_name", "$folder-name")
        self.assertEqual(self.kinds(self.tree(en=en)), ["variables"])

    def test_equality_inside_a_value_is_not_a_variable(self):
        self.assertEqual(i18n_check.check(self.tree()), [])  # `n == x` in CODE_OK

    def test_a_translation_of_a_removed_id_is_stale(self):
        pt = PT + "agent-sandbox-gone = Sumiu\n"
        self.assertEqual(self.kinds(self.tree(pt=pt)), ["stale"])

    def test_a_translation_reading_an_unknown_variable_is_reported(self):
        pt = PT.replace("Ocultar o host", "Ocultar { $host }")
        self.assertEqual(self.kinds(self.tree(pt=pt)), ["foreign"])

    def test_a_translation_may_leave_a_variable_out(self):
        pt = PT.replace("*[other] { $count } jails", "*[other] Várias jails")
        self.assertEqual(i18n_check.check(self.tree(pt=pt)), [])

    def test_an_ftl_file_catalog_rs_does_not_embed_is_unlisted(self):
        self.assertEqual(self.kinds(self.tree(extra_ftl="# empty\n")), ["unlisted"])

    def test_an_ftl_file_catalog_rs_embeds_but_is_missing_is_absent(self):
        catalog = CATALOG_RS.replace("pt-BR/zeo.ftl", "pt-BR/gone.ftl")
        kinds = self.kinds(self.tree(catalog=catalog))
        self.assertIn("absent", kinds)

    def test_a_sentence_left_in_a_translated_file_is_a_literal(self):
        code = (
            "use zeo_i18n::t;\n"
            + CODE_OK
            + 'fn more() { Label::new("Hide the Disks"); }\n'
        )
        problems = i18n_check.check(self.tree(code=code))
        self.assertEqual([p.split()[0] for p in problems], ["literal"], problems)
        self.assertIn("sandbox.rs:12", problems[0])

    def test_a_sentence_ending_in_a_period_is_a_literal(self):
        code = (
            "use zeo_i18n::t;\n"
            + CODE_OK
            + 'const NOTE: &str = "restart the agent to apply.";\n'
        )
        self.assertEqual(self.kinds(self.tree(code=code)), ["literal"])

    def test_logs_markers_comments_and_tests_are_not_literals(self):
        code = (
            "use zeo_i18n::t;\n"
            + CODE_OK
            + 'log::warn!("Could not read the file");\n'
            + 'let key = "Agent Sandbox (ai-jail)"; // i18n: not text\n'
            + '// Label::new("Hide the Host")\n'
            + '#[cfg(test)]\nmod tests { const A: &str = "Hide the Host"; }\n'
        )
        self.assertEqual(i18n_check.check(self.tree(code=code)), [])

    def test_a_test_only_item_does_not_hide_the_rest_of_the_file(self):
        # agent_jail_page.rs, 2026-11-24: #[cfg(test)] on a single helper made the lint skip
        # everything after it, and "Default for every project" shipped untranslated.
        code = (
            "use zeo_i18n::t;\n"
            + CODE_OK
            + "#[cfg(test)]\nfn helper() {}\n"
            + 'fn later() { Label::new("Default for every project"); }\n'
        )
        self.assertEqual(self.kinds(self.tree(code=code)), ["literal"])

    def test_a_file_that_does_not_use_t_is_not_linted(self):
        code = 'fn f() { Label::new("Hide the Host"); }\n'
        self.assertNotIn("literal", self.kinds(self.tree(code=code)))

    def test_a_tree_without_the_crate_is_a_usage_error(self):
        root = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        script = pathlib.Path(__file__).with_name("i18n-check.py")
        result = subprocess.run(
            [sys.executable, "-I", str(script), str(root)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == "__main__":
    unittest.main()
