"""Skills management endpoints.

Provides listing, detail view, enable/disable, create/edit/delete, and
refresh for loaded skills.  User-created skills are persisted as SKILL.md
files under ``~/.selenespace/skills/<name>/SKILL.md``.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.dependencies import (
    AuthenticatedUser,
    get_settings_service,
    get_skill_registry,
    optional_user,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/skills", tags=["skills"])

_USER_SKILLS_DIR = Path.home() / ".selenespace" / "skills"
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_SKILL_FILENAME = "SKILL.md"
#: extra_dirs 只允许落在这些根之下。
_ALLOWED_SKILL_ROOTS = [
    Path.home() / ".selenespace",
    Path("/opt/selenespace"),
]


def _user_skills_dir(user: AuthenticatedUser) -> Path:
    """Return the skills directory scoped to the current user."""
    if user.id == "local":
        return _USER_SKILLS_DIR
    return Path.home() / ".selenespace" / "users" / user.id / "skills"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class SkillEnableRequest(BaseModel):
    enabled: bool = Field(..., description="Whether to enable or disable the skill")


class SkillCreateRequest(BaseModel):
    """Create or import a skill.  The server builds the SKILL.md file."""

    name: str = Field(
        ..., min_length=1, max_length=64, description="Skill identifier (a-z, 0-9, - _)"
    )
    description: str = Field("", max_length=256, description="Short description")
    tags: list[str] = Field(default_factory=list, description="Categorisation tags")
    content: str = Field(..., min_length=1, description="Markdown body of the skill")
    user_invocable: bool = Field(True)
    model_invocable: bool = Field(True)
    requires_tools: list[str] = Field(default_factory=list)


class SkillUpdateRequest(BaseModel):
    """Update an existing user skill."""

    description: str | None = Field(None, max_length=256)
    tags: list[str] | None = None
    content: str | None = Field(None, min_length=1)
    user_invocable: bool | None = None
    model_invocable: bool | None = None
    requires_tools: list[str] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _source_str(skill: Any) -> str:
    """SkillSource 的字符串形式（enum 取 value，否则 str）。"""
    return skill.source.value if hasattr(skill.source, "value") else str(skill.source)


def _require_registry(registry: Any) -> None:
    if registry is None:
        raise HTTPException(503, "Skill registry not initialized")


def _validate_skill_name(name: str) -> None:
    if not _SAFE_NAME_RE.match(name):
        raise HTTPException(400, "Invalid skill name")


def _coalesce(new_value: Any, current: Any) -> Any:
    """新值非 None 则用它, 否则沿用现值(部分更新语义)。"""
    return new_value if new_value is not None else current


def _frontmatter_or(skill: Any, field: str, default: Any) -> Any:
    """取 frontmatter 字段; 没有 frontmatter 就用默认值。"""
    frontmatter = getattr(skill, "frontmatter", None)
    if frontmatter is None:
        return default
    return getattr(frontmatter, field, default)


def _redact_file_path(raw_path: str) -> str:
    """Strip absolute prefix, keep only skills-relative portion."""
    match = re.search(r"[/\\]skills[/\\](.+)", raw_path)
    if match:
        return "skills/" + match.group(1).replace("\\", "/")
    return raw_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def _skill_to_dict(
    skill: Any, enabled_map: dict[str, bool] | None = None
) -> dict[str, Any]:
    name = skill.name
    enabled = True if enabled_map is None else enabled_map.get(name, True)
    return {
        "name": name,
        "description": skill.description,
        "source": _source_str(skill),
        "tags": _frontmatter_or(skill, "tags", []),
        "user_invocable": _frontmatter_or(skill, "user_invocable", True),
        "model_invocable": _frontmatter_or(skill, "model_invocable", True),
        "requires_tools": _frontmatter_or(skill, "requires_tools", []),
        "file_path": _redact_file_path(skill.file_path),
        "enabled": enabled,
    }


#: 详情视图里直接取 frontmatter 字段的部分(键 → frontmatter 属性)。
_DETAIL_FRONTMATTER_FIELDS = (
    "tags",
    "user_invocable",
    "model_invocable",
    "requires_tools",
    "requires_env",
    "version",
    "author",
)


def _skill_detail(skill: Any) -> dict[str, Any]:
    """详情视图: 比列表多出 env/version/author/content 等。"""
    detail: dict[str, Any] = {
        "name": skill.name,
        "description": skill.description,
        "source": _source_str(skill),
    }
    for field in _DETAIL_FRONTMATTER_FIELDS:
        detail[field] = getattr(skill.frontmatter, field)
    detail["file_path"] = _redact_file_path(skill.file_path)
    detail["content"] = skill.content
    return detail


def _under_allowed_root(resolved: Path) -> bool:
    return any(
        resolved == root or root in resolved.parents for root in _ALLOWED_SKILL_ROOTS
    )


def _sanitize_extra_dirs(raw_dirs: list[str]) -> list[str]:
    """Filter extra_dirs to only allow paths under safe root directories."""
    safe: list[str] = []
    for raw in raw_dirs:
        try:
            resolved = Path(raw).expanduser().resolve()
        except Exception:
            logger.warning("Invalid skill directory path: %s", raw)
            continue
        if _under_allowed_root(resolved):
            safe.append(str(resolved))
        else:
            logger.warning("Rejected unsafe skill directory: %s", raw)
    return safe


def _refresh_into(registry: Any, cfg: dict[str, Any]) -> None:
    """按配置重设注册表并刷新。"""
    from selene_space_agent.skills.types import SkillsConfig

    registry._config = SkillsConfig(
        extra_dirs=_sanitize_extra_dirs(cfg.get("extra_dirs", [])),
        enabled=cfg.get("enabled", {}),
    )
    registry.refresh()


def _rebuild_registry(registry: Any, settings_svc: Any) -> None:
    if registry is None or settings_svc is None:
        return
    try:
        _refresh_into(registry, settings_svc.get_skills_config())
    except Exception:
        logger.warning("Failed to rebuild skill registry", exc_info=True)


def _yaml_list(items: list[str]) -> str:
    return "[" + ", ".join(items) + "]"


def _yaml_bool(value: bool) -> str:
    return "true" if value else "false"


def _frontmatter_block(pairs: list[tuple[str, str]]) -> str:
    """把 (键, 值) 序列编成 YAML frontmatter 块(带 --- 包围)。"""
    lines = ["---", *[f"{key}: {value}" for key, value in pairs], "---", ""]
    return "\n".join(lines)


def _ensure_heading(name: str, content: str) -> str:
    """正文若不以 # 开头, 补一个同名一级标题。"""
    stripped = content.strip()
    if stripped.startswith("#"):
        return stripped
    return f"# {name}\n\n{stripped}"


