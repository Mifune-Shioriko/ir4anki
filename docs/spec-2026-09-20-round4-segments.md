# Round 4 spec — 片段化阅读（segments + 递归切割 + 双向溯源）

日期 2026-09-20 · 状态：设计定稿（用户多轮讨论拍板），待实施
取代：round 3 的 chunk_key 派生身份；notes-rag 卡片溯源
实施前必读：本文件是唯一施工图纸；代码里的 `user spec 2026-09-19 round 3` 标注在 P1 落地后由 round 4 标注取代。

## 0. 一句话架构

**文件存文本，DB 存结构，app 是唯一写者。**
chunk_key（`line_start:heading_path`，内容派生、编辑即漂移）换成 DB 发号的 `seg_id`（SM 式原生 handle，身份与内容位置解耦）；切割是纯 DB 区间操作；卡片↔片段用 rcards 双向精确溯源；notes-rag 退役。

## 1. 决策记录（为什么是这个形态）

1. **弃 Obsidian/git 同步** → `~/anki-notes` 只由 app 写；git 仓库降级为纯备份（保留，白捡版本历史）。
2. **文件零标记**（用户不接受隐形 `<!-- cid -->`）→ 结构全进 reading.db（sidecar）。sidecar 的两个经典坑——同步看不见结构、外部写者静默漂移——分别被决策 1、3 消除。
3. **app 唯一写者**：将来的 .md 编辑功能 = 同一事务改文件 + 改 DB。指纹重锚定保留，仅作外部改动的保险丝。
4. **片段 = 有序划分**：所有叶子片段无缝、不重叠、并集 = 全文。「全文视图 = 文件本身」结构性恒真；拼接回溯零成本。
5. **递归切割 = 区间细化**：每次切割把一个区间替换成 2k+1 个连续子区间（k 个选中段 + 间隔）。父片段变 `container`，**区间永久保留** → 卡片锚点在任何切割深度下不过期。优于 SM 的 extract（SM 复制文本，冗余随深度累积；这里零文件 I/O）。
6. **卡片溯源 = rcards 反查**（精确历史），不是 RAG 相似度（猜测）→ **notes-rag 服务退役**（:8791、两次 daily sync timer、chunks.jsonl、embedding 缓存全下线）。**anki-rag（:8789 相似卡查重）保留**，与溯源无关。
7. **复习/预览界面制卡继承来源**：过卡 a 时新制的卡 b 自动挂到 a 的 reading_source。无来源卡（历史卡/桌面 Anki 卡）= 孤儿，UI 显示「无来源」，不迁移不修补。
8. **图片管线修复**：markdown-it `html:false` 吃原始 `<img>`、相对路径打到 /assets 404、`/media/` 只服务 Anki collection——三处断点用一条新路由 + 一条 image 渲染规则解决（P5）。

## 2. 数据模型（reading.db）

### 2.1 新表 segments（取代 rchunks）

```sql
CREATE TABLE segments (
    seg_id        INTEGER PRIMARY KEY AUTOINCREMENT,  -- DB 发号，永不复用
    path          TEXT NOT NULL,
    start_line    INTEGER NOT NULL,        -- 1-based inclusive
    end_line      INTEGER NOT NULL,
    fingerprint   TEXT NOT NULL,           -- sha256(区间文本)，重锚定保险丝
    status        TEXT NOT NULL DEFAULT 'todo',
    parent_seg_id INTEGER,                 -- 切割血缘；NULL = 播种第一代
    updated_at    TEXT
);
CREATE INDEX idx_segments_path ON segments(path, start_line);
CREATE INDEX idx_segments_parent ON segments(parent_seg_id);
```

状态六值：

| status | 语义 | 可发牌 |
|---|---|---|
| todo | 未读 | ✓ |
| active | 正在制卡 | ✓（phase 0 优先重推） |
| done | 制卡完成 | ✗ |
| skipped | 读过，判定无需制卡（终点） | ✗ |
| **background** | 切割产生的未排期上下文，可随时升格 | ✗ |
| **container** | 被切割的父片段，历史+卡片锚点 | ✗ |

### 2.2 rcards 换键

```sql
CREATE TABLE rcards (path TEXT NOT NULL, seg_id INTEGER NOT NULL, note_id INTEGER NOT NULL);
CREATE INDEX idx_rcards_seg ON rcards(path, seg_id);
CREATE INDEX idx_rcards_note ON rcards(note_id);   -- 反向溯源（卡→片段）
```

### 2.3 保留

rfiles（阅读清单+优先级）、rorphans（重锚定失败的 segment，带原区间+指纹）、rround（轮次 JSON，pending 里的 chunk_key 值变为 seg_id 字符串）、rmeta。

### 2.4 wire 兼容策略

API 字段名 **chunk_key 保留**，值变为 `str(seg_id)`（前端当不透明字符串用，改动最小）；payload 新增 `seg_id`、`parent_seg_id`、`status`、`line_start/line_end`（区间来自 segments 行，不再来自 chunker 实时输出）。

## 3. 核心流程

### 3.1 播种（seeding）
文件加入阅读清单时（`/api/reading/list/add`）或首次 `_file_view` 时：chunker.chunk_markdown 输出逐段 INSERT 进 segments（status=todo，parent=NULL，fingerprint=区间 sha）。**chunker 从身份来源降级为初始化工具**；`_chunk_cache` 只服务播种和渲染，不再参与身份。

