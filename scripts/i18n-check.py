"""Check Zeo's translation ids against the code that uses them.

    python3 -I scripts/i18n-check.py <tree>

<tree> is a Zed tree with the series applied (patch 0177 adds crates/zeo_i18n). Every
`t!("id", var = …)` in crates/**/*.rs is compared with crates/zeo_i18n/locales/*/*.ftl:

  missing      an id (or id.attribute) the code uses and en-US does not define -- it would
               render as the bare id;
  unused       an en-US message nothing uses, attributes included -- dead text translators
               would still be asked to translate;
  variables    the code passes a different set of variables than en-US's pattern reads --
               Fluent renders a missing one as "{$name}" and silently ignores an extra one;
  stale        an id a translation defines and en-US no longer does;
  foreign var  a translation reading a variable en-US's pattern never gets;
  unlisted     an .ftl file on disk that catalog.rs does not embed, so it would never ship;
  absent       an .ftl file catalog.rs embeds that is not on disk -- the build would fail;
  literal      a sentence left as a string literal in a file that already uses t!.

Why a script and
not a build.rs: a build script sees only its own crate, never the t! calls in the crates that
use it (docs/I18N-PROPOSAL.md in zeo-workspace, section 2).

Exit: 0 clean · 1 problems found · 2 usage or a tree without crates/zeo_i18n.
"""

import pathlib
import re
import sys

I18N_CRATE = pathlib.Path("crates/zeo_i18n")
FALLBACK = "en-US"

T_CALL = re.compile(r'\bt!\s*\(\s*"([^"]+)"')
MESSAGE = re.compile(r"^([a-zA-Z][a-zA-Z0-9_-]*)\s*=")
ATTRIBUTE = re.compile(r"^\s+\.([a-zA-Z][a-zA-Z0-9_-]*)\s*=")
VARIABLE = re.compile(r"\{[^}]*?\$([a-zA-Z][a-zA-Z0-9_-]*)")
# `name = value` at the macro's top level, not `==`, `<=`, `>=` or `!=`.
NAMED_ARG = re.compile(r"(?:^|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=(?!=)")
USES_T = re.compile(r"\bzeo_i18n::t\b|\buse zeo_i18n::")
# A capitalised word followed by another word, or text ending in a sentence's punctuation:
# what a person reads. Identifiers, ids, paths and commands rarely look like either.
PROSE = re.compile(r'"(?:[A-Z][a-z]+(?: [A-Za-z0-9.,:;()\'/~-]+)+|[^"\n]*[a-z][.…])"')
NOT_TEXT_LINE = re.compile(
    r"\blog::|event=|anyhow!|\.context\(|\.with_context\(|expect\(|panic!|debug_assert|"
    r"unreachable!|debug_selector|KeyBinding|actions!"
)
SHIPPED_FILE = re.compile(r'include_str!\("\.\./locales/([^"]+\.ftl)"\)')


def call_args(text: str, open_paren: int) -> str:
    """The text inside t!( … ) after the id literal, up to the matching close paren."""
    depth, i = 0, open_paren
    while i < len(text):
        ch = text[i]
        if ch == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = text[open_paren + 1 : i]
    # Drop the id literal; what is left is `, name = expr, …`. Nested brackets are blanked so a
    # `=` inside a closure or a struct literal is not taken for a variable.
    body = body.split('"', 2)[2] if body.count('"') >= 2 else ""
    flat, depth = [], 0
    for ch in body:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        flat.append(ch if depth == 0 else " ")
    return "".join(flat)


