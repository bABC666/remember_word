# 2026-09-25 G8 修复与验收：手机设置页横向溢出与保存按钮被底栏遮挡

> 分支：`codex/g8-settings-overflow`（自 `origin/main` = `2798a31` 建立独立 worktree；**未 push、未 merge**）
> 结论：**27/27 项检查通过（exit 0）**。原始数据：`docs/2026-09-25-G8-SETTINGS-OVERFLOW-ACCEPTANCE.json`。
> 真机条件不具备的项目在 §5 如实列为未验收。本轮**未重做**已合入的 OCR 缓存修复、PWA、图片列表或 F-6。

## 1. 根因（实测，不是推测）

用固定布局视口（`mobile: false`，即 390 就是 390，Chrome 不会自动放宽）逐元素测量后，溢出**不是**表单本身造成的，而是「登录设备」区里的一行 User-Agent：

| 元素 | 宽度 | 关键样式 |
|---|---|---|
| `.session-head strong`（UA 字符串） | **799px（min-content）** | `white-space: nowrap; overflow: hidden; text-overflow: ellipsis`，但**没有** `min-width` |
| `.session-head`（flex 行） | 907px | `display: flex` |
| `.session-row` / `.session-list`（单列 grid） | 907 / 945px | 隐式 `auto` 轨道 |
| `SECTION.settings-section` | 945px | 被上述宽度撑开 |
| 文档 | **961px** | 视口 390px |

原因是经典的 flex 默认值：flex item 的 `min-width: auto` 让它**不会收缩**到内容宽度以下，于是 `overflow: hidden` + `text-overflow: ellipsis` 这对声明永远不生效，UA 的 799px 最小宽度顺着每一层单列网格一路上传，把整页撑到 961px。

同一类问题的结构性根源：这些网格轨道用的是**裸 `1fr`**，即 `minmax(auto, 1fr)`，其下限是 min-content；只有 `.session-meta` 在 >620px 时用了 `minmax(0,1fr)`（作者显然知道这个写法）。

**修复前的宽度实测（本轮新增基线，比路线图记录更广）**：

| 视口 | 320 | 375 | 390 | 430 | 620 | 621 | 900 | 901 | 1280 |
|---|---|---|---|---|---|---|---|---|---|
| `scrollWidth`（修复前） | 961 | 961 | 961 | 961 | 961 | **1063** | **1063** | **1236** | 1265（正常） |
| 超宽元素数 | 58 | 55 | 52 | 52 | 49 | 30 | 23 | 23 | 0 |

也就是说 G8 **不只是手机问题**：平板与 901–1235px 的窄桌面同样横向滚动。

## 2. 修复

**① 让长值按设计截断，而不是撑开页面**（`frontend/src/styles.css`）

- `.session-head strong { min-width: 0; }` —— 让这个 flex item 能收缩，已有的 `overflow: hidden; text-overflow: ellipsis` 才真正生效。
- 把所有相关网格轨道从裸 `1fr` 改为 `minmax(0, 1fr)`：`.settings-grid`、`.form-grid`（含 ≤620px 覆盖）、`.session-list`、`.session-row`、`.session-meta`（≤620px 覆盖）、`.data-section dl > div`（`110px minmax(0, 1fr)`）。

**② 保存按钮不再被底栏遮挡**（本轮新发现的第二个 G8 缺陷）

`.settings-save` 是 `position: sticky; bottom: 0`，而 ≤620px 时 `.sidebar` 是 `position: fixed; bottom: 0`、高 64px 的底栏——两者贴在同一条边上，保存栏被压在**底栏下面**。实测（修复前）：

| 视口 | 保存按钮 top–bottom | 底栏 top | 结果 |
|---|---|---|---|
| 320 | 773–815 | 765 | `elementFromPoint` 命中底栏 → **不可点** |
| 375 / 390 / 430 / 620 | 788–830 | 780 | 同上 → **手机上完全无法保存设置** |
| 621 / 900 / 901 / 1280 | 788–830 | 0（左侧栏） | 可点 |

