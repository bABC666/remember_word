# 迁移历史取证：2026-09-22 15:04 那次未授权 upgrade

本文回答一个问题：**production 在 15:04 执行的 0004→0006 migration，用的到底是哪一份代码？**
以及由此暴露出的、我自己造成的一个真实问题：`0005_lexicon_and_migration.py` 在这次 migration
被应用**之后**又被修改过。

结论一句话：**本次未授权 migration 使用的是 commit `6ad8fce` 的脚本；六个已应用 revision 中
只有一个（0005）在应用后被修改过，改动不改变最终 schema、不改变数据迁移语义，只改变冲突时的
中止时机。** 该事实现在由 `docs/migration-history-manifest.json` 记录，并由
`tools/verify_migration_history.py` 逐字节校验。

本轮未修改 production：`data/vocab.db` 仍是 0006，未迁移到 0007，未 downgrade，未写入。

---

## 1. 该次 migration 实际使用的 Git commit

| 证据 | 内容 |
|---|---|
| reflog | `6ad8fce` 提交于 2026-09-22 14:53:17；HEAD 的下一次变动是 16:50:35 的 `b0d9d09`。整个 15:04 前后 HEAD 一直是 `6ad8fce`。 |
| 触发路径 | 桌面快捷方式 `start-vocab.bat` → `scripts/start-vocab.ps1:64` 无条件执行 `alembic upgrade head`（第一次审计的 F-01/F-02）。 |
| 结论 commit | **`6ad8fce58041b03977deb2a98ae668cc22eef35a`** |
| 执行前 revision | `0003_article_reading_tools` |
| 执行后 revision | `0006_article_exposure_entry` |

## 2. 逐字节比较：当时执行的版本 vs 当前 HEAD

每个 revision 文件的 blob（git 内容寻址，CRLF 无关）：

| revision | 在 `6ad8fce` 的 blob | 在 HEAD 的 blob | 是否变化 |
|---|---|---|---|
| 0001_initial | `343793589a4aef318f2740fdf32bc385d77f6b3f` | 同左 | 否 |
| 0002_reading_assistance | `15dbd1e11ece726912a1f33ff6900fe08ea889d9` | 同左 | 否 |
| 0003_article_reading_tools | `d8c241ab6fa1228fc277c0296b1d9b3470903160` | 同左 | 否 |
| 0004_multiuser_foundation | `ec431e3ee26a8c0e84cc8fbac2df0fa1b1a02d86` | 同左 | 否 |
| **0005_lexicon_and_migration** | **`72348b9be6f3bdee7d8026ab97b6d3ea6caa7813`** | **`5d82e5dc4a08291d10e418532b61efdc16376e49`** | **是** |
| 0006_article_exposure_entry | `951baaf6813a28164b81bf5252c33f3b2f94b56a` | 同左 | 否 |
| 0007_bridge_foreign_keys | 不存在（尚未写出） | `b989274aca805d2cc4fcc2938a7084b9aebe8ebc` | 不适用 |

内容 sha256（文件按 git 存储形态、LF 归一化后计算）：

| revision | sha256 在 `6ad8fce`（= 已应用） | sha256 在 HEAD |
|---|---|---|
| 0001 | `7c527ea31bec431501e48249164e1caad424a19c965a083d49f614bdcf51a170` | 同左 |
| 0002 | `1038790be5f5208a0f3efbea1c2f0d44028953818d0d1346f1d377a7f3abee56` | 同左 |
| 0003 | `efbf1b1e29f67a79c9d4263b9789dd4e4b78be60db55daa5931d104415170a1b` | 同左 |
| 0004 | `750d89e3c8dff24d7bd04a559919ba7954dc6c64555ca558f8dbf26802c828de` | 同左 |
| **0005** | **`f16ce9dd7b6186b96fb0876e95dd97411c8ca41d334959b55055ce0f26ee9e99`** | **`51b6beb66a36c51b3d48a982729f2663d812ed532380307c621f734da6520068`** |
| 0006 | `bebf058a6fc8dcfed008adad34e9016a6cfbd306c5e75b815e3534aa15dc2381` | 同左 |

（表中的 sha256 由 `tools/verify_migration_history.py` 直接从 git 对象库计算，未经任何 shell 管道，
避免 PowerShell 在管道中重编码文本而污染哈希。本文件撰写过程中曾用管道算过一次并得到不同结果，
那个错误结果已丢弃。）

## 3. 报告里的“矛盾”究竟是什么

上一轮报告里同时出现两句话：

1. “0005 的重复词预检已前移到 `upgrade()` 第一行”；
2. “0005/0006 已应用即冻结，未重写”。

