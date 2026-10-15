# ir4anki

在本地 Markdown 阅读中手工制卡，用 Anki 原生调度复习。
面向单一使用者，优先支持 Debian/Linux；安装后可离线阅读、制卡和复习。
前端资源、公式渲染和媒体由本机提供，同步才需要连接配置的服务器。

新安装脚本默认使用官方 **Anki pylib**，无需打开桌面 Anki。
直接运行后端而未设置 `ANKI_BACKEND` 时仍走 `connect` 兼容路径；已有配置也不会被安装脚本覆盖。
pylib 使用集合本身的调度配置，是否启用 FSRS 取决于集合设置，不能由后端类型推断。

## 日常使用

- **学习**：点击「开始」，一次领取阅读片段和待复习卡，按数量均匀交错推进。
- **文件**：浏览语料、上传文件或文件夹、重命名、移动和永久删除。
- **阅读**：维护加入阅读的文件及进度；阅读独立于 Anki 卡片调度，没有 FSRS 到期日。

每个阅读文件起初是一整个片段，每轮最多领取两个可处理片段。
选择行后分割只采用书签语义：选中内容形成子片段，值得保留的未读尾部留待后续轮次。
分割不改写 Markdown 原文；子片段接替父片段在本轮中的位置，单纯分割不会提前开始下一组复习。
复习卡可通过溯源返回制卡时的原文片段，关闭溯源后回到当前学习进度。

阅读时可手工添加问答卡或 Cloze，编辑器支持 Markdown、公式、图片和预览。
已有 HTML 卡保持兼容。制卡来源保存在本地阅读状态中；原文或笔记已变化时，
编辑器拒绝过期覆盖并保留草稿，重新打开后可手工合并。
请使用集合中真实存在的问答/Cloze 笔记类型，字段和模板需匹配用途。

「制卡完成」、跳过和继续会推进阅读位置，复习则使用原生评分结果。
阅读和复习分别显示进度；缺失、挂起或离开复习范围的卡不冒充已作答。
「结束阅读」保留未处理片段供以后阅读，继续本轮卡片；「退出本轮」清除本轮队列并提交当前撤销位置。
这两个动作不会强制同步。

### 预览池与每日默认值

安装脚本生成的配置开启阅读和预览池。直接运行后端时，这两个功能需要显式开启。
预览池是新卡隔夜放行门槛，并非逐卡人工审批：新制卡先挂起在 `预览池`，
到了下一个 Anki 日后，在进入学习等入口按额度自动放入目标牌组 `2026`。

| 配置 | 当前默认行为 |
| --- | --- |
| `ANKI_RELEASE_DAILY_GOAL=36` | 每日最多自动放行 36 张；更早的卡优先 |
| `ANKI_DAILY_NEW=0` | 每轮领取全部可领取新卡；0 表示不另加数量上限 |
| `ANKI_REVIEW_DUE_DIVISOR=1` | 本日首次领取时记录到期量，默认覆盖整批到期卡 |
| `ANKI_ROLLOVER_HOUR=4` | 本地时间凌晨 4 点换日，应与集合设置一致 |
| `ANKI_REVIEW_DECK=` | 空值覆盖全部牌组；指定牌组时包含其子牌组 |

36 是自动放行预算，不是总复习量或阅读制卡配额。
阅读量由加入的文件和片段状态决定。详见[交错学习](docs/interleaved-flow.md)、
[书签分割](docs/bookmark-reading.md)和[复习牌组范围](docs/review-deck-scope.md)。

### 文件操作

上传先检查路径、编码、冲突和大小：默认最多 100 个文件、单文件 25 MiB、整批 100 MiB，
改写后的 Markdown 也受限。同批图片按相对路径关联；I/O 故障仍可能部分完成，
请核对完成回执后再重试。

移动与重命名会协调阅读索引和溯源位置；冲突、路径越界及不安全的符号链接会被拒绝。
永久删除要求确认文件名，已加入阅读的文件会额外提醒进度影响。
删除语料不等于删除已生成的 Anki 卡片；重要原文请另做备份。

## 安装

