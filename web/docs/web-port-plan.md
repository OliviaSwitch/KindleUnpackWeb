# 纯前端重建 KindleUnpack · 可行性评估与实施计划

> **状态：评估完成，方案未采用（2026-09-19）。**
>
> 用户最终选了 Pyodide 路线——把 `lib/` 原样加载进浏览器，而不是这里描述的纯 JS 重写。
> 实际实现见 `web/`，说明见 `web/README.md` 与 `web/docs/HANDOFF.md` 第 2.1 节。
> **已在真实 `.azw3` 上端到端跑通。**
>
> **本文档保留的原因**：第 2 节的移植风险点（尤其 2.1 的 64 位整数语义）和第 5 节的
> 逐模块对照表，是对 `lib/` 可移植性的逐行梳理。将来若要改主意走纯 JS，或只是需要
> 理解某个模块的字节级行为，这份清单比重新读源码快得多。
>
> 另：GPL-3 的约束（第 4 节）对 Pyodide 方案**同样成立**——页面分发的是同一份 GPL-3
> 代码，属于衍生作品。用户已知悉并接受。

---

> 评估对象：`lib/` 全部 22 个模块（7,582 行 Python），不含 GUI。
> 方法：静态阅读 + 逐项核对移植风险点。**未做代码实测**（仓库无样例 `.mobi`）。

---

## 0. 结论

**可行。** 这个项目在架构上恰好是"最适合移植到浏览器"的那一类：零第三方依赖、纯字节级解析、无原生调用、无图像编解码、不处理 DRM。真正困难的只有**一个点**（HUFF-CDIC 解压的 64 位整数语义），其余全部是机械翻译。

主要代价不是"能不能做"，而是**工程量**与**正确性验证**——7,000 行密集二进制解析代码，且仓库里没有任何测试样例。

---

## 1. 为什么可行（支撑证据）

| 事实 | 证据 | 对移植的意义 |
|---|---|---|
| **零第三方依赖** | `lib/` 下只有 stdlib：`os` `sys` `re` `struct` `binascii` `codecs` `zlib` `zipfile` `uuid` `array` `glob` `getopt` `datetime` `xml.sax.saxutils.escape` + 自带的 `imghdr` / `unipath` | 没有 lxml、Pillow、pycrypto 之类的坑，不存在"找不到 JS 对应物"的依赖 |
| **完全不用 XML 解析器** | 全库正则 + 字符串拼装，注释明确写了"Kindlegen 产物常不合法，DOM 会炸" | 这是**移植优势**：同样的理由让你不必和浏览器 DOM 解析器搏斗，正则机械转写即可 |
| **无图像重采样** | 图片按字节原样拷贝；封面缩略靠 SVG `viewBox` | 浏览器端**不需要 canvas / ImageBitmap / WebCodecs**，只需 magic byte 嗅探（`imghdr` 225 行，纯字节比对） |
| **不处理 DRM** | `isEncrypted()` 直接抛异常 | 无需移植任何密码学代码 |
| **二进制解析面窄** | 164 处 `struct`，格式串集中：`>L`×241 `>H`×53 `>2L`×10 `>LL`×9，64 位只有 1 处 | `DataView` 的 `getUint32/getUint16` 覆盖 95% 以上 |
| **输出是文件树 + ZIP** | `unpack_structure.py` 建目录、`zipfile.ZIP_DEFLATED` 打包 | 浏览器端有成熟对应物 |

---

## 2. 六个真实障碍（按风险排序）

### 2.1 64 位整数语义 —— 唯一的硬骨头

**位置：`lib/mobi_uncompress.py` 的 `HuffcdicReader`（131 行文件里最难的 70 行）**

```python
q = struct.Struct(b'>Q').unpack_from        # L59：64 位读取
maxcode = ((maxcode + 1) << (32 - codelen)) - 1   # L71/L80：中间值可达 2^55
code = (x >> n) & ((1 << 32) - 1)           # L111：x 是 64 位
r = (maxcode - code) >> (32 - codelen)      # L124：结果要当数组下标
```

三个 JS 陷阱叠加：

