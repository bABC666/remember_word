# NETEM 来源恢复：最终隔离验证与发布候选

后续用户要求联网补齐最后91词，结果见 [NETEM-WEB91-VALIDATION-2026-10-09.md](NETEM-WEB91-VALIDATION-2026-10-09.md)。本报告与29e90f候选保留为前阶段证据，最终发布候选已被新版取代。

已从上一版候选的 1070 个空缺中恢复 **979 词**，可展示词典释义达到 **5437 / 5528（98.35%）**；原 4458 个可展示词没有退回空缺。**正式库仍为此前已发布的抽取版本，本候选尚未正式写入。**

上一份报告把“现有解析候选没有可用值”过早当成需要补充上游来源。实际还包括未保存的固定修订原文、旧格式漏读、模板留置和仅词性问题导致的整词隐藏。本报告取代旧 `6ff488…` 候选的最终发布建议；旧报告、候选和审阅文件均保留。

## 覆盖与边界

| 指标 | 前一未发布候选 | 本次最终候选 |
| --- | ---: | ---: |
| 可展示中文释义 | 4458 | **5437** |
| 有来源明确词性且有可用意义 | 4216 | **4451** |
| 多词性 | 515 | **561** |
| 多个可用来源显示值 | 1638 | **2181** |
| 有可用但词性留空的意义 | 308 | **1135** |
| 仍无可用中文义项 | 1070 | **91** |
| 来源 IPA 通过格式与词头绑定检查 | 5287 | **5297** |
| 新增人工确认核心释义 / 改 canonical 音标 | 0 / 0 | **0 / 0** |

多个显示值可能是同义译词、地区字形、领域义或繁简变体，不声称全部都是不同常用义项。5297 个来源音标覆盖只表示固定来源、英语区、词头绑定和格式检查；没有发音学核准、口音裁定或标准音标选择。

共处理 14,451 个保留候选/引用值，包括本轮恢复和带原文的格式规范化记录。旧抽取与旧判定仍有证据，不把新规范化记录虚称新增的独立词义。错误片段 2012 项、未决片段 2160 项保留在折叠来源区。原始失败信息另存 `audit.input_failures`；已经补存原文或恢复意义的失败不再混入当前失败统计。未补存的其它维基修订通常已有 WikDict 可用值，不能把“3856 个修订未保存”当作 3856 个词仍缺释义。

## 实际恢复与排除

- 验证原 ZIP、缓存及先前保存的原文指纹；补存 **716 / 716** 个已记录的固定中文维基词典修订号，保存实际 API 响应、修订、词头、完整正文及 SHA。缓存只用于找修订号，不作为最终释义。
- 限定英语标题或明确旧式英语语言标记。旧葡语/满语切换会结束英语区域；menu、linear、naval、nest 的其它语言片段不提升。
- 补齐普通无 POS 中文行、星号与 `*#` 定义、旧 POS 模板及同行词形模板、明示中文翻译行、带 IPA 的内联 POS 和地区字形标记。按原解析器实际收录行去重，修复 `{{-en-}}` 下普通行与编号行漏取。
- 仅展开明确的标签、英语链接和简单单地区字形结构；保留地区/领域/语域限定、展开前文本、原行与定位。未知模板仍留置。原分号串完整保留，没有机械切成多个“义项”。
- 候选必须有具体语义审阅记录，绑定 entry ID、词头、原文体 SHA、行号、原串、实际 POS 标记和审阅 SHA。不能仅因为字段出现就通过。

