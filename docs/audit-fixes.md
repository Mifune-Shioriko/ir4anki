# 审计修复契约（2026-10-14）

这些变更只进入源码；部署、运行中的服务、语料和集合没有在本任务中更新。

## 普通 API 的 Host / 浏览器来源保护

所有 `/api/` 和 `/media/` 请求使用同一服务端保护。默认接受 `localhost` 和回环 IP；其他 Host 必须由操作者用 `ANKI_TRUSTED_HOSTS` 明确配置，逗号分隔，写主机名或裸 IP，不含协议、端口或通配符。手机、桌面、Tailscale 应使用同一个来源提供页面和 API，并把实际访问的服务器主机名/IP 加入该配置，例如 `review.example.ts.net,100.100.1.2`。配置值不是客户端白名单，是服务器可接受的 Host。

有 Origin 的浏览器请求必须与请求的协议、主机和有效端口一致（默认 80/443 等价）；`null`、其他来源以及 Fetch-Metadata 的 `cross-site`/`same-site` 被拒绝。没有 Origin 和 Fetch-Metadata 的直接 CLI 请求在可信 Host 下允许。没有通配 CORS 或跨来源预检许可，同来源 OPTIONS 返回 200。测试夹具单独显式配置 `test` 等合成 Host；这些名称不属于应用默认信任范围。

这用于阻止浏览器跨站访问和 DNS rebinding，不替代网络访问控制或对非浏览器客户端的身份认证。可直接连到监听地址的非浏览器工具仍可使用可信 Host；部署者应按原有受信网络监听/防火墙限制可达性。应用不依据 Forwarded/X-Forwarded-Host 扩大 Host 信任。反向代理需要提供正确的 Host 和同来源协议；代理的可信来源配置仍由部署者负责。

受保护的管理 API 仍要求原有 bearer。`/api/local/sync` 仍额外要求直接回环 peer、回环 Host、准确 Origin、自定义确认头和 JSON 确认；普通 Host 配置不能放宽此限制。不增加 token，也不将凭据写入 URL、浏览器存储或 bundle。同步默认仍是手动，不自动选择完整同步方向。

## 阅读编辑

ReadingChunk 和 ReadingSource 返回 `edit_revision`（整份逻辑 UTF-8 文本 SHA256，读取时按现有规则归一换行）。保存 `/api/reading/edit` 必须提供 `path`、`seg_id`、`new_text`、`expected_revision` 和 `expected_fingerprint`。客户端在打开编辑器时保留返回的版本；不能在保存前读取新版本替换旧版本。

服务器在同一状态锁内比较版本、磁盘文本与存储摘要；不同则在修改文件/数据库之前返回 409。片段变为 container 等既有保护继续生效。缺少版本的旧调用返回 422，这是有意的严格契约变更；不提供隐式无条件覆盖兼容路径。编辑器保留草稿并显示冲突，用户重新打开后手动合并。其他片段的编辑也会改变整文件版本，因此保守拒绝旧编辑器。

## 上传

默认预算：每文件 26,214,400 字节（25 MiB）、整批 104,857,600 字节（100 MiB）、100 个文件。可用 `ANKI_UPLOAD_MAX_BYTES`、`ANKI_UPLOAD_BATCH_MAX_BYTES`、`ANKI_UPLOAD_MAX_FILES` 覆盖，必须为正整数，非法值使应用启动失败。应用读取每块最多 64 KiB，达到预算后只多读取一个字节用来检测越界；引用改写后的字节数也受单文件/整批限制。413 包含实际预算。Multipart 解析器在端点前会将大文件 spool 到临时文件；应用批内容的内存预算是有限的，不是无限读取。反向代理/HTTP 层的请求体限制仍由部署者自行配置。

上传读取和 UTF-8 解码在状态锁外进行；重新校验路径、冲突分配和提交在锁内完成。整批解码、目标分配、父路径、最终分配的文件名/路径长度及批内重复目标先检查，再写入。批内重复的原始目标（包括规范化后的相同路径）在任何策略下均拒绝 409；不同文件的 rename 分配保留所有本批目标，不会互相覆盖。预检失败不写任何文件。

图片映射的键为原始规范化相对路径，不是 basename。Markdown inline/reference 图片（平衡括号、转义、尖括号路径）和原有支持的 HTML `src`/`href` 相对原笔记路径解析，只改写本批内精确对应的图片；外部 URL、绝对/越界、无关引用以及 fenced/inline code 保持原样。输出仍兼容按 basename 服务；有空格的改名路径进行 URL 编码。`images_renamed` 只列真正改名的图片，值为最终 corpus 路径。

每个文件使用同目录临时文件、文件 fsync、atomic replace、目录 fsync；整批不是文件系统事务。真实 I/O 失败返回 503，`detail` 包含 `partial`、`saved`、`failed`、`remaining` 和提示。`saved` 是已替换文件的清单；如果目录 fsync 失败，该文件可能已在清单内而持久性未确认。客户端显示已保存路径并刷新缓存，用户核对后重试；不自动重复覆盖。故障发生前创建的空目录可能保留。进程/断电中断上传不提供整批回滚日志，不能把它当作原子导入。

## 轮次状态与性能

前端使用统一 generation：新的 GET 只应用其当前 generation；评分、撤销、开始、分割、退出等 mutation 在开始时失效旧 GET，权威 mutation 成功又失效执行中发起的 GET。旧成功或失败均不能回退新状态。评分仍等待服务器的 state，不在收到评分确认前推进下一张。

原生 whole-round flow 发牌只保留轻量元数据/ID；普通 flow state 在阅读阶段不渲染复习卡，复习阶段只返回 `cards` 中当前可见的一张。完整顺序和总数仍由持久队列及 flow 字段提供。评分响应已经采用同一可见卡契约；旧独立 `/api/session/*` 批量 API 保留其批量兼容行为，Connect fallback 保持既有语义。

阅读持久化在事务中比较当前 DB 行，只更新变化的文件/片段和该片段的 provenance；未变行不删除重插。没有跨请求快照缓存。orphans 变化时更新该文件的 orphan 组；归档、轮次和删除保持事务语义。

迁移只在局部连接使用 `synchronous=FULL`，DB remap 与 operation marker 同事务提交；再 fsync state 目录及父目录，然后 durably 写 flow 别名，最后允许迁移 journal 完成和清 pending。已有 marker 的恢复仍重放缺失 flow bookkeeping。故障测试验证 ordering 和独立进程重启，不等价于硬件断电模拟。

重启后的答案撤销将完整 scheduling guard 放进恢复写事务，避免同一次撤销重复快照；card/revlog/decks/deck_config/config/FSRS 保护保持完整，没有以缓存版本猜测替代。仍需读取完整 revlog，不能据此声称所有答案校验都变成常数成本。

### 上传代码区域解析依赖（独立复查修正）

后端新增固定运行依赖 `markdown-it-py==4.0.0`；未来部署必须先按 `backend/requirements.txt` 更新运行环境。本任务只在 RUN 隔离测试环境复用本地包缓存，没有修改 operational venv 或部署。上传使用本地 CommonMark 块 token 行映射识别顶层、引用、嵌套引用和列表中的缩进/围栏代码，按原始字符偏移屏蔽；不重新序列化 Markdown。保留现有精确反引号段屏蔽，普通容器图片继续改写。解析仅运行 block 阶段，嵌套深度上限 100，受现有单文件/批次上传预算约束；不调用 Node 或网络，离线运行。
