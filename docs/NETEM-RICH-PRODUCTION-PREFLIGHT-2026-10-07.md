# NETEM 正式发布准备记录（2026-10-07）

本记录保留首次被代码门禁拒绝时的状态。后续用户已明确授权冻结 NETEM 改进及此前词库切换、队列补词、设置刷新、500 上限、全量分页、未学习筛选、来源说明折叠和对应测试/发布工具；无关文件和历史草稿不纳入。用户亦明确授权通过现有 CLI 恢复 admin 口令，生成随机口令并仅保存本机 DPAPI 加密恢复文件，再继续认证发布。实际提交 SHA、恢复备份、发布结果及回滚收据另存发布操作目录。

用户已明确批准固定计划进入正式发布，并要求继续使用现有受保护流程。此批准有效，后续无需再次询问是否发布。用户选择在本机终端输入管理员口令；尚未进入口令输入阶段。

**实际状态：尚未正式发布。** 原有未提交改动保留，没有提交、reset、停止正式服务或执行正式迁移。正式库仍为 `0015_session_autoincrement`。

## 已完成

新增 `tools/phase29/netem_rich_release.py`，沿用既有 `netem_release.py` 的固定生产路径、干净代码提交和管理员认证门禁。加强代码锁定，逐个核验新入口、导入器、解析器、迁移、后端及前端可执行源码归属于指定提交且内容一致，避免旧检查忽略未跟踪文件。

本机输入工具 `netem_rich_credential.ps1` 只保存 CurrentUser DPAPI 密文。发布入口的 `--credential-file` 只接受 test-artifacts 内显式文件，解密输出仅在进程内捕获；消费后无论成功或失败均删除临时文件。长期账号恢复文件属于用户明确批准的本机恢复材料，单独保存且不纳入 Git，不能将它作为临时消费文件直接删除。

只接受已批准的全量计划 SHA `c71f1b1238da8f5c896bee6e91e36a399c79dbccc985d4d7bf8115c6335ba696`。入口执行当时备份、来源与词条基线检查、固定 `0016_dictionary_extraction` 迁移、按 ID 增量更新、事务内管理员身份复核和全旧表保留核验。自动内容保持未核实，不新增核心确认。

隔离副本已经验证完整入口的备份、迁移、5528 条更新及局部回滚；脏工作区和错误管理员认证在备份/迁移之前拒绝。原有更新/回滚测试继续通过，包括更新之后的学习记录保留。

新建只读一致性备份：

- 路径：`test-artifacts/netem-rich-production-preflight-20261007/174609/before.db`
- 核验收据：同目录 `backup.json`
- 时间：2026-10-07 17:46:11 +08:00
- SHA256：`f20dc8f128bb23a43dcac01ba30d685070587a38116d2f12b41c2c33a6cdc179`
- integrity ok，外键错误 0，25 张原业务表指纹一致。
- 当前学习状态 204、复习事件 123、文章暴露 16、原来源证据 16507；正式库全部词条 5592，NETEM 5528。

这是新的发布前备份，但因本轮停在代码门禁，不能替代后续真正写入当刻的新备份；入口会另建操作目录自动备份，拒绝覆盖旧备份。

## 当前阻断及依据

实际正式入口命令以当前 HEAD `ddcea13709543c7137c383badac45d40a5ef762a` 调用，先于口令和数据库写入被拒绝：`code SHA not locked or tracked worktree dirty`。

直接依据是 `tools/phase29/netem_release.py` 的 `check_code`：`if actual != expected or dirty: raise ValueError("code SHA not locked or tracked worktree dirty")`。工作区有原先改动及本次新文件，不能假报干净、使用旧提交号、reset 用户改动或将无关改动自动混入发布提交。

需要先确定可发布的代码锁定方式并整理出真实的干净提交；本轮没有自行放宽此门禁，也没有将管理员身份存在视作口令已认证。待该前提就绪，使用用户已批准的计划继续发布。

## 后续操作命令

先整理发布版本，使用实际干净发布 SHA；在本机终端执行，口令由 getpass 读取，不发送到聊天或写入报告。先用既有 `scripts/stop-vocab.ps1` 停写并证明服务退出；若代码未锁定，不进入停机窗口。

```powershell
$releaseSha = '<实际干净发布提交SHA>'
$operation = 'test-artifacts/netem-rich-production-20261007/publish-NEW'
./backend/.venv/Scripts/python.exe tools/phase29/netem_rich_release.py apply --database data/vocab.db --operation $operation --code-sha $releaseSha --admin admin --confirm c71f1b1238da8f5c896bee6e91e36a399c79dbccc985d4d7bf8115c6335ba696 --production-update
```

成功后 `$operation/batch-undo.json` 为本批次局部回滚收据，`backup.json` 为当刻备份，`result.json` 为保留核验。尚未发布，所以现在没有正式批次回滚收据；隔离收据不能冒充正式收据。启动正式服务前构建当前前端、核对真实数据库路径与新 revision，并进行桌面/手机实际页面验收。

局部回滚（需先停写、新备份，并在本机再次管理员认证）：

```powershell
./backend/.venv/Scripts/python.exe tools/phase29/netem_rich_release.py rollback --database data/vocab.db --operation test-artifacts/netem-rich-production-20261007/rollback-NEW --code-sha $releaseSha --admin admin --confirm c71f1b1238da8f5c896bee6e91e36a399c79dbccc985d4d7bf8115c6335ba696 --receipt "$operation/batch-undo.json" --production-update
```

只撤回收据对应的抽取行，核对当前 payload SHA；保留学习记录、ID、旧来源证据、新表 schema，不降级或整库覆盖后续学习。
