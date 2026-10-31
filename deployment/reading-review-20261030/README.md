# 阅读与复习更新部署材料

2026-10-31 已在原 ECS 完成发布和验收。包目录沿用 20261030 标识。操作结果见 [acceptance.json](acceptance.json)，产品规则、状态含义和验收范围见[部署总结](../../docs/READING-REVIEW-DEPLOYMENT-2026-10-31.md)。

## 文件

- `reading-review-20261030.tar.gz`：实际安装的冻结增量包，20 个文件及内置清单。
- `manifest.json`：旧版本基线与新文件 SHA256，来自基线提交 `08dc2d35c859ca1f63e1009e7d55132b232a198c`。
- `package.sha256`：冻结包 SHA256，`f0e19c16507f1d076dc66a6fc1832e60185d5421d40f5d9f4a1b64402f6efe41`。
- `deploy.py`：迁移演练、停写备份、发布、完整行保留验证、启动及失败恢复。
- `probe.py`：真实路由配合固定 AI 回答的隔离验收，不连接收费 AI。
- `acceptance.json`：本次真实服务器发布摘要；不包含密钥、用户数据或内部行指纹。
- 浏览器截图仅留在本地；本次公开提交使用不含截图的 JSON 验收摘要。

实际后端／前端源码和测试位于仓库正常目录，包中不包含依赖环境和用户数据库。此次未改依赖锁文件。

## 复现部署步骤

**当前原服务器已经升级，不要重新执行本增量脚本。** 脚本只接受清单中的旧源码与 0016 数据库基线；已有 payload 或 backup 会拒绝重跑。需要进一步更新时，应制作新的增量包和独立目录。

以下步骤供同一基线的恢复环境或复现部署使用：

1. 确认应用位于 `/opt/shici/current`，服务名 `shici`、服务用户 `shici`，Python 环境 `/opt/shici/current/.venv`，SQLite 为 `/var/lib/shici/vocab.db`。保留原环境文件 `/etc/shici/shici.env`，不要将仓库覆盖到数据库目录。
2. 创建 `/opt/shici/reading-review-20261030`，权限 700。上传 `deploy.py`、`probe.py`、`package.sha256`；将本冻结包上传为该目录下的 `patch.tar.gz`。
3. 核对上传文件和包 SHA256。在原环境运行：

```sh
/opt/shici/current/.venv/bin/python /opt/shici/reading-review-20261030/deploy.py
```

4. 成功必须同时出现 `READING_REVIEW_PROBE_OK` 与 `READING_REVIEW_DEPLOYED`，并生成 `result.json`。查看服务状态、首页和新静态资源，数据库版本应为 `0017_contextual_lookup`。匿名学习接口必须仍为 401。
5. 使用新阅读文章或重新请求旧文章逐句翻译，检查“显示逐句翻译”、不同句子中的同词解释、目标词选择和加练剩余数。普通刷新后仍看到旧界面时强制刷新一次。

脚本先复制真实库到隔离环境，迁移并逐表比较原行，再跑探针；通过后才停止服务。正式备份在停写后创建，避免用较早快照覆盖刚产生的学习记录。所有新文件哈希通过后才算发布成功。首页最后安装，旧的带哈希静态资源保留，保护尚未刷新页面的客户端。

## 失败检查与恢复

- 切换前失败：正式服务未停止；查看 `rehearsal-migration.log` 或 `probe.log`，不要清空工作目录后盲目重试。
- 切换中失败：脚本尝试恢复停写时备份和原源码、入口 HTML、部署清单，打印 `ORIGINAL_CODE_AND_DATABASE_RESTORED`。
- 新代码启动后出现新业务写入：打印 `ROLLBACK_REFUSED_NEW_USER_WRITES`，保留新版本并重新启动，避免用旧整库丢弃新记录。此时先审计数据和失败原因，不能直接复制 `backup/before.db` 覆盖正式库。
- 0017 降级时发现同一文章同一词已有多句缓存，会拒绝降级；不会擅自合并或删除新查词记录。
- `backup/`、`verification.db`、演练数据和日志留在受限服务器目录，均不上传 GitHub。

## 本次验证

相关后端 87 项、前端 32 项通过；TypeScript/Vite 构建、相关 Ruff／ESLint 通过。另有选词与语境读取 17 项定向验证。服务器在演练源码和正式安装源码上各完成一次隔离 API 验收，原业务表的所有行保持一致，正式 API 已提供新句子索引参数，公网浏览器加载新资源。

固定 AI 探针证明接口、缓存和调度行为；本次没有新增收费模型质量验收。原共享 API 配置已保留。学习日继续使用原有 UTC 边界。

后端相关回归（从 backend 目录执行，Python 环境需已安装测试依赖）：

```sh
python -m pytest tests/test_contextual_reading.py tests/test_weighted_practice.py tests/test_reading_flow.py tests/test_review_flow.py tests/test_daily_new_words.py tests/test_migrations.py tests/test_review_article_ownership.py tests/test_concise_meaning.py::test_0009_follows_the_unpublished_0008_and_keeps_one_head tests/test_entry_source_revision.py::test_0010_stacks_on_0009_and_the_graph_has_one_head -q
```

前端（从 frontend 目录执行）：

```sh
npm test -- src/pages/ReadingPage.test.tsx src/pages/StudyPage.test.tsx src/App.test.tsx
npm run build
```
