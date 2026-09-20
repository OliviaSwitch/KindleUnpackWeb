# 交接说明 · KindleUnpack 架构研读

> 给新 session 的入场简报。读这一份即可接上进度，不必重新通读 `lib/`（约 7,900 行）。

---

## 1. 背景与工作约定

**任务**：研读 `kevinhendricks/KindleUnpack` 这个 Python 电子书解包库，理解其整体框架逻辑，并产出文档。

**硬性约定**：

- **排除 GUI**。`KindleUnpack.pyw`（Tk 界面、`multiprocessing` 起子进程）与整个 `libgui/` 目录都不在分析范围内。用户明确说过"不要 gui"。分析只覆盖 `lib/` 的库与命令行逻辑。
- **用中文回复**。技术标识符、文件名、代码保持英文原样，不翻译。
- 本地克隆是**研读对象**，不是要修改的上游代码。

**仓库状态**：`/home/epiphany/KindleUnpack`，tag `v084`，HEAD `bf0ca6e "Fix Python 2.x breakage"`，来源 `https://github.com/kevinhendricks/KindleUnpack`。

---

## 2. 已产出物

| 文件 | 说明 |
|---|---|
| `web/` | **Pyodide 网页版**（已跑通）。构建脚本、胶水层、页面骨架、说明文档 |
| `web/docs/architecture.html` | 完整的架构文档，自包含单文件，浏览器直接打开 |
| `web/docs/HANDOFF.md` | 本文件，入场简报 |
| `web/docs/web-port-plan.md` | 纯 JS 重写方案评估。**评估后未采用**，但含逐模块的可移植性陷阱清单，仍有参考价值 |

**所有新增都收敛在 `web/` 一个目录下**，`lib/` 等上游文件一个字节没动。目录布局与理由见第 5 节。

`web/docs/architecture.html` 覆盖 9 节：四种输入→四种输出、五层架构、主流水线 `unpackBook()`、资源段扫描分派表、三条重建路径、六个核心概念、22 模块速查表、输出目录结构、已知缺陷。**要看细节读它，本节只给结论骨架。**

### 2.1 网页版（`web/`，已跑通）

2026-09-19 追加。把 `lib/` 下**未经修改**的 22 个模块通过 Pyodide 314.0.7（Python 3.14.2）
加载进浏览器跑，产出文件树后打包成 zip 下载。**已在真实 `.azw3` 文件上端到端跑通。**

- 构建：`python3 web/build.py` → 单文件 `web/kindleunpack.html`（336 KB，内联全部 Python 源码）
- 运行：`python3 -m http.server -d web 8000`
- 离线版：`python3 web/build_full.py` → `web/kindleunpack-full.html`（约 17 MB，机制见 2.2）
- `web/app.js` 是胶水层，`web/template.html` 是页面骨架，`web/README.md` 是完整说明

**两个非显而易见的坑，已在代码里处理**：

1. `unpackBook()` 把 `DUMP` / `WRITE_RAW_DATA` / `SPLIT_COMBO_MOBIS` 三个模块级 global 锁成 `True` 且**从不重置**——重复运行时必须在调用前显式重置，否则第二次会静默继承第一次的选项。
2. Python 3.13+ 移除了 stdlib `imghdr`，仓库自带的 `lib/imghdr.py` 会接管。Pyodide 用的是 3.14，这条路径已实测。

### 2.2 中国大陆可用性问题（已实现，未实测）

用户指出：**国内用户基本加载不了这个页面**。运行时来自 `cdn.jsdelivr.net`，
不带代理会卡在"下载 wasm 与标准库"这一步。

2026-09-20 用户改了口径，方向收敛为：

- **「同源托管」不做了。** 原方案是把 Pyodide 运行时放站点的同源路径下、`indexURL` 指过去，
  用户明确否掉。Pages 上的在线版继续走 CDN。
- **只做完全版，且形态是单文件 HTML、双击即用。** 另两种形态讨论后都被否掉：
  zip + 本地 HTTP 服务器（普通国内用户起不来服务）、zip + 文件夹（体积与单文件相当，
  却多一层构建期文本转换，得不偿失）。
