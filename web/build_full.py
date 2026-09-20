#!/usr/bin/env python3
"""
Build web/kindleunpack-full.html — the offline variant of the single-file page.

`web/build.py` produces a 336 KB page that pulls its Pyodide runtime from
cdn.jsdelivr.net at runtime, which is exactly what makes it useless behind a
blocked CDN. This build carries the runtime inside the page instead, so it runs
with no network access at all — including straight off the filesystem.

Pyodide reads four files. Each one is handed over through a documented config
hook rather than the `indexURL` directory, so nothing is ever fetched:

  pyodide.asm.mjs    inlined as an ordinary <script>. A build-time transform
                     strips the little ESM syntax the file has — three
                     `import.meta.url` and one `export default` — which is what
                     lets it load from file://, where dynamic import() and
                     fetch() of local files are both blocked.
  pyodide.asm.wasm   base64 -> Module.wasmBinary, plus a replacement
                     instantiateWasm that also restores the two Jsv imports the
                     default path injects (see JSV_WASM_B64 in the page).
  python_stdlib.zip  base64 -> Blob URL for stdLibURL, with a preRun hook that
                     writes the same bytes straight into the Emscripten FS as
                     insurance in case fetching that Blob is refused.
  pyodide-lock.json  inlined as a JS object for lockFileContents, which is the
                     loader's only fetch(...).json() call site.

Usage:  python3 web/build_full.py [--runtime-dir DIR]

Without --runtime-dir the runtime comes from $KU_PYODIDE_DIR, an existing
web/.pyodide-runtime/<version>/ cache, a local node_modules/pyodide, or finally
a download from the CDN.
"""

import argparse
import base64
import datetime
import importlib.util
import json
import os
import pathlib
import re
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent


def _load_light_build():
    """Reuse build.py's paths, version pin and JS-literal escaper.

    Loaded by path rather than by name so an unrelated `build` module on
    sys.path can never shadow it.
    """
    spec = importlib.util.spec_from_file_location("_ku_build", HERE / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build = _load_light_build()
ROOT, WEB, LIB = build.ROOT, build.WEB, build.LIB
PYODIDE_VERSION = build.PYODIDE_VERSION
to_js_literal = build.to_js_literal

RUNTIME_FILES = (
    "pyodide.js",
    "pyodide.asm.mjs",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
)
CDN_BASE = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"

# --------------------------------------------------------------- boot script

# Runs in the page. Defines globalThis.__KU_BOOT_OFFLINE__, which app.js calls
# instead of reaching for the CDN. Kept in ES5-with-async style to match app.js.
BOOT_JS = r"""
(function () {
  'use strict';

  // Pyodide's loader compiles this 90-byte wasm to upgrade two imports that the
  // emscripten glue only stubs out (`()=>{}`). Those stubs are enough to
  // instantiate, but they stop JS exceptions raised inside a Python callback
  // from ever being noticed. Our replacement instantiateWasm skips the loader's
  // own upgrade, so we compile our own copy and inject it.
  var JSV_WASM_B64 = '/*__JSV_B64__*/';

  var LOCK = /*__LOCK__*/;
  var PY_TAG = '/*__PY_TAG__*/';   // "3.14.2" -> "314", i.e. /lib/python314.zip

  function inlineText(id) {
    var el = document.getElementById(id);
    if (!el) throw new Error('内置数据块缺失：' + id);
    return el.textContent.trim();
  }

  function b64ToBytes(b64) {
    if (typeof Uint8Array.fromBase64 === 'function') return Uint8Array.fromBase64(b64);
    // atob() alone hands back one char per byte; the copy out is the slow part,
    // which is why the native decoder above is preferred where it exists.
    var bin = atob(b64);
    var out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  async function loadJsv() {
    var nav = globalThis.navigator;
    var ios = !!nav && (/iPad|iPhone|iPod/.test(nav.userAgent) ||
      (nav.platform === 'MacIntel' && typeof nav.maxTouchPoints !== 'undefined' && nav.maxTouchPoints > 1));
    if (!ios) {
      try {
        var mod = await WebAssembly.compile(b64ToBytes(JSV_WASM_B64));
        // instantiate() on a Module resolves to an Instance, not {module, instance}.
        var inst = await WebAssembly.instantiate(mod);
        return inst.exports;
      } catch (e) {
        if (!(e instanceof WebAssembly.CompileError)) throw e;
      }
    }
    // Same fallback the loader uses when the helper will not compile.
    var marker = Symbol('error marker');
    return {
      Jsv_GetError_import: function () { return marker; },
      JsvError_Check: function (v) { return v === marker; }
    };
  }

  async function bootOffline() {
    var wasmBytes = b64ToBytes(inlineText('ku-wasm-b64'));
    var stdlibBytes = b64ToBytes(inlineText('ku-stdlib-b64'));
    var stdlibUrl = URL.createObjectURL(new Blob([stdlibBytes], { type: 'application/zip' }));
    var jsv = await loadJsv();
    var createModule = globalThis.__KU_CREATE_PYODIDE_MODULE;
    if (typeof createModule !== 'function') {
      throw new Error('内置运行时未加载（pyodide 胶水脚本缺失）');
    }

    var pyodide = await loadPyodide({
      indexURL: './',
      // Keeps packageBaseUrl off the CDN default: nothing calls loadPackage()
      // here, but a future wheel dependency should not silently fetch jsdelivr.
      packageBaseUrl: './',
      lockFileContents: LOCK,
      stdLibURL: stdlibUrl,
      createPyodideModule: function (settings) {
        // Pyodide's own stdlib install still runs and normally wins. This write
        // is the backstop for when fetching the Blob is refused — browsers block
        // local subresource loads on file:// origins, and a failed install is
        // only logged, which would otherwise boot Python with no stdlib at all.
        settings.preRun = [function (mod) {
          try {
            mod.FS.mkdirTree('/lib');
            mod.FS.writeFile('/lib/python' + PY_TAG + '.zip', stdlibBytes);
          } catch (e) {
            console.warn('stdlib pre-write failed', e);
          }
        }].concat(settings.preRun || []);

        // With the bytes in hand nothing has to be fetched from indexURL — which
        // is what makes this work at all: the loader builds its wasm URL by
        // string-concatenating indexURL, so indexURL cannot be a blob or data URL.
        settings.wasmBinary = wasmBytes;
        settings.instantiateWasm = function (imports, success) {
          imports.env.Jsv_GetError_import = jsv.Jsv_GetError_import;
          imports.env.JsvError_Check = jsv.JsvError_Check;
          WebAssembly.instantiate(wasmBytes, imports).then(
            function (result) { success(result.instance, result.module); },
            function (err) { console.warn('wasm instantiation failed!'); console.warn(err); }
          );
          return {};   // empty object == "asynchronous, the callback will fire"
        };
        return createModule(settings);
      }
    });

    URL.revokeObjectURL(stdlibUrl);
    return pyodide;
  }

  globalThis.__KU_BOOT_OFFLINE__ = bootOffline;
})();
"""

# ------------------------------------------------------------ runtime lookup


def _complete(d: pathlib.Path) -> bool:
    return d.is_dir() and all((d / n).is_file() for n in RUNTIME_FILES)


def check_runtime_version(rt: pathlib.Path) -> None:
    """Refuse a runtime that is not the release this build pins.

    Mixing, say, a 314.0.7 loader with an older wasm gets as far as a confusing
    loadPyodide() version error in the browser, several megabytes too late.
    Skip the check for CDN downloads, which ship no manifest — those are pinned
    by the URL instead.
    """
    pkg = rt / "package.json"
    if not pkg.is_file():
        return
    version = json.loads(pkg.read_text(encoding="utf-8")).get("version")
    if version and version != PYODIDE_VERSION:
        raise SystemExit(f"error: {rt} holds pyodide {version}, but this build pins {PYODIDE_VERSION}")


def resolve_runtime(explicit):
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    env = os.environ.get("KU_PYODIDE_DIR")
    if env:
        return pathlib.Path(env).expanduser().resolve()
    cache = WEB / ".pyodide-runtime" / PYODIDE_VERSION
    if _complete(cache):
        return cache
    for cand in (ROOT / "node_modules" / "pyodide", WEB / "node_modules" / "pyodide"):
        if _complete(cand):
            return cand
    return cache  # nothing local; the caller downloads into the cache


def download_runtime(dest: pathlib.Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in RUNTIME_FILES:
        target = dest / name
        if target.is_file():
            continue
        print(f"  downloading {name} …", flush=True)
        with urllib.request.urlopen(CDN_BASE + name, timeout=180) as resp:
            data = resp.read()
        if len(data) < 1024:
            raise SystemExit(f"error: {name} came back suspiciously small ({len(data)} bytes)")
        target.write_bytes(data)


# --------------------------------------------------------------- transforms


def transform_glue(src: str) -> str:
    """Turn the ESM build of the emscripten glue into a classic script.

    The file is one `async function _createPyodideModule(moduleArg={}){...}`
    followed by a single `export default`, and its only other ESM syntax is
    three `import.meta.url` reads. Both are checked for by count so a Pyodide
    upgrade that changes the shape fails the build loudly instead of shipping a
    page that dies on a syntax error.
    """
    seen = src.count("import.meta.url")
    if seen != 3:
        raise SystemExit(f"error: glue has {seen} import.meta.url, expected 3")
    if not src.rstrip().endswith("export default _createPyodideModule;"):
        raise SystemExit("error: glue does not end with the expected `export default`")

    out = src.replace("import.meta.url", "document.baseURI")
    out = out.replace(
        "export default _createPyodideModule;",
        "globalThis.__KU_CREATE_PYODIDE_MODULE = _createPyodideModule;",
    )

    if "import.meta" in out:
        raise SystemExit("error: import.meta survived the transform")
    if re.search(r"(?m)^\s*export\b", out):
        raise SystemExit("error: an ESM export survived the transform")
    if "</script" in out.lower():
        raise SystemExit("error: glue contains </script and cannot be inlined")
    return out


def extract_jsv_b64(pyodide_js: str) -> str:
    """Pull the Jsv helper wasm out of pyodide.js so it cannot drift."""
    m = re.search(r'var [A-Za-z_$][\w$]*=j\("([A-Za-z0-9+/=]{60,})"\)', pyodide_js)
    if not m:
        raise SystemExit("error: could not locate the Jsv helper wasm in pyodide.js")
    b64 = m.group(1)
    raw = base64.b64decode(b64)
    names = set(re.findall(rb"Jsv_GetError_import|JsvError_Check", raw))
    if raw[:4] != b"\x00asm" or len(names) != 2:
        raise SystemExit("error: the Jsv constant does not decode to the expected wasm")
    return b64


def python_tag(lock: dict) -> str:
    """'3.14.2' -> '314', matching the /lib/python<tag>.zip Pyodide installs."""
    major, minor = lock["info"]["python"].split(".")[:2]
    return major + minor


# ------------------------------------------------------------------ assemble


def offline_runtime_block(glue, pyodide_js, wasm_b64, stdlib_b64, lock, jsv_b64, tag) -> str:
    boot = (BOOT_JS
            .replace("/*__JSV_B64__*/", jsv_b64)
            .replace("/*__LOCK__*/", to_js_literal(lock))
            .replace("/*__PY_TAG__*/", tag))

    # Base64 carries no `<`, so these raw-text blocks need no escaping.
    return "\n".join([
        '<script type="text/plain" id="ku-wasm-b64">',
        wasm_b64,
        "</script>",
        '<script type="text/plain" id="ku-stdlib-b64">',
        stdlib_b64,
        "</script>",
        "<script>",
        pyodide_js,
        "</script>",
        "<script>",
        glue,
        "</script>",
        "<script>",
        boot,
        "</script>",
        "",
    ])


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the offline single-file page.")
    ap.add_argument("--runtime-dir", help="unpacked pyodide package to embed")
    ap.add_argument("-o", "--output", help="output path (default web/kindleunpack-full.html)")
    args = ap.parse_args()

    if not LIB.is_dir():
        print(f"error: {LIB} not found", file=sys.stderr)
        return 1

    runtime = resolve_runtime(args.runtime_dir)
    if not _complete(runtime):
        print(f"Pyodide {PYODIDE_VERSION} runtime not found locally; fetching into {runtime} …")
        download_runtime(runtime)
        if not _complete(runtime):
            print("error: runtime still incomplete after download", file=sys.stderr)
            return 1
    check_runtime_version(runtime)
    print(f"runtime  {runtime}")

    sources = {p.name: p.read_text(encoding="utf-8") for p in sorted(LIB.glob("*.py"))}
    if "kindleunpack.py" not in sources:
        print(f"error: no kindleunpack.py under {LIB}", file=sys.stderr)
        return 1

    pyodide_js = (runtime / "pyodide.js").read_text(encoding="utf-8")
    if "</script" in pyodide_js.lower():
        print("error: pyodide.js contains </script and cannot be inlined", file=sys.stderr)
        return 1

    glue = transform_glue((runtime / "pyodide.asm.mjs").read_text(encoding="utf-8"))
    jsv_b64 = extract_jsv_b64(pyodide_js)
    wasm_b64 = base64.b64encode((runtime / "pyodide.asm.wasm").read_bytes()).decode("ascii")
    stdlib_b64 = base64.b64encode((runtime / "python_stdlib.zip").read_bytes()).decode("ascii")
    lock = json.loads((runtime / "pyodide-lock.json").read_text(encoding="utf-8"))

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

    offline = offline_runtime_block(glue, pyodide_js, wasm_b64, stdlib_b64, lock, jsv_b64,
                                    python_tag(lock))

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    html = (template
            .replace("/*__LIB_SOURCES__*/", lib_js)
            .replace("/*__OFFLINE_RUNTIME__*/", offline)
            .replace("/*__APP_JS__*/", app_js))
    html = html.replace(
        "<title>KindleUnpack Web</title>",
        f"<title>KindleUnpack Web</title>\n"
        f"<!-- built {stamp} · offline build · lib/ {len(sources)} modules · "
        f"Pyodide {PYODIDE_VERSION} embedded -->",
    )

    out = pathlib.Path(args.output) if args.output else WEB / "kindleunpack-full.html"
    out.write_text(html, encoding="utf-8")

    lib_bytes = sum(len(s.encode("utf-8")) for s in sources.values())
    print(f"built {out}")
    print(f"  lib/     {len(sources):>3} modules, {lib_bytes / 1024:.0f} KB of Python")
    print(f"  runtime  wasm {len(wasm_b64) / 1048576:.1f} MB + stdlib {len(stdlib_b64) / 1048576:.1f} MB"
          f" + glue {len(glue) / 1024:.0f} KB, all base64-inlined")
    print(f"  output   {out.stat().st_size / 1048576:.1f} MB  (no network access at runtime)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