1. **`< 2^53` 边界被突破**。`(maxcode + 1) << (32 - codelen)` 中 `maxcode` 来自 `v>>8`（可达 2^24），`codelen` 最小为 1，故中间值可达 **2^55**。这超出了 JS `Number` 的精确整数范围，**用 double 硬算会得到错误结果**，不是"精度略差"而是"下标完全错位"。
2. **`<< 32` 在 JS 里是 `<< 0`**。`(1 << 32) - 1` 在 Python 里是 `0xFFFFFFFF`，在 JS 里是 `0`。这一行如果直译，掩码直接失效。
3. **`>>` 只对 32 位有符号整数有效**。`x` 是 64 位值，必须走 `BigInt` 的 `>>`。

**方案**：整个 HUFF-CDIC 解码器用 `BigInt` 重写，只在**最终取下标那一步**转回 `Number`（`r` 一定小）。`DataView.getBigUint64()` 直接给出 `BigInt`。

**已存在的解**：见第 3 节——`lingo-reader` 用 `BigInt` 位缓冲实现了同一算法（`bits << 8n | BigInt(byte)` 累加再切片），可以直接对照。

### 2.2 bytes 正则 → JS 正则

全库约 **60 处**正则，几乎全部是字节模式 `re.compile(br'''...''')`（`mobi_html.py` 最密集，21 处）。

**方案**：字节串按 `latin-1` 映射成 JS 字符串（1 字节 → 1 个 UTF-16 码元，双向无损），正则在字符串上跑，结果用 `charCodeAt` 映射回字节。

**必须注意的语义差**：

- Python 字节正则的 `\d` `\s` `\w` 是 **ASCII-only**；JS 正则不加 `u` 标志时**也是** ASCII-only → **恰好一致**，不要手贱加 `u`。
- `re.IGNORECASE` 在 Python 字节模式下只折叠 ASCII 大小写；JS 的 `i` 标志会把 latin-1 高位的 `À`↔`à` 也折叠。实际匹配目标都是 ASCII 标签，风险低，但**不要**把这条当成零风险。
- `re.sub(pattern, repl, string, count)` 的**第 4 个位置参数是 count**；JS 的 `String.replace` 第 3 个参数是 flags。需要一个 `reSub()` 包装层统一处理，否则 `mobi_ncx.py:158/249` 的 `re.sub(re.compile('^', re.M), indent, entry, 0)` 这类调用会静默走样。
- 替换串语法：Python `\1` → JS `$1`。

### 2.3 文本编码

实际只用到三种：`windows-1252`（默认，`codepage=1252`）、`utf-8`（`codepage=65001`）、`latin-1`（`palmname` 与工具函数）。`codec_map` 只有这两个条目（`mobi_header.py:564`）。

`TextDecoder` 全部支持，`{fatal: false}` 语义约等于 Python 的 `errors='replace'`。

**注意**：`compatibility_utils.py:274` 有个 `codecs.register` 给 `cp65001` 打补丁的 hack——那是 Python 特有的，**不需要移植**，直接映射成 `utf-8` 即可。

### 2.4 没有文件系统

读入侧简单（`File` → `ArrayBuffer`）。真正的问题是**中间产物会"写出去再读回来"**：

- `mobi_cover.py:52/72` 写封面后又读回
- `unpack_structure.py:110-125` 从 `imgdir` 读出再搬进 `k8fonts` / `k8images`
- `mobi_k8proc.py:219` 把 `assembled_text.dat` 落盘

**方案**：实现一层内存 VFS（`Map<path, Uint8Array | string>`，提供 `mkdir/write/read/listdir/isfile/isdir`），把 `unipath` + `pathof` 的接口原样顶掉。路径分隔符全部按 POSIX 处理——`unipath`/`pathof` 那 93 行本来就是为了抹平 Windows 路径编码，浏览器里整块删掉。

**最终输出**两条路：
- **目录树** → File System Access API（`showDirectoryPicker`）。**仅 Chromium 系支持**，Firefox / Safari 没有。
- **降级** → `fflate`（~30KB，比 JSZip 小很多）打包成单个 `.zip` 下载。全平台可用。

