# 试用后修正（2026-10-10，未部署）

已保留进入任务时所有脏文件和未跟踪工作；未 stash/reset/commit/push，未编辑凭据，未对生产集合、状态或 corpus 写入，未启动线上评分/同步/flow，未替换 served dist 或重启服务。项目及祖先目录未找到适用 AGENTS.md。第 3 项已删除，不实施。

## 实现与诊断

- `frontend/src/lib/clean.ts`、`Flashcard.tsx`、`ActionArea.tsx`：评分标签后不再显示间隔；复习卡不再显示稳定性/难度/保持率；显示层剥离遗留模板统计和 AI/相似卡片容器。只读安装元数据确认实际问答模板含 `explain-section`、`explain-content`、`sc`、`sl`；均纳入清理。编辑器使用单独的保留内容路径，防止显示清理写回字段。调度、评分与管理统计功能保留。
- `backend/app.py`、`backend/anki_backend.py`、`Flashcard.tsx`：`restore_round_cards` 原先先读取全部待复习 `cardsInfo` 用于过滤，随后又 `fetch_cards` 读取/渲染同一批。现在复用第一次结果，保持请求顺序、消失卡片处理和原生 journal 转移。返回字段、标签、真实类型和 ord，原生切卡无需额外 `/api/note` 请求。编辑保存同时更新字段元数据，避免显示旧 Markdown。
- `frontend/src/lib/cloze.ts`、`index.css`：Markdown 挖空替换原来只留下普通文字，未生成可被 CSS 高亮的元素。现在正反面保留 `.cloze`，兼容嵌套、提示、数学、代码与非连续序号，标记只进入渲染文本而不进入 URL/alt 属性；原生/HTML 路径共享黄底高亮，系统暗色有对应颜色。未改模型、转换旧笔记或重编号。
- `App.tsx`、`types.ts`、`NavRail.tsx`、`ProgressRing.tsx`：右下角阅读/复习两个圆环，分别有文字、可访问名称及不同颜色。读取服务器 `reading_consumed/reading_slots` 与 `card_handled/card_count`；阅读按原始槽位计数，拆分子片段不会扩大总量。撤销、刷新、空侧和耗尽沿用服务器协调，完成页保留两个结果；退出本轮隐藏。
- `EditDialog.tsx`、`MarkdownField.tsx`、`index.css`：问答输入左右排列，各自预览在其下；窄屏单列；桌面输入高度 120–180px。原先 MarkdownField 无条件渲染空 `.edit-error`，CSS 有上下各 12px padding 和 error-container 背景：即无错误也产生 24px 色带。现在只在有错误时挂载。CM6 原有 `root: document` 修复保留；几何、shadow root 样式、滚动末行断言已加入浏览器套件；父环境已通过首次桌面/窄屏检查，后续源码断言修订待复跑。
- 测试：新增 `scripts/post_trial_{test,api_test,ui_test,verify}.py`，扩展 `frontend/tests/markdown.test.mjs`，loader 支持本机 Node 20，`package.json` 测试不再依赖 Node 的原生 strip-types 参数。原 reading UI 圆环断言更新；reading/desktop/flat_panels/modes UI 截图遵循 TMPDIR。`scripts/REGRESSION.md` 记录隔离复查入口。未修改 generated tokens.css。

## 类型与引擎管理

只读 SQLite 模型元数据及安装配置：pylib；创建模型为“问答题”（普通型 kind=0）和“填空题”（挖空型 kind=1）。现有笔记使用这两种模型，没有新建 Markdown 模型。HTML 与 Markdown 创建仍选同一配置模型；区别是字段存储格式及 `ir4anki::markdown` 标签。QA/挖空是 note type；新卡/学习/复习是调度中的 card type，和 Markdown 无关。隔离 API 测试验证两种存储的模型 ID 相同，c3/c7 仍为 ord 2/6，编辑后卡片 ID、序号、标签和混合原文均保留。没有打开生产集合的原生引擎。

“引擎管理”是原生管理面板：官方统计、同步/撤销状态、手工备份、导出下载和经确认的同步。管理令牌只在当前页面内存；统计、备份、导出、同步需要后台授权。显式同步会结束最近一次作答的撤销机会。普通阅读与复习不需要打开此面板；本次保留它。

## 最初 worker 验证与限制