修复：`@media (max-width: 620px) { .settings-save { bottom: calc(64px + var(--safe-area-bottom)); } }`，即把保存栏抬到底栏之上，沿用 F-4 已有的 safe-area 变量。修复后手机档保存按钮位于 724–766、底栏从 780 开始，实测可点（§3.1）。

**改动文件**：`frontend/src/styles.css`、`frontend/src/settingsLayout.test.ts`（新增，4 项契约测试）、`frontend/src/security.test.tsx`（+2 断言）。

**关于截断的可见影响**：UA 行现在在更多宽度下会省略号截断（例如 1280px 下可用宽 745px < 字符串 799px）。这是该规则本来的设计意图，且 `title` 属性已经带着完整 UA（`SettingsPage.tsx` 第 122 行原本就有），因此没有信息丢失；本轮把这个 `title` 用测试固定下来，避免以后被删掉后截断变成"看不见也拿不到"。

## 3. 隔离浏览器验收（27/27，exit 0）

环境：全新临时数据库（`alembic upgrade head`）、临时 uploads 与模型缓存根、随机端口、独立 Chrome 配置、headless Chrome `153.0.8010.54` + CDP；`VOCAB_REAL_DATA_DIR` 指向生产 `data` 以触发应用自身守卫。验收账号建为**管理员**（DeepSeek 区与 OCR 语言/GPU 控件仅管理员可见，否则表单不完整）。运行后临时目录、无头 Chrome、临时 uvicorn 均已清理。

"可达"依旧是**实测**：先 `elementFromPoint` 命中测试，再用 `Input.dispatchMouseEvent` 派发真实鼠标事件；模拟视口每次交互前校验，漂移即报错（本次 `viewport_reapplied = 0`）。

### 3.1 宽度扫描（320 / 375 / 390 / 430 / 620 / 621 / 900 / 901 / 1280）

| 视口 | `scrollWidth` | 超宽元素 | 设置区/表单网格全部在视口内 | 底栏条目 |
|---|---|---|---|---|
| 320 | 320 | 0 | ✅（5 个设置区） | 5 项，56px |
| 375 | 360 | 0 | ✅ | 5 项，67px |
| 390 | 375 | 0 | ✅ | 5 项，70px |
| 430 | 415 | 0 | ✅ | 5 项，78px |
| 620 | 605 | 0 | ✅ | 5 项，116px |
| 621 | 606 | 0 | ✅ | 6 项（图标栏） |
| 900 | 885 | 0 | ✅ | 6 项（图标栏） |
| 901 | 886 | 0 | ✅ | 6 项（桌面侧栏） |
| 1280 | 1265 | 0 | ✅ | 6 项（桌面侧栏） |

- **每个宽度都没有横向溢出，也没有任何元素宽于视口**（修复前 23–58 个）。
- UA 行在每个宽度都被截断且右边界在视口内（320px 下 142px、390px 下 197px、1280px 下 745px，`truncated=true`）。
- **API Key 字段在每个宽度都可聚焦、命中测试为目标、且位于视口内**。
- **软键盘代理**：把视口高度从 844 压到 420（约半个屏幕，键盘占位的近似）时①不产生横向溢出；②聚焦字段滚动后仍位于*视觉视口*内且未被覆盖（每个宽度均 `true`）。见 §5 对"这不是真键盘"的说明。
- **保存按钮在每个宽度都未被导航遮挡**（`elementFromPoint` 命中的就是按钮本身）。
- 手机档 5 个底栏条目全部位于视口内且 ≥44×44；621–1280px 为 6 项且账号区块可达。

### 3.2 设置表单在 390px 下真的可用

