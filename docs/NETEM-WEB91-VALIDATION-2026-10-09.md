# NETEM 最后91词：联网补词与隔离验收

按用户新增要求联网查词，最后91个空缺均已有可展示内容。最终候选 **5528 / 5528 词有来源释义，空缺0**。本次为91词补入275个选择值：**92个原中文译文、183个据英文定义忠实整理的中文译义**；派生身份在界面明确显示。没有把它们登记为人工确认核心释义。**正式库尚未写入。**

检索与原文保存开始于2026-10-08，最终验收于2026-10-09完成。本报告取代此前5437词/91空缺候选的发布建议；旧候选、原文、审阅、错误片段和回滚收据保留。

## 来源与逐义核对

联网保存英文维基词典91词的实际页面、中文维基词典对照材料及六个原文明确引用的拼写/同义目标，合计187份固定页面正文（含许可页）。API原始响应、修订号、UTC版本时间、确切词头、正文/文件SHA全部保存。直接中文仅来自英语义项翻译表的明确中文字段；中文站的错译和错标语言头没有采用。

[英文维基词典版权说明](https://en.wiktionary.org/wiki/Wiktionary:Copyrights)允许按CC BY-SA 4.0条件复用其原创条目文字。候选保留贡献者历史、固定页面、许可证链接、原文与本次选择/翻译的修改说明。外部引用材料仍遵循其原条件，本次义项不取例句、引用段、词源、参考材料或其它语言内容。

每个值绑定词头、固定版本、英语小节、原定义行、实际POS标题、中文模板字段或明确的派生身份。拼写变体只沿原页明示的关系跟随，没有把不同词形凭常识混合。275个值全部重新绑定并复核，语域及适用范围保留；原分号不会自动拆造成多个义项。

| 词 | 本次补入与核查 |
| --- | --- |
| certify | 动词：證明；以书面形式认证或核实；认证达到官方标准。原译和派生分开标识 |
| dioxide | 名词：二氧化物。[原定义与中文表](https://en.wiktionary.org/wiki/dioxide)明确两个氧原子；旧二氧化碳片段留在证据，不继续展示 |
| lease | 分清租赁权、租约、租期，以及出租者/承租者的动作，不再将租住义硬配出租 |
| odds | 概率之比；赔率。没有把ratio缩成一般概率 |
| o'clock | ……点钟及钟面方向，保留与一至十二数字连用范围；不将仅one o’clock的中文表当整词义 |
| him | 他（he的宾格），不采用旧“他的” |
| snobbish | 势利的、自命不凡的，依据英文定义整理；草莓旧错译保留但排除 |
| aluminum / generalise / industrialise / sceptical / theatre | 明确拼写关系和目标定义双方原页均保留，POS与关系行独立核对 |
| double | 形容词、副词、名词、动词分别分组，不以3条为整词上限 |

独立复核发现并修正：nation同一原行后半country的“不推荐”不能套到前半主权国家义；fetch的also figuratively应显示“亦可用于比喻”；reveal名词必须保留“影视或故事中”的范围。nation、obsession、powder的原中文表不足以表达具体范围时，改为明确标记的忠实派生译义。所有5项独立纠正都绑定之前选择的摘要，原审阅文件不覆盖。

## 覆盖、预览和验收

| 指标 | 本次最终候选 |
| --- | ---: |
| 有可展示中文意义 | 5528 |
| 有明确来源POS且有意义 | 4542 |
| 多词性 | 600 |
| 多个来源显示值 | 2259 |
| 仍无可用中文意义 | 0 |
| 来源IPA格式/词头绑定检查覆盖 | 5300 |
| 人工核心确认新增 / canonical音标变更 | 0 / 0 |

5300不是标准读音核准或口音裁定。多个来源显示值可能包括同义译词/变体，不声称都是不同常用核心义。986词仍没有可通过的POS，但可显示可靠意义，不猜补字段。原有未决/错误片段仍在折叠来源中，不能把这些计数当作还有相同数量的空词。

最终全量：`test-artifacts/netem-web91-20261008/full-final/plan.json`，SHA256 **91eaf2f7d9ea5c397fa09a6794251cfe14156e0a0b18b0987f4c62878a781610**；逐词预览 `per-word.jsonl`，本次91词完整记录 `online91.json`。366份来源证据指纹绑定在计划中。

试点恰为100词：全部91个缺口，加abundant、above、absent、run、set、play、April、mean、grown-up。`pilot-final/plan.json` SHA256 **8152c7a4b7509dd2a2dbc0ed7361845b3c406e47c9bb83acf2952569e888885f**；100词有意义、98词有POS。

- 相关后端解析/API/更新/保护回归53项通过；作用域修复及最终受保护发布/备份/回滚12项通过。
- 前端完整216项/23文件通过；TypeScript、独立构建及相关Ruff/ESLint通过。没有覆盖正式前端构建目录。
- 试点、全量均实际检查全部91词桌面1440×1000和手机390×844，各 **1264项通过，捕获的JS异常与API失败均0**。覆盖源原译/派生身份、词性、折叠证据、移动布局和正确范围，今日学习使用odds验证派生译义。
- 报告 `browser-pilot.json`、`browser-full.json`，每批12张截图。实际看过手机lease截图；使用Chrome视口模拟，未声称实体手机验收。
- 100词apply/repeat/rollback/repeat为100/0/100/0；全量为5528/0/5528/0。所有原业务表指纹相同，全部原抽取行恢复，integrity_check=ok、foreign_key_errors=0。保留验证与隔离收据在 `pilot-final-rehearsal/`、`full-rehearsal-final/`。
- 独立复核最终275值、366文件指纹与187页面：没有剩余确定P1/P2；91词旧senses完整保留，其余5437词payload与之前固定候选一致。

D盘空间不足曾中断副本建立和独立构建；清理本聊天可复建的临时SQLite副本后重演成功。正式库、原始词典、审阅、预览及JSON回滚收据均保留。已结束临时9018/5198服务并删除其QA凭据；正式8000服务未更改。

## 可复跑与局部回滚

从`D:\背单词web`执行，只读指定输入库，输出使用新的test-artifacts目录。

```powershell
$py='./backend/.venv/Scripts/python.exe'
$root='test-artifacts/netem-web91-20261008'
& $py tools/phase29/netem_web_supplement.py --database data/vocab.db --base-plan test-artifacts/netem-source-audit-20261008/full-recovery-locked/plan.json --evidence $root --review "$root/review-web91-0.json" --review "$root/review-web91-1.json" --review "$root/review-web91-2.json" --corrections "$root/review-web91-corrections-final.json" --out "$root/rerun-preview-NEW"
# 加 --pilot 可复建本次100词试点
& $py tools/phase29/rehearse_netem_recovery.py --source data/vocab.db --plan "$root/full-final/plan.json" --plan-sha256 91eaf2f7d9ea5c397fa09a6794251cfe14156e0a0b18b0987f4c62878a781610 --out "$root/rerun-rehearsal-NEW"
```

本次替换为包含全部联网补词的新固定计划。遵守用户最初要求，交付此具体预览和保留验证后，仍需新版最终发布确认；之前固定候选的批准不直接授权改变后的新计划。

发布入口沿用真实干净提交锁定、正式库固定路径、当前管理员认证、维护停写、当刻新备份、事务内身份与原抽取SHA核验、全旧表保留核验及局部收据。0016 schema已存在，不重建词库或再迁移；不改词条ID、学习历史、文章暴露、个人备注或覆写。管理员继续使用现有本机认证，不再次重置密码。

```powershell
# 新版确认后，用本次真实冻结提交SHA；口令仅在本机输入或现有DPAPI流程中处理
$releaseSha='<本次真实冻结提交SHA>'
& scripts/stop-vocab.ps1
& $py tools/phase29/netem_source_audit_release.py apply --database data/vocab.db --operation "$root/production/publish-NEW" --code-sha $releaseSha --admin admin --confirm 91eaf2f7d9ea5c397fa09a6794251cfe14156e0a0b18b0987f4c62878a781610 --production-update
& scripts/start-vocab.ps1
```

正常重启会重新构建当前前端；快捷方式在旧8000实例未停止时只打开旧实例。因此正式发布要停止并重启，之后从实际快捷方式核验页面，而非只看Vite。

正式操作目录包含before.db、backup.json、result.json、batch-undo.json。回滚另作当刻备份，只恢复本批抽取，不覆盖其后学习进展；若抽取又被后续变更，整个回滚拒绝。

```powershell
& scripts/stop-vocab.ps1
& $py tools/phase29/netem_source_audit_release.py rollback --database data/vocab.db --operation "$root/production/rollback-NEW" --code-sha $releaseSha --admin admin --confirm 91eaf2f7d9ea5c397fa09a6794251cfe14156e0a0b18b0987f4c62878a781610 --receipt "$root/production/publish-NEW/batch-undo.json" --production-update
& scripts/start-vocab.ps1
```

隔离重演收据只能用于对应测试库，不能替代将来的正式发布收据。四份既有未跟踪历史草稿未纳入提交。
