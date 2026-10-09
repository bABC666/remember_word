# NETEM 自动来源核验及学习展示改进（2026-10-08）

后续继续恢复的结果见 [NETEM-SOURCE-RECOVERY-VALIDATION-2026-10-08.md](NETEM-SOURCE-RECOVERY-VALIDATION-2026-10-08.md)。本文件保留前一候选证据，1070空缺和6ff488计划已被新版取代；两版均未正式发布。

本轮核验及隔离验证已经完成，**正式数据库尚未更新**。用户允许开始来源核验；上次批准的正式计划 `c71f1b…` 不代表本次改变后的 `6ff488…` 计划已获正式写入确认。

## 可用释义覆盖及准确边界

| 指标 | 原已发布版本 | 本轮全量隔离候选 |
| --- | ---: | ---: |
| NETEM 词条 | 5528 | 5528，ID 保留 |
| 有可展示来源释义 | 254 | **4458（80.6%）** |
| 有核验释义及明确来源词性 | — | **4216** |
| 有多个来源词性 | — | 515 |
| 有多个可展示来源值 | 115 | 1638 |
| 含可用释义但词性留空 | — | 308（可与有词性组共存） |
| 无可用核验释义 | — | **1070** |
| 新增人工确认核心释义 | 0 | **0** |

审阅完整覆盖原 10,742 个中文候选；另外对 77 个既有英文维基词典补词来源重新对照，按明确引用行恢复 103 个显示值。合计 **10,845** 个候选/引用值，**1875** 个错误片段排除、**1467** 个疑点保留。多个来源值可能是同义译词或繁简写法，不声称全部都是新增的不同常用义项。

这不是人工逐词确认，也不是权威标准词典裁定：每条结论记录为自动来源语义对照，绑定确切词头、中文原串、定位、原文体 SHA、输入抽取 SHA 和审阅文件 SHA。不得以“原文存在”代替语义一致判断；独立复核发现的“苟评家”等疑似源文笔误已保留原字并改待核，没有擅自纠错。

剩余 1070 词明确分为：

- **302**：现有保留内容没有中文来源候选；
- **385**：有候选但全部为明确不对应的片段；
- **383**：候选仍有语义、模板、许可或对应范围疑点（可能与已排除片段混合）。

逐词列表见 `test-artifacts/netem-source-audit-20261008/remaining-gaps.json`，例如 Catholic 的旧“常用”是大小写折叠导致的不可靠采用，不能为避免空白再提升。后续需要额外的固定原文或可靠词典材料，不能猜填。

## 判定与展示

1. 明确可靠的意义可以独立通过；原 POS 不可靠则保持空。例如 April/月份/Bible/Christmas 源错误 pronoun，不改猜 noun，仍显示正确原中文。
2. 英汉词典可以只有确切词头和中文对应，没有另附英英解释。明确词头译文可保存为 `word_translation`，不假称逐英文义项一一对应。
3. 多个英文义共用一中文节点，包括嵌套在同一个 li 中的情形，统一保留为整词译文并记录对应限制，不伪造多义结构。
4. 词源、例句、图片、分类、未知模板和明确错译不在普通正文展示。保留完整原文、旧字段、之前的抽取判定及新的排除理由。
5. 原核心释义的人工确认契约不变。普通来源显示优先明确独立释义，按每词性最多 3 条显示；额外值完整保留在“更多词典释义”里，3 条不是整词上限。
6. 正文标为“词典释义 · 自动来源核验”；详细待核/排除信息在默认折叠的“查看词典来源”中。词库切换到另一个词时重新折叠，详情底部“查看来源说明”保持默认折叠。
7. canonical `phonetic`、`part_of_speech`、`source_meanings`、`source_raw`、锚点、用户状态、备注及覆写均不改变。

来源音标另有 **5287** 词通过固定来源、词头绑定和 IPA 格式检查；**这仅表示源中音标可引用，不表示已经作发音学核准、标准读音或英美口音裁定**。保留来源变体、不选择标准读法，canonical 音标字段继续原值。原有 790 词“明确英语区 IPA”与此扩展的来源格式检查口径不同，不能混称全部都已确认标准音标。

