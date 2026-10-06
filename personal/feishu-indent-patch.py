#!/usr/bin/env python3
"""Local patch: stop Feishu mobile from swallowing content after indented blocks.

Background
----------
Feishu's mobile rich-text ("post") renderer silently drops everything after a
Markdown *indented* code block (a line starting with 4+ spaces preceded by a
blank line).  Desktop Feishu renders the same message in full, so the bug is
invisible unless you check a phone.  Verified 2026-08-11 with single-variable
A/B messages sent through the real API (msg_type=post):

    T6  4-space indents + bold      -> truncated
    T7  same, indents -> U+3000     -> complete
    T8  same, bold removed          -> truncated   (bold is irrelevant)

Upstream tracks this as NousResearch/hermes-agent#77306 with an unmerged fix in
PR #77307.  The shipped adapter only isolates ``` fenced blocks, not indented
ones, so every post-type reply is still exposed.

What this patch does
--------------------
Injects a sanitizer into `_build_markdown_post_rows` (the single choke point
every post-type payload passes through) that rewrites leading half-width
indentation to U+3000 IDEOGRAPHIC SPACE.  Visually identical, but Markdown no
longer sees a code block.  Content inside ``` fenced blocks is left untouched.

Self-removal
------------
`apply` re-detects upstream state on every run: it first strips any previously
injected block, then inspects the pristine function.  If upstream has grown its
own indent handling, the patch is NOT re-applied and the script prints a loud
notice instead.  So once PR #77307 lands, the next auto-update run retires this
patch by itself and says so.

Usage:  feishu-indent-patch.py [apply|remove|status]
Exit:   0 ok / 1 error
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ADAPTER = Path(
    "/Users/max/.hermes/hermes-agent/plugins/platforms/feishu/adapter.py"
)

MARK_BEGIN = "# ===== HERMES-LOCAL-PATCH feishu-indent BEGIN ====="
MARK_END = "# ===== HERMES-LOCAL-PATCH feishu-indent END ====="
CALL_MARK = "# HERMES-LOCAL-PATCH feishu-indent CALL"

ANCHOR_DEF = "def _build_markdown_post_rows("

IDEOGRAPHIC_SPACE = "　"

# The block injected immediately above _build_markdown_post_rows.
INJECTED_BLOCK = f'''{MARK_BEGIN}
# Rewrites leading half-width indentation to U+3000 so Feishu's mobile post
# renderer never sees an indented code block (which makes it swallow all
# following content).  Fenced-block bodies are preserved verbatim.
# Remove with: ~/.hermes/scripts/feishu-indent-patch.py remove
_HLP_INDENT_RE = re.compile(r"^([ \\t]+)(?=\\S)")


def _hlp_sanitize_indent(content: str) -> str:
    if not content:
        return content
    out = []
    in_fence = False
    for raw in content.splitlines():
        stripped = raw.strip()
        is_fence = bool(
            _MARKDOWN_FENCE_CLOSE_RE.match(stripped)
            if in_fence
            else _MARKDOWN_FENCE_OPEN_RE.match(stripped)
        )
        if is_fence:
            in_fence = not in_fence
            out.append(raw)
            continue
        if in_fence:
            out.append(raw)
            continue
        m = _HLP_INDENT_RE.match(raw)
        if m:
            lead = m.group(1)
            width = sum(4 if ch == "\\t" else 1 for ch in lead)
            if width >= 4:
                raw = (
                    "\\u3000" * (width // 2)
                    + " " * (width % 2)
                    + raw[len(lead):]
                )
        out.append(raw)
    return "\\n".join(out)


{MARK_END}
'''

CALL_LINE = f"    content = _hlp_sanitize_indent(content)  {CALL_MARK}\n"

# Signals that upstream grew its own indented-code handling.
UPSTREAM_SIGNALS = ("indent", "{4,}", "{4}")


def read() -> str:
    return ADAPTER.read_text(encoding="utf-8")


def write(text: str) -> None:
    ADAPTER.write_text(text, encoding="utf-8")


def strip_patch(text: str) -> str:
    """Remove the injected block and call line, restoring the pristine source.

    The blank lines `inject()` adds after MARK_END are eaten too, otherwise
    each apply/strip cycle would leave one behind and they would pile up.
    """
    out, skipping, eating_blanks = [], False, False
    for line in text.splitlines(keepends=True):
        if line.startswith(MARK_BEGIN):
            skipping = True
            continue
        if line.startswith(MARK_END):
            skipping, eating_blanks = False, True
            continue
        if skipping:
            continue
        if eating_blanks:
            if not line.strip():
                continue
            eating_blanks = False
        if CALL_MARK in line:
            continue
        out.append(line)
    return "".join(out)


def function_body(text: str) -> str:
    """Source of _build_markdown_post_rows, up to the next top-level def."""
    idx = text.find(ANCHOR_DEF)
    if idx < 0:
        return ""
    nxt = text.find("\ndef ", idx + 1)
    return text[idx:nxt] if nxt > 0 else text[idx:]


def upstream_fixed(pristine: str) -> tuple[bool, str]:
    body = function_body(pristine)
    if not body:
        return False, "anchor function not found"
    low = body.lower()
    hits = [s for s in UPSTREAM_SIGNALS if s in low]
    return (bool(hits), ", ".join(hits) if hits else "no indent handling found")


def is_patched(text: str) -> bool:
    return MARK_BEGIN in text and CALL_MARK in text


def inject(pristine: str) -> str:
    idx = pristine.find(ANCHOR_DEF)
    if idx < 0:
        raise RuntimeError(f"anchor not found: {ANCHOR_DEF}")

    head, tail = pristine[:idx], pristine[idx:]
    tail_lines = tail.splitlines(keepends=True)

    # Insert the call after the def line, skipping the docstring if present.
    insert_at = 1
    if len(tail_lines) > 1 and tail_lines[1].lstrip().startswith('"""'):
        first = tail_lines[1].strip()
        if first != '"""' and first.endswith('"""') and len(first) > 6:
            insert_at = 2  # single-line docstring
        else:
            for i in range(2, len(tail_lines)):
                if tail_lines[i].rstrip().endswith('"""'):
                    insert_at = i + 1
                    break
    tail_lines.insert(insert_at, CALL_LINE)
    # Two blank lines after MARK_END keep PEP 8 spacing; strip_patch() eats them.
    return head + INJECTED_BLOCK + "\n\n" + "".join(tail_lines)


