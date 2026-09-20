/*
 * KindleUnpack Web — Pyodide glue.
 *
 * Runs the unmodified Python package in lib/ inside the browser via Pyodide,
 * then packages the resulting output tree as a zip. The Python sources are
 * injected by web/build.py as the LIB_SOURCES global (below/footnote).
 */
(function () {
  'use strict';

  var PYODIDE_VERSION = '314.0.7';
  var INDEX_URL = 'https://cdn.jsdelivr.net/pyodide/v' + PYODIDE_VERSION + '/full/';
  var EXT_OK = ['.mobi', '.prc', '.azw', '.azw3', '.azw4'];

  var $ = function (id) { return document.getElementById(id); };
  var ui = {
    drop: $('drop'), file: $('file'), fileName: $('fileName'),
    run: $('run'), status: $('status'), log: $('log'), pyver: $('pyver'),
    result: $('result'), tree: $('tree'), dl: $('dl'), saveDir: $('saveDir'),
    resultInfo: $('resultInfo')
  };

  var pyodide = null;
  var picked = null;   // { name, bytes: Uint8Array }
  var outZip = null;   // Uint8Array
  var outListing = []; // [{ path, size }]

  /* ---------------------------------------------------------------- log */

  function log(text, cls) {
    var line = document.createElement('div');
    if (cls) line.className = cls;
    line.textContent = text;
    ui.log.appendChild(line);
    ui.log.scrollTop = ui.log.scrollHeight;
  }

  function logBlock(block, cls) {
    String(block).split('\n').forEach(function (l) { log(l, cls); });
  }

  /* --------------------------------------------------------------- boot */

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = src;
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error('无法加载 ' + src)); };
      document.head.appendChild(s);
    });
  }

  async function boot() {
    if (globalThis.__KU_BOOT_OFFLINE__) {
      // Offline build: the whole Pyodide runtime rides along inside this file.
      log('加载内置运行时（离线版，不访问网络）…');
      pyodide = await globalThis.__KU_BOOT_OFFLINE__();
    } else {
      log('加载 Pyodide ' + PYODIDE_VERSION + ' 运行时 …');
      await loadScript(INDEX_URL + 'pyodide.js');

      log('下载 wasm 与标准库（约 10 MB，首次加载较慢）…');
      pyodide = await loadPyodide({ indexURL: INDEX_URL });
    }

    // Python's stdout/stderr are the only progress channel the library has.
    pyodide.setStdout({ batched: function (s) { logBlock(s); } });
    pyodide.setStderr({ batched: function (s) { logBlock(s, 'e'); } });

    var pyver = pyodide.runPython('import sys; "%d.%d.%d" % sys.version_info[:3]');
    ui.pyver.textContent = '· Python ' + pyver + ' · ' + Object.keys(LIB_SOURCES).length + ' 个模块';

    // Mirror lib/*.py into the virtual filesystem as the package `lib`.
    pyodide.FS.mkdirTree('/kindleunpack/lib');
    Object.keys(LIB_SOURCES).forEach(function (name) {
      pyodide.FS.writeFile('/kindleunpack/lib/' + name, LIB_SOURCES[name], { encoding: 'utf8' });
    });

    log('已写入 lib/ 下 ' + Object.keys(LIB_SOURCES).length + ' 个模块');
    pyodide.runPython(
      "import sys\n" +
      "if '/kindleunpack' not in sys.path:\n" +
      "    sys.path.insert(0, '/kindleunpack')\n" +
      "import lib.kindleunpack\n"
    );
    log('lib.kindleunpack 导入成功，可以开始解包', 's');
    ui.pyver.textContent += ' · 就绪';

    ui.run.disabled = !picked;
    ui.status.textContent = picked ? '就绪' : '请先选择文件';
  }

  /* --------------------------------------------------------- file input */

  function acceptFile(f) {
    if (!f) return;
    var lower = f.name.toLowerCase();
    var dot = lower.lastIndexOf('.');
    var ext = dot < 0 ? '' : lower.slice(dot);

    if (EXT_OK.indexOf(ext) < 0) {
      ui.fileName.innerHTML = '';
      ui.fileName.appendChild(document.createTextNode('不支持的文件类型：' + f.name));
      ui.fileName.appendChild(document.createElement('br'));
      var hint = document.createElement('span');
      hint.className = 'meta';
      hint.textContent = '支持 ' + EXT_OK.join(' / ');
      ui.fileName.appendChild(hint);
      picked = null;
      ui.run.disabled = true;
      return;
    }

    var name = f.name.replace(/[\/\\]/g, '_');

    // a fresh file invalidates whatever the previous run produced
    outZip = null;
    outListing = [];
    ui.result.classList.remove('show');
    ui.run.textContent = '开始解包';

    ui.fileName.textContent = '';
    ui.fileName.appendChild(document.createTextNode(name + ' '));
    var meta = document.createElement('span');
    meta.className = 'meta';
    meta.textContent = '· ' + (f.size / 1048576).toFixed(2) + ' MB · 读取中…';
    ui.fileName.appendChild(meta);

    f.arrayBuffer().then(function (buf) {
      picked = { name: name, bytes: new Uint8Array(buf) };
      meta.textContent = '· ' + (f.size / 1048576).toFixed(2) + ' MB · 已就绪';
      ui.run.disabled = !pyodide;
      ui.status.textContent = pyodide ? '就绪' : '正在加载 Pyodide 运行时…';
    });
  }

  ui.drop.addEventListener('click', function () { ui.file.click(); });
  ui.file.addEventListener('change', function () { acceptFile(ui.file.files[0]); });

  ['dragenter', 'dragover'].forEach(function (ev) {
    ui.drop.addEventListener(ev, function (e) {
      e.preventDefault(); e.stopPropagation();
      ui.drop.classList.add('over');
    });
  });
  ['dragleave', 'drop'].forEach(function (ev) {
    ui.drop.addEventListener(ev, function (e) {
      e.preventDefault(); e.stopPropagation();
      ui.drop.classList.remove('over');
    });
  });
  ui.drop.addEventListener('drop', function (e) {
    if (e.dataTransfer && e.dataTransfer.files.length) acceptFile(e.dataTransfer.files[0]);
  });

  /* --------------------------------------------------------------- run */

  var DRIVER = [
    "import os, sys, io, json, zipfile, traceback",
    "sys.path.insert(0, '/kindleunpack')",
    "import lib.kindleunpack as ku",
    "",
    "INFILE = '/work/' + INPUT_NAME",
    "OUTDIR = '/work/out'",
    "",
    "# unpackBook latches these module globals to True and never clears them,",
    "# so without this reset a second run would silently inherit the first run's flags.",
    "ku.DUMP = bool(DUMP)",
    "ku.WRITE_RAW_DATA = bool(RAW)",
    "ku.SPLIT_COMBO_MOBIS = bool(SPLIT)",
    "",
    "os.makedirs(OUTDIR, exist_ok=True)",
    "print('输入 : %s (%.2f MB)' % (INPUT_NAME, os.path.getsize(INFILE) / 1048576.0))",
    "print('选项 : epub_version=%s  split=%s  hd=%s  dump=%s  raw=%s'",
    "      % (EPUBVER, SPLIT, USE_HD, DUMP, RAW))",
    "print('-' * 64)",
    "",
    "try:",
    "    ku.unpackBook(INFILE, OUTDIR, None, EPUBVER, bool(USE_HD), bool(DUMP), bool(RAW), bool(SPLIT))",
    "except Exception:",
    "    # Surface a formatted traceback on stderr; runPython still raises, which",
    "    # is what tells the caller to skip zipping a half-written output tree.",
    "    traceback.print_exc()",
    "    raise",
    "",
    "print('-' * 64)",
    "print('unpackBook 完成，正在打包 …')",
    "",
    "listing = []",
    "for root, dirs, files in os.walk(OUTDIR):",
    "    for f in files:",
    "        full = os.path.join(root, f)",
    "        listing.append({'path': os.path.relpath(full, OUTDIR), 'size': os.path.getsize(full)})",
    "listing.sort(key=lambda x: x['path'])",
    "",
    "buf = io.BytesIO()",
    "with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:",
    "    for item in listing:",
    "        z.write(os.path.join(OUTDIR, item['path']), item['path'])",
    "data = buf.getvalue()",
    "with open('/work/out.zip', 'wb') as fh:",
    "    fh.write(data)",
    "",
    "print('共 %d 个文件，zip 大小 %.2f MB' % (len(listing), len(data) / 1048576.0))",
    "RESULT_JSON = json.dumps(listing)"
  ].join('\n');

  function fmtSize(n) {
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1048576).toFixed(2) + ' MB';
  }

  async function run() {
    if (!picked || !pyodide) return;

    ui.run.disabled = true;
    ui.dl.disabled = true;
    ui.saveDir.disabled = true;
    ui.result.classList.remove('show');
    ui.log.textContent = '';
    ui.status.textContent = '解包中 … 界面会暂时无响应，请勿关闭标签页';

    // The unpack itself is synchronous Python, so it will block this thread.
    // Yield once so the browser actually paints the status above first.
    await new Promise(function (r) { requestAnimationFrame(function () { setTimeout(r, 50); }); });

    var t0 = performance.now();

    try {
      pyodide.runPython("import shutil\nshutil.rmtree('/work', ignore_errors=True)");
      pyodide.FS.mkdirTree('/work');
      pyodide.FS.writeFile('/work/' + picked.name, picked.bytes);

      pyodide.globals.set('INPUT_NAME', picked.name);
      pyodide.globals.set('EPUBVER', $('epubver').value);
      pyodide.globals.set('SPLIT', $('optSplit').checked);
      pyodide.globals.set('USE_HD', $('optHd').checked);
      pyodide.globals.set('DUMP', $('optDump').checked);
      pyodide.globals.set('RAW', $('optRaw').checked);

      pyodide.runPython(DRIVER);

      outListing = JSON.parse(pyodide.globals.get('RESULT_JSON'));
      outZip = pyodide.FS.readFile('/work/out.zip');

      var elapsed = ((performance.now() - t0) / 1000).toFixed(1);
      var total = outListing.reduce(function (a, b) { return a + b.size; }, 0);

      ui.tree.textContent = outListing.map(function (i) {
        return i.path + '  (' + fmtSize(i.size) + ')';
      }).join('\n');
      ui.resultInfo.textContent = outListing.length + ' 个文件 · ' + fmtSize(total) +
        ' · 解包 + 打包耗时 ' + elapsed + ' 秒';
      ui.result.classList.add('show');
      ui.status.textContent = '完成';

      ui.dl.disabled = false;
      ui.saveDir.disabled = false;
      log('完成，用时 ' + elapsed + ' 秒', 's');
    } catch (err) {
      var msg = (err && err.message) ? err.message : String(err);
      logBlock('失败：' + msg, 'e');
      ui.status.textContent = '失败 — 详见日志';
    } finally {
      ui.run.disabled = false;
      ui.run.textContent = '再解包一次';
    }
  }

  /* ------------------------------------------------------------ output */

  ui.dl.addEventListener('click', function () {
    if (!outZip) return;
    var blob = new Blob([outZip], { type: 'application/zip' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = picked.name.replace(/\.[^.]+$/, '') + '.zip';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 30000);
  });

  // Chromium-only progressive enhancement: write the tree straight to disk.
  if (window.showDirectoryPicker) {
    ui.saveDir.style.display = '';
    ui.saveDir.addEventListener('click', async function () {
      var root;
      try {
        root = await window.showDirectoryPicker({ mode: 'readwrite' });
      } catch (e) { return; }  // user cancelled

      ui.status.textContent = '导出中 …';
      ui.saveDir.disabled = true;
      try {
        for (var i = 0; i < outListing.length; i++) {
          var parts = outListing[i].path.split('/');
          var fname = parts.pop();
          var dir = root;
          for (var j = 0; j < parts.length; j++) {
            dir = await dir.getDirectoryHandle(parts[j], { create: true });
          }
          var fh = await dir.getFileHandle(fname, { create: true });
          var w = await fh.createWritable();
          await w.write(pyodide.FS.readFile('/work/out/' + outListing[i].path));
          await w.close();
          ui.status.textContent = '导出中 … ' + (i + 1) + '/' + outListing.length;
        }
        ui.status.textContent = '已导出到所选文件夹';
      } catch (e) {
        log('导出到文件夹失败：' + e.message, 'e');
        ui.status.textContent = '导出失败 — 请改用下载 .zip';
      } finally {
        ui.saveDir.disabled = false;
      }
    });
  }

  ui.run.addEventListener('click', run);

  /* -------------------------------------------------------------- init */

  log('KindleUnpack Web — 基于 kevinhendricks/KindleUnpack v0.84 (GPL-3.0)');
  log('所有处理都在本机浏览器内完成，文件不会上传到任何服务器。');
  log('');
  boot().catch(function (err) {
    log('Pyodide 启动失败：' + err.message, 'e');
    if (!globalThis.__KU_BOOT_OFFLINE__) {
      log('请确认网络可以访问 cdn.jsdelivr.net。', 'e');
    }
    ui.status.textContent = '运行时加载失败';
  });
})();
