# 2026-09-25 接管 GitHub PR #1：OCR 模型缓存修复与 PWA／移动端整合

> 分支：`codex/pr1-ocr-pwa-integration`（自 `origin/main` = `d42d678` 建立独立 worktree）
> 范围：**只在本分支提交**。未 push、未 merge、未对真实模型缓存或生产库运行清理。
> 接手对象：GitHub PR #1（head `codex/ocr-pwa-mobile-20260924` @ `8b7ef15`，merge-base 为 `cc3bf66`）。等待原 PR 作者不是本批工作的前提。

## 1. 为什么先修 OCR 缓存

PR #1 的实现（`backend/app/services/ocr/paddle.py`）在 `_get_engine()` 里、导入 PaddleOCR 之前调用 `repair_incomplete_paddle_model_cache()`：遍历 `official_models/` 下每个目录，凡是不满足「`inference.yml`、`inference.json` 存在且非空 + 至少一个非空 `*.pdiparams`」的目录一律 `shutil.rmtree`。

对照 PaddleX 3.7.2 实际代码（`paddlex/inference/utils/official_models.py`、`paddlex/utils/cache.py`）复核后，有两个会伤到真实用户的缺陷：

**① 可能误删无关目录或正在下载的目录。** PaddleX 的模型宿主（HuggingFace／ModelScope／AIStudio）在 `official_models/<模型名>` **已存在时会直接往该目录里写**（`_HuggingFaceModelHoster._download` 的 `if os.path.exists(save_dir): _clone(save_dir)`），也就是说"目录存在但不完整"恰恰是**下载进行中**的常态。原来的实现会把它删掉，打断另一个进程正在进行的下载；同时它对目录名与内容不做任何限定，`official_models/` 下任何缺少这三个文件的目录（无关目录、别家工具的目录、用户自己放的目录）都会被删除。PaddleX 自己在 `locks/official_models/<sha256>.lock` 用 `filelock` 做跨进程下载互斥，原实现完全没有参考它。

**② 非零但被截断的文件被当作完整缓存。** 原判断只有 `is_file() and stat().st_size > 0`。一次因磁盘写满或被终止而中断的复制会留下**非零前缀**（例如 76 MB 参数文件只剩几 MB），PaddleX 认为缓存命中，随后在加载时失败——正是事故报告里那类"看起来有、其实不能用"的状态，只是原来的判据抓不到它。

### 修复后的判据

只有同时满足以下全部条件的目录才会被删除；任一不满足即**保留并记录原因**（`ModelCacheRepair.skipped`）：

1. 它是 `official_models/` 的**直接子目录**（`locks/`、`temp/`、`func_ret/` 结构上不参与）；
2. 不是符号链接／目录联接（`shutil.rmtree` 穿过链接会删掉目标内容）；
3. 目录名在 PaddleX 官方模型清单内（`ALL_MODELS`，允许 `_safetensors`／`_onnx` 后缀）。**该清单读不到时不做任何删除**——PaddleX 未安装、导入抛错、或清单为空／不可迭代，都视为"未知"，跳过清理并在 `skipped` 里记录原因。名字形状 `[A-Za-z0-9][A-Za-z0-9._-]*` 只用作廉价的前置过滤（避免为明显无关的目录付出导入 PaddleX 的代价），**本身绝不构成删除授权**：按名字形状猜测正是最初实现会误删无关目录的原因；
4. 内容确实是模型材料（空目录算——中断的复制正是留下空目录；含有内容但没有任何模型文件/宿主标记的不算）；
5. **确实不可用**：`inference.json` 必须是能 `json.loads` 且非空的对象；`inference.yml` 必须是能 `yaml.compose_all` 解析出的映射（用 `compose_all` 而不是 `safe_load`，避免 PaddleX 自定义 tag 被误判为损坏）；`*.pdiparams`／`*.safetensors`／`*.onnx` 必须存在且不小于 `1 MiB`（真实 PP-OCRv6 参数为 62 MB 与 76 MB，阈值只用于拦住明显的截断）；
6. 在 HTTP 意义上"空闲"：目录树（含嵌套，深度与条数有界）最新写入时间已超过 `300 s` 宽限窗口；
7. 能拿到 PaddleX 自己的下载锁 `locks/official_models/<sha256(name)>.lock`（`filelock`，非阻塞）；
8. 拿到锁后**再检查一次**可用性——并发下载在这期间补全目录时不会被删。