- release 目前仍只发轻量版一个产物；把完全版接进去是待办，见第 6 节。

需要内联的文件是五个（约 13.5 MB）：

| 文件 | 大小 | 加载方式 |
|---|---|---|
| `pyodide.asm.mjs` | 1.2 MB | JS 胶水，由 `indexURL` + 文件名动态 `import()` |
| `pyodide.asm.wasm` | 9.6 MB | 二进制本体 |
| `python_stdlib.zip` | 2.5 MB | 标准库 |
| `pyodide.js` | 19 KB | 加载器本身，classic script，挂 `globalThis.loadPyodide` |
| `pyodide-lock.json` | 119 KB | 包索引，`loadPyodide()` 默认会 `fetch` 它 |

**2026-09-20 更正——原先这里写的三条"技术前提"有两条是错的，已作废：**

- ❌ 「把 `indexURL` 指向一个 blob/data 目录、三个文件全内联，理论上可行」。
  **不成立**。`URL.createObjectURL()` 生成的形如 `blob:https://host/<uuid>`，而加载器是用
  **字符串拼接**构造 wasm 地址的（`indexURL + "pyodide.asm.wasm"`，见 `xe()`），拼出来的 URL
  在 blob 注册表里查不到，`fetch` 必然失败。data: 同理，且 Chromium 的 URL 长度上限约 2 MB，
  连 stdlib 的 base64（3.4 MB）都塞不下。**`indexURL` 必须是真目录。**
- ❌ 「`findWasmBinary()` 优先走 `Module.locateFile`，是关键路径」。**它根本不在路径上。**
  `createSettings()` 总是塞入 `instantiateWasm`，于是胶水的 `createWasm()` 直接走
  `Module["instantiateWasm"]` 分支，`findWasmBinary()` 一次都不会被调用。原先把一条死路径
  当成了关键路径。

**正确的做法**：绕开 `indexURL`，改用加载器自己提供的四个配置钩子，逐一喂给它——

| 输入 | 钩子 | 做法 |
|---|---|---|
| `pyodide.asm.mjs` | `createPyodideModule` | 构建期转成 classic script 内联，省掉动态 `import()` |
| `pyodide.asm.wasm` | `Module.wasmBinary` + 自定义 `instantiateWasm` | base64 → 字节，完全不取网络 |
| `python_stdlib.zip` | `stdLibURL` | base64 → Blob URL |
| `pyodide-lock.json` | `lockFileContents` | 内联成 JS 对象，跳过唯一的 `fetch().json()` |

三条已核实的关键事实：

1. **胶水会读 `Module["wasmBinary"]`**（`if(Module["wasmBinary"])wasmBinary=Module["wasmBinary"]`），
   所以 wasm 字节可以直接注入，完全不经过 URL。
2. **548 个 wasm 导入（254 `env` / 14 `wasi_snapshot_preview1` / 280 `GOT.func`）全部由胶水自带的
   `wasmImports` 覆盖**，包括 `Jsv_GetError_import` / `JsvError_Check`——但胶水里这两个是
   `()=>{}` 空桩，`pyodide.js` 会用一段 90 字节的 base64 小 wasm 把它们升级成真实现。
   自己写 `instantiateWasm` 时必须顺手补上，否则 Python 回调里抛的 JS 异常会**静默消失**。
3. **`pyodide.asm.mjs` 整体就是一个 `async function _createPyodideModule(moduleArg={}){…}`
   加末尾一行 `export default`**，ESM 语法只有 3 处 `import.meta.url`。可以机械地转成 classic
   script——这一点很关键，因为 `file://` 下动态 `import()` 加载本地模块会被浏览器拦掉。

`cdnUrl` 默认仍指向 jsdelivr，但只有 `loadPackage`（装 wheel 包）会用到；当前场景不装任何包，
所以不触发。已在配置里显式把 `packageBaseUrl` 设成本地，防将来引入依赖时突然要网络。

