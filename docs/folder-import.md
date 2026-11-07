# 文件夹导入

## 使用

在「文件」中进入目标目录，直接点击工具栏的「导入文件夹」。所选文件夹自身及其文件层级保留，例如导入到 `2026` 后得到 `2026/金钱心理学/001-正文.md` 与 `2026/金钱心理学/_assets/a.png`。

- 开发版接受 Markdown 和图片；现有讲义部署版还保留 PDF/PPT/PPTX 导入。
- 导入前筛选格式，`manifest.json`、`.DS_Store` 和系统配置目录等不上传，并显示可展开的跳过清单。不支持的附件不再导致整组 Markdown/图片失败。
- 保留原始相对路径，图片重名继续复用后端的名称分配与 Markdown 引用改写。不要手动改名 `_assets` 来绕过保护。
- 默认不覆盖。Markdown 重名仍须明确选择跳过、改名或覆盖；课件版本沿用原有独立修订流程。
- 不自动加入阅读，不同步本地目录，不导入空目录。不自动重试结果不明的请求。
- 原有大小和数量限制不变：Markdown/图片默认100个文件、单文件25MiB、整批100MiB。不提供跨批导入会话或断点续传。

## 安全边界

`backend/app.py::_files_resolve` 默认仍拒绝受保护目录。仅 `files_upload` 的两次路径检查显式启用 `upload_image=True`：只允许图片进入语料库内部非根级的 `_assets` 目录。根级编辑器 `_assets`、`.git`、`.obsidian`、`.trash`、`node_modules`、越界和符号链接限制保留。此例外不开放对受保护目录的直接重命名、移动或删除。

筛选是前端便捷行为，不是后端安全边界。绕过UI直接向上传API提交JSON等不支持格式，后端仍拒绝整批，且预检失败不写文件。

## 验证

- `backend/.venv/bin/python scripts/folder_import_test.py`：临时官方Anki集合和语料，真实ASGI上传，12组检查。可通过 `FOLDER_IMPORT_BACKEND` 指定部署版的隔离副本。
- `backend/.venv/bin/python scripts/file_mgmt_test.py`：既有66项文件管理回归。
- `scripts/audit_fixes_test.py -k upload`：26项上传边界/引用/预算/故障回归，使用独立pytest环境和 `NATIVE_FIXTURE_ROOT`。
- 在前端目录运行 `node --loader ./tests/loader.mjs tests/folder-import.test.mjs`：混合格式筛选、原路径与文件对象保留。
- `scripts/folder_import_ui_test.py`：需要Playwright/Chromium与 `REGRESSION_DIST`；独立uvicorn、临时官方Anki集合、真实目录选择器，覆盖落盘、图片读取、跳过清单、重名确认、全杂项零上传、5种视口宽度。可用 `PLAYWRIGHT_CHROMIUM_EXECUTABLE` 指定已有浏览器。

测试不使用真实学习集合或语料。

## 本机部署记录（2026-11-06）

开发仓库与本机讲义release不是同一份代码。部署使用基于实际后端与最新卡片编辑器前端快照的独立工作副本，不能直接以开发版构建覆盖讲义UI。

已仅替换实际release的 `source/backend/app.py` 与 `dist` 并重启服务，未修改unit/env、数据结构或调用导入/同步写API；没有代用户导入书籍。停服务后备份集合/媒体、阅读状态与语料，逐文件SHA校验；阅读库SQLite完整性检查通过，Anki集合在备份的临时副本上通过原生完整性检查。普通SQLite环境不支持Anki的 `unicase` 排序规则，不能直接用于集合完整性检查。

源码快照、旧代码/构建、私有数据快照、部署记录与代码/UI回退脚本位于 `~/.local/share/ir4anki/deployments/folder-import-20261106/`。`rollback.py` 只回退代码/UI，不覆盖学习数据；执行前会验证当前代码与备份SHA。数据快照应私下保管，不提交Git。