删除本身是「先原子改名、再删除改名后的路径」：Windows 拒绝改名仍被打开句柄占用的目录，因此正在被使用的缓存不可能被抽走；改名本身即已恢复「未缓存」状态，后续 `rmtree` 只是回收磁盘。拿不到锁或改名失败都只记录原因，不再抛异常中断 OCR 初始化。

### 证据

- `backend/tests/test_ocr_model_cache_repair.py`：**22 项**，全部在 `tmp_path` 的隔离缓存树内构造，覆盖完整缓存、空目录、缺失配置、截断 JSON／YAML／参数文件、无关目录（`uploads/`、`.cache/`）、同级 `locks`／`temp`／`func_ret`、指向别处的目录联接、最近写入、嵌套 `.incomplete` 残留、崩溃残留、被下载锁占用的并发情形（另一线程持锁）、锁释放后重新可修、锁内被补全的竞态、重复清理的收敛性、锁路径与 PaddleX 算法一致、以及"修复动作不越出被交给它的缓存目录"。其中 3 项专门覆盖**清单读不到**时的保守边界：清单为 `None` 时任何名字形状的目录都不得删除且必须记录"无法读取清单"的原因、空清单必须被当成损坏清单而不是"没有模型"、清单导入失败必须返回 `None` 而不是抛异常（用 `sys.modules[...] = None` 制造导入失败）。
- 只读探针（不对真实缓存调用清理）：真实 `data/ocr-models/official_models` 下两个模型目录均判定为**完整**（无误删风险）；`official_model_lock_path()` 计算出的锁文件名与 PaddleX 的 `_official_model_download_lock_path()` 对 4 个模型名**逐一相同**；运行前后真实缓存目录列表不变。
- 本机 PaddleX 可正常导入，因此生产路径上第 3 条闸门走的是"清单可读"分支；"清单不可读"分支只由上述 3 项回归测试与代码审查覆盖，**未在真实缺件环境验证**。

## 2. 整合 PR #1 的前端部分

保留 PR 的实现与意图，逐项复核后整合：

| 项 | 结论 |
|---|---|
| `frontend/public/manifest.webmanifest` | 采用。`display: standalone`、`start_url`／`scope` 为 `/`、`theme_color` 与页面一致 |
| `frontend/public/icons/shici-{180,192,512}.png` | 采用。逐像素比对确认是 `assets/shici-app.png`（1254×1254，741 KB）的机械缩放产物，**未**直接复用桌面启动图 |
| `frontend/public/sw.js` | 采用并收紧：`/api/**` 的判定抽成 `isPrivateApiRequest()`，同时覆盖 `/api` 与 `/api/` 两种拼写，并且该判定位于**第一个 `respondWith` 之前**；缓存写入只在 `isCacheableStaticResource()` 之后发生 |
| `frontend/src/pwa.ts` + `main.tsx` | 采用。仅 production 构建注册，注册失败静默 |
| `frontend/index.html` | 采用。`viewport-fit=cover`、Apple 元信息、manifest 链接、`apple-touch-icon` |
| `frontend/tsconfig.app.json` | 采用。补 `vite/client` 类型（`import.meta.env.PROD` 需要） |
| `frontend/src/styles.css` | **改写后采用**，见下 |
| `README.md`、`docs/PROJECT_*.md` | **改写后采用**，见下 |

### styles.css 的冲突处理（与 main 的 F-2 直接相关）

PR 的分支基线是 `cc3bf66`，而 main 之后已经落地 F-2 手机词库详情独立路由，并把移动端规则从 `.word-detail { display: none; }` 改成 **`.library-layout > .word-detail { display: none; }`**，再由 `.library-detail-page .word-detail { display: block; }` 让 `/library/:wordStateId` 在手机上可见。PR 的补丁是直接**删掉** `.word-detail { display: none; }`，若按原样合并等于把桌面双栏里的内联详情面板也放到手机上显示，破坏 main 已验收的 F-2 行为。

本分支的解决方式：