**已实施**：`web/build_full.py` 产出 `web/kindleunpack-full.html`（约 17 MB）。完整机制、
踩坑清单与验证步骤见 `web/README.md` 的「离线版」一节。**浏览器实测尚未做**，见第 6 节。

---

## 3. 技术知识地图（结论骨架）

### 3.1 一句话概括

用 `Sectionizer` 把文件切成 record → 用 `MobiHeader` 把 record 0 变成元数据 + 索引指针 → 用 `MobiIndex` 把 INDX 变成可读的树/表 → 按书型分三路还原 → 统一汇聚到 `OPFProcessor` 生成 OPF → `fileNames` 落盘并打包。

### 3.2 五层架构

| 层 | 模块 |
|---|---|
| 编排层 | `kindleunpack.py` — 唯一的流程控制中心 |
| 容器/元数据层 | `mobi_sectioner` `mobi_header` `mobi_uncompress` `mobi_split` `mobi_utils` |
| 索引层 | `mobi_index` — 全库复用度最高，NCX/骨架/片段/Guide/字典/页码表全用它 |
| 格式重建层 | `mobi_k8proc` `mobi_html` `mobi_k8resc` `mobiml2xhtml` `mobi_ncx` `mobi_nav` `mobi_pagemap` `mobi_dict` |
| 输出层 | `mobi_opf` `mobi_cover` `unpack_structure` |
| 横切 | `compatibility_utils` `unipath` `imghdr` |

### 3.3 主流水线锚点（`lib/kindleunpack.py`）

| 函数 | 行 | 作用 |
|---|---|---|
| `unpackBook()` | 876 | 总入口：建目录 → `Sectionizer` → `MobiHeader` → 判定书型 → 处理 |
| `process_all_mobi_headers()` | 751 | 逐 MobiHeader：元数据 → DRM 检查 → 资源扫描 → 按类型出口 |
| 资源扫描循环 | 809–854 | 按 record magic 分派，构建 `rscnames` |
| `processMobi7()` | 621 | Mobi7 路径 |
| `processMobi8()` | 470 | KF8 路径 |
| `processPrintReplica()` | 431 | Print Replica 路径 |
| `processUnknownSections()` | 708 | 收尾归类 |

### 3.4 三条路径的分野

- **Mobi7**：`getRawML()` → `ncxExtract.parseNCX()` → `HTMLProcessor.findAnchors()` 把 `filepos=N` 转成 `<a id="fileposN"/>` → `insertHREFS()` 把 `recindex` 转成 `Images/xxx`。
  **关键陷阱**：`findAnchors` 必须先收集全部插入点再统一拼装，边扫边插会让后续偏移全部失效。
- **KF8**：`K8Processor.buildParts()` 做 **FDST 切 flows + 骨架/片段重组**（这是理解 KF8 的钥匙）→ `XHTMLK8Processor.buildXHTML()` 做 `kindle:pos` / `kindle:embed` / `kindle:flow` / `aid` 的正则改写（**顺序敏感，`kindle:pos` 必须最先**）→ `K8RESCProcessor` 从 RESC 段恢复 spine 顺序与 EPUB3 元数据。
- **Print Replica**：rawML 本身是表结构，按 `(offset, length)` 切片，每表第 0 段是 PDF。

### 3.5 六个核心概念

1. **Section ≡ PalmDB record**。record 0 = `16B PalmDOC 前缀 + MOBI header + EXTH + 标题`；所有内部偏移相对 record 0，由 `MobiHeader.__init__` 逐个 `+= self.start` 转绝对段号。
2. **三种压缩**：`1` 无压缩 / `2` PalmDOC LZ77 / `0x4448` HUFF-CDIC（canonical Huffman + 短语字典，`0x8000` 位标记是否已展开，未展开则惰性递归 + memoize）。
3. **Trailer 剥离**：正文记录尾部元数据，变长 base-128 反向扫描，仅 `BOOKMOBI` + `mobi_length ≥ 0xE4` + `version ≥ 5` 启用。
4. **两套位置引用**：Mobi7 用 `filepos=N` / `recindex="N"`；KF8 用 `kindle:pos:fid:<b32>:off:<b32>` / `kindle:embed:<b32>` / `kindle:flow:<b32>`。
5. **INDX / TAGX / CTOC**：`INDX` 头 → `TAGX` 标签表 `(tag, valuesPerEntry, mask, endFlag)` → `IDXT` 条目偏移 → 值区（VWI 变长）。
6. **`usedmap` 三态**：`'used'` / `'not used'` / `'maybe'`；封面缩略图被**故意**标 `not used`。