需要 Python ≥3.10、可用的 Python venv/pip、Git、npm，以及 **Node 20.19+（20 系列）或 22.12+**。
当前锁定的 Vite 8 不支持旧 Node 18；安装脚本只检查主版本 ≥18，不能代替此要求。
Debian 可先安装 `python3-venv` 等系统依赖，并自行准备符合要求的 Node。

后端固定使用 `anki==25.2.7` 和 `markdown-it-py==4.0.0`；全部运行依赖见
[requirements.txt](backend/requirements.txt)。不要只更新前端或漏装解析器依赖。

先在兼容版本的桌面 Anki 中准备好现有集合、笔记类型和媒体，再完全关闭桌面 Anki。
pylib 不会替你创建空集合；同一集合只能交给一个进程、一个 worker 持有。
不要让桌面 Anki 与服务同时打开集合，也不要让测试实例指向真实集合。

```bash
git clone https://github.com/Mifune-Shioriko/ir4anki.git
cd ir4anki
bash deploy/install.sh --collection /absolute/profile/collection.anki2
```

安装脚本会安装后端依赖、询问路径、构建前端，并安装、启用及重启 systemd 用户服务。
新配置写到 `~/.config/ir4anki.env`，权限为 600；已有文件原样保留，
`--backend` 和 `--collection` 不会改写已有文件里的选择。
默认访问地址为 `http://127.0.0.1:8901`。

其他安装参数：

```bash
bash deploy/install.sh --help
# 仅安装和构建，不操作服务；仍会创建配置、安装依赖
bash deploy/install.sh --no-service --non-interactive --collection /absolute/profile/collection.anki2
# 可选桌面兼容路径
bash deploy/install.sh --backend connect
```

`--non-interactive` 采用默认值，首次原生安装仍需有效的集合路径。
Connect 模式要求桌面 Anki 正在运行并安装 AnkiConnect，默认地址 `http://127.0.0.1:8765`；
其同步由桌面 Anki 管理，原生备份、持久撤销和管理能力不可直接照搬。

### 配置与网络边界

按[配置示例](deploy/ir4anki.env.example)编辑本机配置，不要把口令提交到仓库。
示例仍以 `connect` 开头；原生安装应明确设置 `ANKI_BACKEND=pylib`。
下面仅展示关键项，路径必须为绝对路径，不能用 `~`：

```ini
IR4ANKI_HOST=127.0.0.1
IR4ANKI_PORT=8901
ANKI_BACKEND=pylib
ANKI_COLLECTION_PATH=/absolute/profile/collection.anki2
ANKI_MEDIA_DIR=/absolute/profile/collection.media
ANKI_STATE_DIR=/absolute/ir4anki-state
ANKI_NOTES_DIR=/absolute/markdown-corpus
ANKI_BACKUP_DIR=/absolute/ir4anki-backups
ANKI_ADD_MODEL=问答题
ANKI_ADD_CLOZE_MODEL=填空题
ANKI_READING_MODE=1
ANKI_PREVIEW_MODE=1
```

用户服务从配置文件加载环境，使用单 worker。检查配置：

```bash
backend/.venv/bin/python deploy/native.py check --env "$HOME/.config/ir4anki.env" --require-offline
systemctl --user status ir4anki
journalctl --user -u ir4anki -n 40
```

桌面浏览器使用 localhost/回环 IP 时无需额外 Host 配置。手机、局域网或 Tailscale 访问时，
需设置监听地址（例如 `IR4ANKI_HOST=0.0.0.0`），并明确配置
`ANKI_TRUSTED_HOSTS=review.example.ts.net,100.100.1.2` 为实际访问的服务器主机名/IP，
不带协议、端口或通配符。监听所有接口会扩大可达范围，应同时限制网络访问。
页面与 API 必须同源，反向代理也需保持正确的 Host/Origin。
Host 白名单、同源和 Fetch-Metadata 检查防止一类浏览器跨站请求，**不是非浏览器多用户认证**。
服务应限制在可信本机、私网或受控 VPN 边界内，不应直接暴露给公网多人使用。

## 撤销与同步

原生模式保留最近一次评分的持久撤销位置，重启后仍可撤销；下一次评分会结束上一次撤销位置。
撤销只恢复该次评分和相关队列计数，不回滚阅读、已编辑字段或新制卡。
检测到不相容的集合/调度外部变化时会拒绝不安全恢复。

