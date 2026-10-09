# 每日备份本地隔离验收记录

实施日期：2026-10-05 至 2026-10-06（Asia/Shanghai）。

**本地功能已验收，尚未在服务器安装或启用。**
这里的“本地功能”指下面列出的合成库备份专项验收，不代表 Linux 调度或生产灾备已验收。

## 范围与交付

- 阅读 `README.md`、`docs/PROJECT_HANDOFF.md`、`docs/PROJECT_STATUS_CURRENT.md`，核对既有服务、CLI、启动/UI 备份调用及验证工具。
- 起点 `865e7e8`。原服务已使用 online backup，但缺校验、元数据、锁和保留策略，源库缺失时会创建空库；仓库没有 systemd 模板。
- 加固现有 `create_backup`，新增 `run_backup`；提供 `app.cli backup` 和共用同一实现的 `tools/backup.py`。
- 交付 `deployment/backup/` 的 service/timer/配置示例、[操作手册与服务器清单](DAILY-BACKUP-RUNBOOK.md)、可复跑的 `scripts/test-daily-backup.ps1` 和合成库测试。
- 未访问真实 `data/vocab.db`，未为证明隔离而读取其哈希；没有生产服务启动/停用、真实数据复制、迁移或远端操作。
- 不运行 `scripts/check.ps1`：该脚本后半段明确读取真实库和 data 目录，与本次限制冲突。使用单独的 pytest 与 Ruff 命令。

## 专项验收

