# TGWatcher

Telegram 群组消息爬虫，带 Web 管理面板和新闻信号分析引擎。

## 功能

- **消息爬取** — 增量/全量爬取 Telegram 群组消息，支持 SOCKS5 代理
- **Web 管理面板** — 群组管理、消息浏览、数据统计、热力图
- **信号引擎** — 关键词预筛选 + LLM 因子提取（情绪/事件/范围/紧迫度）
- **数据存储** — SQLite，规范化 Chat/Sender 表，支持编辑/删除追踪

## 快速开始

### 1. 安装依赖

```bash
pip install -e .
```

或：

```bash
pip install -r requirements.txt
```

### 2. 配置

复制模板并填入你的 Telegram 凭证：

```bash
cp config.example.yaml config.yaml
```

编辑 `config.yaml`，填入：

| 字段 | 说明 |
|------|------|
| `telegram.api_id` | 从 [my.telegram.org](https://my.telegram.org) 获取 |
| `telegram.api_hash` | 同上 |
| `telegram.phone` | 你的手机号（含国际区号） |
| `groups` | 要监控的群组列表 |
| `proxy` | 代理设置（国内用户通常需要） |

### 3. 运行

```bash
python main.py
```

首次运行会要求 Telegram 验证码登录。登录成功后 session 会保存在 `sessions/` 目录。

### 4. Web 面板

启动后访问 `http://localhost:5000`，可管理群组、浏览消息、查看统计。

## 项目结构

```
tgwatcher/
├── client.py          # Telegram 客户端封装
├── listener.py        # 消息监听
├── models.py          # 数据模型
├── storage.py         # 数据库操作
├── parsers.py         # 消息解析器
├── schemas.py         # 数据结构定义
├── signal_engine.py   # 信号引擎
├── signal_filter.py   # 关键词预筛选
├── signal_llm.py      # LLM 因子提取
├── web/
│   ├── app.py         # Flask 应用入口
│   ├── api.py         # REST API
│   ├── crawl_service.py  # 爬取服务
│   ├── signal_service.py # 信号 API
│   └── static/        # 前端静态文件
├── config.example.yaml  # 配置模板
└── main.py            # 程序入口
```

## 信号引擎

启用信号分析需在 `config.yaml` 中配置 LLM：

```yaml
signal:
  enabled: true
  llm:
    provider: deepseek
    base_url: https://api.deepseek.com/v1
    model: deepseek-chat
    api_key: YOUR_API_KEY
```

提取 4 个因子维度：
- **情绪** — bullish / bearish / neutral
- **事件类型** — regulatory / macro / exploit / listing / partnership
- **范围+强度** — macro/micro + high/medium/low
- **紧迫度** — high / medium / low

## 注意事项

- `config.yaml` 含敏感凭证，已在 `.gitignore` 中排除，切勿提交
- `data/` 和 `sessions/` 目录同样被排除
- 国内用户需配置代理才能连接 Telegram

## License

MIT — 见 [LICENSE](LICENSE)

### Third-Party Assets

- [Chart.js v4.5.1](https://www.chartjs.org) — MIT License (bundled in `tgwatcher/web/static/chart.min.js`)
- [Inter](https://github.com/rsms/inter) — SIL Open Font License 1.1 (bundled in `tgwatcher/web/static/fonts/Inter-*.woff2`)
- [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono) — SIL Open Font License 1.1 (bundled in `tgwatcher/web/static/fonts/JetBrainsMono-*.woff2`)

Font license text: [`tgwatcher/web/static/fonts/OFL.txt`](tgwatcher/web/static/fonts/OFL.txt)