证据根目录 `/home/shioriko/.hermes/cache/scratch/post-trial/`，最终检查清单 `final/checks.json`，15 套回归清单 `final/regression.json`。

- RED：`scripts/post_trial_test.py` 原实现读取同一批 12 卡两次，断言失败，Python exit 1，`red-native.log`。每卡人为增加 25ms，恢复处理实测 0.6455s。
- Markdown 块级高亮 RED/GREEN：`red-block-markdown.log` exit 1，发现新增标记干扰整块挖空中的 heading/fence/table；改为独立块包装后 `final/markdown.log` exit 0，保留这些解析结构。
- GREEN：同探针读取一次，exit 0。最终 `final/latency.log`：恢复 0.3337s；评分 handler 0.4362s、其后服务器刷新 handler 0.3568s；分别渲染 12+1 和 11 卡，且真实评分后可撤销并恢复服务器进度。保留提交后串行服务器刷新，未测线上 HTTP 往返或浏览器绘制延迟。数字包含人为延迟，不能当作 live latency。
- `npm --prefix frontend run test:markdown`、`tsc -p frontend/tsconfig.app.json --noEmit --incremental false`、隔离 `npm ... build -- --outDir .../final/dist`：exit 0。构建仅是构建证据；Vite 大 chunk/Node loader 警告仍存在。
- `post_trial_api_test.py`：exit 0，四组 HTML/Markdown QA/挖空精确保存、再读取、标签、类型和非连续序号检查。`pylib_markdown_api_test.py`：exit 0。
- 原生 pytest 指定的六文件：27 passed、1 failed，exit 1；唯一失败是 `pylib_durable_sync_test.py` 的独立 loopback 同步服务器不能创建 socket，其余真实评分/FSRS/revlog/撤销/重启/备份/生命周期通过。
- `interleave_policy_test.py`、`interleave_integration_test.py`、`interleave_recovery_test.py`：exit 0；覆盖独立数量比例、空侧、耗尽、刷新、跨阶段 undo 和恢复。
- 15 套 runner：8 套通过，258 条断言；7 套 UI 均因 socket `PermissionError: [Errno 1] Operation not permitted` 退出 1；runner exit 1。
- 新 post-trial UI 与 interleave UI：各 exit 1，同一 socket 阻断。绕过 HTTP 直接启动缓存 Chromium 也失败：`FATAL:content/browser/sandbox_host_linux.cc:41 ... shutdown: Operation not permitted (1)`。备用 CUA 工具返回空浏览器列表。在线安装 Playwright 曾因 DNS 失败，随后从已有本机缓存获得依赖，未改服务 venv。

最初 worker 运行没有生成截图；这一环境限制不适用于下述 Hermes 父环境。可运行套件包含上述断言；完整 post-trial 浏览器验收仍需父环境复跑。成功时新截图落在运行目录 `ui/`：`review-legacy.png`、`editor-desktop.png`、`editor-narrow.png`、`complete-dark.png`。本次隔离构建绝对路径：`/home/shioriko/.hermes/cache/scratch/post-trial/final/dist`。

## Hermes 父环境结果与 CM6 断言修订（2026-10-10）

`/home/shioriko/.hermes/cache/scratch/post-trial/hermes-check/checks.json` 中，除 `post-ui` exit 1 外所有检查 exit 0：原生 pytest 28 passed、15 套 runner 全通过（521 条断言）、interleave-ui 通过。父环境允许 sockets/Chromium；其 `post-ui.log` 的实际失败是旧第 100 行 `.cm-line` 拼接后与完整源码比较，不能归因于 worker 的 socket 限制。

已查看父环境 `ui/review-legacy.png`、`editor-desktop.png`、`editor-narrow.png`。桌面图输入滚动到第 35–42 行，末行可见，预览有全篇；左右输入/下方预览与窄屏堆叠检查在失败之前已通过。服务器日志记录保存及回读成功，但 HTTP 200 本身不能证明该编辑笔记原文精确保存；旧测试未记录失败时的完整 EditorState/viewport，所以不能从旧日志证明该次重开没有丢源。

已确认断言本身错误：安装的 `@codemirror/view` 公共 API 文档明确 DOM 仅绘制 viewport 加 margin，`.cm-line` 不是文档序列化接口。42 行源码超过 180px 编辑器的视口；截图与此一致。换行转义也已检查，不是转义错误。未发现足以要求修改保存/编辑器实现的丢源证据，本次仅修订测试及文档。