def uses_in(path: pathlib.Path, tree: pathlib.Path) -> list[tuple[str, frozenset, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    uses = []
    for match in T_CALL.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        if text[line_start : match.start()].lstrip().startswith("//"):
            continue
        open_paren = text.index("(", match.start())
        names = frozenset(NAMED_ARG.findall(call_args(text, open_paren)))
        line = text.count("\n", 0, match.start()) + 1
        uses.append((match.group(1), names, f"{path.relative_to(tree)}:{line}"))
    return uses


def literals_in(path: pathlib.Path, tree: pathlib.Path) -> list[str]:
    """Sentence-like string literals left in a file that already translates with t!.

    Only such a file is read: a file with no t! has not been translated yet, and listing every
    English literal in Zed would bury the ones that matter. A line is skipped when it is a
    comment, a log or an internal error, or carries `i18n: not text` (a key, a command, a
    name that must not be translated); test files are skipped whole.
    """
    if path.name.endswith("_tests.rs") or path.name == "tests.rs":
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    if not USES_T.search(text):
        return []
    found = []
    lines = text.splitlines()
    in_tests = False
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#[cfg(test)]"):
            # Only an inline test module is skipped (to the end of the file, where this tree
            # puts it); a #[cfg(test)] on one helper must not blind the rest of the file.
            following = next((l.strip() for l in lines[number:] if l.strip()), "")
            if re.match(r"(pub(\([^)]*\))? )?mod \w+ \{", following):
                in_tests = True
        if in_tests or stripped.startswith(("//", "#[", "*")):
            continue
        previous = lines[number - 2] if number > 1 else ""
        if "i18n: not text" in line or "i18n: not text" in previous:
            continue
        if NOT_TEXT_LINE.search(line):
            continue
        for match in PROSE.finditer(T_CALL.sub("", line)):
            found.append(
                f"literal      {path.relative_to(tree)}:{number}: {match.group(0)[:60]}"
            )
    return found


def parse_ftl(path: pathlib.Path) -> dict[str, tuple[frozenset, int]]:
    """id or id.attribute -> (variables its pattern reads, line). Selector variables count."""
    entries: dict[str, tuple[set, int]] = {}
    message = None
    current = None
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if raw.startswith("#") or not raw.strip():
            if raw.startswith("#"):
                message = current = None
            continue
        if m := MESSAGE.match(raw):
            message = current = m.group(1)
            entries[current] = (set(), number)
        elif (m := ATTRIBUTE.match(raw)) and message:
            current = f"{message}.{m.group(1)}"
            entries[current] = (set(), number)
        elif raw.startswith((" ", "\t", "}")) and current:
            pass  # a continuation line of the current pattern, or a select's closing brace
        else:
            message = current = None  # a term (-name) or junk: not a message
            continue
        entries[current][0].update(VARIABLE.findall(raw))
    return {key: (frozenset(names), line) for key, (names, line) in entries.items()}


def check(tree: pathlib.Path) -> list[str]:
    locales_dir = tree / I18N_CRATE / "locales"
    catalog_rs = tree / I18N_CRATE / "src" / "catalog.rs"
    problems: list[str] = []

    shipped = set(SHIPPED_FILE.findall(catalog_rs.read_text(encoding="utf-8")))
    for rel in sorted(shipped):
        if not (locales_dir / rel).is_file():
            problems.append(
                f"absent       {I18N_CRATE}/locales/{rel}: catalog.rs embeds it and it is not there"
            )
    catalogs: dict[str, dict[str, tuple[frozenset, int, str]]] = {}
    for ftl in sorted(locales_dir.glob("*/*.ftl")):
        rel = ftl.relative_to(locales_dir).as_posix()
        if rel not in shipped:
            problems.append(
                f"unlisted     {ftl.relative_to(tree)}: catalog.rs does not embed it"
            )
        language = ftl.parent.name
        for key, (names, line) in parse_ftl(ftl).items():
            catalogs.setdefault(language, {})[key] = (
                names,
                line,
                f"{ftl.relative_to(tree)}:{line}",
            )

    english = catalogs.get(FALLBACK, {})
    used: dict[str, list[tuple[frozenset, str]]] = {}
    for rs in sorted((tree / "crates").rglob("*.rs")):
        if I18N_CRATE in rs.relative_to(tree).parents:
            continue  # its own tests use their own catalogs
        for key, names, where in uses_in(rs, tree):
            used.setdefault(key, []).append((names, where))

    for key, calls in sorted(used.items()):
        if key not in english:
            for _, where in calls:
                problems.append(f"missing      {where}: {key!r} is not in {FALLBACK}")
            continue
        # Compared as written: t! names are Rust identifiers, and Fluent looks a variable up by
        # its exact name, so `$folder-name` in the .ftl never receives `folder_name = …`.
        wanted = set(english[key][0])
        for names, where in calls:
            if set(names) != wanted:
                problems.append(
                    f"variables    {where}: {key!r} passes {sorted(names)}, "
                    f"{FALLBACK} reads {sorted(wanted)}"
                )

    for rs in sorted((tree / "crates").rglob("*.rs")):
        if I18N_CRATE not in rs.relative_to(tree).parents:
            problems.extend(literals_in(rs, tree))

    used_messages = {key.split(".", 1)[0] for key in used}
    for key, (_, _, where) in sorted(english.items()):
        if "." not in key and key not in used_messages:
            problems.append(f"unused       {where}: {key!r} is used nowhere")

    for language, entries in sorted(catalogs.items()):
        if language == FALLBACK:
            continue
        for key, (names, _, where) in sorted(entries.items()):
            if key not in english:
                problems.append(
                    f"stale        {where}: {key!r} is no longer in {FALLBACK}"
                )
            elif extra := names - english[key][0]:
                problems.append(
                    f"foreign var  {where}: {key!r} reads {sorted(extra)}, which {FALLBACK} never gets"
                )
    return problems


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python3 -I scripts/i18n-check.py <tree>", file=sys.stderr)
        return 2
    tree = pathlib.Path(sys.argv[1]).resolve()
    if not (tree / I18N_CRATE / "src" / "catalog.rs").is_file():
        print(
            f"i18n-check: {tree} has no {I18N_CRATE} (is 0177 applied?)",
            file=sys.stderr,
        )
        return 2
    problems = check(tree)
    for problem in problems:
        print(problem)
    print(f"i18n-check: {len(problems)} problem(s)", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
