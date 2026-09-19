# KindleUnpack Web

浏览器版的 KindleUnpack。把仓库里 `lib/` 下**未经修改**的 Python 库通过
[Pyodide](https://pyodide.org/) 加载进页面运行，产出与原版命令行完全一致的文件树，
再打包成 zip 交给用户下载。所有处理都在本机完成，文件不上传。

本目录是相对上游 `kevinhendricks/KindleUnpack` 的**唯一新增目录**，`lib/` 等上游文件一个字节未改。
目录布局与发布方案（GitHub Pages）见 `docs/HANDOFF.md` 第 5 节。

## 构建

```bash
python3 web/build.py
```

产出 `web/kindleunpack.html` —— 单文件，约 340 KB，内联了 `lib/` 全部 22 个模块
（306 KB Python 源码）+ UI + 胶水代码。

改 `lib/` 或 `web/app.js` / `web/template.html` 后需要重新构建。

| 文件 | 作用 |
|---|---|
| `build.py` | 构建脚本：内联 `lib/*.py` 与 `app.js` 到 `template.html`，产出 `kindleunpack.html` |
| `template.html` | 页面骨架 + 全部 CSS + UI 结构，含两个注入占位符 |
| `app.js` | Pyodide 胶水层：启动运行时、写虚拟文件系统、调用 `unpackBook`、打包输出 |
| `kindleunpack.html` | **构建产物**，不要直接编辑。已在 `.gitignore` 中，不入库 |
| `docs/` | 项目文档：架构解剖、入场简报、移植方案评估 |
| `README.md` | 本文件 |

## 运行

```bash
python3 -m http.server -d web 8000
# 打开 http://localhost:8000/kindleunpack.html
```

直接双击 `kindleunpack.html` 用 `file://` 打开**可能**也能用（Pyodide 从 CDN 拉取，
jsDelivr 带 `Access-Control-Allow-Origin: *`），但部分浏览器会拦截 `file://` 页面的
跨源 `fetch`，所以推荐用上面的本地服务器。

## 部署

`master` 每次 push 都会触发 `.github/workflows/pages.yml`：CI 里跑 `build.py`，然后一次构建、
两路发布：

- **Pages** —— 产物复制成 `_site/index.html`，连同 `docs/architecture.html` 一起部署。
  在线版：<https://oliviaswitch.github.io/KindleUnpackWeb/>
- **Release** —— 每次构建发一个自己的 release，标题是构建日期，历史版本连同资产都留在
  Releases 页上，不覆盖旧的。新 release 会自动标成 latest，所以下载地址仍然固定，
  永远指向最新构建：
  <https://github.com/OliviaSwitch/KindleUnpackWeb/releases/latest/download/kindleunpack.html>

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
  文件越大越明显。**这是当前最主要的技术债**，正解是把整个 Pyodide 生命周期挪进
  Web Worker。（注意不能简单用 blob URL worker——`file://` 下浏览器会拦截。）
- **依赖网络。** Pyodide 运行时从 CDN 加载，离线不可用。要离线需要把
  `pyodide.asm.wasm` / `python_stdlib.zip` / `pyodide.js` 一起放到 `web/vendor/`
  并把 `INDEX_URL` 指向本地路径。
- **首屏慢。** 首次打开要下载约 10 MB，之后浏览器缓存。
- **`-d` / `-r` 的输出** 会写进输出目录并一起打进 zip，文件数可能很多。
- 没有解包进度百分比。`unpackBook()` 不提供回调，只有 stdout 日志。

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
