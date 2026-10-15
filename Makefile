.PHONY: dev test lint format typecheck install clean

# NOTE: 不再使用 pip install -e . —— editable install 会把绝对路径写入全局 site-packages 的 .pth,
# 多副本并行时会静默加载错误副本的源码。改用 activate.bat 注入 PYTHONPATH (见 activate.bat).
# 复制副本后零配置:activate.bat 用 %~dp0 自动定位当前副本.

dev:
	call activate.bat && cd services/local-api && uvicorn app.main:app --reload --host 127.0.0.1 --port 8088

install:
	@echo "本工程不再使用 pip install -e . (会污染全局 site-packages)."
	@echo "运行前请先: activate.bat (或在 cmd 里 call activate.bat)"
	@echo "首次安装依赖: pip install pydantic pydantic-settings fastapi uvicorn[standard] httpx websockets python-socks[asyncio] orjson structlog python-dotenv pandas numpy aiosqlite pyyaml keyring cryptography"
	@echo "可选依赖见 pyproject.toml [project.optional-dependencies]"

# ── Quality ──────────────────────────────────────────────────

lint:
	ruff check .

format:
	ruff format .
	ruff check --fix .

typecheck:
	mypy packages/

test:
	call activate.bat && pytest packages/ tests/ -q --tb=short

test-cov:
	call activate.bat && pytest packages/ tests/ --cov=packages --cov-report=term-missing -q

# ── Cleanup ──────────────────────────────────────────────────

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	rm -rf htmlcov/ .mypy_cache/