`frontend/tests/cm6-browser-probe.mjs` 使用公共 `EditorView.findFromDOM` 找到真实视图，读取 `view.state.doc.toString()`。测试独立构建这个 reader 到当前 scratch 运行目录，Playwright 同源拦截提供模块，不修改应用全局或 served dist。`scripts/post_trial_ui_test.py` 等待实际视图初始化（不轮询直到源码符合预期），精确比较双字段文档；增加提交载荷、保存后 API、重开、滚动后文档及再次 API 回读断言。几何、CM6 样式和末行可见断言保留，重开后还增加末行检查与 `editor-reopened.png`。`document-checks.json` 记录文档/DOM 行数、viewport、visibleRanges、长度和 SHA256，能够区分视口裁剪与真实丢源/错误文档，不记录完整源码。

本次 worker：Python AST 检查及独立 probe 构建 exit 0；本地 CM6 EditorState 探针测试 exit 0（42 行文档/2 行 DOM 时旧断言 RED、权威读取 GREEN，错误文档与未初始化视图也检出）。这只是 reader 验证，不是浏览器 GREEN。实际 UI 重跑 exit 1，仍在第一个 socket 创建处 `PermissionError: [Errno 1] Operation not permitted`，尚未初始化任何测试集合。日志 `/home/shioriko/.hermes/cache/scratch/post-trial/cm6-followup/post-ui.log`，probe 构建 `/home/shioriko/.hermes/cache/scratch/post-trial/cm6-followup/ui/probe/cm6-browser-probe.js`。修订后的完整 post-ui 结果等待父环境复跑。

## Hermes 独立检查

在可运行浏览器的同机环境，使用包含 pinned Anki、pytest、Playwright 的 Python（本次本机缓存 scratch venv 可用）：

```bash
cd /home/shioriko/ir4anki
/home/shioriko/.hermes/cache/scratch/post-trial/venv/bin/python scripts/post_trial_verify.py --output /home/shioriko/.hermes/cache/scratch/post-trial/hermes-check-cm6
```

该入口自行清除全部继承的 ANKI_* 设置，隔离集合、media、state、corpus、同步配置、pytest、截图及 dist；完整命令、exit code、日志写入 `hermes-check-cm6/checks.json`。有 blocker 会返回非零，不跳过 UI。不要将 served frontend/dist 传给测试。

## 已检查脚本的部署步骤（仅说明，未执行）

先在可运行浏览器的环境通过上述验收。运营者自行处理 pending undo；`native.py backup` 对未解决的 answer journal 会拒绝备份，不能删除 journal 绕过。关闭桌面 Anki，保持单 worker。

只准备产物而不部署的精确命令：

```bash
cd /home/shioriko/ir4anki
TMPDIR=/home/shioriko/.hermes/cache/scratch bash deploy/update.sh --local --no-restart
```

`--local` 不 pull，允许当前脏树；`--no-restart` 不改服务 venv，不 npm ci，不替换 served dist、不重启，打印隔离构建路径。

真正部署须由 Hermes/运营者另行执行：

```bash
cd /home/shioriko/ir4anki
systemctl --user stop ir4anki.service
backend/.venv/bin/python deploy/native.py check --env /home/shioriko/.config/ir4anki.env --require-offline
backend/.venv/bin/python deploy/native.py backup --env /home/shioriko/.config/ir4anki.env --output /home/shioriko/.hermes/cache/scratch/post-trial/operator-offline-backup --offline-confirmed
TMPDIR=/home/shioriko/.hermes/cache/scratch bash deploy/update.sh --local
backend/.venv/bin/python deploy/native.py health --url http://127.0.0.1:8901
```

每步成功后继续；备份输出必须是新的目录且保持私密（包含 env.private）。`update.sh --local` 会安装 pinned backend 依赖、npm ci、先隔离构建，再将旧 dist 保留为 `frontend/dist.previous-<UTC>-<pid>`，替换 dist 并重启用户服务。最后硬刷新浏览器。旧 dist 是前端回退材料，不是集合/状态回滚。当前只读安装元数据确认端口为 8901、工作目录为本项目/backend、TimeoutStopSec=300。此任务未执行这些生产操作。
