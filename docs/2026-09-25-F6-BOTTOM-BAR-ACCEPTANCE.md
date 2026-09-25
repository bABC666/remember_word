# 2026-09-25 F-6 手机底栏验收（五入口 + 可达的「更多」）

> 分支：`codex/f6-mobile-bottom-nav`（自 `origin/main` = `93da75a` 建立独立 worktree；**未 push、未 merge**）
> 结论：**32/32 项检查通过（exit 0）**。原始数据：`docs/2026-09-25-F6-BOTTOM-BAR-ACCEPTANCE.json`。
> 真机条件不具备的项目在 §5 如实列为未验收，不计入本次结论。

## 1. 本轮改动

| 项 | 内容 |
|---|---|
| 底栏入口 | ≤620px 由 6 项改为 **5 项**：今日概览 `/`、今日学习 `/study`、阅读练习 `/reading`、我的词库 `/library`，第 5 项是「更多」按钮 |
| 「更多」面板 | 底部弹层（`role="dialog"`、`aria-modal`、`aria-labelledby`），内含 **单词导入** `/import`、**设置** `/settings`、**使用说明**（帮助对话框）、**退出登录**，并显示当前账号 |
| 优先修复 | 账号区块（含唯一的「退出登录」）在 ≤620px 改为 `display: none`，退出登录移入「更多」；实测退出按钮在弹层内 340×48、完全位于视口内 |
| 顺带修复 | 底栏图标项在 ≤900px 时标签为 `display: none`，原本**没有任何可访问名称**；现在每项显式带 `aria-label` |
| 保留不变 | 621–900px 仍是左侧图标栏（6 项 + 账号区块 + 帮助入口），≥901px 桌面侧栏完全不变 |
| 保留边界 | 详情返回状态（F-2）、账号隔离、`/api/**` 永不进入 Service Worker 缓存 |

改动文件：`frontend/src/components/AppShell.tsx`、`frontend/src/components/HelpCenter.tsx`（新增 `openRequest` 让弹层能打开帮助对话框）、`frontend/src/styles.css`、新增 `frontend/src/components/AppShell.test.tsx`。

### 1.1 过程中发现并修掉的 CSS 顺序缺陷

第一次实现把 `.sidebar-account { display: none; }` 写进第 91 行的 `@media (max-width: 620px)` 块，但基础规则 `.sidebar-account { display: flex }` 在**第 109 行**（680 行之后）——媒体查询不增加特异性，因此**后面的基础规则胜出**，账号区块仍然留在底栏里。这正是"退出登录被挤出视口"的机制之一：只要规则顺序变化，隐藏就会静默失效。

修正方式是把该规则放到文件末尾的 `@media (max-width: 620px)` 块中，并加注释说明顺序约束；同时新增回归测试断言 `display: none` 出现的位置**晚于**基础规则。浏览器验收在修正前后各跑了一次，修正前该检查项为 FAIL、修正后 PASS（两轮都在报告与本节留有记录）。

## 2. 环境与隔离方式

| 项 | 值 |
|---|---|
| 被测代码 | `codex/f6-mobile-bottom-nav` 的隔离 worktree；前端为 `npm run build` 产物 `frontend/dist` |
| 浏览器 | Chrome `153.0.8010.54`，`--headless=new`，DevTools 协议（`websockets` 17.1） |
| 应用实例 | `uvicorn app.main:app` 单 worker，`127.0.0.1:<随机空闲端口>` |
| 数据 | **全新**临时库（`VOCAB_DATA_DIR=<临时目录>/data` + `alembic upgrade head`），两个临时账号；账号 A 预置 40 个词条（`alpha01…20`、`beta01…20`）以便测试筛选与滚动 |
| 生产数据保护 | `VOCAB_REAL_DATA_DIR` 显式指向生产 `data`；同目录下的 `uploads/`、`ocr-models/` 均为临时目录 |
| 其它 | 独立 Chrome `--user-data-dir`；驱动脚本在仓库外（`f6_acceptance.py`，写明真实数据路径，不能放进会被 `test_static_guards.py` 扫描的仓库内） |

**"可达"的判定方式**：不使用 `element.click()`。每个点击先用 `elementFromPoint` 命中测试（必须解析到目标或其子节点），再用 CDP `Input.dispatchMouseEvent` 派发真实鼠标事件，因此**被遮挡或位于视口外的元素会真的失败**。同时每次点击都记录并校验模拟视口（见 §4）。

## 3. 结果明细

### 3.1 断点扫描（每档都重新加载页面后测量）

