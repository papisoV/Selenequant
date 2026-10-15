# Dev Quickstart — 本地启动必读

> 新人(和未来的你)第一次跑本项目前,花 30 秒读完。三条提示能省掉最常见的三个"为什么页面是白的/卡在 Connecting"问题。

## 启动

```bash
# 终端 1:后端(45-60s 冷启动,属正常)
cd services/local-api
call ..\activate.bat
python -m uvicorn app.main:app --port 8088

# 终端 2:前端
cd apps/desktop
npm run dev
```

## 三条必读提示

1. **浏览器必须用 `http://localhost:<port>` 访问** — 端口以 `dev.bat` / `npm run dev` 打印的为准(默认 3000;多副本并行时 Next dev 会自动顺延到 3001 等)。关键是主机名用 `localhost`,用 `127.0.0.1` 会被 Next dev 的跨源开发防火墙拦截(所有资源 403),页面永远卡在 "Connecting to runtime"。
2. **后端就绪需要 45-60 秒** — local-api 冷启动含迁移+数据源探测,健康门(`BackendReadyProvider`)轮询最长 60s,等待期间显示启动日志,不要提前关页面。
3. **首次进页面内容出现约需 15 秒** — 健康门放行后,SWR 首次拉取(慢轮询)还需几秒;页面骨架先出,数据随后填充,不是卡死。

## 常见症状速查

| 症状 | 原因 | 解法 |
|---|---|---|
| 永卡 "Connecting to runtime" | 用了 127.0.0.1 访问 | 改用 `http://localhost:<dev 打印的端口>`(默认 3000) |
| console 全红 CORS | 后端未启动 / 端口不对 | 确认 8088 在跑(`curl http://127.0.0.1:8088/api/v1/health`) |
| 页面空白但无报错 | SWR 慢轮询首拉 | 等 ~15s,或刷新一次 |
| Mission 详情无四动作 | Mission 不在 needs_input 态 | 跑 `python scripts/push_acceptance_seed.py` 复位验收种子 |
| 驾驶舱提示"回测记录 id" | Mission subject 指向 bt- 回测而非策略 | 去 MISSION 页看该回测的调查记录 |