默认 `ANKI_SYNC_POLICY=manual`：启动、评分、制卡和阶段切换不自动联网同步。
明确确认手动同步会先结束当前撤销位置，即使随后联网失败也不应期待该位置仍可撤销。
有未决撤销时，保留的后台同步兼容策略会推迟同步。

界面的手动同步只允许**直接访问 loopback、同源浏览器请求**，同时核验连接来源、Host、Origin 和确认头。
经反向代理、局域网或 VPN 访问页面不自动获得该权限，转发头也不能证明本机身份。
管理用原生统计、备份、导出和 `/api/engine/sync` 则使用本地配置的
`ANKI_ENGINE_API_TOKEN` bearer；不要把管理令牌嵌入公开页面或分享给其他使用者。

原生同步必须显式配置自己的服务器 `ANKI_SYNC_ENDPOINT`，并在本机设置
`ANKI_SYNC_HKEY`，或 `ANKI_SYNC_USERNAME` / `ANKI_SYNC_PASSWORD`。
只使用自己的账户，不会隐式回退到 AnkiWeb。
遇到需要全量同步时，应用拒绝自行决定上传/下载方向；先备份，再用兼容的桌面 Anki 人工处理。
切换桌面前须停止原生服务，详见[迁移与应急处理](docs/pylib-migration.md)。

## 备份与升级

集合与 `collection.media` 保存卡片和媒体；`ANKI_STATE_DIR` 保存阅读进度、溯源、轮次和额度，
`ANKI_NOTES_DIR` 保存 Markdown 语料。Anki 同步不等于阅读状态与语料同步。
跨机器迁移需整体处理这些数据，不能仅复制集合后假设阅读进度仍在。

原生集合有启动及周期备份（默认间隔 1800 秒）；它不能替代媒体、语料和阅读状态的完整备份。
升级前先决定撤销或明确提交最后一次答案；不要删除答案 journal 来绕过检查。
关闭桌面 Anki，停止服务后使用完整离线快照，输出目录必须在源数据目录之外：

```bash
systemctl --user stop ir4anki
backend/.venv/bin/python deploy/native.py backup \
  --env "$HOME/.config/ir4anki.env" \
  --output /absolute/separate-backups --offline-confirmed
```

备份脚本会拒绝活动服务、桌面进程或未决答案日志，并复制集合、媒体、状态、语料和私有配置。
快照包含凭据，必须私下保管，不要上传 GitHub。另留一份旧源码、依赖版本及可恢复的前端构建。

推荐在独立干净检出中先审阅更新和安装依赖、构建、验收。
实际升级前确认运行实例已经停止、备份可用，且当前仓库没有未处理修改：

```bash
bash deploy/update.sh
```

默认脚本拒绝脏仓库，执行 `git pull --ff-only`、安装依赖、构建、保留旧 dist 并重启服务。
如果远端没有新提交，它会直接退出；若代码已提前拉取，应使用 `--local` 构建当前快照。
它不会改写本机配置，也没有完整数据回滚事务。

```bash
# 准备当前代码：依赖需事先在隔离环境装好，不拉取、不部署、不重启
bash deploy/update.sh --local --no-restart
```

`--no-restart` 只生成临时前端构建，不更新运行中的依赖或页面。
依赖须按 `backend/requirements.txt` 在停机或独立环境完整安装。
前后端必须配套部署：严格编辑版本字段会让旧客户端返回 422；部署后硬刷新所有页面。
回滚时先停止服务，恢复配套的旧源码、依赖及前端；若需要恢复数据，使用完整离线快照。
保留旧 dist 不能回滚集合、状态迁移或已发生的同步，也不能撤回已同步到其他设备的数据。

更多说明见[离线使用](docs/offline-use.md)、[原生能力验证](docs/pylib-verification.md)。

## 许可

当前版本采用 [GNU AGPL v3 或更新版本](LICENSE)，包含官方 Anki pylib 的相应许可义务。
向网络使用者提供修改后的服务时，应依 AGPL 第 13 条提供对应源码。
历史 MIT 授权仍然有效，原版权及第三方声明见 [NOTICE](NOTICE)。
