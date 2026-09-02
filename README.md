# anki-review-app

自托管 Anki 复习栈的复习端：FastAPI 后端（经 AnkiConnect 代理操作 headless Anki）+ SolidJS/MD3 前端。

## 布局

```
backend/    FastAPI review backend (app.py), serves frontend/dist as SPA
frontend/   SolidJS 1.9 + @material/web (Material Design 3) frontend
```

后端所有外部端点/路径均可用环境变量覆盖（ANKICONNECT_URL / ANKI_RAG_URL /
ANKI_EXPLAIN_URL / ANKI_MEDIA_DIR / REVIEW_DIST_DIR / ANKI_STATE_DIR），
默认按本仓库布局（仓库根下 frontend/dist）。

## 部署（Beelink）

- systemd 用户服务 `anki-review`：uvicorn backend/app.py :8901
- 前端构建：`cd frontend && npm run build`（产物 frontend/dist/）
- 部署细节（配额常量联动、前端规则）见 Hermes skills `anki-self-hosted` / `anki-automation`

## 新卡预览模式（preview mode）

新制卡先进入预览池（子牌组 `2026::预览池`），复习端以"先看后考"的纯阅读轮
消化：正反面同屏、无评分，逐张放行（取消挂起→进入正常新卡队列）或推迟。
由环境变量 `ANKI_PREVIEW_MODE=1` 启用；关闭时行为与无预览完全一致。

回滚：
1. 系统级回滚 = 关闭 `ANKI_PREVIEW_MODE`（或换回旧部署目录 + 重启服务）。
2. 数据回滚 = 预览池只是子牌组 + 未挂起标记，`findCards deck:2026::预览池`
   列出后取消挂起即全部回到正常学习流；牌组可随时删除而不丢卡。