def _build_skill_md(
    name: str,
    description: str,
    tags: list[str],
    content: str,
    *,
    user_invocable: bool = True,
    model_invocable: bool = True,
    requires_tools: list[str] | None = None,
    version: str = "0.1.0",
) -> str:
    """Build a complete SKILL.md string with YAML frontmatter."""
    pairs = [
        ("name", name),
        ("description", description),
        ("version", version),
        ("tags", _yaml_list(tags)),
        ("user_invocable", _yaml_bool(user_invocable)),
        ("model_invocable", _yaml_bool(model_invocable)),
        ("requires_tools", _yaml_list(requires_tools or [])),
    ]
    body = _ensure_heading(name, content)
    return f"{_frontmatter_block(pairs)}\n{body}\n"


def _write_user_skill_scoped(base_dir: Path, name: str, md_content: str) -> Path:
    """Write a SKILL.md into a user-scoped skills directory."""
    skill_dir = base_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / _SKILL_FILENAME
    skill_file.write_text(md_content, encoding="utf-8")
    return skill_file


def _parse_frontmatter_lines(block: str) -> dict[str, str]:
    """把 frontmatter 块按 `键: 值` 逐行解析成 dict。"""
    parsed: dict[str, str] = {}
    for line in block.strip().splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        parsed[key.strip()] = val.strip()
    return parsed


