# 渐进制卡（Progressive Card-Making）v1 设计

日期：2026-09-19 · 状态：方案定稿待实施 · 目标仓库：~/anki-review-app

## 0. 动机

AI 批量制卡不可控，改烂卡比自己写还慢。改为「人读笔记 → 人写卡」，用 notes-rag 已有的
切片逻辑做渐进阅读式进料。记忆的排程继续由 Anki/FSRS 负责，阅读片段只做「进料队列」——
因此 v1 不复现 SuperMemo 的片段排程算法，用「优先级队列 + 三态状态机」即可，这是架构
上的正确分工而非妥协。未来若要加「已完成片段的间隔重读」，再引入 due-date 机制。

## 1. 已确认决策（用户拍板 2026-09-19）

1. 「正在制卡」的 chunk 每轮**自动优先重现**，直到标记完成/跳过。
2. 文件间顺序 = **手动优先级**（阅读清单页可置顶/拖动排序）。
3. 标「制卡完成」**不强制**产出卡片；另设独立的「**无需制卡，跳过**」终态，与完成区分统计。
4. 每轮阅读 chunk 数**跟 quick/focus 档位走**：STUDY_MODES 加 `read` 字段，
   暂定 quick=2、focus=5（数字后续再调，env 可覆盖 ANKI_QUICK_READ / ANKI_FOCUS_READ）。
5. 阅读界面 = **左右两栏**：左栏只渲染当前 chunk；右栏回溯整份 .md 并锚定/高亮到该
   chunk 的章节（复用 NotePanel 的锚点机制），便于看上下文。制卡**不做划线摘录**，
   直接用现有「添加卡片」按钮（EditDialog mode='add'）。

## 2. 非目标（v1 不做）

- SuperMemo 式片段间隔重现（done 片段的 due-date 重读）。
- 划词摘录 → 预填卡片（明确否决）。
- 全语料自动入队（改为文件级 opt-in）。
- 在线编辑笔记（notes-rag Phase-2 的 Vditor，另行推进）。

## 3. 数据模型

### 3.1 chunk 状态机（人工标记）

```
todo(未读) ──开始制卡──▶ active(正在制卡) ──制卡完成──▶ done(完成)
    │                          │
    └──────无需制卡，跳过───────┴──────────────────────▶ skipped(跳过)
```

- todo / active / done / skipped 四值；done 与 skipped 均为终态、均解锁后续 chunk。
- active 是「软终态」：本轮处理完就离开阅读段，但**下一轮优先重现**。
- todo 的 chunk 也可被直接标 skipped（读了一眼觉得不值得制卡，不必先 active）。

### 3.2 状态文件（沿用 preview.json 模式）

`ANKI_STATE_DIR/reading.json`，所有读写走现有 `_state_lock`：

```json
{
  "list": [
    {
      "path": "2026/局部解剖学/颈部.md",
      "priority": 0,
      "added_at": "...",
      "chunks": {
        "<chunk_key>": {
          "status": "active",
          "updated_at": "...",
          "cards_created": [1234567890]
        }
      }
    }
  ],
  "round": { "mode": "quick", "total": 2, "done": 0,
             "chunks": [{"path": "...", "chunk_key": "...", "status_at_deal": "todo"}],
             "last": null }
}
```

- `priority` 整数越小越靠前；置顶 = 设为 min-1；拖动排序 = 整表重写。
- `cards_created`：制卡时记录的 note id 列表（可回溯「这个片段做过哪几张卡」）。
- `round`：当前阅读段，支持刷新恢复（照抄 preview round 的 resume 模式）。

### 3.3 chunk 身份与文件漂移（已知坑，v1 处理策略）

notes-rag 的 chunk_id = sha1(file:line_start)，**文件一编辑行号漂移身份即变**。
v1 策略：

- 状态以 `chunk_key = "{line_start}:{heading_path join '>'}"` 存储（行号 + 标题面包屑）。
- 每次发牌前现场重切该文件；若发现文件的 mtime/size 变了：
  - 先按 **heading_path 精确匹配**迁移旧状态（行号变了但标题没变 → 状态保留，line_start 更新）；
  - 匹配不上的（标题也改了/删了）→ 状态保留在孤儿表里，该 chunk 回到 todo，
    阅读清单页显示「文件已变更，N 个片段状态待确认」提醒（v1 只做提醒不做交互修复）。
- 未编辑的文件零成本（mtime 缓存切片结果）。

### 3.4 切片来源

