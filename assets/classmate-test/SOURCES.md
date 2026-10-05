# 固定公共测试数据与许可

此目录是独立来源文件的集合，没有一个覆盖全部成员的单一许可证。构建仅读取此目录，不读取数据库、上传、日志、配置或本机发布证据。没有生产账号、生产词条ID、会话或学习历史；`gap/choices.json`只含固定公共材料的采用决策。

| 材料 | 固定版本与归属 | 许可与修改 |
|---|---|---|
| NETEM 5528词头及顺序 | exam-data/NETEMVocabulary及贡献者，提交70dc6b68c855f21e666a7a291ff8ead5ca1f7b44；5530行首见去重，恢复上游词形 | CC BY-NC-SA 4.0；仅词头/顺序，未使用其释义。完整正文见base/NETEM-LICENSE.txt，归属及固定README/历史链接见netem-words.SOURCE.* |
| WikDict 4814释义 | Karl Bartel、WikDict、FreeDict、DBnary及Wiktionary贡献者；固定ZIP摘要见wikdict.SOURCE.json及原.ifo | DBnary原CC BY-SA 3.0，WikDict及本项目HTML抽取/整理改编CC BY-SA 4.0；保留原notice和链条。没有虚构逐词修订号 |
| 中文维基词典637释义 | 逐词固定oldid、贡献者历史及原许可见zhwiktionary-attribution.csv | 2023-06-07前采用修订原CC BY-SA 3.0，其后4.0；清洗、选择、去模板、去重、拼接改编采用4.0；见zhwiktionary.SOURCE.* |
| 英文维基词典77补缺 | 固定oldid/原文行/异形关系见gap/choices.json、CSV、各SOURCE.*及snapshots；English Wiktionary contributors，项目/Codex改编 | CC BY-SA 4.0文本改编；55条中文表选取、22条AI翻译整理。均未做人类逐词语义核准，不是人工确认短义；原文与修改分别标识 |

CC BY-SA 3.0及4.0完整正文、Wikimedia条款的固定公共快照随gap/snapshots提供；页面及历史快照保留原归属、引用与链接，只作采用材料定位，不将其中第三方引文当作项目自产文本或新增释义。原出处、许可、修改及免责均随各文件提供。独立拆分CSV须携对应SOURCE附件，中文维基文件还须携逐词归属表。混合集合不将BY-SA释义改为NC，也不将NETEM改为BY-SA；项目不主张集合的额外数据库专有权，不限制第三方公开许可赋予的权利。

base公共材料取自冻结首发候选0420923f302b40166b23ae56c2652e93b8d83f3d418fc52020dd6427caf776ab；gap取自最终候选5fcc8dfa504ab1d008ce9f015ab2848a6c65f75cf64474eb8d56188cb42ae146。UPSTREAM.json记录来源候选与转换；本目录新的逐文件内容清单及集合SHA在fingerprints.json，构建器另钉住该SHA，篡改任意成员即拒绝构建。安装不会联网获取词典或替换固定版本。

测试用途为个人与朋友、总人数≤10、非商业。不得将整个私有仓库再次公开分发；这不减少独立公共材料原许可证赋予的权利。来源按原样提供，不保证全义覆盖或准确性；对疑义请反馈。
