# NETEM 默认词库来源与再分发

本目录是 NETEM 默认词库的静态导出，不是运行时数据库的完整备份。词头、释义和补缺来源分别遵循不同许可，必须分开标注，不能把 BY-SA 释义改称 NC，也不能把 NETEM 词表称为 BY-SA。

## 完整释义导出

`netem-meanings.csv` 共 5528 行，按 `netem-words.csv` 的 `sequence` 排序。每行包含词头、释义、来源文件、来源定位、固定修订号、来源链接、许可证和来源档案指纹。来源分布如下：

| 来源 | 行数 | 许可 | 说明 |
| --- | ---: | --- | --- |
| WikDict en-zh | 4814 | CC BY-SA 4.0 | 固定 StarDict 包的抽取、去标签、去重和拼接 |
| 中文维基词典 | 637 | CC BY-SA 4.0 | 逐词固定 oldid；旧修订的 3.0 → 4.0 路径见 `zhwiktionary.SOURCE.md` |
| 英文维基词典补缺 | 77 | CC BY-SA 4.0 | 55 条中文翻译表选取，22 条据固定英文定义整理；不是人工逐词核准 |

`netem-meanings.csv` 是这些来源的汇总展示文件；拆分使用 `wikdict.csv`、`zhwiktionary.csv` 或 `en-wiktionary-supplement/` 中的任一文件时，必须携带该文件对应的 `SOURCE.md`／`SOURCE.json`。中文维基词典文件还必须同时携带 `zhwiktionary-attribution.csv`。

## 词头与选序：NETEM

`netem-words.csv` 的词头与排列来自 `exam-data/NETEMVocabulary` 固定提交 `70dc6b68c855f21e666a7a291ff8ead5ca1f7b44` 的 `netem_full_list.json`，许可为 **CC BY-NC-SA 4.0**：

- 来源：<https://github.com/exam-data/NETEMVocabulary/tree/70dc6b68c855f21e666a7a291ff8ead5ca1f7b44>
- 固定数据文件：<https://raw.githubusercontent.com/exam-data/NETEMVocabulary/70dc6b68c855f21e666a7a291ff8ead5ca1f7b44/netem_full_list.json>
- 原文件 SHA-256：`6d71a301321056291902bc4804e223c6926dca0d629a45076a6ca5adab185f62`
- 许可：<https://creativecommons.org/licenses/by-nc-sa/4.0/>

本项目只取单词与序号；按 `strip().casefold()` 首见去重，5530 行保留 5528 词及原顺序。上游排序使用词形还原；本项目没有取上游释义、词频、其他拼写或英英义。词头文件及其选择排列的本项目改编以 CC BY-NC-SA 4.0 提供。来源按原样及可用状态提供，无准确性等保证；署名不表示来源方认可本项目。

## 中文释义：WikDict

`wikdict.csv` 来自 WikDict en-zh 固定包（包内日期 2026-06-23），包声明 **CC BY-SA 4.0**；上游 DBnary 声明 3.0，改编的同要素后续版本路径见 3.0 第 4(b) 节。本项目按词头大小写折叠精确匹配，提取含中文的译文片段，去标签、去重并用分号拼接，剔除已知问题释义。没有逐词修订号，以 ZIP 指纹及 StarDict 索引和正文偏移定位；下载直链可能更新，字节不匹配以下指纹即不是本版本。

- 固定包 SHA-256：`62d6d4a8ccf28c28bbe4ec82ac65fa67fd52c0f34d71294ab1d9d95fa3233830`
- 来源与修改说明：<https://www.wikdict.com/page/about>
- 下载页：<https://www.wikdict.com/page/download>
- DBnary 与 3.0 声明：<https://kaiko.getalp.org/about-dbnary/>

## 中文释义：中文维基词典

`zhwiktionary.csv` 来自中文维基词典逐词固定旧修订，许可为 **CC BY-SA 4.0**，旧修订的许可路径及逐词归属见 `zhwiktionary.SOURCE.md` 和 `zhwiktionary-attribution.csv`。本项目仅在整词无 WikDict 时采用固定英语小节的中文释义，去模板及标记、去重并以分号拼接。`cache:def` 是旧清洗缓存定义序号，不是原文行号；`wikitext:line` 才是原文行。逐词固定页面、历史与贡献者列表必须随 `zhwiktionary.csv` 一起提供，不能将该表省略。

## 补缺释义：英文维基词典

`en-wiktionary-supplement/` 中的 77 个 CSV 用于补足首发包的空义词，来源为英文维基词典固定 oldid，许可为 **CC BY-SA 4.0**。其中 55 条为固定中文翻译表选取，22 条为依据固定英文定义整理的中文译释；后者在学习页应标识为“AI 译释”，未经人工逐词核准。每词 CSV 保留原 oldid、原 wikitext、选取或翻译说明；`ledger.json`、`fingerprints.json` 及各文件 `.SOURCE.json`／`.SOURCE.md` 保留逐词记录和指纹。

## 许可与再分发

- `NETEM-LICENSE.txt` 是词表使用的 CC BY-NC-SA 4.0 全文。
- `CC-BY-SA-4.0.html` 和 `CC-BY-SA-3.0.html` 是释义来源适用许可的固定文本快照。
- 提供或导出任一来源文件时，必须保留对应署名、原许可／免责说明、修改说明和逐词定位；不得对接收者依许可享有的权利附加额外限制。
- 本项目不主张该集合的额外数据库专有权；NETEM 的非商业与 ShareAlike 条件只适用于词头及其选择排列，不自动覆盖 BY-SA 释义。
