"""Extract Zed's own UI strings into the catalog Zeo translates them from.

    python3 -I scripts/i18n-extract.py <tree> [--check]

<tree> is a Zed tree with the WHOLE series applied, every USE group -- the tree test.sh
builds -- so the English that Zeo's own patches leave on Zed's screens is collected too; a
build without a group ships that group's entries unused. Writes
crates/zeo_i18n/locales/en-US/zed-core.ftl: one message per English text, under the id
core_text.rs derives from the text itself (`zed-` + FNV-1a 64 of its UTF-8 bytes in hex), so
no call site in Zed changes. With --check nothing is written: exit 1 when the file on disk
differs from what the tree would produce -- the bump step that lists new strings.

What is collected, from every crates/**/*.rs outside tests and outside zeo_i18n:
  * the literal handed straight to a constructor that translates (ui's Label::new,
    Button::new's label, the Tooltip titles), whatever it looks like -- "Cancel" too;
  * any other literal that reads as prose: a capitalised word followed by more words, or text
    ending in a sentence's punctuation -- text kept in a constant or a variable first.
A literal Fluent cannot hold as plain text (braces, a newline, a leading `[`, `*` or `.`,
surrounding spaces) is left out and stays English. So does text built at run time.

Exit: 0 written or up to date · 1 --check found a difference · 2 usage.
"""

import pathlib
import re
import sys

OUT = pathlib.Path("crates/zeo_i18n/locales/en-US/zed-core.ftl")
# zeo_i18n has its own catalog; the component galleries show sample text nobody translates.
SKIP_CRATES = {"zeo_i18n", "component_preview", "picker_preview"}

STRING = r'"((?:[^"\\\n]|\\.)*)"'
CONSTRUCTOR = re.compile(
    # ui's constructors that translate (0195), and what feeds them: context-menu rows,
    # app-menu items and names, settings-page titles, headers, placeholders.
    r"(?:\b(?:Label::new|Tooltip::(?:text|simple|new|for_action_title|for_action_title_in|with_meta"
    r"|with_meta_in|for_action)|Headline::new|ListHeader::new|SectionHeader|Menu::new"
    r"|MenuItem::(?:action|os_action)|set_placeholder_text|placeholder_text|with_title)"
    r"|\.(?:entry|action|header|toggleable_entry|label|tooltip_label))\s*\(\s*" + STRING
    + r"|\bButton::new\s*\([^,()]*(?:\([^()]*\))?[^,()]*,\s*" + STRING
    + r"|\b(?:title|description):\s*" + STRING
    + r"|\bname:\s*" + STRING + r"\.into\(\)"
)
PROSE = re.compile(r'"((?:[A-Z][a-z]+(?: [A-Za-z0-9.,:;()\'/~?!-]+)+|[^"\n\\]*[a-z][.…?!]))"')
NOT_TEXT_LINE = re.compile(
    r"\blog::|event=|anyhow!|\.context\(|\.with_context\(|expect\(|panic!|debug_assert|"
    r"unreachable!|debug_selector|KeyBinding|actions!|#\[|bail!|assert"
)
ESCAPES = {'\\"': '"', "\\\\": "\\", "\\'": "'"}


# The palette's action names (0195 shows the part after the namespace translated):
# `actions!(namespace, [A, B])` and `#[action(namespace = n)] struct A`.
ACTIONS_MACRO = re.compile(r"\bactions!\(\s*(\w+)\s*,\s*\[")
ACTION_ATTR = re.compile(
    # The attribute right before its item: only other attributes and doc comments between.
    r"#\[action\(([^)]*)\)\]\s*(?:#\[[^\]]*\]\s*|///[^\n]*\n\s*)*"
    r"(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum)\s+(\w+)"
)


def humanize_action_name(name: str) -> str:
    """command_palette.rs's humanize_action_name, char for char."""
    chars, result, index = list(name), [], 0

    def ends(ch):
        return bool(result) and result[-1].endswith(ch)

    while index < len(chars):
        char = chars[index]
        if char == ":":
            result.append(" " if ends(":") else ":")
            index += 1
        elif char == "_":
            result.append(" ")
            index += 1
        elif char.isupper():
            start = index
            index += 1
            while index < len(chars) and chars[index].isupper():
                index += 1
            run = chars[start:index]
            if len(run) > 1:
                split_before_last = index < len(chars) and chars[index].islower()
                acronym_end = len(run) - 1 if split_before_last else len(run)
                if acronym_end > 0:
                    if not ends(" "):
                        result.append(" ")
                    result.append("".join(run[:acronym_end]))
                if split_before_last:
                    if not ends(" "):
                        result.append(" ")
                    result.append(run[acronym_end].lower())
            else:
                if not ends(" "):
                    result.append(" ")
                result.append(char.lower())
        else:
            result.append(char)
            index += 1
    return "".join(result)