## 原文对照示例

| 词 | 本轮可用内容 | 被隔离内容或范围限制 |
| --- | --- | --- |
| April | 四月；词性空；来源音标及原位置保留 | 上游 pronoun 不提升 |
| August/Bible/Christmas/December | 明确词头与原中文；词性空 | 同类错误 pronoun 保留在来源证据 |
| play | 动词：遊玩、演奏、玩；名词：剧 | 旧式 `{{=n/v=\|英\|play}}` 明确 POS；独立原行重新核对，不因无英英解释禁展；打/拉的范围仍待核 |
| run | 动词，跑 | 只保留整词译文，不把共用英义拆成多个中文义 |
| set | 来源引用行的放置、确定、一套，及其它通过节点 | 但是、受体、袖、睡觉等不提升；逐义映射靠具体引用行，不按分号机械切割 |
| above | 上边、多（保留相应范围） | over/on top of 与“酸”不对应，排除 |
| compass | 指南针、明确 noun | 不再仅因没有逐词审阅清单而隐藏 |
| a | 不定冠词义及字母义；明确 article 映射冠词 | “然后”错配、字母词源长段隔离 |
| critic | 批评家/评论家等可靠原义 | 疑似源笔误“苟评家”保留待核，不改字 |
| Catholic | 清楚提示缺可用词典释义 | 旧“常用”没有可靠大写词头对应，不回填到正文 |

77 个既有补词来源沿用原发布的来源、版本、许可证、署名和中文选择/翻译身份。重新校验其固定包、CSV、引用行和词形关系，并作本次对照；没有把历史批准当新语义证据，也没有新增译文。由英文义翻译或选择整理的内容显示相应身份，不包装成逐字中文源文。

## 固定计划与证据

- 全量候选：`test-artifacts/netem-source-audit-20261008/full-candidate/plan.json`
- SHA256：`6ff4887afeaaa22d293937c4ae44373c4981e637c0e09ad131a606d77cb3a83b`
- 小批量：`pilot-candidate/plan.json`；SHA256 `6c4eed81e1c910a2c2d67958859a0ce7d3719bddc785dac7e9447abf135af05c`
- 全量逐词预览：同候选目录的 `per-word.jsonl`；详情及代表原文在 `representatives.json`。
- 冻结审阅：`locked-review-0/1/2.json`、`locked-review-existing-77.json`；针对 play 的独立纠错在 `review-corrections.json`，绑定前一审阅摘要，不覆盖旧审阅文件。
- `records.json` 保存本轮输入抽取，SHA `d5378c330a3b236cb101b01d5c508724a1228df834e86fc78e2c6b523727fcdb`。原 ZIP/缓存/固定修订原文的指纹与此前证据一致，在计划 `source_files` 中逐项列出。

## 验证结果

- 原已覆盖功能和本轮相关后端回归：**115 passed**；最终旧 POS 标记、纠错摘要、API 与并发回滚相关：**22 passed**；新受保护发布入口实际认证/备份/增量/回滚：**3 passed**。
- 完整前端：**213 passed / 23 files**，TypeScript、独立目录生产构建及本轮 Ruff/ESLint 通过。
- 100 词小批量：95 词可用释义、98 词来源 IPA；包含要求的 7 词、专名、缺源、错误片段、模板/词源、多义和多词性。
- Playwright + Chrome：桌面 1440×1000、手机 390×844，小批量和全量隔离副本均 **207 项通过**；从词库分栏打开 April 的用户实际路径也覆盖。
- 应用/API 错误 0。开发 Vite 默认 favicon.ico 的既有 404 单独留档，按确切 URL 排除；没有忽略其它资源错误。报告为 `browser-pilot-final.json`、`browser-full.json`。
- 实际查看了全量副本的桌面 April 和手机 play 截图，布局和多词性来源值正确。
- 全量隔离 apply **5528** → 同收据 repeat **0** → 局部 rollback **5528** → repeat rollback **0**。原业务表逐行指纹不变，全部原抽取行恢复；见 `full-preservation.json`。
- updater 新增旧抽取 SHA 并发保护：后续审阅漂移整次拒绝；源/审阅文件改变、缺候选决策、重复决策、纠错旧摘要不符也拒绝。