### 3.6 两条容易踩空的设计约束

- **`rscnames` 编号契约**：它与 section 一一对应，下标就是 `recindex` / `kindle:embed` 的引用编号。**任何不产出资源的段也必须 `append(None)` 占位**，否则后面全部错位。这正是 `mobi_split` 删 `RESC`/`FONT` 时用 `nullsection()`（清空段体、保留槽位）而非 `deletesectionrange()` 的原因。
- **`K8Boundary` 双重身份**：它既是"这是合体书"的判定依据，又是资源扫描循环的硬上界——否则 Mobi7 那一轮会把 KF8 的资源重复提取一遍。

### 3.7 已知缺陷（源码里的真问题，非设计取舍）

| 位置 | 问题 |
|---|---|
| `mobi_k8resc.py:119` | `page-progession-direction` 拼写错误（少一个 `r`），该属性永远读不到，此路径 RTL 检测失效 |
| `mobi_k8proc.py:190` | `aidtext = idtext[12:-2]` 硬编码切片，依赖 aid 文本确切格式 |
| `mobi_k8resc.py:55` | 用 `data.find(b'\x00')` 找 RESC 结尾，正文含 NUL 会静默截断（仅 Warning） |
| `mobi_header.py` | 偏移表里 `'unknown0'` 重复 2 次、`'Unknown    '` 重复 13 次，dict 字面量覆盖导致 `dumpheader` 输出缺项 |
| `mobi_k8proc.py:83` | FDST 表只保留每对起点，相邻 flow 段必须首尾连续 |

**设计取向（不是缺陷，别"修"）**：全库不用 XML 库解析输入（Kindlegen 产物常不合法，DOM 会炸）；无任何图像重采样（封面缩靠 SVG `viewBox`）；不处理 DRM（`isEncrypted()` 即抛异常）；`EPUB3_WITH_NCX` / `EPUB3_WITH_GUIDE` 注释明确写着"不要改成 False"。

---

## 4. 环境约束

- **`Artifact` 工具在本环境必然失败**：会话用 `ANTHROPIC_AUTH_TOKEN` 认证，它优先于 claude.ai 登录。需要文档时**直接写自包含的本地 HTML 文件**（内联 CSS/JS），不要尝试 Artifact 工具或加载 `artifact-design` skill。
- 需要用户本人执行的交互式命令（如登录），建议其用 `! <command>` 在会话内运行。

---

## 5. GitHub 发布方案（已实施）

2026-09-19：本地克隆已 fork 成 `OliviaSwitch/KindleUnpackWeb` 并推送，`master` 领先上游 1 个提交；
Pages 的 workflow 也已写好。剩下的是在 GitHub 上点两下人工开关（见 5.3）。

### 5.1 目标结构

```
KindleUnpackWeb   （fork 自 kevinhendricks/KindleUnpack，默认分支 master）
├── lib/  libgui/  COPYING.txt  README.md …    上游原样，一个字节不动
├── .github/workflows/pages.yml                已建：构建 + 部署 Pages + 发布 release
└── web/                                       唯一的新增顶层目录
    ├── build.py  app.js  template.html  README.md
    ├── .gitignore                             （已加：忽略构建产物 kindleunpack.html）
    └── docs/                                  本目录
```

### 5.2 已定的决策与理由

