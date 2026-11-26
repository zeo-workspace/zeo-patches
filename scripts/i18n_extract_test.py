"""Unit tests for scripts/i18n-extract.py, on temporary trees only.

python3 -I scripts/i18n_extract_test.py
"""

import importlib.util
import pathlib
import tempfile
import textwrap
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "i18n_extract", pathlib.Path(__file__).with_name("i18n-extract.py")
)
i18n_extract = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(i18n_extract)

CODE = r'''
fn render() {
    Label::new("Cancel");
    Button::new("save-button", "Save All");
    Tooltip::text("Toggle the panel");
    let note = "This setting needs a restart.";
    Label::new("Hello {name}");
    log::info!("Starting the server now");
    Label::new(format!("{} files", n));
    let id = "open-recent"; // a key, not text
}

#[cfg(test)]
mod tests {
    fn t() { Label::new("Only In A Test"); }
}
'''


class ExtractTest(unittest.TestCase):
    def collect(self, code=CODE, crate="editor"):
        root = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        src = root / "crates" / crate / "src"
        src.mkdir(parents=True)
        (src / "view.rs").write_text(textwrap.dedent(code))
        return {text for text, _ in i18n_extract.collect(root).values()}

    def test_ids_match_core_text_rs(self):
        # The same reference vectors core_text_tests.rs asserts in Rust.
        self.assertEqual(i18n_extract.fnv1a64(""), 0xCBF29CE484222325)
        self.assertEqual(i18n_extract.fnv1a64("foobar"), 0x85944171F73967E8)
        self.assertEqual(i18n_extract.core_id("a"), "zed-af63dc4c8601ec8c")

    def test_constructor_literals_and_prose_are_collected(self):
        found = self.collect()
        self.assertTrue(
            {"Cancel", "Save All", "Toggle the panel", "This setting needs a restart."} <= found
        )

    def test_menus_settings_titles_and_placeholders_are_collected(self):
        code = """
        fn menus() {
            menu.entry("Cut", None, cut);
            MenuItem::action("Open Recent", OpenRecent);
            Menu { name: "File".into(), items: vec![] };
            SettingsPageItem::SectionHeader("Interface");
            SettingItem { title: "Interface Language", description: "Pick one." };
            editor.set_placeholder_text("Execute a command…", window, cx);
            let hint = "Coming from Neovim? Use our Vim Mode";
        }
        """
        found = self.collect(code)
        for text in ("Cut", "Open Recent", "File", "Interface", "Interface Language",
                     "Pick one.", "Execute a command…", "Coming from Neovim? Use our Vim Mode"):
            self.assertIn(text, found)

    def test_braces_logs_keys_and_tests_are_left_out(self):
        found = self.collect()
        for text in ("Hello {name}", "Starting the server now", "open-recent", "Only In A Test"):
            self.assertNotIn(text, found)

    def test_a_public_test_module_and_previews_are_left_out(self):
        code = """
        fn render() { Label::new("Kept Here"); }

        #[cfg(test)]
        pub(crate) mod tests {
            fn t() { Label::new("Hidden papaya reasoning."); }
        }
        """
        found = self.collect(code)
        self.assertIn("Kept Here", found)
        self.assertNotIn("Hidden papaya reasoning.", found)
        self.assertEqual(self.collect(crate="component_preview"), set())

    def test_action_names_are_humanized_like_the_palette(self):
        # The vectors of command_palette.rs's test_humanize_action_name.
        h = i18n_extract.humanize_action_name
        self.assertEqual(h("editor::GoToDefinition"), "editor: go to definition")
        self.assertEqual(h("go_to_line::Deploy"), "go to line: deploy")
        self.assertEqual(h("agent::OpenGlobalAGENTS.mdRules"), "agent: open global AGENTS.md rules")
        self.assertEqual(h("editor::OpenURL"), "editor: open URL")
        self.assertEqual(h("editor::OpenURLParser"), "editor: open URL parser")

    def test_declared_actions_contribute_their_action_part(self):
        code = """
        actions!(
            editor,
            [
                /// Goes there.
                GoToDefinition,
                #[action(deprecated_aliases = ["editor::OldName"])]
                OpenURL,
                #[action(name = "FoldAtLevel_1")]
                FoldAtLevel1,
                GoToDefinitionSplit,
            ]
        );

        #[derive(Clone)]
        #[action(namespace = git)]
        pub struct StageAll;
        """
        found = self.collect(code)
        self.assertTrue({"go to definition", "open URL", "fold at level 1", "go to definition split",
                         "stage all"} <= found)

    def test_title_case_follows_heck(self):
        t = i18n_extract.heck_title_case
        self.assertEqual(t("System"), "System")
        self.assertEqual(t("OnLastWindowClosed"), "On Last Window Closed")
        self.assertEqual(t("HTTPServer"), "Http Server")
        self.assertEqual(t("Add to Existing Window"), "Add To Existing Window")

    def test_enum_dropdown_values_are_collected(self):
        code = """
        #[derive(Clone, Copy, strum::VariantArray, strum::VariantNames)]
        pub enum ThemeAppearanceMode {
            /// Light.
            Light,
            Dark,
            #[strum(serialize = "Follow the System")]
            System,
            Custom(u32),
        }
        """
        found = self.collect(code, crate="settings_content")
        self.assertTrue({"Light", "Dark", "Follow The System", "Custom"} <= found)

    def test_run_time_text_is_collected_as_a_template(self):
        t = i18n_extract.format_template
        self.assertEqual(t("About {}"), "About %1")
        self.assertEqual(t("Discard {changes} to {count} files?"), "Discard %1 to %2 files?")
        for raw in ("{}", "{:?} items", "{}%", "plain text", "{name}.rs", "Error: {e:#}"):
            self.assertIsNone(t(raw), raw)
        code = """
        fn render() {
            Label::new(format!("Quit {}", app_name));
            log::info!("{}", format!("Starting {}", x));
        }
        """
        found = self.collect(code)
        self.assertIn("Quit %1", found)
        self.assertNotIn("Starting %1", found)

    def test_zeo_i18n_itself_is_not_scanned(self):
        self.assertEqual(self.collect(crate="zeo_i18n"), set())

    def test_rendered_file_round_trips_through_the_check(self):
        root = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "crates/editor/src").mkdir(parents=True)
        (root / "crates/editor/src/view.rs").write_text('fn f() { Label::new("Cancel"); }\n')
        (root / "crates/zeo_i18n/locales/en-US").mkdir(parents=True)
        out = root / i18n_extract.OUT
        out.write_text(i18n_extract.render(i18n_extract.collect(root)), encoding="utf-8")
        self.assertIn(f"{i18n_extract.core_id('Cancel')} = Cancel", out.read_text())


if __name__ == "__main__":
    unittest.main()