后端直接 `sys.path` 引入 `~/notes-rag/chunker.py`（env `NOTES_RAG_DIR` 可覆盖，默认
`~/notes-rag`）。不复制代码（避免漂移），不经过 :8791 服务（阅读不依赖嵌入/同步节奏，
新笔记保存即入队可选）。语料根目录 `~/anki-notes`（git 管理，天然安全网）。

## 4. 发牌算法（每轮阅读段）

```
files = reading.list 按 priority 排序
dealt = []
for f in files:                       # 每个文件最多贡献 1 个 chunk
    frontier = f 的第一个 status ∉ {done, skipped} 的 chunk（按 line_start 升序）
    if frontier: dealt.append(frontier)   # active 天然就是 frontier，自动优先重现
    if len(dealt) >= STUDY_MODES[mode].read: break
```

- 「前面的 chunk 未完成就不推后面的」由 frontier 定义天然保证。
- 一轮可跨多个文件（各文件前沿并行推进），文件多的时候不会饿死低优先级——
  不，会：优先级高的前 N 个文件占满本轮名额。这是手动优先级的预期语义。
- 没有 frontier（全部完成/跳过/清单为空）→ 阅读段显示「阅读清单已清空」，直接进预览段。

## 5. 后端 API（全部 gate 在 ANKI_READING_MODE，off → 404 + status 报 reading_mode=false）

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/reading/status` | GET | 清单概览：每文件进度 x/y、前沿 chunk 状态、active 数、待确认漂移数 |
| `/api/reading/corpus` | GET | 语料所有 .md（排除空文件/.obsidian），标注是否已在清单 |
| `/api/reading/list/add` | POST | {path} 入队（追加到队尾）；409 已在清单 |
| `/api/reading/list/remove` | POST | {path} 出队（状态保留在孤儿表，重加不丢进度） |
| `/api/reading/list/reorder` | POST | {order: [path...]} 或 {path, top: true} |
| `/api/reading/state` | GET | 当前阅读段（resume 用），无则 null |
| `/api/reading/start?mode=` | POST | 发牌（§4），返回 chunks 全文 + 锚点信息 |
| `/api/reading/act` | POST | {chunk_key, action: mark_active\|complete\|skip, note_id?} |
| `/api/reading/finish` | POST | 中途退出，未处理的 chunk 状态不变（下轮重发） |

- 发牌 payload 每 chunk 带：path、title、heading_path、line_start/line_end、
  **chunk 全文（markdown 原文）**、status、cards_created。右栏整文件内容走
  **已有的** `/api/notes/raw`（notes-rag /file 中继）+ `lib/markdown.ts` 的
  `[data-src-line]` 锚点定位——零新端点。
- `complete` 动作若带 note_id（刚添加的卡）则追加进 cards_created。
- `/api/card/add` 增加可选字段 `reading_source: {path, chunk_key}`：成功后把
  note id 记入对应 chunk 的 cards_created，并自动加 tag `read-src`（出处溯源，
  tag 名待定，见 §9）。卡片正文不自动注入任何出处文本——保持现有卡片干净。
- round/preview 不受影响：阅读段是 session 的第 0 段，`/api/session/start` 前先走
  `/api/reading/start`（前端编排，后端不耦合）。session/state 增加
  `reading_round` 摘要（照抄 preview_round 的挂载方式）。

## 6. 前端（SolidJS）

### 6.1 新 phase 与流程

```
start(选档位) → reading(阅读段) → previewStart → preview → review → done
                     │