### 3.2 发牌与闸门（逻辑不变，键变了）
`_deal_reading` 三阶段、`_reading_gates` B·二段重推、all_gated 语义、STUDY_MODES 配额全部原样，只把「遍历 chunker 输出+状态字典」换成「SELECT segments WHERE path=? AND status NOT IN ('container','background','done','skipped') ORDER BY start_line」。叶子顺序 = 文件顺序，天然成立。

### 3.3 切割 `POST /api/reading/split`
`{path, seg_id, selections: [{start_line, end_line}, ...]}` → 一个事务：
- 父 segment status→container（区间不动）；
- 按 selections 生成 2k+1 个子 segment（parent_seg_id=父）：选中区间 status=todo（排期），其余 status=background；
- 零文件 I/O。

规则：**父片段上挂的卡片锚点留在父（container）上，子片段全部拿新 seg_id**——卡片指向的区间永不被切割改写（rcards 不迁移）。

### 3.4 升格/降格（`/api/reading/act` 扩展）
新 action：`promote`（background→todo）、`demote`（todo/active→background）。原有 mark_active/complete/skip/next 不变。complete/skip 只对叶子有意义；container 不可 act。

### 3.5 重锚定（取代 `_migrate_entry`）
`file_sha` 漂移时（外部改动，保险丝场景）：对每个非 container segment，按 fingerprint 在新文本中重新定位区间（行号平移 + 首行/内容相似度）；container 区间 = 子区间之并，子对齐后父重算。对不上的 → rorphans + 文件标记「需重新同步」（新 rmeta/rfiles 列 `needs_resync`），该文件停发牌直到用户在 app 里确认重播种。

### 3.6 溯源渲染
- 复习/预览右栏：rcards(note_id) → (path, seg_id) → 源片段高亮 + 血缘面包屑（沿 parent_seg_id 上溯：`原节 > 选中段 > 核心`）+「看全文」。
- 全文视图 = 文件渲染，按 segments 区间叠加边界与状态色；FileViewer 锚定从 `data-src-line`（会漂）换成 `data-seg`（不漂）。
- 新端点 `GET /api/reading/source?note_id=` → `{path, seg_id, breadcrumb, line_start, line_end, text}`；无溯源 → 404/空，前端显示「无来源」。

### 3.7 复习/预览制卡继承
前端：复习/预览界面打开 EditDialog/ClozeDialog 前，用当前卡 note_id 调 `/api/reading/source` 拿 (path, seg_id)，塞进 `readingSource`；后端 `_reading_record_card` 不改（fail-soft 照旧）。

## 4. 图片管线（P5，独立）

- 后端 `GET /api/reading/media/{name}`：basename 在 NOTES_DIR 递归查找，防穿越照抄 `_read_note_text` 的 `is_relative_to(root)`；只允许图片扩展名。
- 前端 markdown.ts：image 渲染规则把 src 重写到该路由；`html:false` 维持，摄取约定用标准 `![](...)` 语法（Obsidian 附件文件夹习惯平移：附件统一放 NOTES_DIR 下一个文件夹，basename 查找两种写法都解析）。

## 5. 分期计划（每期独立 commit，可单独回滚）

| 期 | 内容 | 风险 |
|---|---|---|
| **P1** | 后端身份层：segments 表 + 播种 + 全链路换键（_file_view/_deal/_gates/_record_card/round rehydration）+ 重锚定 + **旧数据迁移** | 中（核心重构，生产路径） |
| **P2** | 切割 + background/container + act 扩展（后端） | 低 |
| **P3** | 前端：切割 UI（选中文本→切）、data-seg 锚定、新状态展示 | 中 |
| **P4** | 双向溯源：/api/reading/source + 复习/预览源片段面板 + 制卡继承 | 低 |
| **P5** | 图片路由 + 前端 image 规则 | 低 |
| **P6** | notes-rag 退役（service+timer 下线、NotePanel 移除/替换） | 低 |

依赖：P2/P3/P4 都依赖 P1；P4 与 P2/P3 可并行；P5/P6 独立。

### P1 旧数据迁移（当前生产数据：2 文件、15 个 chunk 状态、37 条 rcards、闸门活跃）
文件未漂移（file_sha 匹配）时 chunker 重跑输出与旧 chunk_key 完全一致 → 播种后按 `旧 chunk_key == "line_start:heading_path"` 精确等值匹配，把 rchunks 状态和 rcards note_id 搬到对应 seg_id；匹配不上的进 rorphans。迁移在一个事务里完成，写 rmeta `round4_migrated` 防重入。

## 6. 部署与回滚（遵循既有偏好：零删除、指针切换）

- 开发在 **git worktree**（`../anki-review-app-round4`，分支 round4-segments）进行，生产工作树不动；
- 每期完成：`cp state/reading.db state/reading.db.pre-<phase>.bak` → 停 anki-review → systemd unit 的 `--app-dir` 指针切到新树（或合并回 master）→ 起服务 → 验证发牌/制卡/闸门 → 不满意则指针切回，旧树旧 DB 原样保留；
- reading.db 加入 cron 备份（DB 丢 = 结构丢、文本无恙：可按 heading 重播种，损失仅限进度）。