| 检查 | 结果 |
|---|---|
| API Key / Model / 每日新词目标 / 启用 GPU / 当前密码 五个字段：可滚动到、命中测试为目标、可获得焦点 | ✅ 全部（每个都附 rect 与 `hitIsTarget`） |
| 真实键盘输入改变「每日新词目标」 | ✅ `value='23'` |
| 点击「保存设置」→ 出现"设置已保存" | ✅ |
| 保存真的落到 API | ✅ `GET /api/settings` 返回 `daily_new_words = 23` |

最后一条尤其重要：修复前该按钮被底栏完全遮住，**这个往返在手机档根本做不成**。

### 3.3 回归（本轮改动不该影响的部分）

| 检查 | 结果 |
|---|---|
| F-6 「更多」仍打开底栏之上的对话框（在设置页上验证） | ✅ `role=dialog`、`aboveBar=true`、`aria-expanded=true` |
| 「更多」→ 使用说明 仍可达并打开帮助 | ✅ |
| 「更多」→ 退出登录 仍可达且真的登出 | ✅ 出现登录表单 |
| F-2 详情返回状态：筛选列表 → 进详情 → 返回 | ✅ 过滤 `search=alpha`（20 行）与滚动位置 **272 → 272** 均恢复 |
| Service Worker 仍接管页面 | ✅ `controller=…/sw.js` |
| 任何 Cache Storage 桶内无 `/api` 条目 | ✅ `[]` |
| 缓存响应体不含账号 A 的数据 | ✅ `[]` |

## 4. 契约测试（jsdom 无法排版，故固定声明）

`frontend/src/settingsLayout.test.ts`（4 项）：UA 行必须同时具备 `min-width: 0` 与省略号三件套；六条设置网格规则必须用 `minmax(0, 1fr)`；**禁止**设置区规则里出现裸 `1fr`/`1fr 1fr`/`repeat(n, 1fr)`；保存栏的手机档 `bottom` 覆盖必须**位于基础规则之后**（媒体查询不增加特异性，顺序写反就会静默失效——F-6 已经踩过一次）。

`frontend/src/security.test.tsx`（+2 断言）：UA 行保留完整值的 `title`，避免截断后信息不可达。

## 5. 明确未验收（真机条件不具备）

| 项 | 为什么未验收 |
|---|---|
| **真实软键盘**（Android Chrome / iOS Safari 弹出后的实际视觉视口、`adjustResize`/`adjustPan` 行为、键盘是否遮住聚焦字段） | headless Chrome 无法弹出真实软键盘。本轮用"把视口高度压到 420px"作代理，只能证明**布局在变矮时不会溢出、且聚焦字段可滚动到且不被遮挡**；真机上键盘的实际高度、滚动时机与 iOS 的视觉视口语义都未验证 |
| iPhone `env(safe-area-inset-bottom)` 的真实像素（保存栏用 `calc(64px + var(--safe-area-bottom))`、底栏同理） | 模拟环境该变量恒为 0px，只验证了 `calc()` 接线 |
| iOS Safari 与 Android Chrome 的实际渲染、触摸目标手感、滚动惯性、动态字体放大、横竖屏切换 | 无可用真机与调试通道 |
| 手机宽度下 `title` 属性的可达性（iOS 无 hover） | 需要真机交互；本轮只固定了属性存在 |
| 其它引擎（Firefox / 桌面 Safari） | 本批只跑了 Chrome |
| 「添加到主屏幕」安装与主屏幕冷启动 | 需要真机；且服务只监听 `127.0.0.1:8000`，手机无法访问，安装验收还缺部署前置 |
| 断点快照与交互回归测试（roadmap T12） | 未实现；本轮是定向断言 + 宽度扫描，不是快照回归 |

## 6. 与路线图的关系

G8 的**两个**缺陷都已修复并有浏览器级证据：①横向溢出（961px → 全部 ≤ 视口）；②手机档保存按钮被底栏遮挡（不可点 → 可点且保存往返成功）。过程中新增的"修复前基线"把 G8 的范围从"390px 手机"扩大到"320–1235px 全部窄视口"，建议路线图按此更新。真机项与 §5 一致，仍属未验收。