阅读清单管理页（独立入口，随时可进，不占轮次）
```

- App.tsx 加 phase：`readingList` / `reading`。StartScreen 的档位牌显示
  「读 N 片段」来自 wire 的 study_modes.read（前端永不硬编码数字）。
- 阅读段可中途退出（「结束阅读，直接去预览/复习」按钮，调 /api/reading/finish）。

### 6.2 ReadingCard 组件（新）

- **左栏**：md-elevated-card，`lib/markdown.ts` 渲染 chunk 原文（KaTeX 已有）。
  头部：文件面包屑（2026 > 局部解剖学 > 颈部.md > 标题路径）+ 状态 chip。
- **右栏（≥1180px，复用现有 matchMedia gate）**：整份 .md 渲染 + scrollIntoView
  到 line_start 最近的 `[data-src-line]` 锚点 + `.note-hl` 闪烁高亮——与 NotePanel
  完全同套路，建议把 NotePanel 里的「文件渲染 + 锚点定位 + 高亮」抽成共享
  `FileViewer` 组件，两处复用（NotePanel 传入 top-3 文件，ReadingCard 传入 chunk 所在文件）。
- **动作行**（MD3，照 PreviewCard 的按钮层级）：
  - status=todo：`开始制卡`(filled, →active 并下一张) / `无需制卡，跳过`(text) / `添加卡片`(outlined, 开 EditDialog)
  - status=active：`制卡完成`(filled) / `跳过`(text) / `添加卡片`(outlined) / `下一张`(text, 保持 active)
  - 添加卡片成功 → snackbar 提示 + cards_created 计数即时 +1；可连续加多张再标完成。
- 键盘：Space=开始制卡/制卡完成（主行动），S=跳过，A=添加卡片，N=下一张；
  window 级监听 + isTypingTarget guard（沿用 preview 的模式）。
- 阅读段无 reveal（不是检索练习，直接可读）——与 preview 的「先想一想」不同，别混淆。

### 6.3 ReadingListScreen（新）

- 清单文件卡片列表：优先级上下移/置顶按钮（v1 不做拖拽，用按钮，md-icon-button
  arrow_upward/vertical_align_top）、进度条（done+skipped / total）、
  前沿状态 chip、漂移提醒 badge、移除按钮（ConfirmDialog 防呆）。
- 「添加文件」→ corpus 抽屉/对话框：列出所有未入队 .md，点选入队。
- 入口：StartScreen 加一个 md-text-button「阅读清单」（仅 reading_mode=true 时渲染）。

## 7. 与现有系统的复用关系

| 能力 | 来源 | 方式 |
|---|---|---|
| 切片 | ~/notes-rag/chunker.py | sys.path import（单一事实源） |
| markdown 渲染 + 数学 | 前端 lib/markdown.ts | 直接复用 |
| 整文件读取 | 已有 /api/notes/raw → notes-rag /file | 直接复用 |
| 锚点定位 + 高亮 | NotePanel 现有逻辑 | 抽 FileViewer 共享 |
| 制卡 | 已有 /api/card/add + EditDialog mode='add' | 加 reading_source 可选字段 |
| 状态文件 + 并发锁 | preview.json + _state_lock 模式 | 照抄 |
| 档位 | STUDY_MODES | 加 read 字段，wire 下发 |
| 回滚 | ANKI_PREVIEW_MODE 模式 | ANKI_READING_MODE 同款 gate |

## 8. 测试计划

1. `scripts/reading_test.py`：fake-Anki（monkeypatch backend.anki，new_again_test.py 模式）
   + in-process ASGITransport，覆盖：状态机四态转移、frontier 顺序约束（前面未完成
   绝不发后面）、active 优先重现、清单优先级、文件漂移的 heading_path 迁移与孤儿提醒、
   轮次 resume、finish 不改状态、reading_source 记录 cards_created、
   ANKI_READING_MODE=0 时全部 404。
2. `scripts/reading_ui_test.py`：Playwright vs :8902 一次性后端（既有模式），
   覆盖两栏渲染、右栏锚点高亮命中、动作按钮流转、窄屏无右栏、清单页操作。
   用临时 ANKI_STATE_DIR + 临时语料目录（不碰真 ~/anki-notes 状态）。
3. 真机 spot-check：入队颈部.md，走完 todo→active→加卡→complete 全链路，
   验证 note 出现在预览池、cards_created 落盘、git status 干净。

## 9. 开放问题（不阻塞 v1 开工）

1. quick=2 / focus=5 的默认值——上线后按体感调（env 可覆盖，无需改码）。
2. 制卡自动 tag 的名字：`read-src`？还是不打 tag 只存 cards_created？（v1 先只存
   cards_created，tag 后补，避免污染现有 tag 体系。）
3. skipped 片段将来要不要「复活」入口（清单页点开看 skipped 列表再改回 todo）——
   v1 清单页只显示计数，复活入口 v2 再说。
4. 漂移孤儿表的交互修复 UI——v1 只提醒。

## 10. 实施分期

- **P1（后端）**：reading.json 状态层 + chunker 接入 + 发牌算法 + 8 个端点 +
  STUDY_MODES.read + card/add 的 reading_source + reading_test.py 全绿。
- **P2（前端）**：FileViewer 抽取 + ReadingCard + 阅读段编排（phase 流转、
  wire 字段）+ ReadingListScreen + reading_ui_test.py。
- **P3（部署）**：ANKI_READING_MODE=1 进 systemd unit → 用户批准 restart
  anki-review（dist 从磁盘服务，前端 build 即生效；新端点必须 restart）→
  真机 spot-check → git push。
- 回滚：unit 删 env + restart（端点 404，前端自动隐藏）；reading.json 留着无害。