命令：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/test-daily-backup.ps1
```

结果：**19 passed / 2 warnings，exit 0**（18 个新增参数化用例＋1 个原有备份用例）。
Python 3.13.0 / pytest 8.4.2 / Windows；该脚本把 TEMP/TMP、pytest basetemp 指向仓库内独立的
`test-artifacts/daily-backup/run-<随机标识>/`，清除继承的显式数据库路径，启用测试保护。
所有库由 SQL 建表生成，应用共享测试库由既有 conftest 在临时目录迁移建立，不使用真实数据副本。

| 验收点 | 证据与结果 |
|---|---|
| online backup / WAL | 保持 WAL writer 打开、关闭自动 checkpoint、写入并提交新行；确认 WAL 非空；备份包含新行 |
| 独立恢复 | 仅复制完成的 `.db` 到独立目录；读取预期行；完整性 `ok`、外键零违规、无需 WAL/SHM |
| 元数据 | 比对文件大小、独立计算 SHA-256、synthetic revision、带时区完成时间 |
| 同日幂等 | CLI 再次执行成功，原副本逐字节未被覆盖 |
| 默认保留 | 连续模拟 33 个日期，保留最近 30 份；旧日期被清理 |
| 保护边界 | 手动、旧命名、只有相似文件名、损坏副本均原字节保留；无效副本不计入有效份数 |
| 失败处理 | 源库缺失、非 SQLite 内容、外键违规均非零；既有备份不变；不创建缺失源库 |
| 失败日志 | 持久事件日志有失败状态，敏感测试字符串和异常原文不进入日志 |
| 目标不可写 | 用普通文件占据目录路径；非零退出，stderr 记录失败与日志不可写，原文件保留 |
| 发布中断 | 注入 JSON 发布 I/O 错误；旧副本不清理，临时文件清理；孤立 DB 不被覆盖 |
| 元数据损坏 | 拒绝视为成功，保留 DB 和损坏元数据等待人工检查 |
| 进程并发 | 一个进程持锁，另一个真实 CLI 返回 3、无重复文件；释放后正常执行 |
| 异常退出 | 子进程持锁后 `os._exit(17)`，后续 CLI 成功获取同一锁 |
| 启动兼容 | 子进程原有启动备份等待持锁任务；释放后成功，不因短暂冲突立即退出 |
| 手动保留 | 同一时刻附近连续手动备份创建两个不同文件，后续保留策略不删 |
| 配置 | 显式环境路径、独立脚本任意 cwd 均可运行；缺少路径拒绝回退到应用默认库 |
| 非法配置 | 非正保留数、非数字保留数、无效时区均非零，无备份及敏感参数泄露 |

新增测试先在旧实现上得到 **8 failed**（缺 CLI/服务功能与缺失源库行为），实现后转绿。
代码评审指出启动时共享锁立即失败会影响重启，另加子进程测试复现失败后修复为原入口最多等待 30 秒；
CLI 仍立即返回 3。复核未发现剩余实质问题。
Windows `fsync` 只读句柄失败也已由测试发现，修复为临时目标文件使用可写句柄同步。

## 回归与限制

- 全量后端回归：**1058 passed / 1 failed / 1 skipped / 2 warnings，325.14 秒**。
  唯一失败为 `test_phase29_frozen300_decisions.py::test_isolated_runner_refuses_sibling_of_configured_pytest_temp`：
  本轮 basetemp 位于 `test-artifacts/daily-backup/final-standard`，该历史工具明确允许整个 `test-artifacts` 树，
  所以用例构造的相邻目录仍属允许路径，无法触发其期待的拒绝。不是备份行为失败。
  将 TEMP/TMP 设置为 `backend/.pytest-tmp-backup-layout`、basetemp 设置为其 `recheck` 子目录，
  对该文件全部用例复测：**7 passed / 2 warnings，exit 0**；未修改历史工具或测试。
  全量这一轮并非零失败，不把复测结果改写为原轮次全绿。
- 全量命令为 `python -m pytest backend/tests -q --tb=short --basetemp=test-artifacts/daily-backup/final-standard`，
  清除了继承的 `VOCAB_DATABASE_PATH` 和额外 `VOCAB_TEST_MODE`，保留标准 pytest 标志及 conftest 合成库保护。
  原始结果保留于 Git 忽略的 `test-artifacts/daily-backup/final-standard.log`。
- 唯一 skipped 是 `test_public_lexicon_preview_guards.py:72` 的符号链接测试，当前 Windows 缺少创建符号链接特权。
- `python -m ruff check backend/app backend/tests tools/backup.py`：**通过**。
- `git diff --check`：**通过**；Git 的 LF/CRLF 提示不是错误。
- 两条 pytest 警告是既有 Starlette/httpx 和 AnyIO API 弃用警告。
- 扩大 Ruff 到整个 `backend` 时，历史迁移 `0011_entry_concise_meaning_pos.py:149,154` 报两个 `ISC004`；该文件没有本次 diff，未修改已发布迁移。项目正常 lint 范围 `app tests` 通过。
- 沙箱初次测试遇到默认 TEMP 和复用 basetemp 的权限问题，改为工作区唯一临时目录后专项测试通过。
- 沙箱全量测试卡在 `test_admin_reauth` 的 TestClient 初始化；45 秒线程栈定位为 asyncio 的 Windows `socket._fallback_socketpair → accept`，不是 SQLite 备份。沙箱外运行可正常推进。
- 一次额外设置 `VOCAB_TEST_MODE=1` 的全量尝试与 `test_guard_is_inert_in_a_normal_process` 冲突（该用例有意清除 pytest 标志模拟普通进程）；中止后改用标准 pytest 环境。未修改该用例或关闭 conftest 的路径保护。
- 前次最终重跑曾因自动审批额度用尽而未执行；收到继续指令后重试。没有绕过审批。
- 未改前端，未运行前端构建；Linux systemd 解析、03:30 实际触发、漏执行补跑、POSIX 锁、目录 fsync 和服务权限留作服务器待验收。
- 没有异机目标，未配置任何上传或异机副本。服务器完整执行清单见操作手册。

## 原有文档与提交边界

下列三份原有未跟踪文档在任务前后 SHA-256 一致，保持未跟踪，不纳入本次提交：

| 文件（docs/ 下） | SHA-256 |
|---|---|
| V1.2-NETEM-DEFAULT-LEXICON-LAUNCH-CANDIDATE-2026-10-03.md | `8bdce41ae071d76284f3ca3d196e73c716714a6b905a14ea5af2d8270ee155ed` |
| V1.2-PHASE2.9-POS-MEANING-CONTRACT-DESIGN.md | `dfae28454a18a3603815139e6fe549e72332ff85defa241d7cd7416d78668bf4` |
| V1.2-PHASE2.9-ZHWIKTIONARY-LICENSE-EVIDENCE-CHECK.md | `28284746f65db876938af58e36e58b59103e9efd8d891c3cd9076a9f9a660122` |

合成数据库、事件日志、pytest 原始输出位于 Git 忽略的 `test-artifacts/`；历史用例的目录布局复测位于
同样被忽略的 `backend/.pytest-tmp-backup-layout/`，均不作为源码提交。
提交只包含备份服务/入口、测试、部署模板、配置示例和文档，不含数据库、日志或凭据；不推送 Git。