def selftest() -> list[str]:
    """Exec the injected sanitizer standalone and assert real behaviour."""
    ns = {
        "re": re,
        "_MARKDOWN_FENCE_OPEN_RE": re.compile(r"^```([^\n`]*)\s*$"),
        "_MARKDOWN_FENCE_CLOSE_RE": re.compile(r"^```\s*$"),
    }
    src = INJECTED_BLOCK.replace(MARK_BEGIN, "").replace(MARK_END, "")
    exec(compile(src, "<injected>", "exec"), ns)
    fn = ns["_hlp_sanitize_indent"]
    errs = []

    got = fn("intro\n\n    code line\n\ntail")
    if "\n    code line" in got or IDEOGRAPHIC_SPACE not in got:
        errs.append(f"4-space indent not converted: {got!r}")
    if "tail" not in got:
        errs.append("content loss")

    got = fn("a\n```\n    stay raw\n```\nb")
    if "\n    stay raw" not in got:
        errs.append(f"fenced body was altered: {got!r}")

    got = fn("- item\nplain\n  two spaces")
    if got != "- item\nplain\n  two spaces":
        errs.append(f"short indent must not change: {got!r}")

    got = fn("\t\ttabbed")
    if got.startswith("\t"):
        errs.append(f"tabs not converted: {got!r}")

    got = fn("")
    if got != "":
        errs.append("empty input mangled")
    return errs


def compile_ok() -> str | None:
    r = subprocess.run(
        [sys.executable, "-m", "py_compile", str(ADAPTER)],
        capture_output=True,
        text=True,
    )
    return None if r.returncode == 0 else (r.stderr.strip() or "compile failed")


def cmd_status() -> int:
    text = read()
    pristine = strip_patch(text)
    fixed, why = upstream_fixed(pristine)
    errs = selftest()
    print(f"adapter        : {ADAPTER}")
    print(f"patched        : {'YES' if is_patched(text) else 'no'}")
    print(f"upstream fixed : {'YES' if fixed else 'no'}  ({why})")
    print(f"selftest       : {'PASS' if not errs else 'FAIL -> ' + '; '.join(errs)}")
    return 0


def cmd_remove() -> int:
    text = read()
    if not is_patched(text):
        return 0
    write(strip_patch(text))
    err = compile_ok()
    if err:
        write(text)
        print(f"ERROR: rollback, adapter broken after removal:\n{err}")
        return 1
    print("feishu-indent patch removed.")
    return 0


def cmd_apply() -> int:
    errs = selftest()
    if errs:
        print("ERROR: patch selftest failed, refusing to apply:")
        for e in errs:
            print(f"  - {e}")
        return 1

    original = read()
    pristine = strip_patch(original)
    was_patched = is_patched(original)

    fixed, why = upstream_fixed(pristine)
    if fixed:
        if was_patched:
            write(pristine)
            err = compile_ok()
            if err:
                write(original)
                print(f"ERROR: rollback, adapter broken after removal:\n{err}")
                return 1
            print("=" * 62)
            print("Upstream now handles indented blocks -> local Feishu patch removed.")
            print(f"  signal: {why}")
            print("  You can delete the patch hook from hermes-auto-update.sh.")
            print("=" * 62)
        return 0

    try:
        patched = inject(pristine)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1

    if patched == original:
        return 0  # already patched, byte-identical

    write(patched)
    err = compile_ok()
    if err:
        write(original)
        print(f"ERROR: rollback, patched adapter does not compile:\n{err}")
        return 1
    if not is_patched(read()):
        write(original)
        print("ERROR: rollback, patch markers missing after write")
        return 1

    print("feishu-indent patch applied (restart the gateway to activate).")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    cmd = args[0] if args else "apply"
    if not ADAPTER.exists():
        print(f"ERROR: adapter not found: {ADAPTER}")
        return 1
    if cmd == "apply":
        return cmd_apply()
    if cmd == "remove":
        return cmd_remove()
    if cmd == "status":
        return cmd_status()
    print(f"unknown command: {cmd}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