- **不回退 F-2**：`.word-detail` 的隐藏仍然只作用于 `.library-layout >`，`.library-detail-page` 的显示规则保持；整文件里 `.word-detail { display: none` 只剩**一处**且必须是带前缀的那一处。
- **保留 PR 的 F-3 意图**：删掉 ≤900px 中的 `.image-list { display: none; }`，导入流程在该断点本就是单列，因此手机上可以查看、添加、移除已上传图片。
- **保留 PR 的 F-4 意图**：新增统一变量 `--safe-area-bottom: env(safe-area-inset-bottom, 0px)`，用于底部导航高度/内边距、`.app-shell` 内容预留与 ≤720px 查词浮层 `bottom`。
- 在文件末尾补一段注释，说明这三件事的边界与理由，避免后续又被"顺手"改回去。

`frontend/src/pwa.test.ts`（**13 项**）把上述边界写成断言：SW 只缓存静态资源且 API 判定先于任何 `respondWith`、只有一处 `cache.put`、安装元信息与图标实际像素尺寸（直接读 PNG IHDR）、safe-area 三处用法、**F-2 详情可见性**（隐藏规则唯一且带 `.library-layout >` 前缀，`.library-detail-page` 显示规则存在）、**F-3 手机图片列表**（`.image-list { display: none` 不存在且单列规则存在）。

## 3. 后端静态托管测试

`backend/tests/test_static_hosting.py` 新增一项：`/manifest.webmanifest`、`/sw.js`、`/icons/shici-192.png`、`/icons/shici-180.png` 由构建产物经同一条 SPA 兜底路径公开提供，并断言 worker 文本中存在 `/api` 与 `/api/` 两种拼写、非 GET 与跨源守卫。HTTP 层的 `/api` 边界（未知 API 路径绝不返回应用壳）main 已有专门测试（`backend/tests/test_spa_fallback.py`，18 项中含 `/apiary`、`/api-docs` 反例），本批未重复实现。

## 4. 后续补做的隔离浏览器验收与仍然缺的验收

**2026-09-25 补做**：在隔离环境（全新临时数据库与非生产模型缓存目录、独立端口、独立 Chrome 配置、headless Chrome 153 + CDP）完成了一次真实浏览器验收，**24/24 项通过**，覆盖 Service Worker `install`/`activate`/版本升级与旧缓存清理、`/api/**` 从不进入 Cache Storage（含缓存响应体扫描）、A 退出后 B 登录与离线状态都不出现 A 的内容、手机详情、手机导入图片列表、底栏与 safe-area 接线。明细与未验收清单见 `docs/2026-09-25-PHASE3-BROWSER-ACCEPTANCE.md`，原始数据见 `docs/2026-09-25-browser-acceptance-evidence.json`。

因此下面这些**已不再是缺口**：SW 生命周期与版本切换、`/api/**` 不进缓存、离线壳不重放上一账号数据、F-2 手机详情与桌面双栏、F-3 手机图片列表、F-4 的 calc 接线。

**仍然缺的验收（如实记录）**：

- **手机真机验收**：Android Chrome 与 iOS Safari 上的真实渲染与触摸、iOS 安全区避让的真实像素（模拟环境 `env(safe-area-inset-bottom)` 恒为 `0px`）、「添加到主屏幕」安装与 `display: standalone` 启动、主屏幕冷启动离线、软键盘与横竖屏。
- **其它引擎**：Firefox／Safari 未验证（只跑了 Chrome）。
- **真机不可达的前提**：当前服务只监听 `127.0.0.1:8000`，手机无法访问；要真机验收安装，必须先解决监听地址或反向代理（属部署范围，不在本批）。
- **断点快照与交互测试**（roadmap T12）未做：本批只做了定向布局断言的浏览器检查，没有建立三档断点的快照回归。
- **Phase 3 的整体 DoD／M2 验收**未做。
- **新发现（属 Phase 3 可用性缺口，留给 F-6 切片）**：390×844 下 `.sidebar-account`（含「退出登录」）位于视口下方 852–888，**手机上无法退出登录**；实测底栏 6 项各 65px。详见验收文档 §3。

## 5. 明确另列的后续切片（本批不做）