| 宽度 | 底栏条目 | 单项宽度 | 侧栏定位 | 账号区块 | 帮助入口 |
|---|---|---|---|---|---|
| 360px | **5** | 67px | fixed | none | none |
| 390px | **5** | 73px | fixed | none | none |
| 414px | **5** | 78px | fixed | none | none |
| 620px | **5** | 119px | fixed | none | none |
| 621px | 6 | 57px | sticky | flex（视口内） | flex |
| 720px | 6 | 57px | sticky | flex（视口内） | flex |
| 900px | 6 | 57px | sticky | flex（视口内） | flex |
| 901px | 6 | 193px | sticky | flex（视口内） | flex |
| 1280px | 6 | 193px | sticky | flex（视口内） | flex |

对应检查（全部 PASS）：

- ≤620px 恰好 5 项且存在「更多」；621–1280px 恰好 6 项且**没有**「更多」。
- ≤620px 每个底栏条目的矩形**完全位于视口内**（360/390/414/620 四档，越界清单均为空）。
- ≤620px 每个条目 ≥44×44 触控目标（实测最小 67×50 @360px）。
- 账号区块在 ≤620px 为 `display: none`；在 621–1280px 为可见且**位于视口内**（top 分别为 539/539/539/503/503）。
- 帮助入口在 ≤620px 隐藏、621px 起可见。

### 3.2 触控目标与横向溢出

- ≤620px 五个条目最小触控尺寸 **67×50 @360px**、**73×50 @390px**（对比修复前：6 项各约 65px，且账号区块溢出视口）。
- 390px（固定布局视口）下本批涉及页面**无横向溢出**：`/` 375、`/study` 375、`/reading` 390、`/library` 375、`/import` 390，均 ≤ `innerWidth`。

### 3.3 五个入口都能真正导航（真实点击 + 命中测试）

| 入口 | 结果 | 实测（390×844） |
|---|---|---|
| 今日概览 → `/` | PASS | 73×50，center(46.6, 813)，`hitIsTarget=true` |
| 今日学习 → `/study` | PASS | 73×50，center(120.8, 813)，`hitIsTarget=true` |
| 阅读练习 → `/reading` | PASS | 73×50，center(195.0, 813)，`hitIsTarget=true` |
| 我的词库 → `/library` | PASS | 73×50，center(269.2, 813)，`hitIsTarget=true` |
| 更多 → 弹层 | PASS | 73×50，center(343.4, 813)，`hitIsTarget=true` |

### 3.4 「更多」弹层

| 检查 | 结果 | 实测 |
|---|---|---|
| 弹层是 `role="dialog"`、由 `aria-labelledby` 命名 | PASS | `role=dialog`、`labelledby=mobile-more-title` |
| 弹层**在底栏之上**且完全位于视口内 | PASS | `aboveBar=true`、`insideViewport=true`、`aria-expanded=true` |
| 单词导入可达并跳转 | PASS | 340×48 @ (195, 515)，`hitIsTarget=true`，落到 `.import-page` |
| 设置可达并跳转 | PASS | 340×48 @ (195, 565)，`hitIsTarget=true`，落到 `.settings-section` |
| 使用说明可达并打开帮助对话框 | PASS | 340×48 @ (195, 615)，`hitIsTarget=true`，`.help-dialog` 出现（`dialogs=["help-title"]`）、弹层随即关闭 |
| 退出登录可达 | PASS | 340×48 @ (195, 669)，`hitIsTarget=true` |
| 弹层内退出登录**真的登出** | PASS | 出现登录表单 `form[aria-label="登录拾词"]` |

### 3.5 修复前后对比（同一驱动脚本）

| 检查 | 修复前 | 修复后 |
|---|---|---|
| ≤620px 条目数 | 5 | 5 |
| 账号区块在手机档 | **flex（FAIL）** | none（PASS） |
| 打开弹层前可见的「退出登录」数量 | 1（在溢出的底栏里） | **0** |
| 手机档退出登录可达性 | 位于视口下方 852–888（视口 844）→ 不可达 | 弹层内 340×48、视口内（PASS） |

### 3.6 保留的边界（回归）

