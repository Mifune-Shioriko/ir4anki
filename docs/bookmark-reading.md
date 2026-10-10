# 阅读分割

普通阅读和溯源阅读都使用行选区与分割确认条，没有模式按钮或常驻模式提示。旧浏览器 `anki-reading-gap-policy` 偏好直接忽略。

选区成为 todo 子片段；选区前和多选区之间的内容成为 background。有效未读尾段保留 todo，不进入本轮 pending 或 child_chunks，后续轮次继续阅读。空白、仅标题或低于正文阈值的尾段成为 background。父片段成为 container，保留制卡关联和父子溯源；源 Markdown 不改写。

新客户端发送 `{path, seg_id, selections, fingerprint?, line_start?}`。坐标 guard 保持不变。旧客户端可显式发送 `gap_policy: "bookmark"`；`extract` 和未知值在请求校验阶段返回 422，不写状态、轮次或 flow。响应没有模式回显。兼容字段不参与运行时分割逻辑。

手动问答/挖空制卡、编辑、图片、相关卡片、历史溯源、阅读动作、交错 slot、评分撤销以及 promote/demote 能力保留。历史 background 不会自动恢复，没有数据库迁移或新增书签表。