- **F-6 底部导航重排**：≤620px 下 6 项各 65px（浏览器实测）偏拥挤，且账号/退出入口被挤出视口（见验收文档 §3.1）。需要重新设计（建议 5 项 + 中央加号），改动 `AppShell.tsx` 与信息架构，属独立切片，本批**有意不改**。
- **T11 `.br` 预压缩产物**：为 Phase 4 的 Caddy `precompressed` 准备，独立于本批。
- **PR #1 的其余文档改动**：PR 的 `PROJECT_HANDOFF`／`ROADMAP`／`STATUS` 文本是在 F-2 合并前写的，直接套用会回退 main 的现状；本批按 main 现状逐处改写，而不是整段替换。

## 6. 本批未做（有意）

- 未 `push`、未 `merge`、未创建或修改任何标签；未触碰 `data/`（`vocab.db`、上传图片、模型缓存）内容。
- 未对真实模型缓存运行清理；所有清理验证都在 `tmp_path` 内完成。对真实缓存的唯一动作是**只读**判断。
- 浏览器验收使用全新临时数据库与临时缓存目录；生产数据目录只被**指名**用于触发应用自身的隔离守卫，未被读写。
- 未运行采集/迁移/备份类工具。

## 7. 门禁结果（隔离 worktree，`scripts/check.ps1`）

在 `codex/pr1-ocr-pwa-integration` 的独立 worktree（`C:\Users\丁铭哲\.codex\worktrees\pr1-ocr-pwa\背单词web`）内原样运行 `scripts/check.ps1`：**exit 0，All checks passed.**

| 步骤 | 结果 |
|---|---|
| ruff（backend `app`／`tests`，以及 `tools`） | 全部通过 |
| 后端测试 | **568 passed, 1 skipped**（跳过的是 `test_public_lexicon_preview_guards.py` 的 symlink 用例：本机无创建符号链接权限，main 上同样跳过） |
| 前端测试 | **67 passed（9 个文件）**（main 基线 54，本批 +13：`frontend/src/pwa.test.ts`） |
| 前端 typecheck／lint／build | 全部通过（`vite build` 1752 modules，`dist/` 含 `sw.js`、`manifest.webmanifest`、`icons/`） |
| 测试隔离证明 | `data/` **200 文件，added/removed/changed 均为 none** —— pytest 未触碰真实数据目录 |
| VERIFIED BACKUP | revision `0007`、`integrity ok`、`fk_check 0 violations`，逐表核对为合法增长，判定 **VERIFIED BACKUP** |

后端计数变化：545（main 记录）→ 568，新增 23 项 = `test_ocr_model_cache_repair.py` **22 项** + `test_static_hosting.py` 的 PWA 情形 1 项。

**生产库在整个会话期间逐字节未变**：两次 `check.ps1`（含其间的隔离浏览器验收）报出的 `data/vocab.db` 均为 `sha256 fe99e640274b717d1f2e887ba337be1bc2943223bbdec70a59b75c90f4e67993` / `937984` 字节，逐表计数也完全相同（如 `import_candidate 142`、`user_word_state 60`、`user_session 1`）。浏览器验收使用的是全新临时库。

**注意运行顺序**：`check.ps1` 的第 2 步（后端测试）在第 3 步（前端构建）之前，而 `test_static_hosting.py`／`test_spa_fallback.py` 需要 `frontend/dist` 存在。在**全新 worktree** 里必须先跑一次 `npm run build` 再跑 `check.ps1`，否则这两组测试会因缺少构建产物失败。这不是本批引入的问题，但会影响任何新 worktree 的首次门禁。

## 8. 与 PR #1 文档的关系

PR #1 自带的 `docs/2026-09-24-OCR-PWA-MOBILE-PROGRESS.md`（进度叙述）与 `PROJECT_HANDOFF`／`ROADMAP`／`STATUS_CURRENT` 的改写，写在 F-2 合入 main **之前**：它们把 F-2 记为"未完成"，并声称 ≤900px 导入流程"保留图片列表的查看、添加与移除操作"。本批**没有**整段套用这些文本，而是按 main 现状逐处改写（F-2 保持已完成，F-3/F-4/F-5 按本分支实际状态标注，F-6 明确留作后续切片），并把技术细节收进本文件。PR 原进度文档中仍有效的部分（PWA 元信息清单、图标来源、缓存策略约束）已并入 `README.md`、`docs/PROJECT_ROADMAP.md` 与本文件；其"缓存清理只按文件是否存在判断"的描述已被本批修订覆盖。