- **不改上游任何文件**，新增全部落在 `web/` 下，于是 `git merge upstream/master` 永久无冲突。
  唯一被考虑过的破例是根 `README.md` 加一行网页版链接——代价是上游改 README 时冲突一次，**决定不做**；
  可发现性靠 Pages 部署成功后仓库侧栏自动出现的 `github-pages` 环境链接。
- **不用「Deploy from a branch」**。那条通道的文件夹下拉只有 `/ (root)` 和 `/docs` 两个选项；
  而若把 `index.html` 放仓库根再走 `/`，Pages 会把**整个仓库**当网站发布，`lib/*.py` 全部变成
  可访问的网页文件。
- **改用 GitHub Actions 构建 + 部署**。`actions/upload-pages-artifact` 可以只上传构建产物到临时目录，
  仓库根因此保持零新增，构建产物也永不进 git 历史——顺带避免了 `build.py` 每次写入 `datetime.now()`
  时间戳、导致「重建一次就产生 336 KB 无意义 diff」的问题。
- **不做定期自动同步上游**。同步仍手动作，CI 只管构建并在 push 后自动重建。
  上游 `v084` 基本处于静止状态；而定时的自动 merge 一旦遇到冲突会静默失败。

### 5.3 实施时的两个坑

1. **fork 的 Actions 默认关闭。** 要在 Actions 标签页手动点一次
   「I understand my workflows, go ahead and enable them」，否则 workflow 永远不触发。
   这是 fork 上「配好了却怎么都不触发」的头号原因。
2. **Pages 的 Source 必须手动切成「GitHub Actions」。** 默认是「Deploy from a branch」，
   不切过去 `deploy-pages` 会报 Pages not enabled。

### 5.4 一条容易混淆的边界

**「不发布到 Pages」不等于「不公开」。** 公开仓库的 fork 无法设为私有，`web/docs/`（含本文件）
在 GitHub 上任何人都能读到。Actions 只发布构建产物，所以本目录不会出现在站点上，但它仍是公开内容。
要真正不公开，只能不提交。

### 5.5 实施状态

**已完成**：`.github/workflows/pages.yml` 已跑通。分三个 job：`build` 构建并暂存产物，
`deploy` 与 `release` 都 `needs: build`、**并列而非串联**，所以 release 失败不会连累站点部署。
产物经 `actions/upload-artifact` 从 `build` 传给 `release`，保证发布出去的就是部署上线的那些字节
（而不是另起一个 runner 重新构建一遍）。两路发布：

- **Pages** —— 产物复制成 `_site/index.html`，连同 `web/docs/architecture.html` 一起部署
- **Release** —— 每次构建发一个自己的 release（`softprops/action-gh-release`），
  tag 是 `build-<构建日期>-<run_number>`，标题是构建日期。历史版本连同资产一起保留，
  不再往固定 tag 上覆盖。新 release 标 `make_latest`，于是**下载地址固定不变**

`web/build.py` **不需要改**——产物仍写 `web/kindleunpack.html`，本地开发流程不变。

注意 `.github/` 是 `web/` 之外**唯一**的新增目录，这是 GitHub 的硬性要求（workflow 只能放这里），
不构成对隔离约定的破坏。`contents: write` 只授予 `release` job，workflow 其余部分仍是只读。

**离线版已接进 CI**（2026-09-20，在用户浏览器实测通过之后）：

- `build` job 多两步：先 `npm install pyodide@<版本>` 取运行时——版本号从 `web/build.py` 的
  `PYODIDE_VERSION` 现读，不走 CDN 是为了拿到 registry 的 integrity 校验，且与本地开发
  同一条来源——再跑 `build_full.py --runtime-dir`。两步都标了 `continue-on-error: true`：
  运行时取不到、或离线版构建失败时只发轻量版并打一条 warning，**不连累 Pages 部署和
  轻量版发布**。
- 两个产物先收进 `dist/` 再经 `upload-artifact` 传给 `release`，保证发布出去的字节与
  构建产出的完全一致。
- `release` job 的文件清单用 `ls -1 *.html` 现算，写死的话离线版缺席时 action 会报错。
- **离线版不上 Pages**：在线用户用轻量版就够了，没必要让他们下 18 MB。

