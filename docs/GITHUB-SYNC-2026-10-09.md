# 2026-10-09 GitHub 源码同步与 PR #1 合流

## 范围与基线

- 本地起点 `fcca4446dd113fb26db41ddb96dd3d3b37074805`，远端 main `0c0b2616d95439ee84b637e478ff6accf234c8c4`。
- 本地独有 52 个提交，远端独有 5 个 OCR CPU/推理并发/部署提交；双方历史均保留，不强制推送、不重写已有历史。
- PR #1 原头 `8b7ef15288331a6d3b58a0dd06a50d04b027daf4`，原题为“修复 OCR 模型缓存并推进 PWA 与移动端适配（含进度报告）”。
- 用户授权更新介绍并解决 PR 合并冲突。同步在 Codex 管理的隔离工作目录完成，复用既有依赖，仅在隔离目录构建前端。
- 四份原有未跟踪历史草稿不纳入提交；不写正式数据库、不执行生产迁移、不重启正式服务，也不发布最新词库候选。

## 冲突取舍

PR 原版本的功能已在 main 后续开发中分别实现并加固，但提交历史没有合流，因此出现 9 处冲突：

| 文件 | 保留的实现与理由 |
| --- | --- |
| README.md | 更新后的导入、学习、释义来源及部署介绍，保留详细 OCR 缓存保护说明 |
| backend/app/services/ocr/paddle.py | 当前缓存修复：官方注册表、内容解析与参数量级、下载锁、活动时间、链接保护；并保留远端 CPU 低内存与推理并发配置 |
| backend/tests/test_static_hosting.py | 当前静态托管回归同时验证 `/api` 与 `/api/` 不缓存 |
| docs/PROJECT_HANDOFF.md | 当前词库、会话与来源记录，增加本批入口，保留历史 |
| docs/PROJECT_ROADMAP.md | 当前产品方向与移动端完成记录，保留历史 |
| docs/PROJECT_STATUS_CURRENT.md | 当前记录与最新候选入口，历史数字注明日期 |
| frontend/public/sw.js | 保留 `/api` 与 `/api/` 两种路径的缓存拒绝边界 |
| frontend/src/pwa.test.ts | 保留完整缓存边界、安装图标、安全区和移动详情测试 |
| frontend/src/styles.css | 保留后续移动详情、底部导航、释义与来源样式 |

`test_ocr_optimization.py` 的自动合并会重新引入已被后续安全修复取代的三项旧测试：它们使用非空即完整的假模型，并假设修复返回列表。保留当前版本与 `test_ocr_model_cache_repair.py` 的完整覆盖，后者验证完整缓存保留、不完整缓存处理、缺失目录、引擎构建顺序以及锁、截断与下载活动保护。没有删除当前安全断言或弱化缓存保护。

PR 独有的 2026-09-24 进度报告作为历史记录保留；部署手册的新空库示例 revision 从 0010 更新到当前源码头 0016，现有数据库仍须单独备份与迁移演练。

## 验证

合并前前端：23 文件、216 项通过；TypeScript、ESLint、生产构建通过。冲突处理未改变前端源码，已核对合并前后前端树指纹均为 `0491c92e49da7c4235b3b992e095565d3e2b8d48`。

第一轮完整后端：1102 passed / 26 failed / 19 skipped，448.94 秒；该轮并非通过。26 个失败已逐项定位：两处头版本仍固定0015、一项 CLI 预览未指定合成 baseline、一项共享词库夹具默认 `manual` 来源与既有公共词库的唯一约束冲突，其余依赖本机保存的固定证据或历史测试快照。

首先修正四份测试：迁移链头改为0016并增加0016→0015断言；CLI 预览显式使用 `world.baseline_path`；共享词库夹具显式 `source_type="test"`。原安全与隔离断言保留，未改应用代码。相关迁移/固定行证据/CLI 定向复测19项通过。

隔离目录准备固定词典输入，并补齐候选包和冻结计划，复制后逐指纹核对；两份既有历史测试库通过 SQLite online backup 写入该目录的 `data/staging/` 并检查完整性与外键，测试源路径使用同盘硬链接。没有读取主工作区正式数据库。所有输入和快照均被 Git 忽略，不随源码推送。

最终完整串行后端回归：**1132 passed / 15 skipped / 0 failed**，用例身份与首轮完整收集的1147项逐一核对一致。测试使用独立临时目录与 conftest 保护；本机固定候选包/生产备份收据缺失时的既有条件跳过将如实列出。

Ruff（backend/app、backend/tests、tools）、Git diff 检查通过。独立评审未发现 Critical/Important；后续复核确认测试修正未弱化验证。

同步期间主工作区新增的未提交词义展示开发改动不纳入本批，继续保留在本地。

后续隔离发布测试使用 `netem_production_sentinel`：pytest 临时文件只提供受保护文件身份，实际目录、存在性与硬链接守卫仍执行；不依赖正式数据库。两个5528项发布/备份/回滚测试与历史top1000导入定向复测3项通过，候选包定向3项通过。

中间一轮完整回归为1126 passed / 5 failed / 16 skipped（489.24秒），失败来自候选输入尚未补全、隔离目录没有生产文件身份，以及历史冻结计划缺失；补全后定向复测通过。一次四组并行验证因C盘空间不足无效，未记为通过；仅清理本任务可重建输出，保留日志和固定输入，最终改用E盘独立临时目录串行运行完整套件。

最终命令：`python -m pytest tests -q -ra --basetemp=E:/codex-test-artifacts/github-sync-20261009-01a11f90/run --junitxml=.pytest-tmp-sync-complete/results.xml -o faulthandler_timeout=120`。仅测试输入/夹具、过时断言与文档有本批增量，应用代码与前端未改变。两条警告是既有Starlette/httpx和AnyIO弃用提示。

历史 `scripts/check.ps1` 仍固定要求生产 revision 0015，并读取生产历史基线；当前源码 head 已为 0016，隔离目录不携带正式库。本批执行其源码 lint、完整后端测试、前端测试/类型/lint/构建组成的检查，不将未执行的生产库检查声称为通过，也不为满足该历史门禁改变生产库。

## 数据与发布边界

GitHub 推送只同步已跟踪源码与文档，`data/`、`.env`、依赖和测试产物仍按 .gitignore 排除。最新 5528/5528 来源释义更新是通过隔离验收的候选，尚未写入正式库；每日自动备份的服务器安装与调度亦尚未验收。

最终条件跳过的原因：

- local final package unavailable
- local latest online-backup receipt unavailable
- no project baseline recorded yet
- Windows 环境缺少创建符号链接的权限