**建议以 zip 下载为主路径**，目录选择作为 Chromium 的增强。

### 2.5 解压循环的拼接性能

`mobi_uncompress.py` 的两处输出都是逐块追加：

```python
o += i[p:p+c]     # PalmdocReader L34/37/39/47
s += slice        # HuffcdicReader L130
```

Python 里这是可接受的；JS 里 `Uint8Array` 拼接是**每次重新分配 + 全量拷贝**，对 10MB 级正文会退化到 O(n²)，几百 MB 的 Print Replica 会直接卡死。

**方案**：改成分块数组 `chunks.push(subarray)`，最后一次性 `concat`。或者预估大小预分配 growable buffer。

### 2.6 外围工具（低风险，但要记得删）

| 模块 | 行数 | 处置 |
|---|---|---|
| `compatibility_utils.py` | 278 | **整块删除**。PY2/PY3 shim、stdout 编码 hack、Windows `ctypes.windll` 控制台标题——全部无意义 |
| `unipath.py` | 93 | **整块删除**，由内存 VFS 顶替 |
| `imghdr.py` | 225 | **大幅简化**。这是 Python 标准库 `imghdr` 的 vendored 副本（该模块在 Python 3.13 已被移除），但真正用到的只有 magic byte 嗅探，估计 40 行搞定 |
| `mobiml2xhtml.py:42` | — | `open(self.filename, 'r').read()` 是独立工具的入口，**不在主流水线上**，不用移植 |
| `lib/kindleunpack.py` 的 `getopt` / `imghdr.py` 的 `glob` | — | CLI 专用，换成 UI 控件 |

---

## 3. 已有先例（重要的省力发现）

### `@lingo-reader/mobi-parser` — MIT，纯 TypeScript

仓库 `hhk-png/lingo-reader` 的 `packages/mobi-parser`，源码 9 个文件。**已经实现了移植中最难的部分**：

- `book.ts:167` `huffcdic()` —— **用 BigInt 实现**，`bits << 8n | BigInt(byte)` 位缓冲累加（L250），末尾 `& 0xFFFFFFFFn`（L252）
- `decompressPalmDOC()` —— PalmDOC LZ77
- INDX / TAGX 解析、EXTH、NCX、字体提取、编码映射
- 依赖 `fflate` 做 zlib（`unzlibSync`）

**但它不是 KindleUnpack 的替代品**：它是**阅读器**（`loadChapter` 返回处理好的章节对象、资源换成 blob URL），不是**解包器**——**不产出 EPUB 文件树**。它的目标是"在网页上读"，不是"还原成可编辑的电子书工程"。

**怎么用**：当作 **2.1 / 2.2 / 索引层的参考实现**。直接照抄它的 HUFF-CDIC，能省掉整个项目里最凶险的一段调试。许可证也干净（MIT）。

### npm 上的 `kindleunpack` —— **不是 JS 移植**

`kindleunpack@1.0.5` 只是把上游 Python 项目打包发到 npm（包里能看到 `lib/__pycache__/__init__.cpython-39.pyc`），文档里明确说它 **wrap 上游 KindleUnpack**。对纯前端方案**没有参考价值**。

---

## 4. 许可证：这条会改变你的产品形态

`COPYING.txt` 是 **GPL v3**（`main()` 打印的 banner 也明确写了 version 3）。

**移植 = 创作衍生作品 → 你的 JS 版本必须以 GPL-3 发布。** 如果你打算做成闭源产品或商业服务，这条路直接堵死，只能走第 7 节的捷径 B/C。

反向是通的：MIT 的 `lingo-reader` 代码可以吸收进 GPL-3 项目（MIT 与 GPL 兼容）。

---

## 5. 实施计划

### 阶段 0 · 地基（新增，约 600 行）

| 交付物 | 说明 |
|---|---|
| `ByteReader` | `DataView` 封装：`uint8/16/32`、`bigUint64`、`slice()` 返回 `Uint8Array`、`seek` |
| `bytes.js` | `bchr` `bord` `bstr` 对应物；`bytesToLatin1()` / `latin1ToBytes()` 双向无损映射 |
| `regex.js` | Python 正则 → JS 的转写层；`reSub(pat, repl, str, count)` 包装；集中处理 `\1`→`$1` 与 count 语义 |
| `vfs.js` | 内存文件树：`mkdir/write/read/listdir/isfile/isdir` + `fflate` zip 导出 |
| `chunkbuf.js` | 分块字节缓冲（解决 2.5） |