def _parse_existing_skill_md(file_path: Path) -> dict[str, Any]:
    """Parse frontmatter + body from an existing SKILL.md."""
    text = file_path.read_text(encoding="utf-8")
    result: dict[str, Any] = {"content": text, "frontmatter": {}}
    if not text.startswith("---"):
        return result
    parts = text.split("---", 2)
    if len(parts) < 3:
        return result
    result["body"] = parts[2].strip()
    result["frontmatter"] = _parse_frontmatter_lines(parts[1])
    return result


def _enabled_map(settings_svc: Any) -> dict[str, bool]:
    """当前启用映射(无 settings 服务则为空)。"""
    if settings_svc is None:
        return {}
    return dict(settings_svc.get_skills_config().get("enabled", {}))


def _require_skill(registry: Any, name: str) -> Any:
    """取技能; 不存在则 404(停用的也算存在,详情页要能看到它再启用)。"""
    skill = registry.get_any(name)
    if skill is not None:
        return skill
    raise HTTPException(404, f"Skill '{name}' not found")


def _existing_user_skill(registry: Any, name: str, action: str) -> Any:
    """取同名技能; 存在但不是用户创建的则 403(用户技能才可改/删)。"""
    skill = registry.get_any(name)
    if skill is None:
        return None
    if _source_str(skill) != "user":
        raise HTTPException(403, f"Only user-created skills can be {action}")
    return skill


def _normalize_new_name(raw: str) -> str:
    """新建技能名规范化: 去空白、小写、空格换连字符。"""
    return raw.strip().lower().replace(" ", "-")


_CREATE_NAME_HINT = (
    "Invalid skill name. Use only letters, numbers, hyphens, underscores (1-64 chars)."
)


def _reject_bad_new_name(name: str) -> None:
    """创建时的名称校验(比通用校验多一句提示文案)。"""
    if not _SAFE_NAME_RE.match(name):
        raise HTTPException(400, _CREATE_NAME_HINT)


def _md_from_create_request(name: str, body: Any) -> str:
    """按创建请求构造 SKILL.md。"""
    fields = {
        "description": body.description,
        "tags": body.tags,
        "content": body.content,
        "user_invocable": body.user_invocable,
        "model_invocable": body.model_invocable,
        "requires_tools": body.requires_tools,
    }
    return _build_skill_md(name, **fields)


def _written_response(name: str, file_path: Path, verb: str) -> dict[str, Any]:
    """写盘类端点的统一响应(name + 相对路径 + 动作标记)。"""
    return {
        "name": name,
        "file_path": _redact_file_path(str(file_path)),
        verb: True,
    }


def _persist_skill(
    skills_dir: Path, name: str, md: str, registry: Any, settings_svc: Any
) -> Path:
    """写盘并刷新注册表, 返回写入的文件路径。"""
    file_path = _write_user_skill_scoped(skills_dir, name, md)
    _rebuild_registry(registry, settings_svc)
    return file_path