`build_full.py` 另外加了一道运行时版本守卫：运行时目录里若带 `package.json` 且版本与
`PYODIDE_VERSION` 不符，直接构建失败。混用版本的报错本来要到浏览器里 `loadPyodide()` 才
冒出来，那时已经晚了好几 MB。

固定地址：

- 在线版 <https://oliviaswitch.github.io/KindleUnpackWeb/>
- 下载 <https://github.com/OliviaSwitch/KindleUnpackWeb/releases/latest/download/kindleunpack.html>

---

## 6. 未完成 / 可能的下一步

本 session 没有做的事，供参考：

- **架构结论仍全部来自静态阅读**。第 3 节的每一条都是读代码得出的，没有靠运行验证过。
- **代码本身现在跑过了，但只覆盖一种形态**。`web/` 网页版在真实 `.azw3` 上端到端跑通（2026-09-19），这验证了 AZW3（KF8）这条路径。**仍未测**：Mobi7 单机书、Print Replica（`.azw4`）、合体书拆分的 `-s` 路径、带 FONT / RESC 段的书，以及 `-d` / `-r` / `-i` 三个选项。测试样本是用户自己提供的，不在仓库里。
- **未深入的两个点**（用户被问过是否要继续往下钻，尚未回答）：
  - KF8 骨架/片段重组算法（`mobi_k8proc.buildParts` 的插入点推进与容错分支）
  - INDX/TAGX 的位级解码（`mobi_index.getTagMap` 的控制字节 + mask + VWI 逻辑）
- **未覆盖**：`mobi_dict.py` 的变形规则状态机（`applyInflectionRule`）只做了概述；`DumpMobiHeader_v023.py` 这个独立工具只扫了一眼。
- **`web/docs/architecture.html` 未发布**成可分享链接（见第 4 节的认证限制；发布方案见第 5 节）。
- **离线版已实测通过、已接进 CI**（见 2.2 与 5.5）。只剩一个小口子：实测记录没说清是
  **双击 `file://` 打开**还是走本地服务器，所以「`file://` 下 `fetch(blobURL)` 取 stdlib 是否
  被拦」严格说来还没定论。想确认就看 `file://` 打开后 console 里有没有
  `Error occurred while installing the standard library`——有的话说明跑的是 `preRun` 预写入
  那条兜底。**无论结果如何都不要删兜底**，两条路是互补的。
- **界面冻结不是问题**（用户实测），别再为它做改造。

---

## 7. 如何继续

新 session 开始时如果有具体问题，直接问即可——上面的知识地图 + `web/docs/architecture.html` 足以支撑绝大多数追问，不需要重读源码。只有在需要**核对具体行号或未覆盖模块**时，才去读 `lib/` 下对应文件。

按问题类型分流：

- **网页版相关**（怎么构建/运行、界面为什么冻结、怎么改 UI、怎么加选项）→ 先读 `web/README.md`，它有完整的构建、运行、限制说明。改 `web/app.js` 或 `web/template.html` 后记得重跑 `python3 web/build.py` **和** `python3 web/build_full.py`。
- **离线版 / Pyodide 运行时怎么内联** → `web/README.md` 的「离线版」一节，那里列了四个配置钩子和五个踩坑点，比重新读 `pyodide.js` 快得多。
- **"某个模块能不能移植到别的语言"** → 读 `web/docs/web-port-plan.md` 第 2、5 节，那里按模块列了字节级陷阱（64 位整数、bytes 正则、编码、文件名），比重新读源码快。
- **`lib/` 本身的逻辑** → 下面的阅读顺序。

若要核对某条结论，建议的阅读顺序（按依赖关系，不是按重要性）：

```
mobi_sectioner.py  →  mobi_header.py  →  mobi_uncompress.py
                   →  mobi_index.py
                   →  mobi_k8proc.py  →  mobi_html.py   （KF8 线）
                   →  kindleunpack.py （把上面串起来）
```