### 阶段 1 · 容器与解压（1,187 行 → 约 1,300 行 JS）

| Python | 行 | 难点 |
|---|---|---|
| `mobi_sectioner.py` | 120 | 无。PalmDB 头 + record 偏移表 |
| `mobi_header.py` | 936 | 篇幅最大但**高度机械**：偏移表 dict + EXTH 映射表。注意 `dumpheader` 里 `'unknown0'` / `'Unknown    '` 重复键的已知缺陷 |
| `mobi_uncompress.py` | 131 | **全项目最难**。见 2.1 |

> **里程碑 A**：复刻 `DumpMobiHeader_v023.py` 的能力——输入一个文件，打印完整 header + EXTH + section 数量 + 压缩类型。这是**第一个可与 Python 原版逐字段对照**的验证点，务必先做到这里再往下走。

### 阶段 2 · 索引（467 行 → 约 500 行 JS）

`mobi_index.py`（INDX/TAGX/IDXT/CTOC）+ `mobi_utils.py`（base32、`mangle_fonts`）。

> **注意 `mobi_utils.py:159`**：`scalelst` 含 `34359738368`（2^35），且后续 `scale = scale * 32` 继续增长。这里**必须用 `*` 和 `+`，绝不能用 `<<` / `|`**。好消息是值域远小于 2^53（一本书的字节偏移最多 2^30 量级），用 `Number` 是安全的。
>
> 同理 `mobi_index.py:159` 的 `(value << 7) | (ord(v) & 0x7f)` 要改写成 `value * 128 + (byte & 0x7f)`。

> **里程碑 B**：能 dump 出完整的 NCX 树 / 骨架表。

### 阶段 3 · 三条重建路径（2,592 行 → 约 2,800 行 JS）

**建议顺序：Mobi7 → KF8 → Print Replica**（由易到难，且 Mobi7 能最早打通端到端）。

- **3a Mobi7**：`mobi_html.py`(453) + `mobi_ncx.py`(275)。核心陷阱`findAnchors()` 必须先收集全部插入点再统一拼装（边扫边插会让后续偏移全失效）——这个约束在 JS 里同样成立，用同样的两趟结构即可。
- **3b KF8**：`mobi_k8proc.py`(496) + `mobiml2xhtml.py`(527) + `mobi_k8resc.py`(271) + `mobi_nav.py`(187)。理解 KF8 的钥匙是 `buildParts()` 的 **FDST 切 flows + 骨架/片段重组**。`buildXHTML()` 的正则改写**顺序敏感**（`kindle:pos` 必须最先），移植时保持原顺序别"优化"。
- **3c Print Replica**：`kindleunpack.py:431` 那段，按 `(offset, length)` 切片，每表第 0 段是 PDF。**最简单但最少见**，放最后。
- `mobi_dict.py`(383) 是独立分支（字典类 MOBI），只在 `processDictionary()` 触发，可以最后单独做。

> **里程碑 C**：Mobi7 输入能产出一个结构完整、能被 calibre 打开的 EPUB。

### 阶段 4 · 输出层（1,694 行 → 约 1,800 行 JS）

`mobi_opf.py`(685)、`mobi_cover.py`(246)、`unpack_structure.py`(167)、`mobi_pagemap.py`(158)、`mobi_split.py`(438)。

大部分是**字符串模板拼装**，机械。两条容易踩空的约束必须原样保留：

- **`rscnames` 编号契约**：下标就是 `recindex` / `kindle:embed` 的引用编号，**不产出资源的段也必须 `append(null)` 占位**。这正是 `mobi_split` 删 `RESC`/`FONT` 时用 `nullsection()`（清空段体、保留槽位）而非 `deletesectionrange()` 的原因。
- **`K8Boundary` 双重身份**：既是"合体书"判定依据，又是资源扫描循环的硬上界。