def actions_lists(text: str):
    """(namespace, body) of every `actions!(ns, [ ... ])`. The list is read by bracket depth:
    its items carry attributes like `#[action(deprecated_aliases = ["..."])]`, whose own
    brackets would end a lazy regex early (it cut editor's list after two names)."""
    for match in ACTIONS_MACRO.finditer(text):
        depth, index = 1, match.end()
        while index < len(text) and depth:
            if text[index] == '"':
                index = text.index('"', index + 1) if '"' in text[index + 1 :] else len(text)
            elif text[index] == "[":
                depth += 1
            elif text[index] == "]":
                depth -= 1
            index += 1
        yield match.group(1), text[match.end() : index - 1]


def action_parts(text: str):
    """The part after `namespace: ` of every action name declared in a file."""
    names = []
    for namespace, body in actions_lists(text):
        body = re.sub(r"//[^\n]*", "", body)
        renamed = None
        attr = r'#\[action\(((?:[^()"]|"[^"]*")*)\)\]'
        for token in re.finditer(attr + r"|\b([A-Z]\w*)\b", body):
            if token.group(1) is not None:
                found = re.search(r'\bname\s*=\s*"([^"]+)"', token.group(1))
                renamed = found.group(1) if found else None
                continue
            names.append(f"{namespace}::{renamed or token.group(2)}")
            renamed = None
    for args, ident in ACTION_ATTR.findall(text):
        namespace = re.search(r"\bnamespace\s*=\s*(\w+)", args)
        renamed = re.search(r'\bname\s*=\s*"([^"]+)"', args)
        if namespace:
            names.append(f"{namespace.group(1)}::{renamed.group(1) if renamed else ident}")
    for name in names:
        humanized = humanize_action_name(name)
        if ": " in humanized:
            yield humanized.split(": ", 1)[1]


def heck_title_case(text: str) -> str:
    """heck 0.5's to_title_case (settings_ui's enum dropdowns), char for char."""
    words = []
    for word in re.split(r"[^\w]|_", text):
        init, mode, chars = 0, "boundary", list(word)
        for i, c in enumerate(chars):
            if i + 1 < len(chars):
                nxt = chars[i + 1]
                next_mode = "lower" if c.islower() else "upper" if c.isupper() else mode
                if next_mode == "lower" and nxt.isupper():
                    words.append(word[init : i + 1])
                    init, mode = i + 1, "boundary"
                elif mode == "upper" and c.isupper() and nxt.islower():
                    words.append(word[init:i])
                    init, mode = i, "boundary"
                else:
                    mode = next_mode
            else:
                words.append(word[init:])
    return " ".join(w[:1].upper() + w[1:].lower() for w in words if w)


ENUM_WITH_NAMES = re.compile(
    r"#\[derive\([^\]]*\bVariantNames\b[^\]]*\)\][\s\S]{0,600}?\benum\s+\w+[^{]*\{"
)


def enum_dropdown_labels(text: str):
    """What settings_ui's EnumVariantDropdown shows for every enum deriving VariantNames:
    each variant's name (or its strum(serialize)), title-cased."""
    for match in ENUM_WITH_NAMES.finditer(text):
        depth, index = 1, match.end()
        while index < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[index], 0)
            index += 1
        body, renamed, level = text[match.end() : index - 1], None, 0
        for line in body.splitlines():
            stripped = line.strip()
            if level == 0:
                serialize = re.match(r'#\[strum\(serialize\s*=\s*"([^"]+)"', stripped)
                if serialize:
                    renamed = serialize.group(1)
                variant = re.match(r"([A-Z]\w*)\b", stripped)
                if variant:
                    yield heck_title_case(renamed or variant.group(1))
                    renamed = None
            level += line.count("{") + line.count("(") - line.count("}") - line.count(")")


# Text built at run time: `format!("About {}", name)` reaches a label as "About Zed Dev",
# which no id matches. Its template is stored with %1, %2... for the placeholders, in their
# order, and core_text.rs fits an unknown text into the templates (0195).
FORMAT = re.compile(r'\bformat!\(\s*"((?:[^"\\\n]|\\.)*)"')
PLACEHOLDER = re.compile(r"\{[A-Za-z_0-9]*\}")


def format_template(raw: str) -> str | None:
    """`About {}` -> `About %1`; None when it is not prose a person reads, or holds a format
    spec (`{:?}`), an escape or a literal % that the template syntax could not tell apart."""
    if not PLACEHOLDER.search(raw) or re.search(r"\{[^}]*:[^}]*\}|\{\{|\}\}|%|\\", raw):
        return None
    literal = PLACEHOLDER.sub("", raw)
    words = re.findall(r"[A-Za-z]{3,}", literal)
    if not words or not re.match(r"^[^A-Za-z]*[A-Z][a-z]", literal) and len(words) < 2:
        return None
    count = iter(range(1, 100))
    return PLACEHOLDER.sub(lambda _: f"%{next(count)}", raw)


