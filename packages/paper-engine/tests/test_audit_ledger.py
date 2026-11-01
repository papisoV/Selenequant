"""Tests for HashChainLedger (C1) — chain integrity, redact, refuse-on-broken."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from selene_space_paper.audit_ledger import (
    HashChainLedger,
    LedgerCorruptionError,
)


@pytest.fixture()
def ledger(tmp_path: Path) -> HashChainLedger:
    return HashChainLedger(tmp_path / "decisions.chain.jsonl")


class TestHashChainLedger:
    def test_append_and_verify(self, ledger: HashChainLedger) -> None:
        e1 = ledger.append({"strategy_id": "s1", "symbol": "BTC/USDT", "reason": "ok"})
        e2 = ledger.append({"strategy_id": "s1", "symbol": "ETH/USDT", "reason": "ok2"})
        ok, bad = ledger.verify_chain()
        assert ok and bad == 0
        assert e1.seq == 1 and e2.seq == 2
        assert e2.prev_hash == e1.record_hash
        assert e1.record_hash != e2.record_hash

    def test_redact_truncates_and_masks(self, ledger: HashChainLedger) -> None:
        entry = ledger.append(
            {"reason": "x" * 500, "api_key": "sk-secret", "symbol": "BTC/USDT"}
        )
        assert len(entry.payload["reason"]) == 200
        assert entry.payload["api_key"] == "[redacted]"
        # 落盘文件里同样只有 redact 后内容
        line = (ledger._path).read_text(encoding="utf-8").splitlines()[0]
        assert "sk-secret" not in line

    def test_tamper_detected_and_located(
        self, ledger: HashChainLedger, tmp_path: Path
    ) -> None:
        ledger.append({"symbol": "BTC/USDT"})
        ledger.append({"symbol": "ETH/USDT"})
        ok, _ = ledger.verify_chain()
        assert ok

        lines = ledger._path.read_text(encoding="utf-8").splitlines()
        tampered = json.loads(lines[0])
        tampered["payload"]["symbol"] = "HACKED"
        lines[0] = json.dumps(tampered, ensure_ascii=False, sort_keys=True)
        ledger._path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        ok2, bad_seq = ledger.verify_chain()
        assert not ok2
        assert bad_seq in (1, 2)

    def test_refuse_append_on_broken_chain(self, ledger: HashChainLedger) -> None:
        ledger.append({"symbol": "BTC/USDT"})
        lines = ledger._path.read_text(encoding="utf-8").splitlines()
        broken = json.loads(lines[0])
        broken["payload"]["symbol"] = "HACKED"
        lines[0] = json.dumps(broken, ensure_ascii=False, sort_keys=True)
        ledger._path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with pytest.raises(LedgerCorruptionError):
            ledger.append({"symbol": "ETH/USDT"})

    def test_deletion_detected(self, ledger: HashChainLedger) -> None:
        ledger.append({"symbol": "A"})
        ledger.append({"symbol": "B"})
        ledger.append({"symbol": "C"})
        lines = ledger._path.read_text(encoding="utf-8").splitlines()
        del lines[1]  # 删中间一条
        ledger._path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, bad_seq = ledger.verify_chain()
        assert not ok

    def test_persistence_across_instances(
        self, ledger: HashChainLedger, tmp_path: Path
    ) -> None:
        ledger.append({"symbol": "BTC/USDT"})
        fresh = HashChainLedger(tmp_path / "decisions.chain.jsonl")
        ok, _ = fresh.verify_chain()
        assert ok
        e = fresh.append({"symbol": "ETH/USDT"})
        assert e.seq == 2  # 续前链而非重开
