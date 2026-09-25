# 2026-09-25 Phase 3 隔离浏览器验收（PWA 外壳与移动端布局）

> 分支：`codex/pr1-ocr-pwa-integration`（**未 push、未 merge**）
> 结论：**24/24 项检查通过（exit 0）**。原始数据：`docs/2026-09-25-browser-acceptance-evidence.json`。
> 真机条件不具备的项目在 §5 如实列为未验收，不计入本次结论。

## 1. 环境与隔离方式

| 项 | 值 |
|---|---|
| 被测代码 | `codex/pr1-ocr-pwa-integration` 的隔离 worktree；前端为 `npm run build` 产物 `frontend/dist` |
| 浏览器 | Chrome `153.0.8010.54`，`--headless=new`，DevTools 协议（WebSocket，`websockets` 17.1） |
| 应用实例 | `uvicorn app.main:app` 单 worker，监听 `127.0.0.1:<随机空闲端口>`（本次运行 65460） |
| 数据库 | **全新**临时库：`VOCAB_DATA_DIR=<临时目录>/data` + `alembic upgrade head`（revision 0007），两个临时账号 |
| 上传 / 模型缓存 | 同一临时目录下的 `uploads/`、`ocr-models/`；**未使用生产上传目录、未触碰生产模型缓存** |
| 生产数据保护 | `VOCAB_REAL_DATA_DIR` 显式指向生产 `data` 目录，让应用自身的隔离守卫继续把生产目录视为禁区 |
| 浏览器配置 | 独立 `--user-data-dir`（临时目录），与用户日常 Chrome 配置完全分离 |
| 驱动脚本 | 仓库外：`C:\Users\丁铭哲\.codex\worktrees\pr1-ocr-pwa\browser_acceptance.py`（故意不放进仓库：它写明真实数据路径，而 `backend/tests/test_static_guards.py` 会扫描仓库内所有 `*.py`） |

复现命令（脚本已含全部准备与清理步骤）：

```powershell
backend\.venv\Scripts\python.exe <worktree 父目录>\browser_acceptance.py <worktree 父目录>\browser-acceptance-report.json
```

运行结束后已确认：临时目录（3 个）、无头 Chrome 进程、临时 uvicorn 全部清理；`frontend/dist/sw.js` 的版本号已还原为 `shici-shell-v1.2.0`；本次运行期间生产实例（`8000` 端口）并未在监听。

## 2. 结果明细

### 2.1 Service Worker 安装 / 激活

| 检查 | 结果 | 观测值 |
|---|---|---|
| worker 安装并进入 `activated` | ✅ | `state=activated` |
| 重新加载后由 worker 接管页面 | ✅ | `controller=http://127.0.0.1:65460/sw.js` |
| 壳缓存写入预期的壳 URL | ✅ | `shici-shell-v1.2.0` → `["/", "/icons/shici-192.png", "/manifest.webmanifest"]` |

### 2.2 版本升级

| 检查 | 结果 | 观测值 |
|---|---|---|
| 新版本 worker 替换旧缓存 | ✅ | 把 `frontend/dist/sw.js` 的 `CACHE_NAME` 临时改为 `shici-shell-v9.9.9`，调用 `registration.update()` 后 `caches.keys()` **只剩** `["shici-shell-v9.9.9"]`——`activate` 已按前缀清理旧版本 |

即：`skipWaiting` + `clients.claim` + `activate` 的前缀清理在真实浏览器里成立。

### 2.3 `/api/**` 永不进入 Cache Storage

| 检查 | 结果 | 观测值 |
|---|---|---|
| 任何 Cache Storage 桶中都没有 `/api` 条目 | ✅ | `[]` |
| 缓存的响应体内不含账号 A 的数据 | ✅ | `[]`（扫描关键词：A 的单词、释义、用户名、显示名） |

第二项是对"缓存里到底存了什么"的直接检查，而不是只看 URL 前缀：把每个缓存条目的响应体读出来做子串扫描。

### 2.4 账号切换与离线

| 检查 | 结果 | 观测值 |
|---|---|---|
| A 能看到自己的单词 | ✅ | `/library` 文本含 `serendipity` |
| **离线重载不重放 A 的词库** | ✅ | 以 A 登录、看过 `/library` 后断网重载：`leaked=[]`，页面回到登录页 |
| B 看不到 A 的任何内容 | ✅ | A 退出、B 登录后 `leaked=[]`（单词、释义、用户名、显示名全不在 DOM 中） |
| B 的词库是空的而不是 A 的 | ✅ | 页面显示"没有找到单词／从单词书导入"空状态 |
| 离线壳仍能渲染 | ✅ | `#root` 有子节点；页面为应用壳 + 登录页 |
| 离线状态不显示 A 的内容 | ✅ | `leaked=[]` |
| `/api` 离线时仍是网络调用 | ✅ | `fetch('/api/words')` → `rejected`，说明没有缓存的 API 应答可回放 |

**离线行为说明（重要，属于设计的一侧）**：离线重载时 Service Worker 提供了应用壳，但会话校验 `GET /api/auth/me` 是网络调用，因此应用显示登录页而不是任何账号的内容。也就是说，离线状态下**壳可用、私有数据不可达**——这正是 `/api/**` 不进缓存想要的结果。