### 阶段 5 · UI（新增，约 800 行）

拖拽/选择文件 → 选项面板（对应 CLI 的 `-s` / `-i` / `-d` / `--epub_version`）→ 进度条 → 输出。

**Web Worker 是必须的**，不是优化：解压和 K8 重组是 CPU 密集的同步代码，放主线程会冻结 UI。把整个 `lib/` 移植层跑在 Worker 里，主线程只做文件搬运和进度显示。注意 `HuffcdicReader` 的惰性递归 + memoize（`mobi_uncompress.py:126-129`）在 Worker 里同样适用。

---

## 6. 验证策略（决定项目成败的一半）

**仓库里没有任何 `.mobi` 样例文件**，这是最大风险。建议：

1. **建立 golden 输出集**。用 calibre 或 Kindle Previewer 生成覆盖各形态的样本：Mobi7、KF8、合体书（`-s` 路径）、Print Replica、带 FONT 段、带 RESC 段、字典。
2. **用 Python 原版产出参照结果**（本地跑或 Pyodide 跑），存成文件树快照。
3. **逐文件 byte-diff**。JS 版的输出与 golden 树做二进制比对——这个格式的输出是确定性的，diff 必须全绿。
4. **分层卡点，不要等到最后**。里程碑 A/B/C 各做一次 diff。二进制解析的 bug 会向下游传播并伪装成别的问题，越晚定位越贵。

---

## 7. 捷径选项（诚实地说）

如果你要的是"网页版能用"而不是"纯 JS 重写"这个技术目标本身：

| 方案 | 成本 | 代价 |
|---|---|---|
| **A. Pyodide 包一层** | **约 200 行胶水** | 把 `lib/` 原样加载进浏览器跑。首屏 wasm 约 6–10MB，冷启动几秒。**不是纯 HTML/CSS/JS**，但功能 100% 等价——因为就是原版 |
| **B. 用 lingo-reader** | 集成成本极低 | MIT、已实现 HUFF-CDIC + INDX + NCX。但它**只读不出**——没有 EPUB 输出。适合"网页预览器"，不适合"网页版 KindleUnpack" |
| **C. 完整重写** | 见第 8 节 | 唯一能同时满足"纯前端"和"产出 EPUB"的路径 |

**如果目标只是尽快有个能用的网页版，A 是压倒性划算的选择。** 如果目标就是"纯 JS 实现"本身（学习、可控性、体积、无 wasm），才走 C。

---

## 8. 规模与工期

| 指标 | 数值 |
|---|---|
| 待移植核心代码 | 约 7,000 行 Python（7,582 − compat 278 − unipath 93 − imghdr 大部分） |
| JS 产出预估 | **7,000 – 9,000 行**（BigInt 重写、分块缓冲、VFS 会加一些） |
| 新增基础设施 | 约 1,400 行（阶段 0 + 阶段 5） |
| 相对难度分布 | 阶段 1 最险（2.1 一节占大头），阶段 3b KF8 最耗时，阶段 4 最枯燥 |

以一个有经验、且熟悉二进制格式的开发者节奏，**2–4 个月**是现实量级。其中 HUFF-CDIC 和 KF8 骨架/片段重组两处会占掉不成比例的调试时间——但前者有 `lingo-reader` 可对照，后者没有现成参考。

---

## 9. 三个决策点（已决，2026-09-19）

技术上完全可行，架构上没有拦路虎。真正的三个决策点是：

1. **要不要纯 JS** → **不要**。改走 Pyodide，已实现并在真实 `.azw3` 上跑通，见 `web/`。
2. **能不能接受 GPL-3** → **可以**。所以纯 JS 重写在许可上并不受阻，是主动放弃的。
3. **有没有测试样本** → **暂时不建**。用一个自己提供的 `.azw3` 做了端到端验证。
   若要覆盖 Mobi7 单机书 / Print Replica / 合体书拆分，仍需按第 6 节建立 golden 输出集——
   **这条风险并没有消失，只是被推迟了。** 尤其 `KF8` 之外的路径目前完全没跑过。