| 词 | 来源对照结果 |
| --- | --- |
| Catholic | 固定大写词头原文“天主教的”恢复；POS、音标无证据则留空；旧“常用”不回填 |
| against | `介系詞` 标题和 `*#` 原行支持“針對、抵抗、對著” |
| ability / aboard / aeroplane | 能力、在船上及飛機原内容恢复；词性只取明示标记 |
| administer / whisky | 明确标签展开后恢复“管理，治理”等、威士忌酒；分摊/法律贈予等疑点不解除 |
| mean / grown-up | mean 第一独立中文节点“卑鄙”无需另附英英解释才能保留；grown-up “成人”概念对应，展示 POS 留空，不猜 noun |
| play / set / run | 保留原复核的多词性、引用行义项及共用译文范围；每词性最多3条，更多值可展开 |
| electric / pop / rebellion | 分别剔除混植的“本质/要点”“私人的/士兵/阴部”“答覆/应答”等块 |
| above / about | 酸、蜜蜂等原错误片段继续排除 |

CC-CEDICT 标记单独核对了 [固定模板旧修订](https://zh.wiktionary.org/w/index.php?oldid=5583383)、[项目官网与贡献者](https://cc-cedict.org/wiki/) 和 [CC BY-SA 3.0 正文](https://creativecommons.org/licenses/by-sa/3.0/legalcode)。保留旧原料的 3.0 声明和额外署名，仅本项目选择、格式整理及标注的改编按第4(b)节后续版本路径提供4.0；没有因官网现在标4.0而倒推旧原文改许可。45 项先通过语义检查的来源值补齐署名后展示；语义拒绝、未知模板不因许可检查放行。所有条件快照在 `license-evidence/`，来源链接在默认折叠的详细依据中。

## 剩余91词

逐词详见 `test-artifacts/netem-source-audit-20261008/full-recovery-locked/remaining-gaps.json`，不是永久不可解决的名单。

- **7词**：保留原文中仍没有中文候选：certify、dubious、nominate、retail、surpass、surround、theatre。它们有原文件，并非未找到文件；缺的是可引用的中文内容。
- **26词**：现有候选全部明确不对应。例如 dioxide→二氧化碳把泛称限制为碳；snobbish→草莓；lease 的原英义是租住者行为，中文却是出租者动作。
- **58词**：仍有字误、截断、模板、语义范围或其它第三方来源条件疑点。例如 inhale 的“吸人”、barn 整串混入“靶恩”、mountain 长段标明 MGH/WRIGHT；不改字、不删除疑点后包装通过。

后续需要另行补充可引用的正确原文或解决对应来源条件。当前没有猜填这91词，也没有把自动判断登记为人工确认。

## 固定证据与验证

最终全量计划：`full-recovery-locked/plan.json`，SHA256 **29e90f367ea571773b64aedeb639bd59d01ac1c073088b0de2a4e53a3a11cb10**。包含343份证据指纹；逐词预览在同目录 `per-word.jsonl`，本轮恢复差异在 `recovery-delta.json`。原冻结审阅不覆盖，新纠正文件绑定之前的决策摘要。

100词最终试点：`pilot-recovery-diverse/plan.json`，SHA256 **c3e1a273ceb219cb96f6396129788bfb4c4c06dd8ff8ef3bc04b3884d805cc05**；97词可用意义、85词明确POS、97词来源IPA，另含 certify、snobbish、mountain 三个真实负例。

- 后端相关解析/API/更新/受保护发布回归：**53 passed**。最终计划认证、备份、回滚与审阅守卫再核：**26 passed**。
- 前端完整：**214 passed / 23 files**；TypeScript、独立目录生产构建、相关 Ruff/ESLint 通过。
- 最终100词桌面1440×1000、手机390×844验收报告：`browser-recovery-pilot-diverse-final.json`，**315项通过**。全量同尺寸：`browser-recovery-full-locked-diverse.json`，**387项通过，应用/API错误0**。包括真正从词库分栏打开 April、今日学习显示答案、多义每POS上限、折叠证据、未知POS意义及真实缺口。
- 实际检查了桌面 Catholic 和手机 against 截图。使用Chrome视口模拟，未声称真实手机硬件验收。
- 试点和全量均通过 apply→repeat→局部rollback→repeat；全量分别 **5528 / 0 / 5528 / 0**。所有原业务表逐行指纹相同，全部原抽取行恢复，integrity_check=ok，foreign_key_errors=0。
- 独立只读复核最终计划及证据，未发现确定剩余P1/P2；没有读取凭据或写正式库。

重演收据与保留验证：`pilot-recovery-diverse-rehearsal/{batch-undo.json,preservation.json}`、`full-recovery-locked-rehearsal/{batch-undo.json,preservation.json}`。这些是隔离收据，不能用于正式库。

验收曾出现旧共享 `.pytest-tmp` 删除权限错误，改用新的显式 basetemp 后通过；扩大浏览器批次时反复完整Vite重载耗尽Windows连接缓冲区，改用实际SPA路由切词；另补API健康检查避免启动竞态。原失败报告保留，没有忽略其它HTTP/应用错误。原环境的Python子进程启动元数据解码告警也留在隔离日志中；不影响API响应与最终页面检查。

## 复跑与发布/撤回

从仓库根目录执行，输出必须是新的 `test-artifacts` 目录，输入库只读。命令和写入保护代码均已纳入本次冻结范围。

```powershell
$py = './backend/.venv/Scripts/python.exe'
$evidence = 'test-artifacts/netem-source-audit-20261008'
& $py tools/phase29/preview_netem_recovery.py --database data/vocab.db --out "$evidence/rerun-preview-NEW"
& $py tools/phase29/preview_netem_recovery.py --database data/vocab.db --limit 100 --out "$evidence/rerun-pilot-NEW"
& $py tools/phase29/rehearse_netem_recovery.py --source data/vocab.db --plan "$evidence/full-recovery-locked/plan.json" --plan-sha256 29e90f367ea571773b64aedeb639bd59d01ac1c073088b0de2a4e53a3a11cb10 --out "$evidence/rerun-rehearsal-NEW"
```

正式写入遵循用户先前要求：先交付本次具体预览和保留/回滚验证，**取得本新版最终发布确认之后**，才能调用正式入口。先前批准的旧固定计划不是本次变更后的正式授权。

发布时锁定真实干净提交，检查正式路径、当前管理员认证及现有0016 schema；停写后当刻新备份，写锁内再核身份和原抽取，增量更新5528行，不迁移已存在schema，不删除词库，不改ID、旧字段、学习历史、备注或覆写。恢复现有管理员认证，不再次重置密码。

```powershell
# 本新版确认后，用本次真实提交SHA；口令仅本机输入或既有本机DPAPI流程
$releaseSha = '<本次真实干净提交SHA>'
& scripts/stop-vocab.ps1
& $py tools/phase29/netem_source_audit_release.py apply --database data/vocab.db --operation "$evidence/production/publish-NEW" --code-sha $releaseSha --admin admin --confirm 29e90f367ea571773b64aedeb639bd59d01ac1c073088b0de2a4e53a3a11cb10 --production-update
& scripts/start-vocab.ps1
```

`start-vocab.ps1` 在停止后的正常启动中重新构建当前前端；若旧8000服务仍运行，快捷方式只打开旧实例，不会替换它。因此本次正式发布需停止并重启，之后以实际快捷方式再核页面，不能只验证隔离Vite。

正式操作目录会留下 `before.db`、`backup.json`、`result.json`、`batch-undo.json`。需撤回时先停写，再用新操作目录作当刻备份，只恢复本批抽取行；若已经被后续变更，整次局部回滚拒绝，不能用旧整库覆盖新学习记录。

```powershell
& scripts/stop-vocab.ps1
& $py tools/phase29/netem_source_audit_release.py rollback --database data/vocab.db --operation "$evidence/production/rollback-NEW" --code-sha $releaseSha --admin admin --confirm 29e90f367ea571773b64aedeb639bd59d01ac1c073088b0de2a4e53a3a11cb10 --receipt "$evidence/production/publish-NEW/batch-undo.json" --production-update
& scripts/start-vocab.ps1
```

本轮只更新NETEM恢复、对应测试与发布工具。四份既有未跟踪历史草稿保留，不纳入提交。
