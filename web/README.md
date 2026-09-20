# KindleUnpack Web

浏览器版的 KindleUnpack。把仓库里 `lib/` 下**未经修改**的 Python 库通过
[Pyodide](https://pyodide.org/) 加载进页面运行，产出与原版命令行完全一致的文件树，
再打包成 zip 交给用户下载。所有处理都在本机完成，文件不上传。

本目录是相对上游 `kevinhendricks/KindleUnpack` 的**唯一新增目录**，`lib/` 等上游文件一个字节未改。
目录布局与发布方案（GitHub Pages）见 `docs/HANDOFF.md` 第 5 节。

## 构建

两个构建，产物不同，互不影响。

```bash
python3 web/build.py          # 轻量版 → web/kindleunpack.html       约 340 KB
python3 web/build_full.py     # 离线版 → web/kindleunpack-full.html  约 17 MB
```

**轻量版**内联 `lib/` 全部 22 个模块（306 KB Python 源码）+ UI + 胶水代码，
运行时从 `cdn.jsdelivr.net` 现拉。**离线版**在此之上把整个 Pyodide 运行时也内嵌进页面，
完全不访问网络（机制见下面「离线版」一节）。

改 `lib/` 或 `web/app.js` / `web/template.html` 后两个都需要重新构建。

`build_full.py` 需要一个解包好的 Pyodide npm 包（`lib/` 那 5 个文件）。查找顺序：
`--runtime-dir` 参数 → 环境变量 `KU_PYODIDE_DIR` → `web/.pyodide-runtime/<版本>/` 缓存 →
仓库内 `node_modules/pyodide` → 从 CDN 下载到缓存目录。已经装过 `npm i pyodide@314.0.7`
的话直接指过去最省事：

```bash
python3 web/build_full.py --runtime-dir node_modules/pyodide
```

| 文件 | 作用 |
|---|---|
| `build.py` | 轻量版构建脚本：内联 `lib/*.py` 与 `app.js` 到 `template.html`，产出 `kindleunpack.html` |
| `build_full.py` | 离线版构建脚本：在轻量版基础上再内联整个 Pyodide 运行时，产出 `kindleunpack-full.html` |
| `template.html` | 页面骨架 + 全部 CSS + UI 结构，含三个注入占位符 |
| `app.js` | Pyodide 胶水层：启动运行时、写虚拟文件系统、调用 `unpackBook`、打包输出 |
| `kindleunpack.html` | **构建产物**，不要直接编辑。已在 `.gitignore` 中，不入库 |
| `kindleunpack-full.html` | **构建产物**，同上 |
| `docs/` | 项目文档：架构解剖、入场简报、移植方案评估 |
| `README.md` | 本文件 |

## 运行

轻量版推荐用本地服务器（直接用 `file://` 打开**可能**也行——Pyodide 从 CDN 拉取，
jsDelivr 带 `Access-Control-Allow-Origin: *`——但部分浏览器会拦截 `file://` 页面的跨源 `fetch`）：

```bash
python3 -m http.server -d web 8000
# 打开 http://localhost:8000/kindleunpack.html
```

**离线版直接双击即可**，不需要服务器，也不需要网络：

```
双击 web/kindleunpack-full.html
```

首次打开要解码约 13 MB 内嵌运行时，会有几秒白屏，属正常。

## 部署

`master` 每次 push 都会触发 `.github/workflows/pages.yml`：CI 里跑两个构建，然后两路发布：

- **Pages** —— 只放轻量版，产物复制成 `_site/index.html`，连同 `docs/architecture.html`
  一起部署。在线用户没必要下 18 MB，所以离线版**不上 Pages**。
  在线版：<https://oliviaswitch.github.io/KindleUnpackWeb/>
- **Release** —— 每次构建发一个自己的 release，标题是构建日期，历史版本连同资产都留在
  Releases 页上，不覆盖旧的。新 release 会自动标成 latest，所以下载地址仍然固定，
  永远指向最新构建：
  - 轻量版 <https://github.com/OliviaSwitch/KindleUnpackWeb/releases/latest/download/kindleunpack.html>
  - 离线版 <https://github.com/OliviaSwitch/KindleUnpackWeb/releases/latest/download/kindleunpack-full.html>

**离线版是可选产物。** 它的构建步骤标了 `continue-on-error`：Pyodide 运行时取不到、
或离线版构建失败时，只发轻量版并在日志里打一条 warning，不会连累 Pages 部署和轻量版发布。
运行时走 `npm install pyodide@<版本>` 取（拿到 registry 的 integrity 校验），
版本号从 `web/build.py` 的 `PYODIDE_VERSION` 现读，`build_full.py` 再核对一次是否一致。

构建产物不入库，站点和下载包都无需人工更新——同步上游后会自动重建。

本地开发流程不受影响，仍然是 `python3 web/build.py` 产出 `web/kindleunpack.html`。

## 工作原理

页面启动时：

1. 从 `cdn.jsdelivr.net` 加载 Pyodide 314.0.7（Python 3.14.2，wasm 约 9.6 MB + 标准库）。
2. 把内联的 `LIB_SOURCES` 逐文件写进 Pyodide 的虚拟文件系统 `/kindleunpack/lib/`。
3. `sys.path.insert(0, '/kindleunpack')` 后 `import lib.kindleunpack`。

解包时：

1. 清空 `/work`，从 JS 侧把用户选的字节写进 `/work/<原文件名>`。
2. 通过 `pyodide.globals.set()` 传入选项，执行 `app.js` 里的 `DRIVER` 脚本。
3. 驱动脚本调用 `ku.unpackBook(...)`，然后用 Python 的 `zipfile` 把 `/work/out`
   整个打包到 `/work/out.zip`，并把文件清单 JSON 回传。
4. JS 用 `pyodide.FS.readFile('/work/out.zip')` 取出字节，做成 Blob 下载；
   浏览器支持 File System Access API 时额外提供"导出到文件夹"。

`lib/` 源码在这里是**只读的研读对象**，Web 版不改动它们一行。

## 已知限制

- **解包期间界面会冻结。** `unpackBook()` 是同步 Python，Pyodide 跑在主线程上，
  无法让出事件循环。UI 会在开始前先重绘一次并显示提示，但处理过程中标签页没有响应，
  文件越大越明显。用户实测后认为可以接受，**不要再为它做改造**——把 Pyodide 生命周期
  挪进 Web Worker 的方案已被否决。（顺带一提，blob URL worker 在 `file://` 下会被拦截。）
- **轻量版依赖网络。** 运行时从 `cdn.jsdelivr.net` 现拉，国内直连基本加载不了。
  离线/无代理场景改用 `build_full.py` 产出的离线版。
- **首屏慢。** 轻量版首次打开要下载约 10 MB，之后浏览器缓存；离线版不下载，但每次
  打开都要在本地解码约 13 MB 内嵌数据，白屏几秒。
- **`-d` / `-r` 的输出** 会写进输出目录并一起打进 zip，文件数可能很多。
- 没有解包进度百分比。`unpackBook()` 不提供回调，只有 stdout 日志。

## 离线版

`build_full.py` 把 Pyodide 运行时整个内嵌进页面。做法不是「把 CDN 地址换成本地目录」——
那行不通：Pyodide 的加载器是用**字符串拼接**构造 wasm 地址的
（`indexURL + "pyodide.asm.wasm"`），所以 `indexURL` 必须是真目录，
而不能是 blob: / data: 这种伪目录。

改用加载器自己提供的四个配置钩子，逐一喂给它：

| 输入 | 钩子 | 做法 |
|---|---|---|
| `pyodide.asm.mjs` | `createPyodideModule` | 构建期转成 classic script 内联。省掉动态 `import()`——`file://` 下加载本地模块会被拦 |
| `pyodide.asm.wasm` | `Module.wasmBinary` + 自定义 `instantiateWasm` | base64 → 字节，完全不取网络 |
| `python_stdlib.zip` | `stdLibURL` | base64 → Blob URL；另有一个 `preRun` 钩子直接把同样的字节写进 Emscripten FS 作保险 |
| `pyodide-lock.json` | `lockFileContents` | 内联成 JS 对象，跳过加载器唯一的 `fetch().json()` |

几处踩过的坑，改这块前先看：

- **胶水是 ESM，但可以机械地转成 classic script。** `pyodide.asm.mjs` 整体就是一个
  `async function _createPyodideModule(moduleArg={}){…}` 加末尾一行 `export default`，
  文件里唯一的 ESM 语法是 3 处 `import.meta.url`。`build_full.py` 按**出现次数**断言后再替换，
  升级 Pyodide 时若形状变了会直接构建失败，而不是产出一个语法错误的页面。
- **别丢掉 `Jsv_*` 两个导入。** 自己写 `instantiateWasm` 就绕过了 `pyodide.js` 的升级逻辑，
  它会往 `imports.env` 里塞 `Jsv_GetError_import` / `JsvError_Check`（来自 pyodide.js 里
  一段 90 字节的 base64 小 wasm）。胶水自带的 `wasmImports` 里这两个只是 `()=>{}` 空桩，
  够 wasm 实例化，但会让 Python 回调里抛出的 JS 异常静默消失。构建期从 pyodide.js 里
  正则提取那段 base64，不硬编码。
- **`data:` URL 有长度上限。** Chromium 把 URL 截在约 2 MB，而 stdlib 的 base64 有 3.4 MB，
  所以 `stdLibURL` 不能用 data: URL，只能用 Blob。
- **`file://` 下能加载的只有 classic `<script>`、CSS 和图片。** `fetch()` 和动态 `import()`
  加载本地子资源都会被拦。整条链路就是围绕这一点设计的。

## 已验证 / 未验证

**端到端已跑通。** 2026-09-19 在浏览器里用真实 `.azw3` 文件实测通过：页面加载、
解包、打包、下载整条链路正常，输出文件树正确。

自动化检查（在 Node + Pyodide，即同一运行时下执行）：

- `lib/` 全部 22 个模块在 Python 3.14 下导入成功，`unpackBook()` 签名正确
  （Python 3.13 移除了 stdlib `imghdr`，仓库自带的 `lib/imghdr.py` 会接管）。
- 驱动脚本能真实执行到 `mobi_sectioner` / `mobi_header`，失败时 traceback
  带正确的 `lib/` 文件行号。
- `zipfile.ZIP_DEFLATED` 在 Pyodide 中可用（15253 字节 → 622 字节）。
- `FS.writeFile` / `FS.readFile` 往返正确，输出以 `PK` 开头。
- 构建产物中 22 个模块的源码与 `lib/*.py` **逐字节一致**。
- 页面里所有被查找的元素 ID 都存在，所有 `ui.X` 都有声明。
- CDN 资产可用：`pyodide.js` 挂载 `globalThis.loadPyodide`，wasm 带
  `Content-Type: application/wasm` 与 `Access-Control-Allow-Origin: *`。

离线版（`build_full.py`）：

- **2026-09-20 用户浏览器实测通过**：完全版可以正常使用。
- 构建期与静态检查：内联的 5 个 `<script>` 块全部通过 `node --check` 语法校验（`lib` 源码、
  `pyodide.js`、转换后的胶水、启动脚本、`app.js`）。
- 内嵌的 `pyodide.asm.wasm`（9.6 MB）与 `python_stdlib.zip`（2.5 MB）解出来与源文件
  sha256 **逐字节一致**。
- 胶水转换后无残留 `import.meta` / `export` 语句，3 处 `import.meta.url` 替换到位。
- 手工核对加载器源码：wasm 声明的 548 个导入（254 `env` / 14 `wasi_snapshot_preview1` /
  280 `GOT.func`）全部由胶水自带的 `wasmImports` 覆盖。
- `build_full.py` 的运行时版本守卫两个方向都验过：版本匹配的 npm 包放行，伪造的版本号拒绝。

**一处未确认的细节**：实测记录没有说清是**双击 `file://` 打开**还是走本地服务器，因此
「`file://` 下 `fetch(blobURL)` 取 stdlib 是否被拦」这条严格说来还没定论。要确认很简单——
用 `file://` 打开后看 DevTools console 里有没有
`Error occurred while installing the standard library`。有的话说明走的是启动脚本里那个
`preRun` 预写入兜底（功能不受影响，但那条分支就是唯一在跑的路径了）；没有则说明 blob 路径正常。
**此时不要关掉兜底**，它俩是互补的。

**尚未覆盖**：

- 只测过 AZW3 一种形态。Mobi7 单机书、Print Replica（`.azw4`）、合体书拆分的
  `-s` 路径、带 FONT/RESC 段的书都还没跑过。
- `-d` / `-r` / `-i` 三个选项和"导出到文件夹"（File System Access API）未测。
- 大文件（数百 MB 的 Print Replica）的内存表现未测。

## 相关文档

| 文档 | 内容 |
|---|---|
| `docs/HANDOFF.md` | 项目入场简报。第 2.1 节是网页版摘要，第 5 节是 GitHub 发布方案，第 7 节按问题类型给出了阅读分流 |
| `docs/architecture.html` | `lib/` 的完整架构解剖（9 节）。浏览器直接打开 |
| `docs/web-port-plan.md` | 纯 JS 重写方案的评估，**未采用**。第 2、5 节按模块列了字节级移植陷阱 |

## 许可

上游 KindleUnpack 是 **GPL-3.0**（见 `COPYING.txt`）。本 Web 版通过 Pyodide 运行
同一份 Python 代码，属于衍生作品，同样以 GPL-3.0 分发。