def fnv1a64(text: str) -> int:
    value = 0xCBF29CE484222325
    for byte in text.encode("utf-8"):
        value ^= byte
        value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return value


def core_id(text: str) -> str:
    return f"zed-{fnv1a64(text):016x}"


def unescape(raw: str) -> str | None:
    """The literal's text, or None when it holds an escape other than \\" \\\\ \\'."""
    out, i = [], 0
    while i < len(raw):
        if raw[i] == "\\":
            pair = raw[i : i + 2]
            if pair not in ESCAPES:
                return None
            out.append(ESCAPES[pair])
            i += 2
        else:
            out.append(raw[i])
            i += 1
    return "".join(out)


def fluent_safe(text: str) -> bool:
    return (
        len(text) >= 2
        and any(ch.isalpha() for ch in text)
        and text == text.strip()
        and not any(ch in text for ch in "{}\n\r\t")
        and text[0] not in "[*."
    )


def rust_files(tree: pathlib.Path):
    for rs in sorted((tree / "crates").rglob("*.rs")):
        rel = rs.relative_to(tree)
        parts = rel.parts
        if parts[1] in SKIP_CRATES or "tests" in parts or rs.name.endswith("_tests.rs"):
            continue
        if rs.name in ("tests.rs", "test.rs", "preview.rs") or "test_support" in rs.name:
            continue
        yield rel, rs


def collect(tree: pathlib.Path) -> dict[str, tuple[str, str]]:
    """id -> (text, crate of its first occurrence)."""
    found: dict[str, tuple[str, str]] = {}
    for rel, rs in rust_files(tree):
        text = rs.read_text(encoding="utf-8", errors="replace")
        # A file's tests usually sit at its end, behind `#[cfg(test)] mod`.
        cut = re.search(
            r"^#\[cfg\(test\)\]\s*\n\s*(?:pub(?:\([^)]*\))?\s+)?mod \w+\s*\{", text, re.M
        )
        if cut:
            text = text[: cut.start()]
        crate = rel.parts[1]
        for part in action_parts(text):
            if fluent_safe(part):
                found.setdefault(core_id(part), (part, crate))
        for label in enum_dropdown_labels(text):
            if fluent_safe(label):
                found.setdefault(core_id(label), (label, crate))
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("//"):
                continue
            candidates = []
            for match in CONSTRUCTOR.finditer(line):
                candidates.append(next(g for g in match.groups() if g is not None))
            if not NOT_TEXT_LINE.search(line):
                candidates.extend(match.group(1) for match in PROSE.finditer(line))
                for match in FORMAT.finditer(line):
                    template = format_template(match.group(1))
                    if template:
                        candidates.append(template)
            for raw in candidates:
                value = unescape(raw)
                if value is None or not fluent_safe(value):
                    continue
                found.setdefault(core_id(value), (value, crate))
    return found


def render(found: dict[str, tuple[str, str]]) -> str:
    lines = [
        "### Zed's own UI strings, extracted by zed-patches/scripts/i18n-extract.py.",
        "### GENERATED -- do not edit; rerun the script. Ids are derived from the English",
        "### (core_text.rs), so a translation keeps its id while the English stays the same.",
        "",
    ]
    for key in sorted(found, key=lambda k: (found[k][1], found[k][0])):
        value, crate = found[key]
        lines.append(f"# {crate}")
        lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--check"]
    if len(args) != 1:
        print("usage: python3 -I scripts/i18n-extract.py <tree> [--check]", file=sys.stderr)
        return 2
    tree = pathlib.Path(args[0])
    if not (tree / "crates").is_dir():
        print(f"i18n-extract: {tree} has no crates/", file=sys.stderr)
        return 2
    found = collect(tree)
    rendered = render(found)
    out = tree / OUT
    if "--check" in sys.argv:
        current = out.read_text(encoding="utf-8") if out.is_file() else ""
        old = set(re.findall(r"^(zed-[0-9a-f]{16}) = ", current, re.M))
        new = set(found)
        print(f"i18n-extract: {len(new)} strings; {len(new - old)} new, {len(old - new)} gone")
        for key in sorted(new - old, key=lambda k: found[k][0]):
            print(f"  new   {found[key][1]}: {found[key][0]}")
        return 0 if rendered == current else 1
    out.write_text(rendered, encoding="utf-8")
    print(f"i18n-extract: wrote {len(found)} strings to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
