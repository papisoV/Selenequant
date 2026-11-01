"""Tests for scripts/check_dead_fields.py — the field-level contract gate.

The gate's whole value is its PRECISION in both directions:

  - it must FLAG a field the frontend renders but the backend never populates
    (a real "dead field" — the page silently shows `—`/`0`);
  - it must NOT flag a field that IS populated at a construction site, even
    though the naive "declared in a pydantic model" view cannot tell the two
    apart (this was the original scan's 121-false-positive root cause).

Running the script end-to-end on the live tree would pass even if the backend
extractor regressed to matching nothing (everything flagged) or everything
(nothing flagged). So these tests pin the extraction primitives directly:

  - `scan_backend` must count kwargs / dict-keys / subscript-assigns / SQL
    aliases, and must NOT count a bare pydantic field declaration;
  - the interface parser must treat a same-named interface in two files as two
    distinct types (name-keying silently merges them);
  - the baseline ratchet must fail only on NEW drift.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_MOD = Path(__file__).resolve().parent.parent / "scripts" / "check_dead_fields.py"
_spec = importlib.util.spec_from_file_location("check_dead_fields", _MOD)
df = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(df)


# --------------------------------------------------------------------------
# Backend production-set extraction: what counts as "populated".
# --------------------------------------------------------------------------


def _produced_from(tmp_path: Path, src: str) -> dict:
    """Run scan_backend against a single throwaway .py file."""
    f = tmp_path / "probe.py"
    f.write_text(src, encoding="utf-8")
    original = df.BACKEND_ROOTS
    df.BACKEND_ROOTS = [tmp_path]
    try:
        return dict(df.scan_backend())
    finally:
        df.BACKEND_ROOTS = original


def test_kwarg_counts_as_populated(tmp_path: Path) -> None:
    got = _produced_from(tmp_path, "x = Model(toxicity_score=1.0, mark_price=2)\n")
    assert got.get("toxicity_score") == 1
    assert got.get("mark_price") == 1


def test_dict_key_counts_as_populated(tmp_path: Path) -> None:
    got = _produced_from(tmp_path, 'd = {"free": 1, "locked": 2}\n')
    assert got.get("free") == 1
    assert got.get("locked") == 1


def test_subscript_assign_counts_as_populated(tmp_path: Path) -> None:
    got = _produced_from(
        tmp_path, 'row["payload"] = _load(r.pop("payload_json", None))\n'
    )
    # pop()'s default-position string must NOT count; the subscript target must.
    assert got.get("payload") == 1
    assert got.get("payload_json", 0) == 0


def test_sql_as_alias_counts_as_populated(tmp_path: Path) -> None:
    got = _produced_from(
        tmp_path,
        'sql = "SELECT COUNT(*) AS episode_count, AVG(x) AS avg_sharpe FROM t"\n',
    )
    assert got.get("episode_count") == 1
    assert got.get("avg_sharpe") == 1


def test_sql_bare_select_column_counts_as_populated(tmp_path: Path) -> None:
    """`SELECT a, b, payload_json FROM t` — no AS alias, still populated."""
    got = _produced_from(
        tmp_path,
        'sql = "SELECT id, timestamp, payload_json FROM events_recent ORDER BY x"\n',
    )
    assert got.get("payload_json") == 1
    assert got.get("id") == 1
    # SQL keywords must not leak in as fake field names
    assert "from" not in got
    assert "select" not in got
    assert "order" not in got
    assert "by" not in got


def test_bare_pydantic_declaration_is_NOT_populated(tmp_path: Path) -> None:
    """The crux: declaring a field in a model is NOT populating it.

    `take_profit_price_2: float | None = None` in a class body must yield no
    production count — otherwise declared-but-never-built fields are invisible.
    """
    got = _produced_from(
        tmp_path,
        "class TradeSetup(BaseModel):\n    take_profit_price_2: float | None = None\n",
    )
    assert got.get("take_profit_price_2", 0) == 0


def test_construction_without_the_field_leaves_it_unproduced(tmp_path: Path) -> None:
    """TradeSetup(direction=None) does NOT populate take_profit_price_2."""
    got = _produced_from(
        tmp_path,
        "x = TradeSetup(direction=None, decision_path=['gate'])\n",
    )
    assert got.get("direction") == 1
    assert got.get("take_profit_price_2", 0) == 0


# --------------------------------------------------------------------------
# Schema-in-prompt dynamic write path (fixes take_profit_price_2 false positive).
# --------------------------------------------------------------------------


def test_schema_emitted_model_fields_are_produced(tmp_path: Path) -> None:
    """A model whose JSON schema is rendered into the prompt is a write path.

    `Cls.model_json_schema()` → sent to the LLM → `Cls.model_validate(json)`
    fills the fields. No kwarg is ever written, but the field has a real
    source, so it must count as produced.
    """
    got = _produced_from(
        tmp_path,
        "import json\n"
        "class TradeSetup(BaseModel):\n"
        "    direction: str | None = None\n"
        "    take_profit_price_2: float | None = None\n"
        "class Stage2Decision(BaseModel):\n"
        "    trade_setup: TradeSetup | None = None\n"
        "prompt = json.dumps(Stage2Decision.model_json_schema())\n",
    )
    # direct field of the emitted class
    assert got.get("trade_setup", 0) >= 1
    # NESTED model's field — reached via the annotation closure
    assert got.get("take_profit_price_2", 0) >= 1
    assert got.get("direction", 0) >= 1


def test_model_without_schema_emission_stays_unproduced(tmp_path: Path) -> None:
    """A model never rendered into a prompt keeps its fields unproduced.

    A field is only "wired" when its model's schema is emitted (or it is passed
    at a construction site). Declaring the model alone is not enough — this is
    what keeps the check from blanket-allowing every pydantic field.
    """
    got = _produced_from(
        tmp_path,
        "class NodeOverride(BaseModel):\n"
        "    node_id: str\n"
        "    new_answer: str\n"
        "class Stage1Result(BaseModel):\n"
        "    node_overrides: list[NodeOverride] = []\n",
    )
    assert got.get("new_answer", 0) == 0
    assert got.get("node_overrides", 0) == 0


def test_generic_schema_helper_does_not_blanket_allow(tmp_path: Path) -> None:
    """A generic `model_cls.model_json_schema()` must NOT mark every model live.

    Otherwise every pydantic model in the tree would be exempt, which would
    silently disable the whole check.
    """
    got = _produced_from(
        tmp_path,
        "def get_json_schema(model_cls):\n"
        "    return model_cls.model_json_schema()\n"
        "class NodeOverride(BaseModel):\n"
        "    new_answer: str\n",
    )
    assert got.get("new_answer", 0) == 0


# --------------------------------------------------------------------------
# Name normalization: snake ↔ camel (fixes sessionId false positive).
# --------------------------------------------------------------------------


def test_norm_names_covers_snake_and_camel() -> None:
    assert df._norm_names("session_id") == df._norm_names("sessionId")
    assert "sessionId" in df._norm_names("session_id")
    assert "session_id" in df._norm_names("sessionId")


def test_norm_names_single_word_is_stable() -> None:
    assert df._norm_names("symbol") == {"symbol"}


def test_norm_names_multiword() -> None:
    assert df._norm_names("totalCallGex") == df._norm_names("total_call_gex")


# --------------------------------------------------------------------------
# Frontend interface parsing: file-scoped identity (no cross-file merge).
# --------------------------------------------------------------------------


def test_same_named_interfaces_are_distinct_types(tmp_path: Path) -> None:
    """Two files each declaring `interface Position` must not be merged."""
    fe = tmp_path / "apps" / "desktop" / "lib" / "api"
    fe.mkdir(parents=True)
    (fe / "a.ts").write_text(
        "export interface Position {\n  symbol?: string;\n  side?: string;\n}\n",
        encoding="utf-8",
    )
    (fe / "b.ts").write_text(
        "export interface Position {\n  market: string;\n  tif?: string;\n"
        "  dur?: string;\n}\n",
        encoding="utf-8",
    )
    original = df.FRONTEND_ROOT
    original_root = df.ROOT
    df.FRONTEND_ROOT = tmp_path / "apps" / "desktop"
    df.ROOT = tmp_path
    try:
        types, resp, _ = df.scan_frontend()
    finally:
        df.FRONTEND_ROOT = original
        df.ROOT = original_root

    keys = {k for k in types if k[1] == "Position"}
    assert len(keys) == 2, "same-named interfaces must stay separate"
    a = next(types[k]["fields"] for k in keys if k[0].endswith("a.ts"))
    b = next(types[k]["fields"] for k in keys if k[0].endswith("b.ts"))
    assert set(a) == {"symbol", "side"}
    assert set(b) == {"market", "tif", "dur"}  # 'dur' not leaked into a.ts


# --------------------------------------------------------------------------
# Baseline ratchet: only NEW drift fails.
# --------------------------------------------------------------------------


def test_ratchet_flags_new_and_ignores_known(monkeypatch, capsys) -> None:
    cand = [
        {"decl_file": "f.ts", "type": "T", "field": "known_dead"},
        {"decl_file": "f.ts", "type": "T", "field": "brand_new_dead"},
    ]
    monkeypatch.setattr(df, "compute_candidates", lambda: cand)
    monkeypatch.setattr(
        df, "load_baseline", lambda: {("f.ts", "T", "known_dead"): "reviewed"}
    )
    rc = df.main([])
    out = capsys.readouterr().out
    assert rc == 1
    # the NEW field is listed in the failure block
    assert "brand_new_dead" in out
    # the known field is not re-reported as a violation
    fail_block = out.split("FAIL")[1]
    assert "known_dead" not in fail_block


def test_ratchet_passes_when_all_known(monkeypatch, capsys) -> None:
    cand = [{"decl_file": "f.ts", "type": "T", "field": "a"}]
    monkeypatch.setattr(df, "compute_candidates", lambda: cand)
    monkeypatch.setattr(df, "load_baseline", lambda: {("f.ts", "T", "a"): "reviewed"})
    assert df.main([]) == 0
    assert "OK" in capsys.readouterr().out


def test_ratchet_reports_stale_without_failing(monkeypatch, capsys) -> None:
    """A baseline entry whose field got fixed is a hint, not a failure."""
    monkeypatch.setattr(df, "compute_candidates", lambda: [])
    monkeypatch.setattr(
        df, "load_baseline", lambda: {("f.ts", "T", "fixed"): "was dead"}
    )
    rc = df.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "fixed" in out  # surfaced as stale hint


# --------------------------------------------------------------------------
# Render signals must come from code, not comments.
# --------------------------------------------------------------------------


def test_strip_comments_keeps_braces_in_code_and_drops_them_in_comments() -> None:
    src = (
        "// 端点: /api/v1/ashare/{lhb,northbound} —— 说明文字\n"
        "/* 块注释 {alpha, beta} */\n"
        "const { delta, epsilon } = props;\n"
        'const u = "http://x/{zeta,eta}";\n'
        "const t = `a // not a comment ${theta}`;\n"
    )
    out = df._strip_comments(src)

    # 注释里的花括号不再参与解构识别
    assert "lhb" not in out and "alpha" not in out
    # 真实解构、字符串内的花括号、模板串里的内容原样保留
    assert "delta" in out and "epsilon" in out
    assert "{zeta,eta}" in out
    assert "theta" in out
    # 行号不漂移：注释换成等量换行
    assert len(out.splitlines()) == len(src.splitlines())


def test_block_comment_newlines_preserved_for_line_numbers() -> None:
    src = "/* one\n   two\n   three */\nlet x = 1;\n"
    out = df._strip_comments(src)
    assert out.count("\n") == src.count("\n")


def test_comment_only_field_is_not_a_render_signal(tmp_path: Path, monkeypatch) -> None:
    """端到端：只在注释里出现的字段不得被判为"渲染"。

    真实回归（2026-10-20）：`// 深度面 {lhb,northbound}` 被解构正则读成渲染了
    `lhb`，于是请求类型 `RecapRequest.lhb` 被报成死字段。该字段在后端实为
    `Query(True)` 查询参数，前端只以 `req.lhb`（成员访问）消费。
    """
    (tmp_path / "page.tsx").write_text(
        "// 端点: /api/v1/ashare/{lhb,northbound}\n"
        "export interface Req {\n"
        "  lhb?: boolean;\n"
        "}\n"
        "export function P(props: { req: Req }) {\n"
        "  return props.req.lhb;\n"
        "}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(df, "FRONTEND_ROOT", tmp_path)
    monkeypatch.setattr(df, "ROOT", tmp_path)

    types, resp_types, rendered = df.scan_frontend()
    assert ("page.tsx", "Req") in types
    assert types[("page.tsx", "Req")]["fields"] == ["lhb"]
    # 成员访问 `req.lhb` 仍然算渲染信号（去掉它这份代码就是死字段）
    assert "lhb" in rendered
    # 而"只有注释"的字段不会被误加进来
    assert "northbound" not in rendered


def test_removing_the_member_access_makes_the_field_unrendered(
    tmp_path: Path, monkeypatch
) -> None:
    """反向对照：证明上一条的 `lhb in rendered` 来自代码而非注释。

    把 `props.req.lhb` 换成不读该字段的写法后，注释原样保留，字段必须从
    rendered 里消失 —— 否则说明注释仍在喂信号。
    """
    (tmp_path / "page.tsx").write_text(
        "// 端点: /api/v1/ashare/{lhb,northbound}\n"
        "export interface Req {\n"
        "  lhb?: boolean;\n"
        "}\n"
        "export function P({ req }: { req: Req }) {\n"
        "  return req && 'ok';\n"
        "}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(df, "FRONTEND_ROOT", tmp_path)
    monkeypatch.setattr(df, "ROOT", tmp_path)

    _, _, rendered = df.scan_frontend()
    assert "lhb" not in rendered


# --------------------------------------------------------------------------
# The committed baseline must be well-formed and actually used.
# --------------------------------------------------------------------------


def test_committed_baseline_is_wellformed() -> None:
    data = json.loads(df.BASELINE_PATH.read_text(encoding="utf-8"))
    assert isinstance(data.get("known"), list)
    for item in data["known"]:
        assert set(item) >= {"file", "type", "field", "reason"}
        # every committed entry must carry a real reason, not the placeholder
        assert item["reason"] and "UNREVIEWED" not in item["reason"]


def test_baseline_has_no_duplicate_keys() -> None:
    data = json.loads(df.BASELINE_PATH.read_text(encoding="utf-8"))
    keys = [(i["file"], i["type"], i["field"]) for i in data["known"]]
    assert len(keys) == len(set(keys)), "duplicate baseline entries"
