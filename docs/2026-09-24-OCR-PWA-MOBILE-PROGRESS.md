# 2026-09-24 OCR 缓存修复与 Phase 3 移动端进展报告

## 基线与范围

- GitHub `main` 核对基线：`cc3bf665a7823d5571720b7b17b4e6494b69647f`（OCR 模型缓存事故记录）。
- 本批处理 OCR 缓存事故的预防措施，并推进路线图 Phase 3 的 PWA 安装基础、F-3 导入图片列表和 F-4 iOS 安全区。
- 本批没有数据库 schema 或迁移变更，也没有修改 `data/vocab.db`、上传文件、密钥配置、发布 tag 或运行中的服务。

## 处理内容

### OCR 模型缓存

`backend/app/services/ocr/paddle.py` 在创建 PaddleOCR 引擎前检查 `official_models` 下的直接子目录。已有模型目录须包含非空的 `inference.yml`、`inference.json` 和至少一个非空 `.pdiparams` 文件；不完整目录会被移除，随后由 PaddleOCR 重新下载。检查范围限制在模型目录，不遍历或清除其它缓存元数据。无法检查或清理时返回明确的 OCR 错误。

`backend/tests/test_ocr_optimization.py` 覆盖完整模型保留、配置或参数缺失时的清理、无缓存时的处理，以及“先修复，再构造引擎”的顺序。README 增加了行为说明。

### Phase 3 PWA 基础

- 增加 manifest、独立的 192/512 像素安装图标和 180 像素 Apple 图标；图标由现有品牌图片机械缩放。
- 生产构建中注册 Service Worker；它只缓存应用壳、manifest 与静态构建资源。`/api/**`、非 GET 请求和其它非静态路径保持网络直通；离线导航可回退到应用壳，但离线时 API 数据不可用。
- 增加 `viewport-fit=cover`、Apple 主屏元信息及主题色。PWA 安装和离线表现仍需 Android/iOS 实机验收。

### 移动端 F-3 / F-4

- ≤900px 导入流程保留已上传图片列表，并用单列排版提供查看、继续添加和删除操作。
- ≤620px 固定底部导航与页面内容预留使用 `env(safe-area-inset-bottom)`；移动端查词浮层也避让同一安全区。iPhone Home Indicator 的实际显示仍需实机验收。

路线图、当前状态和交接文档已按“部分完成”更新；F-2 单词详情入口与 F-6 底部导航重排仍待处理，Phase 3 完成标准尚未达成。

## 验证记录

| 检查 | 结果 |
|---|---|
| 前端 Vitest | 8 个文件、37 项通过 |
| 前端 TypeScript / ESLint | 通过 |
| 前端 Vite 生产构建 | 通过，`dist` 包含 manifest、Service Worker 和安装图标 |
| 后端 OCR 与静态托管定向 pytest | 9 项通过，1 条 Starlette 依赖弃用警告 |
| 相关后端文件 Ruff | 通过 |

完整后端测试套件在本机的测试临时目录权限和运行停滞问题下未取得完整通过结果；上述 9 项只能证明本批直接涉及的后端路径。尚未执行真实 PaddleOCR 模型重新下载、手机安装或断点视觉验收，因此不以单元测试替代这些验证。

## 后续

1. 确定 F-2 在手机上的单词详情交互形式，并保证完整释义、音标、复习历史和文章暴露均可到达。
2. 处理 F-6 六项底部导航在窄屏上的拥挤，并做桌面/手机双断点回归。
3. 在 Android 和 iPhone 上验证安装、独立窗口、离线应用壳、安全区与账号切换行为。
4. Phase 2.9 公共电子词库仍需负责人提供真实来源、版本和授权范围，再设计导入与副本验收。