| 检查 | 结果 | 实测 |
|---|---|---|
| SW 仍接管页面 | PASS | `controller=…/sw.js` |
| 任何 Cache Storage 桶内都没有 `/api` 条目 | PASS | `[]` |
| 缓存响应体不含账号 A 的数据 | PASS | `[]`（扫描 `alpha01`、用户名、显示名） |
| 详情返回状态（F-2）：筛选后进详情再返回 | PASS | 筛选 `search=alpha` → 20 行 → 进详情 → 返回后 `search=alpha` 保留 |
| 详情返回滚动位置 | PASS | 返回前 272 → 返回后 **272** |
| A 能看到自己的词 | PASS | 页面含 `alpha01` |
| 离线重载不重放 A 的内容 | PASS | `leaked=[]` |
| B 登录后看不到 A 的任何内容 | PASS | `leaked=[]` |
| B 的词库是空的而不是 A 的 | PASS | 空状态 |
| 五入口底栏在 B 账号下同样成立 | PASS | `entries=5, hasMore=true` |

## 4. 模拟视口漂移：一次假通过被抓住

驱动最初只记录测量值，未校验模拟视口。在 `/settings` 上 `Emulation.setDeviceMetricsOverride(390×844, mobile)` 会被 Chrome **放宽到 961×2080**（见 §5.1 的成因），于是那一步实际是在"桌面宽度"下测量，却仍然报 PASS——**这正是验收最该防住的假通过**。

处理方式：每次点击前先校验 `window.innerWidth/innerHeight` 是否等于请求的档位；不等则重新施加一次，仍不等就**直接报错终止**，而不是继续测量错误布局。修正后整个运行 `viewport_reapplied = 0`（即不再发生漂移，因为帮助检查前已离开 `/settings`），并且每一条点击证据都带 `viewport` 字段可供核对。

## 5. 过程中测得的新发现（本批不修，建议另立切片）

### 5.1 手机宽度下设置页横向溢出（既有问题，本批未触碰）

390px 固定布局视口下 `/settings`：`innerWidth=390`、`clientWidth=375`、**`scrollWidth=961`**，即内容约为视口宽度的 2.5 倍，手机上必须横向滚动才能看到整页。溢出元素按宽度排序：

| 元素 | 宽度 | 左/右 |
|---|---|---|
| `SECTION.settings-section`（3 个） | 945px | 16 → 961 |
| `SECTION.settings-section data-section` | 945px | 16 → 961 |
| `HEADER` / `DIV.form-grid` / `LABEL` / `INPUT` | 907px | 35 → 942 |

这就是 §4 里移动模拟被迫放宽布局视口的原因。**该元素由本批未修改的规则定义**（`styles.css` 第 81 行 `.settings-grid`／`.settings-section`，本批 diff 只改底栏、帮助入口与新增弹层规则），因此是 main 上的既有缺口，属 Phase 3「手机上真正可用」的后续项（也解释了为什么设置页在手机上体验很差）。本批未改，以免把 F-6 的改动混入设置页布局。

### 5.2 其它观察

- ≤620px 底栏只显示图标（标签在 ≤900px 被隐藏，原设计如此）。本批未改视觉，只补了 `aria-label`；如果后续希望「更多」更易被发现，可考虑在 5 项布局下显示短标签（不属 F-6 范围）。
- 帮助入口在手机上从"右上角固定按钮"改为"仅在「更多」内"，与需求一致；onboarding 首次使用对话框不受影响（验收中已把种子账号的 `onboarding_seen` 置为真，以免遮挡布局检查）。

## 6. 明确未验收（真机条件不具备）

| 项 | 为什么未验收 |
|---|---|
| Android Chrome 真机：底栏与「更多」弹层的实际触摸手感、滚动、软键盘 | 无可用真机与调试通道 |
| iOS Safari 真机：`env(safe-area-inset-bottom)` 的真实像素避让（模拟环境恒为 0px，只验证了 `calc()` 接线） | 同上；弹层底部是 `calc(72px + var(--safe-area-bottom))`，真机像素未验 |
| 「添加到主屏幕」安装、standalone 启动、主屏幕冷启动离线 | 需要真机；且服务只监听 `127.0.0.1:8000`，手机无法访问，安装验收还缺部署前置 |
| 其它引擎（Firefox／Safari 桌面） | 本批只跑了 Chrome |
| 横竖屏切换、动态字体放大、超长账号名在弹层内的换行 | 需要真机交互 |
| 断点快照与交互回归测试（roadmap T12） | 未实现；本批是定向断言 + 断点扫描，不是快照回归 |

## 7. 与路线图的关系

F-6 本轮**完成**：五入口 + 可达「更多」、优先修复的退出登录可达性均有浏览器级证据（§3）。§5.1 的设置页横向溢出是**新发现**，与本批改动无关，建议作为 Phase 3 的后续切片单独处理；本轮不改，也不把它的修复混进 F-6 的提交。
