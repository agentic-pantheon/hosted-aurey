"""Stable LangChain tool argument schemas (names + JSON Schema) for planner contracts."""

from __future__ import annotations

import json
from pathlib import Path

from aurey.custody import FakeSecretStore
from aurey.graphs import DeterministicTxPipeline
from aurey.runtime import AureyRuntime
from aurey.settings import AureySettings
from aurey.tools.agent_tools import build_aurey_subgraph_tools
from tests.fakes.evm_rpc import rpc_factory_from_mapping
from tests.fakes.http_client import ScriptedHttpClient


def _normalize_schema(obj: object) -> object:
    return json.loads(json.dumps(obj, sort_keys=True))


def test_langchain_subgraph_tool_args_schemas_snapshot() -> None:
    settings = AureySettings(
        alchemy_api_secret_path="p/alchemy",
        wallet_signing_key_secret_path="k/sign",
    )
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore({"p/alchemy": "x", "k/sign": "0x1"}),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    tools = build_aurey_subgraph_tools(runtime)
    snapshot_path = Path(__file__).resolve().parent / "_tool_schemas_snapshot.json"
    raw_expected = json.loads(snapshot_path.read_text(encoding="utf-8"))
    expected = {name: _normalize_schema(sch) for name, sch in sorted(raw_expected.items())}
    actual = {
        t.name: _normalize_schema(t.args_schema.model_json_schema())
        for t in sorted(tools, key=lambda x: x.name)
    }
    assert actual == expected
