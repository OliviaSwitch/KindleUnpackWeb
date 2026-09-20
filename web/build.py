#!/usr/bin/env python3
"""
Build web/kindleunpack.html — a single self-contained page that runs the
unmodified KindleUnpack library in the browser via Pyodide.

The 22 modules under lib/ are inlined as a JS object literal; the only
network dependency at runtime is the Pyodide runtime itself (CDN).

Usage:  python3 web/build.py
"""

import datetime
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
LIB = ROOT / "lib"

PYODIDE_VERSION = "314.0.7"  # must stay in sync with app.js


def to_js_literal(obj) -> str:
    """JSON that is also a safe JS string literal inside a <script> tag.

    - `<` is escaped so a literal `</script>` inside a module cannot close the tag.
    - U+2028/U+2029 are legal raw in JSON but were line terminators in JS string
      literals before ES2019, so escape them too.
    """
    return (
        json.dumps(obj, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def main() -> int:
    if not LIB.is_dir():
        print(f"error: {LIB} not found", file=sys.stderr)
        return 1

    sources = {p.name: p.read_text(encoding="utf-8") for p in sorted(LIB.glob("*.py"))}
    if "kindleunpack.py" not in sources:
        print(f"error: no kindleunpack.py under {LIB}", file=sys.stderr)
        return 1

    lib_js = "var LIB_SOURCES = " + to_js_literal(sources) + ";"

    app_js = (WEB / "app.js").read_text(encoding="utf-8")
    template = (WEB / "template.html").read_text(encoding="utf-8")

    for placeholder in ("/*__LIB_SOURCES__*/", "/*__OFFLINE_RUNTIME__*/", "/*__APP_JS__*/"):
        if placeholder not in template:
            print(f"error: placeholder {placeholder} missing from template.html", file=sys.stderr)
            return 1

    if "</script" in app_js.lower():
        print("error: app.js contains </script and would break the host tag", file=sys.stderr)
        return 1

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    # This build leaves the inlined-runtime slot empty: it loads Pyodide from the
    # CDN at runtime. web/build_full.py fills the same slot instead.
    html = (template
            .replace("/*__LIB_SOURCES__*/", lib_js)
            .replace("/*__OFFLINE_RUNTIME__*/", "")
            .replace("/*__APP_JS__*/", app_js))
    html = html.replace(
        "<title>KindleUnpack Web</title>",
        f"<title>KindleUnpack Web</title>\n"
        f"<!-- built {stamp} · lib/ {len(sources)} modules · Pyodide {PYODIDE_VERSION} -->",
    )

    out = WEB / "kindleunpack.html"
    out.write_text(html, encoding="utf-8")

    lib_bytes = sum(len(s.encode("utf-8")) for s in sources.values())
    print(f"built {out.relative_to(ROOT)}")
    print(f"  lib/     {len(sources):>3} modules, {lib_bytes / 1024:.0f} KB of Python")
    print(f"  output   {out.stat().st_size / 1024:.0f} KB")
    print(f"  Pyodide  {PYODIDE_VERSION} (loaded from CDN at runtime)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
