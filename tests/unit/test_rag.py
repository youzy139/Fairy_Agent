"""RAG 单元测试：切块、向量库、知识库 facade 与四个工具（嵌入模型打桩）。

fastembed 以假模块注入 sys.modules，不触碰真实模型与网络。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest

from fairy.agent.core import Agent
from fairy.config import Settings, load_settings
from fairy.memory.embedding import Embedder, EmbeddingError, rag_available
from fairy.memory.vector import MIN_SCORE, VectorStore
from fairy.tools.base import ToolError
from fairy.tools.knowledge import (
    KnowledgeAddTool,
    KnowledgeBase,
    KnowledgeForgetTool,
    KnowledgeListTool,
    KnowledgeSearchTool,
    chunk_text,
)

# ------------------------------------------------------------------
# 桩：把文本长度映射为可预测的单位向量
# ------------------------------------------------------------------


class FakeEmbedder:
    """按首字符生成 one-hot 向量：同字开头的文本相似度为 1。"""

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = np.zeros((len(texts), 26), dtype=np.float32)
        for i, text in enumerate(texts):
            vectors[i, ord(text[0].lower()) % 26] = 1.0
        return vectors

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]


def _kb(tmp_path: Path) -> KnowledgeBase:
    return KnowledgeBase(VectorStore(tmp_path), FakeEmbedder())


# ------------------------------------------------------------------
# chunk_text
# ------------------------------------------------------------------
def test_chunk_short_text_single() -> None:
    assert chunk_text("短文本") == ["短文本"]


def test_chunk_packs_paragraphs() -> None:
    text = "第一段。\n\n第二段。\n\n第三段。"
    assert chunk_text(text, size=100) == ["第一段。\n第二段。\n第三段。"]


def test_chunk_splits_at_size_with_overlap() -> None:
    paras = [f"段落{i}" + "字" * 40 for i in range(5)]
    chunks = chunk_text("\n\n".join(paras), size=100, overlap=10)
    assert len(chunks) > 1
    assert all(len(c) <= 120 for c in chunks)  # 块长受控（重叠可能略超）


def test_chunk_hard_cuts_long_paragraph() -> None:
    chunks = chunk_text("字" * 1200, size=500, overlap=100)
    assert len(chunks) == 3
    assert chunks[1][:20] == "字" * 20  # 重叠衔接


def test_chunk_empty_returns_empty() -> None:
    assert chunk_text("  \n\n  ") == []


# ------------------------------------------------------------------
# VectorStore
# ------------------------------------------------------------------
def test_store_add_search_remove(tmp_path: Path) -> None:
    store = VectorStore(tmp_path)
    texts = ["苹果派做法", "苹果树种植", "香蕉保存"]
    embeddings = FakeEmbedder().embed(texts)
    assert store.add_chunks("食谱.txt", texts, embeddings) == 3

    hits = store.search(FakeEmbedder().embed_one("苹果"))
    assert len(hits) == 2  # 香蕉被阈值过滤
    assert all(source == "食谱.txt" for _, source, _ in hits)

    assert store.remove_source("食谱.txt") == 3
    assert store.count() == 0


def test_store_add_replaces_same_source(tmp_path: Path) -> None:
    store = VectorStore(tmp_path)
    emb = FakeEmbedder()
    store.add_chunks("a.txt", ["旧内容"], emb.embed(["旧内容"]))
    store.add_chunks("a.txt", ["新内容一", "新内容二"], emb.embed(["新内容一", "新内容二"]))
    assert store.count() == 2
    assert store.list_sources() == [("a.txt", 2)]


def test_store_search_empty_returns_empty(tmp_path: Path) -> None:
    store = VectorStore(tmp_path)
    assert store.search(FakeEmbedder().embed_one("任意")) == []


def test_store_rejects_mismatched_lengths(tmp_path: Path) -> None:
    store = VectorStore(tmp_path)
    with pytest.raises(ValueError, match="数量不一致"):
        store.add_chunks("a.txt", ["一", "二"], np.zeros((1, 4), dtype=np.float32))


# ------------------------------------------------------------------
# KnowledgeBase facade
# ------------------------------------------------------------------
def test_add_and_search_roundtrip(tmp_path: Path) -> None:
    kb = _kb(tmp_path)
    kb.add_text("笔记.md", "苹果派的烘烤温度是 180 度。")
    hits = kb.search("苹果相关")
    assert hits and hits[0][1] == "笔记.md"


def test_retrieve_context_wrapped_untrusted(tmp_path: Path) -> None:
    kb = _kb(tmp_path)
    kb.add_text("笔记.md", "苹果派的烘烤温度是 180 度。")
    context = kb.retrieve_context("苹果派怎么做")
    assert context is not None
    assert context.startswith("[知识库参考片段开始]")
    assert "不可信数据" in context
    assert context.endswith("[知识库参考片段结束]")


def test_retrieve_context_empty_kb_returns_none(tmp_path: Path) -> None:
    assert _kb(tmp_path).retrieve_context("任何查询") is None


def test_retrieve_context_below_threshold_returns_none(tmp_path: Path) -> None:
    kb = _kb(tmp_path)
    kb.add_text("笔记.md", "苹果派的做法")
    # one-hot 向量下完全不同字符的相似度为 0，低于 MIN_SCORE
    assert MIN_SCORE > 0
    assert kb.retrieve_context("香蕉") is None


def test_add_empty_text_raises(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="没有可入库的文本"):
        _kb(tmp_path).add_text("空.txt", "  \n\n ")


# ------------------------------------------------------------------
# 工具
# ------------------------------------------------------------------
def test_add_tool_file(tmp_path: Path) -> None:
    doc = tmp_path / "食谱.md"
    doc.write_text("苹果派：180 度烤 40 分钟。", encoding="utf-8")
    tool = KnowledgeAddTool(tmp_path, _kb(tmp_path))
    out = tool.execute("食谱.md")
    assert "1 个文件" in out
    assert tool.permission == "write"


def test_add_tool_directory(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("甲文档内容", encoding="utf-8")
    (tmp_path / "docs" / "b.txt").write_text("乙文档内容", encoding="utf-8")
    (tmp_path / "docs" / "c.exe").write_bytes(b"MZ")  # 非文本，应跳过
    out = KnowledgeAddTool(tmp_path, _kb(tmp_path)).execute("docs")
    assert "2 个文件" in out


def test_add_tool_rejects_outside_workspace(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="越权"):
        KnowledgeAddTool(tmp_path, _kb(tmp_path)).execute("../../etc/passwd")


def test_add_tool_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="不存在"):
        KnowledgeAddTool(tmp_path, _kb(tmp_path)).execute("ghost.md")


def test_search_tool_output_format(tmp_path: Path) -> None:
    kb = _kb(tmp_path)
    kb.add_text("笔记.md", "苹果派的做法")
    out = KnowledgeSearchTool(kb).execute("苹果")
    assert "来源：笔记.md" in out
    assert KnowledgeSearchTool(kb).permission == "read"


def test_search_tool_empty_kb(tmp_path: Path) -> None:
    out = KnowledgeSearchTool(_kb(tmp_path)).execute("任意")
    assert "没有相关内容" in out


def test_list_and_forget_tools(tmp_path: Path) -> None:
    kb = _kb(tmp_path)
    assert "空的" in KnowledgeListTool(kb).execute()
    kb.add_text("a.md", "甲内容")
    kb.add_text("b.md", "乙内容")
    out = KnowledgeListTool(kb).execute()
    assert "a.md" in out and "b.md" in out

    assert "删除" in KnowledgeForgetTool(kb).execute("a.md")
    with pytest.raises(ToolError, match="没有来源"):
        KnowledgeForgetTool(kb).execute("a.md")


def test_tools_hint_when_unavailable(tmp_path: Path) -> None:
    """knowledge=None（依赖缺失）时给出安装提示。"""
    with pytest.raises(ToolError, match=r"fairy-agent\[rag\]"):
        KnowledgeSearchTool(None).execute("任意")
    with pytest.raises(ToolError, match=r"fairy-agent\[rag\]"):
        KnowledgeAddTool(tmp_path, None).execute("x.md")


# ------------------------------------------------------------------
# Embedder（fastembed 打桩）
# ------------------------------------------------------------------
def test_embedder_normalizes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _FakeModel:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def embed(self, texts):
            return [np.array([3.0, 4.0], dtype=np.float32) for _ in texts]

    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=_FakeModel))
    embedder = Embedder(model_dir=tmp_path)
    vectors = embedder.embed(["文本"])
    assert np.linalg.norm(vectors[0]) == pytest.approx(1.0)


def test_embedder_mirror_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import os

    calls: list[str] = []

    class _FlakyModel:
        def __init__(self, *args, **kwargs) -> None:
            calls.append(os.environ.get("HF_ENDPOINT", ""))
            if len(calls) == 1:
                raise ConnectionError("huggingface.co timeout")

        def embed(self, texts):
            return [np.ones(4, dtype=np.float32) for _ in texts]

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=_FlakyModel))
    Embedder(model_dir=tmp_path).embed(["文本"])
    assert calls == ["", "https://hf-mirror.com"]


def test_embedder_load_failure_wrapped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _BoomModel:
        def __init__(self, *args, **kwargs) -> None:
            raise OSError("disk full")

    monkeypatch.setenv("HF_ENDPOINT", "https://hf-mirror.com")  # 跳过重试路径
    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=_BoomModel))
    with pytest.raises(EmbeddingError, match="模型加载失败"):
        Embedder(model_dir=tmp_path).embed(["文本"])


def test_rag_available_is_bool() -> None:
    assert rag_available() is True  # 本环境已安装 fastembed


# ------------------------------------------------------------------
# Agent 自动注入
# ------------------------------------------------------------------
class _StubLLM:
    def chat(self, messages, tools=None):
        class _Msg:
            content = "回答"
            tool_calls = None

        return _Msg()


def _make_agent(tmp_path: Path, knowledge, rag_enabled: bool = True) -> Agent:
    from fairy.safety.audit import AuditLogger
    from fairy.safety.policy import PolicyEngine
    from fairy.tools.registry import ToolRegistry

    settings = Settings(data_dir=tmp_path, rag_enabled=rag_enabled)
    return Agent(
        settings=settings,
        llm_client=_StubLLM(),
        registry=ToolRegistry(),
        policy=PolicyEngine(confirm=lambda p: True, confirm_phrase=lambda p: ""),
        audit=AuditLogger(tmp_path),
        knowledge=knowledge,
    )


def test_agent_injects_knowledge_context(tmp_path: Path) -> None:
    kb = _kb(tmp_path)
    kb.add_text("笔记.md", "苹果派的做法")
    agent = _make_agent(tmp_path, kb)
    agent.chat("苹果派怎么做")
    user_msg = agent.messages[1]["content"]
    assert "知识库参考片段" in user_msg
    assert "用户消息：苹果派怎么做" in user_msg


def test_agent_skips_injection_when_disabled(tmp_path: Path) -> None:
    kb = _kb(tmp_path)
    kb.add_text("笔记.md", "苹果派的做法")
    agent = _make_agent(tmp_path, kb, rag_enabled=False)
    agent.chat("苹果派怎么做")
    assert agent.messages[1]["content"] == "苹果派怎么做"


def test_agent_injection_failure_safe(tmp_path: Path) -> None:
    class _BoomKB:
        def retrieve_context(self, query):
            raise RuntimeError("模型加载失败")

    agent = _make_agent(tmp_path, _BoomKB())
    assert agent.chat("你好") == "回答"  # 检索失败不影响主流程
    assert agent.messages[1]["content"] == "你好"


# ------------------------------------------------------------------
# 配置
# ------------------------------------------------------------------
def test_rag_config_default_and_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FAIRY_RAG", raising=False)
    assert load_settings(dotenv_path=tmp_path / "no.env").rag_enabled is True
    monkeypatch.setenv("FAIRY_RAG", "false")
    assert load_settings(dotenv_path=tmp_path / "no.env").rag_enabled is False