### 2.5 手机详情（F-2）

| 检查 | 结果 | 观测值 |
|---|---|---|
| 390×844 下 `/library/<state_id>` 渲染独立详情面板 | ✅ | `.library-detail-page .word-detail` → `display: block` |
| 详情页显示单词与完整释义 | ✅ | 标题 `serendipity`，正文含"意外发现珍奇事物的能力"、"原书完整释义"、"复习历史"、"文章暴露" |
| 桌面（1280px）保留双栏内联详情 | ✅ | `.library-layout` → `grid-template-columns: 407.25px 497.75px`，内联 `.word-detail` → `display: block`，列表行为 `<button>` |
| 手机（390px）隐藏内联面板并改为跳转独立路由 | ✅ | `.library-layout` → 单列 `388px`，内联 `.word-detail` → `display: none`，列表行为 `a.word-row` 链接 |

这四项合起来就是"F-2 独立详情页可见 + 桌面双栏保留 + 内联面板只在该隐藏的地方隐藏"的浏览器级证据。

### 2.6 导入图片列表（F-3）

| 检查 | 结果 | 观测值 |
|---|---|---|
| 临时批次可上传（用于布局检查） | ✅ | `POST /api/imports` → 200 |
| 390×844 下已保存图片列表可见 | ✅ | `.image-list` → `display: block`；`.import-progress` → 单列 `358px`；渲染 1 张图片 |
| 移除与继续添加控件可达 | ✅ | `button[aria-label^="删除"]` 与 `.add-image-button` 均存在 |

### 2.7 底栏与安全区（F-4 / F-6）

| 检查 | 结果 | 观测值（390×844，模拟） |
|---|---|---|
| 固定底栏接入 safe-area 变量 | ✅ | `.sidebar` → `position: fixed`、`bottom: 0px`、`height: 64px`、`padding-bottom: 7px`；`--safe-area-bottom` → `0px` |
| 内容区预留 = `68px + safe area` | ✅ | `.app-shell` → `padding-bottom: 68px`（模拟环境 safe-area 为 0） |
| F-6 前提可复现：底栏 6 项各约 60px | ✅ | 视口 390px，6 个 `.nav-link` → 每项 **65px** |

## 3. 重要观察（写入 F-6 切片的输入）

1. **手机上「退出登录」不可达（新发现）**：390×844 下 `.sidebar` 占 780–844（高 64px），`.sidebar nav` 占 788–838 正常落在底栏内；但 `.sidebar-account`（含 `button[aria-label="退出登录"]`）位于 **852–888**，整体在视口之下——用户在手机上无法退出登录。本次账号切换检查因此是在 1280px 宽度下完成的。这条既是 Phase 3「手机上真正可用」的缺口，也直接决定 F-6 底栏重排的取舍（账号入口该放哪里）。
2. **帮助入口**：`.help-entry` 在 ≤620px 被固定到右上角（本次测得 `top: 12, bottom: 54`），与底栏无冲突。
3. **safe-area 只验证了接线**：模拟环境 `env(safe-area-inset-bottom)` 恒为 `0px`，所以本次只证明 `calc()` 与变量的接线正确（`68px`、`64px`、`7px`），**没有**证明真机上能避开 Home Indicator。

## 4. 本次未覆盖的浏览器行为

- 没有测真实网络延迟/抖动下的 SW 行为，也没有测 Cache Storage 配额与淘汰。
- 没有测多个标签页同时升级 worker 的情形。
- 没有测 `beforeinstallprompt`、安装横幅或 `appinstalled` 事件。
- 没有测 PWA 从主屏幕**冷启动**（浏览器进程重启后离线打开）。
- 没有测 Firefox / Safari（本次只用 Chrome）。

## 5. 真机条件不具备、明确未验收

| 项 | 为什么未验收 |
|---|---|
| Android Chrome / iOS Safari 真机渲染与触摸操作 | 无可用真机与调试通道 |
| iPhone Home Indicator 的真实避让像素 | 模拟环境 safe-area inset 恒为 0 |
| 「添加到主屏幕」安装、standalone 启动、图标实际显示 | 需要真机；且当前服务只监听 `127.0.0.1:8000`，手机无法访问，安装验收还缺部署前置（监听地址或反向代理） |
| iOS 上 Service Worker 与离线壳行为 | iOS Safari 实现与 Chrome 不同，未验证 |
| 真机竖屏/横屏切换、软键盘弹出时的布局 | 需要真机交互 |
| 断点快照与交互回归测试（roadmap T12） | 未实现，仅做了本次的定向布局断言 |

## 6. 与 F-6 的关系

F-6（≤620px 底栏 6 项拥挤）**本轮仍不做**，作为 Phase 3 的独立后续切片。本次提供的输入是：390px 下每项 65px 的实测值，以及 §3.1 那条"账号/退出入口被挤出视口"的新发现。任何底栏重排都会同时改动 `AppShell.tsx` 的信息架构，需要单独设计与真机验收。
