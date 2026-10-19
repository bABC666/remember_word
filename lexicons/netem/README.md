# NETEM 默认词库数据

本目录是 NETEM 默认词库的静态数据导出，用于版本归档、分发和在新环境中复现词库内容。它不包含任何账号、会话、学习状态、复习记录、上传图片或私有词库。

## 文件

| 文件 | 内容 |
| --- | --- |
| `netem-words.csv` | 5528 个 NETEM 词头及上游序号；词头与选序来源为 `exam-data/NETEMVocabulary` 固定提交 |
| `netem-meanings.csv` | 5528 条完整的中文展示释义；每行带来源文件、来源定位、固定修订号、来源链接和许可证 |
| `wikdict.csv` | 4814 条来自 WikDict en-zh 的中文释义证据 |
| `zhwiktionary.csv` | 637 条来自中文维基词典的中文释义证据 |
| `zhwiktionary-attribution.csv` | 中文维基词典逐词固定修订与归属 |
| `en-wiktionary-supplement/` | 77 个补缺词的英文维基词典 CSV、SOURCE 文件、逐词记录和指纹 |
| `SOURCES.md` | 署名、修改说明、许可和再分发条件 |
| `NETEM-LICENSE.txt` | NETEM 词表使用的 CC BY-NC-SA 4.0 全文 |
| `CC-BY-SA-4.0.html`、`CC-BY-SA-3.0.html` | 释义来源适用的 CC BY-SA 许可文本快照 |
| `SHA256SUMS.txt` | 本目录文件的完整性指纹 |

## 数据口径

- 5528 个去重后的词头，`sequence` 为 1–5528；上游文件中的原始序号保留在 `netem-words.csv` 的 `source_position` 中。
- 每个词恰好有一条选定的中文展示释义，因此 `netem-meanings.csv` 也是 5528 行。
- 当前导出的正式词条没有音标和词性字段；更丰富的词典抽取 payload 没有放进本目录。
- 来源分布：WikDict 4814 条、中文维基词典 637 条、英文维基词典补缺 77 条。

## 为什么不直接提交 `data/vocab.db`

本地正式库约 529 MB，超过 GitHub 的普通单文件限制，而且含有用户、会话和学习记录。本目录只导出公共 NETEM 词库的静态内容，运行时数据库仍由 `data/vocab.db` 保存。

## 许可

词头与选序使用 **CC BY-NC-SA 4.0**；释义使用 **CC BY-SA 4.0**（以 `netem-meanings.csv` 的 `license_id` 为准）。两类材料不能互相改称许可。复用或再分发时必须随附对应署名、修改说明、许可链接和逐词来源信息。详见 [SOURCES.md](SOURCES.md)。