它们各自都对，但**放在一起是误导的**，因为 0005 存在两个不同的版本，而我没有把这件事说清楚：

* 对 **0006** 以及 0001–0004：第 2 句完全成立——字节未变；
* 对 **0005**：第 2 句不成立。**它在一个真实数据库应用之后被修改了。**

也就是说，我上一轮说的“已应用即冻结”实际上只对这些文件的 **DDL 与数据语义**成立，
对 **0005 的文件字节**并不成立。这一点必须明确记录，因为“迁移文件一旦应用就不再改动”
正是可审计性的基础规则；我为了补上一个缺失的预检而破了这条规则，且没有在报告里如实标注。

还有一处我必须自我纠正的描述错误：上一轮我把 0005 的改动说成“预检前移”（暗示被应用的版本里
本来就有预检）。逐字节核对后，**被应用的那一版根本没有任何预检**——这次改动是纯新增（+63/−0）。
详见第 4 节。

## 4. 0005 的改动

| 项目 | 内容 |
|---|---|
| 修改前 commit | `6ad8fce58041b03977deb2a98ae668cc22eef35a`（文件自 `0d789c9` 创建后从未变过） |
| 修改后 commit | `cfbabc62c78361cedfc8bf661b618d4471a95c95` |
| blob | `72348b9be6…` → `5d82e5dc4a…` |
| diff 规模 | **+63 行 / −0 行（纯新增，没有任何一行被删除或修改）** |

`git diff --numstat 6ad8fce HEAD -- backend/alembic/versions/0005_lexicon_and_migration.py`
输出 `63  0`，即这次改动**只增加代码**：

1. 新增三个函数：`_normalized_word_key()`、`find_normalized_duplicates()`、
   `preflight_normalized_words()`（后者只执行 `select id, word from word order by id`，
   有冲突时 `raise RuntimeError`，无冲突时直接返回）；
2. `upgrade()` 第一行新增 `preflight_normalized_words(op.get_bind())` 调用，
   位置在 `_add_compat_columns()` / `_create_lexicon_tables()` **之前**。

**必须纠正的一处错误描述**：上一轮我（在 `cfbabc6` 的提交信息与报告里）说“预检本来就存在，
只是位置太靠后，现在把它前移”。**这不成立。** 经逐字节核对，**被应用的那一版 0005 里完全没有
预检**（`Select-String -Pattern 'preflight|normalized_dup|find_normalized'` 在被应用版本中零命中）。
真相是：预检是本次修复**新增**的能力；`cfbabc6` 是本会话中先在工作区加入预检、随后把调用点放到
`upgrade()` 开头，两次未提交修改合并成的一次提交。因此对 0005 的正确描述是
“**新增预检**”，而不是“前移预检”。`cfbabc6` 的提交信息与上一轮报告中的那句话应视为勘误，
本文与 manifest 以本文为准；那条提交信息本身不重写（改写已提交的历史只会制造新的不可审计点）。

### 4.1 是否改变最终 schema —— 否，且已实测

用 `6ad8fce` 的完整代码树（`git archive 6ad8fce`，迁移脚本即应用版本）对**已验证的 V1.1 源库**
克隆执行 `alembic upgrade head`（止于 0006，与应用时的 head 相同），与 production 逐项比较：

| 比较项 | 结果 |
|---|---|
| revision | 均为 `0006_article_exposure_entry` |
| 表 / 索引 / 视图 / 触发器 数量与名称 | 18/18、61/61、0/0、0/0，**完全一致** |
| 索引 SQL 文本 | 61 个全部逐字节一致 |
| 表 SQL 文本 | 79 个对象中 78 个逐字节一致；唯一差异见下 |
| 每个表的 `PRAGMA foreign_key_list` | 18 张表**全部一致** |
| 每个表的 `PRAGMA index_list` | 18 张表**全部一致** |
| `integrity_check` / `foreign_key_check` | 双方均 `ok` / 0 |

唯一差异：`article_word_exposure` 的 `CREATE TABLE` 文本中两个 FOREIGN KEY 子句的**书写顺序**
不同（`word_id` 在前 vs `article_id` 在前）。两条约束的目标表与 `ON DELETE` 规则完全相同，
`PRAGMA foreign_key_list` 结果一致——这是 SQLAlchemy/SQLite 在重建表时按反射顺序输出子句造成的
文本顺序差异，不是 schema 差异。

新增的 63 行全部是只读函数与一次调用，不含任何 `op.` 语句：`_add_compat_columns()`、
`_create_lexicon_tables()` 与全部数据写入语句在两版之间逐字节相同，因此最终 schema 相同的结论
既来自推理也来自上面的实测。