def _skills_listing(registry: Any, enabled_map: dict[str, bool]) -> dict[str, Any]:
    """列表响应体(含停用技能 —— 否则停用后无法再启用)。"""
    skills = registry.list_all_skills()
    return {
        "skills": [_skill_to_dict(s, enabled_map) for s in skills],
        "count": len(skills),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_skills(
    registry: Any = Depends(get_skill_registry),
    settings_svc: Any = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """List all loaded skills with their enabled state."""
    if registry is None:
        return {"skills": [], "count": 0, "message": "Skill registry not initialized"}
    return _skills_listing(registry, _enabled_map(settings_svc))


@router.post("/create")
async def create_skill(
    body: SkillCreateRequest,
    registry: Any = Depends(get_skill_registry),
    settings_svc: Any = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """Create a new user skill (scoped to current user)."""
    name = _normalize_new_name(body.name)
    _reject_bad_new_name(name)
    skills_dir = _user_skills_dir(user)
    if (skills_dir / name / _SKILL_FILENAME).exists():
        raise HTTPException(409, f"Skill '{name}' already exists. Use PUT to update.")

    md = _md_from_create_request(name, body)
    return _written_response(
        name, _persist_skill(skills_dir, name, md, registry, settings_svc), "created"
    )


def _remove_skill_dir(skills_dir: Path, name: str) -> None:
    """删除技能目录(不存在则跳过)。"""
    skill_dir = skills_dir / name
    if not skill_dir.exists():
        return
    shutil.rmtree(skill_dir)


@router.get("/{name}")
async def get_skill(
    name: str,
    registry: Any = Depends(get_skill_registry),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """Get detailed information about a specific skill."""
    _require_registry(registry)
    return _skill_detail(_require_skill(registry, name))


@router.put("/{name}")
async def update_skill(
    name: str,
    body: SkillUpdateRequest,
    registry: Any = Depends(get_skill_registry),
    settings_svc: Any = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """Update a user-created skill's content or metadata."""
    _validate_skill_name(name)
    _require_registry(registry)
    skill = _existing_user_skill(registry, name, "edited")
    if skill is None:
        raise HTTPException(404, f"Skill '{name}' not found")

    fm = skill.frontmatter
    fields = {
        "description": _coalesce(body.description, skill.description),
        "tags": _coalesce(body.tags, fm.tags),
        "content": _coalesce(body.content, skill.content),
        "user_invocable": _coalesce(body.user_invocable, fm.user_invocable),
        "model_invocable": _coalesce(body.model_invocable, fm.model_invocable),
        "requires_tools": _coalesce(body.requires_tools, fm.requires_tools),
    }
    md = _build_skill_md(name, version=fm.version, **fields)
    file_path = _persist_skill(_user_skills_dir(user), name, md, registry, settings_svc)
    return _written_response(name, file_path, "updated")


@router.delete("/{name}")
async def delete_skill(
    name: str,
    registry: Any = Depends(get_skill_registry),
    settings_svc: Any = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """Delete a user-created skill."""
    _validate_skill_name(name)
    _require_registry(registry)
    _existing_user_skill(registry, name, "deleted")

    _remove_skill_dir(_user_skills_dir(user), name)
    _rebuild_registry(registry, settings_svc)
    return {"name": name, "deleted": True}


def _apply_enabled(
    name: str, value: bool, registry: Any, settings_svc: Any, user_id: str | None
) -> None:
    """把启用状态写进 settings,重扫注册表,并让下次对话重建运行时。

    只改 ``config.enabled`` 不 ``refresh()`` 的话,注册表里仍是旧启停集合 ——
    界面与 prompt 都会继续按旧状态走,直到别处触发一次刷新。运行时把技能
    列表烘进了 ``skills_prompt``,所以也要作废缓存,否则模型下一轮还能看见
    刚被停用的技能。
    """
    if settings_svc is not None:
        enabled = _enabled_map(settings_svc)
        enabled[name] = value
        settings_svc.update_skills_config({"enabled": enabled})
        _rebuild_registry(registry, settings_svc)
    else:
        config = getattr(registry, "_config", None)
        if config is not None:
            config.enabled[name] = value
            registry.refresh()
    try:
        from app.api.v1.agent import invalidate_user_runtime

        invalidate_user_runtime(user_id)
    except Exception:
        logger.debug(
            "Could not invalidate agent runtime after skill toggle", exc_info=True
        )


@router.put("/{name}/enable")
async def toggle_skill(
    name: str,
    body: SkillEnableRequest,
    registry: Any = Depends(get_skill_registry),
    settings_svc: Any = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """Enable or disable a skill, persisted to settings."""
    _validate_skill_name(name)
    _require_registry(registry)
    uid = None if user.id == "local" else user.id
    _apply_enabled(name, body.enabled, registry, settings_svc, uid)
    return {"name": name, "enabled": body.enabled}


@router.post("/refresh")
async def refresh_skills(
    registry: Any = Depends(get_skill_registry),
    settings_svc: Any = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """Re-scan skill directories and reload all skills."""
    _require_registry(registry)
    _rebuild_registry(registry, settings_svc)
    return {"refreshed": True, "count": len(registry.list_skills())}