尚不能保证自动审阅无遗漏；没有作全库人工语义核准、真实手机硬件测试或权威口音裁定。所有可疑信息仍有来源定位和可撤回记录，正式更新不会新增人工 confirmer。

## 复跑与撤回

从 `D:\背单词web` 执行。预览和 clone 需要新目录名，保留原审阅与证据；只使用明确隔离库。

```powershell
$py = './backend/.venv/Scripts/python.exe'
$root = 'test-artifacts/netem-source-audit-20261008'
& $py tools/phase29/netem_source_audit.py preview --database data/vocab.db --inventory "$root/records.json" --review "$root/locked-review-0.json" --review "$root/locked-review-1.json" --review "$root/locked-review-2.json" --supplement-review "$root/locked-review-existing-77.json" --corrections "$root/review-corrections.json" --out "$root/rerun-preview-NEW"
& $py tools/phase29/netem_rich_import.py clone --source data/vocab.db --database "$root/rerun-db-NEW/vocab.db"
& $py tools/phase29/netem_rich_import.py apply --database "$root/rerun-db-NEW/vocab.db" --plan "$root/full-candidate/plan.json" --plan-sha256 6ff4887afeaaa22d293937c4ae44373c4981e637c0e09ad131a606d77cb3a83b --receipt "$root/rerun-undo-NEW.json"
# 原命令重复：0。撤回只恢复抽取行：
& $py tools/phase29/netem_rich_import.py rollback --database "$root/rerun-db-NEW/vocab.db" --receipt "$root/rerun-undo-NEW.json"
```

正式入口已经具体实现为 `tools/phase29/netem_source_audit_release.py`，固定只接受本候选 SHA，沿用真实干净提交、固定生产路径、当前管理员认证、当刻新备份、写锁内身份和旧抽取核验、全旧表保留比对及局部收据。0016 schema 已存在，无自动迁移。**本轮尚未调用正式写入。**

正式更新必须在本候选获得用户最终发布确认后执行；当时再次核验代码锁定、原字段与抽取基线，按既有停止脚本停写后进入发布。口令用已有本机管理员认证，不重复恢复密码。成功后的操作目录包含 `before.db`、`backup.json`、`result.json`、`batch-undo.json`。

```powershell
# 用户确认本候选之后，使用已冻结的真实提交 SHA；口令仅本机输入或 DPAPI 临时凭据。
$releaseSha = '<本次真实干净发布提交SHA>'
& scripts/stop-vocab.ps1
& $py tools/phase29/netem_source_audit_release.py apply --database data/vocab.db --operation "$root/production/publish-NEW" --code-sha $releaseSha --admin admin --confirm 6ff4887afeaaa22d293937c4ae44373c4981e637c0e09ad131a606d77cb3a83b --production-update
& scripts/start-vocab.ps1

# 需撤回时：先停写，新备份由入口创建，只恢复本批抽取；不覆盖发布后的学习进展。
& $py tools/phase29/netem_source_audit_release.py rollback --database data/vocab.db --operation "$root/production/rollback-NEW" --code-sha $releaseSha --admin admin --confirm 6ff4887afeaaa22d293937c4ae44373c4981e637c0e09ad131a606d77cb3a83b --receipt "$root/production/publish-NEW/batch-undo.json" --production-update
```

若当前 payload 已被后续变更，整个局部回滚拒绝，不用旧整库覆盖新学习记录。上次发布的旧收据仍保留，只能在其当时 payload 仍匹配时撤回原批次；本次收据会恢复上次完整抽取内容而非删除它。