### 4.2 是否改变数据迁移语义 —— 对 production 无影响，对冲突库只是中止时机

* 新增的 `preflight_normalized_words()` 只做一次 `SELECT`：无冲突时立即返回，
  对数据库没有任何写入；有冲突时抛 `RuntimeError`。它在 `upgrade()` 的最开头执行，
  早于任何 DDL 与数据语句。
* production 无冲突：`lexicon_entry` 19 行、`normalized_word` 去重后仍为 19、`lexicon` 1 个
  （已实测），即迁移路径上这段新代码什么都没做，行为与应用版本完全一致。
* 回放比对：8 张冻结内容表（`word`、`review_event`、`article`、`article_word_exposure`、
  `article_word_lookup`、`import_batch`、`import_image`、`import_candidate`）以及
  `lexicon_entry`、`user_word_state` 在回放库与 production 之间**逐行相同**。
* 其余差异全部可解释，且都不来自 0005 的改动：`lexicon`/`user`/`user_lexicon`/`user_settings`
  只差迁移时写入的 `created_at`/`updated_at`/`started_at` 时间戳；`history_event`/`app_setting`
  是应用在迁移之后自己写入的行（+1/+3）。
* 唯一的语义差别只出现在**有规范化冲突**的数据库上：应用版本会在 `_ensure_user_word_state`
  撞上 `UNIQUE(user_id, lexicon_entry_id)` 而中途失败（此时 DDL 已执行、部分数据已写入，
  库停在“revision 仍是 0004、新表新列却已存在”的状态，二次迁移会失败）；
  当前版本零改动直接中止并列出冲突行。production 不属于这种情况
  （其 19 个词互不冲突，已实测）。

## 5. 如何恢复 migration history 的可审计性

已落地：

1. **已应用的版本没有丢失。** 它仍在 git 对象库里（blob `72348b9be6…`，可从 `0d789c9` 与
   `6ad8fce` 到达），因此“当时到底跑了什么”永远可以精确复现：
   `git cat-file blob 72348b9be6f3bdee7d8026ab97b6d3ea6caa7813`。
2. **机器可校验的记录**：`docs/migration-history-manifest.json` 为每个 revision 记录
   HEAD 的 blob/sha256、审计基准 commit（`6ad8fce`）的 blob/sha256、已应用版本、应用之后
   触及它的 commit，以及（对变动项）schema 影响与数据语义影响的书面结论。
3. **校验工具**：`python tools/verify_migration_history.py`
   （`--write` 只重算可计算字段并保留人工结论）。它会在下列情况失败并列出具体原因：
   文件与记录不一致、出现未登记的 revision、已应用版本从对象库消失或其内容哈希不匹配、
   `changed_*` 与 git 事实矛盾、或某个“应用后被改动”的 revision 缺少影响说明。
   当前输出：0001–0004 与 0006“applied, byte-identical”，0005“CHANGED after being applied
   (cfbabc62…)”，0007“not applied to the live database”。
4. **本轮起遵守的规则**：已应用的 revision 不再改动，缺陷用**新 revision 表达**——0007 就是
   这个模式（0005/0006 已经在 production 跑过，所以外键缺口用新增 0007 补齐，而不是回头改它们）。
   0005 是这条规则唯一的一次例外，已在此记录在案。
5. **可选的结构性补救**（未执行，需要你决定）：把预检从 0005 中移出、恢复 0005 的应用版本字节，
   代价是“重复词”数据库会退回到“先建表再中止、且无法二次迁移”的行为；相比之下保留现状并把
   例外记录在案，风险更低。若你要求 0005 字节完全还原，这是一次一行的 revert + 一条新 revision，
   可以随时执行。

## 6. 尚未解决的事实（不粉饰）

* production 仍在 **0006**，不在代码 head（0007），因此 `scripts/check.ps1` 的最后一步
  （`verify_backup.py` 对比冻结基线）仍然 FAIL：一是 revision 期望 0003，二是
  `review_event`/`article_word_exposure` 的行指纹因 0006 新增了列而变化。内容层面已另行证明未变
  （`tools/compare_with_source.py`：8 张冻结表逐字段一致；`tools/diagnose_backup_rows.py`：
  所有被记录的内容都未改变，差异只来自新增列）。
* 破坏 V1.1 库时永久丢失的数据（10 条 review_event、5 条 article_word_lookup、1089 字符文章译文、
  6 条 history_event、`onboarding_seen`、9 个词的状态推进）**没有被恢复，也不会被伪造**。
