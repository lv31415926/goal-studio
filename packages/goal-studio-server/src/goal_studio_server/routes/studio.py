"""创作循环 REST API: draft / refine / validate / build + 棘轮门控
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import uuid
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from goal_studio.core.adapter_registry import AdapterRegistry
from goal_studio.core.contract import Artifact, GoalContract, Patch, PatchOp, ProductType
from goal_studio.core.embedding import cosine_similarity
from goal_studio.core.knowledge_store import KnowledgeEntry
from goal_studio.core.llm_judge import LLMJudgeEvaluator
from goal_studio.core.node_graph import NodeGraph, NodeLayer
from goal_studio.core.ratchet_gate import RatchetDecision
from goal_studio.core.session_manager import Session, SessionManager, SessionPhase
from goal_studio.llm.base import BaseLLMProvider
from pydantic import BaseModel

from goal_studio_server.deps import (
    get_adapter_registry,
    get_artifact_store,
    get_knowledge_store,
    get_llm_provider,
    get_session_manager,
)
from goal_studio_server.metrics import METRICS
from goal_studio_server.models import (
    ArtifactModel,
    DraftRequest,
    GoalContractModel,
    NodeGraphModel,
    PatchModel,
    RefineRequest,
    ReportModel,
)
from goal_studio_server.models import (
    ProductType as ModelProductType,
)

router = APIRouter()


# === 棘轮门控响应模型 ===

class RatchetResponse(BaseModel):
    """棘轮门控响应"""
    decision: str
    old_score: float
    new_score: float
    delta: float
    best_score: float
    current_score: float
    reverted: bool = False
    early_stopped: bool = False
    message: str = ""
    graph: NodeGraphModel | None = None


class RatchetHistoryItem(BaseModel):
    round: int
    old_score: float
    new_score: float
    delta: float
    decision: str
    patch_summary: str = ""
    issues: list[str] = []


class RatchetHistoryResponse(BaseModel):
    history: list[RatchetHistoryItem]
    best_score: float
    current_score: float
    snapshot_count: int


# === G3/G4/G5: 撤销响应 / 导出安装请求响应 ===

class UndoResponse(BaseModel):
    """撤销响应(G3: 附带剩余快照数,驱动前端撤销按钮可用态)"""
    graph: NodeGraphModel
    snapshots_remaining: int
    phase: str


class ExportRequest(BaseModel):
    target: str = ""
    """导出目标 file:// URI(默认: 产物库同级 goal-studio-exports/{session_id})"""


class ExportResponse(BaseModel):
    location: str
    phase: str


class InstallRequest(BaseModel):
    target: str = "file"
    """安装目标: file / maxfun"""
    skill_dir: str = ""
    name: str = ""


class InstallResponse(BaseModel):
    result: str
    phase: str


# === G1: 经验检索与反思沉淀 ===

_STOPWORDS = set("的了和与及或在对于把是一个这个那个为要以并等".split())
_STOPWORDS |= {
    "创建", "生成", "制作", "开发", "一个", "系统", "能力", "目标", "需要",
    "the", "a", "an", "of", "for", "and", "to", "with", "create", "build",
}


def _goal_tokens(text: str) -> set[str]:
    """抽取目标文本关键词(中文 bigram + 英文词,去停用词)"""
    tokens: set[str] = set()
    for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text):
        if w.lower() not in _STOPWORDS:
            tokens.add(w.lower())
    han = re.findall(r"[\u4e00-\u9fff]", text)
    cleaned = han
    for i in range(len(cleaned) - 1):
        bg = cleaned[i] + cleaned[i + 1]
        if bg not in _STOPWORDS:
            tokens.add(bg)
    return tokens


def _get_embed() -> Any:
    """获取 embedding provider(默认 None — env 未开启时检索走关键词)"""
    from goal_studio.core.embedding import get_embedding_provider

    try:
        return get_embedding_provider()
    except Exception:  # noqa: BLE001
        return None


def _rank_entries(goal_text: str, entries: list[Any]) -> list[tuple[float, Any]]:
    """P2: 统一检索排序 — embedding 余弦优先,不可用/异常降级关键词重合度

    返回 [(score, entry)] 降序;完全无匹配的条目不返回。
    """
    provider = _get_embed()
    if provider is not None and entries:
        try:
            goal_vec = provider.embed([goal_text])[0]
            scored: list[tuple[float, Any]] = []
            for e in entries:
                # 优先用沉淀时缓存的向量,无则现算(不回写,避免检索路径产生写放大)
                vec = e.metadata.get("embedding")
                if not isinstance(vec, list) or len(vec) != provider.dim:
                    vec = provider.embed([e.value])[0]
                sim = cosine_similarity(goal_vec, vec)
                if sim > 0:
                    scored.append((sim + e.confidence * 0.1, e))
            scored.sort(key=lambda x: -x[0])
            if scored:
                return scored
        except Exception:  # noqa: BLE001 — 向量路径异常整体降级关键词
            pass
    # 关键词降级路径(与升级前行为一致)
    goal_tokens = _goal_tokens(goal_text)
    if not goal_tokens:
        return []
    kw_scored: list[tuple[float, Any]] = []
    for e in entries:
        entry_tokens = _goal_tokens(e.value + " " + " ".join(e.tags))
        overlap = len(goal_tokens & entry_tokens)
        if overlap > 0:
            kw_scored.append((overlap + e.confidence, e))
    kw_scored.sort(key=lambda x: -x[0])
    return kw_scored


def _retrieve_lessons(goal_text: str, store: Any, limit: int = 3) -> list[str]:
    """G1: 检索历史反思经验 Top-N(P2: 向量相似度优先,关键词降级)"""
    entries = store.query(category="experience")
    if not entries:
        return []
    return [e.value for _, e in _rank_entries(goal_text, entries)[:limit]]


def _build_reflection(
    goal_text: str,
    patch_summary: str,
    decision: str,
    old_score: float,
    new_score: float,
    issues: list[str],
) -> str:
    """G1: REVERT/EARLY_STOP 时生成结构化反思(确定性模板,mock LLM 友好)"""
    lines = [
        f"目标: {goal_text[:120]}",
        f"操作: {patch_summary}",
        f"结果: {decision} (旧分 {old_score:.1f} → 新分 {new_score:.1f})",
    ]
    if issues:
        lines.append("评估问题: " + "; ".join(issues[:5]))
    lines.append("教训: 此类修改未通过棘轮门控,后续同类目标起草/精化时应避免。")
    return "\n".join(lines)


# === Voyager 技能库: 验证通过的结构沉淀与复用 ===

_PATTERN_L1_MAX = 8
_PATTERN_VALUE_MAX = 8000


def _retrieve_patterns(goal_text: str, store: Any, limit: int = 2) -> list[dict[str, Any]]:
    """Voyager 读: 按目标关键词重合度检索成功结构 Top-K(解析为 dict,坏条目跳过)"""
    import json as _json

    entries = store.query(category="pattern")
    if not entries:
        return []
    out: list[dict[str, Any]] = []
    for _, e in _rank_entries(goal_text, entries):
        try:
            pat = _json.loads(e.value)
        except Exception:  # noqa: BLE001 — 坏条目跳过,不阻断检索
            continue
        if isinstance(pat, dict) and isinstance(pat.get("l1_nodes"), list):
            out.append(pat)
        if len(out) >= limit:
            break
    return out


def _store_pattern(session_id: str, s: Session, report: Any) -> None:
    """Voyager 写: 验证 PASS 的会话沉淀 (goal, L1 骨架) 为 procedural 记忆

    key=pattern:{session_id} — 同会话重复 validate 覆盖更新而非堆积。
    """
    import json as _json

    if not s.graph or not s.goal or not report.passed:
        return
    l1_nodes = [
        {
            "title": n.title,
            "description": (n.description or "")[:120],
        }
        for n in s.graph.get_layer(NodeLayer.L1_PLANNING)[:_PATTERN_L1_MAX]
    ]
    if not l1_nodes:
        return
    pat = {
        "goal": s.goal.goal[:200],
        "product_type": s.goal.product_type.value,
        "l1_nodes": l1_nodes,
        "score": round(report.score, 1),
        "session_id": session_id,
    }
    value = _json.dumps(pat, ensure_ascii=False)
    if len(value) > _PATTERN_VALUE_MAX:  # 防御: 超长截断 L1 列表
        pat["l1_nodes"] = pat["l1_nodes"][:4]
        value = _json.dumps(pat, ensure_ascii=False)
    # P2: provider 可用时缓存向量,检索免重复计算
    embed_meta: dict[str, Any] = {"session_id": session_id, "score": report.score}
    provider = _get_embed()
    if provider is not None:
        try:
            embed_meta["embedding"] = provider.embed([value])[0]
        except Exception:  # noqa: BLE001 — 向量失败不阻断沉淀
            pass
    get_knowledge_store().put(KnowledgeEntry(
        id=uuid.uuid4().hex[:12],
        category="pattern",
        key=f"pattern:{session_id}",
        value=value,
        source="auto",
        confidence=min(0.95, 0.5 + report.score / 200),  # 分数越高置信越高
        tags=sorted(_goal_tokens(s.goal.goal))[:8],
        metadata=embed_meta,
    ))


_OUTLINE_PER_FILE_MAX = 20
_OUTLINE_TOTAL_MAX = 60


def _extract_outline(files: dict[str, str]) -> dict[str, list[str]]:
    """从产物文件提取结构骨架(markdown 标题 / py 函数类签名 / 通用首行)

    每文件截断 _OUTLINE_PER_FILE_MAX 行,总量 _OUTLINE_TOTAL_MAX 行 —
    控制知识库条目体积,只留结构信号不留内容。
    """
    outline: dict[str, list[str]] = {}
    total = 0
    for rel in sorted(files):
        if total >= _OUTLINE_TOTAL_MAX:
            break
        content = str(files[rel] or "")
        lines: list[str] = []
        if rel.endswith((".md", ".markdown")):
            lines = [ln.strip() for ln in content.splitlines() if ln.lstrip().startswith("#")]
        elif rel.endswith(".py"):
            for ln in content.splitlines():
                t = ln.strip()
                if t.startswith(("def ", "class ", "async def ")):
                    lines.append(t[:100])
        else:
            # 通用: 取非空行前若干(配置/JSON 无稳定结构,只留线索)
            lines = [ln.strip() for ln in content.splitlines() if ln.strip()][:5]
        if not lines:
            continue
        take = lines[:_OUTLINE_PER_FILE_MAX]
        if total + len(take) > _OUTLINE_TOTAL_MAX:
            take = take[: _OUTLINE_TOTAL_MAX - total]
        outline[rel] = take
        total += len(take)
    return outline


def _enrich_pattern_with_artifact(session_id: str, artifact: Any) -> None:
    """Voyager 深化: build 后把产物骨架合并进已沉淀的 pattern

    前提: /validate PASS 已创建 pattern:{session_id}。无 pattern 或无文件时静默跳过;
    失败不阻断 build 主流程。
    """
    import json as _json

    if not getattr(artifact, "files", None):
        return
    store = get_knowledge_store()
    entry = store.get(f"pattern:{session_id}")
    if entry is None:
        return
    try:
        pat = _json.loads(entry.value)
    except Exception:  # noqa: BLE001
        return
    if not isinstance(pat, dict):
        return
    outline = _extract_outline(artifact.files)
    if not outline:
        return
    pat["artifact_outline"] = outline
    value = _json.dumps(pat, ensure_ascii=False)
    if len(value) > _PATTERN_VALUE_MAX * 2:  # 骨架合并后放宽一倍,超限则丢弃骨架
        pat.pop("artifact_outline", None)
        value = _json.dumps(pat, ensure_ascii=False)
        if len(value) > _PATTERN_VALUE_MAX:
            return
    meta = dict(entry.metadata)
    provider = _get_embed()
    if provider is not None:
        try:
            meta["embedding"] = provider.embed([value])[0]  # 内容变化,向量重算
        except Exception:  # noqa: BLE001
            pass
    store.put(KnowledgeEntry(
        id=entry.id,
        category="pattern",
        key=entry.key,
        value=value,
        source="auto",
        confidence=min(0.95, entry.confidence + 0.02),  # 已验证到产物级,小幅提信
        tags=entry.tags,
        metadata=meta,
    ))


REFLECTION_SYSTEM_PROMPT = """你是 goal-studio 的创作复盘助手。一次精化尝试未通过棘轮门控被回滚,
请提炼一条供后续同类目标吸取的教训(Reflexion)。

要求: 2-3 句话;指出具体缺陷与可操作的规避方法;不写空话套话。
输出严格的 JSON(不要其他文字): {"lesson": "..."}"""


def _build_reflection_llm(
    llm: Any,
    goal_text: str,
    patch_summary: str,
    decision: str,
    old_score: float,
    new_score: float,
    issues: list[str],
) -> str | None:
    """G1 升级: LLM 生成结构化反思(失败/不可用返回 None,调用方降级到模板)"""
    if llm is None:
        return None
    user_parts = [
        f"用户目标: {goal_text[:200]}",
        f"被回滚的操作: {patch_summary}",
        f"棘轮判定: {decision} (旧分 {old_score:.1f} → 新分 {new_score:.1f})",
    ]
    if issues:
        user_parts.append("评估问题: " + "; ".join(issues[:5]))
    from goal_studio.llm.base import LLMMessage

    messages = [
        LLMMessage(role="system", content=REFLECTION_SYSTEM_PROMPT),
        LLMMessage(role="user", content="\n".join(user_parts)),
    ]
    try:
        data = llm.complete_json(messages, temperature=0.2, max_tokens=512)
    except Exception:  # noqa: BLE001 — 反思失败降级模板
        return None
    if not isinstance(data, dict):
        return None
    lesson = str(data.get("lesson", "")).strip()
    if not (5 <= len(lesson) <= 600):
        return None
    return lesson


def _dual_eval_enabled() -> bool:
    """G2: 双评估器开关(默认开启,GOAL_STUDIO_DUAL_EVAL=0 关闭)"""
    return os.getenv("GOAL_STUDIO_DUAL_EVAL", "1") != "0"


# === 辅助函数 ===

# PR-ROUND-4/A-05: IDOR 防护 — 当前单 token 共享模式未传 owner_id,多用户时需传入
def _get_session(mgr: SessionManager, session_id: str) -> Session:
    try:
        return mgr.get(session_id)
    except (KeyError, ValueError):
        # PR-ROUND-1/A-04: 非法 session_id 格式(ValueError)同样返回 404
        raise HTTPException(404, f"Session {session_id} not found") from None


def _to_goal_contract(g: GoalContractModel | GoalContract) -> GoalContract:
    return GoalContract(
        goal=g.goal,
        prerequisites=g.prerequisites,
        constraints=g.constraints,
        success_criteria=g.success_criteria,
        product_type=ProductType(g.product_type),
        metadata=g.metadata,
    )


def _to_patch(p: PatchModel) -> Patch:
    return Patch(
        op=PatchOp(p.op),
        target_node_id=p.target_node_id,
        target_node_id_2=p.target_node_id_2,
        slots=p.slots,
        new_node=p.new_node,
        position=p.position,
        source=p.source,
        rationale=p.rationale,
    )


def _patch_summary(patch: Patch) -> str:
    """生成 patch 摘要"""
    parts = [patch.op.value]
    if patch.target_node_id:
        parts.append(patch.target_node_id)
    if patch.slots:
        slot_str = ", ".join(f"{k}={v}" for k, v in list(patch.slots.items())[:3])
        parts.append(slot_str)
    return " ".join(parts)


# === 核心 API ===

@router.post("/draft", response_model=NodeGraphModel)
async def draft(
    session_id: str,
    req: DraftRequest,
    mgr: SessionManager = Depends(get_session_manager),
    registry: AdapterRegistry = Depends(get_adapter_registry),
) -> NodeGraphModel:
    """起草 DAG — 调用 Adapter.draft()"""
    s = _get_session(mgr, session_id)
    goal = _to_goal_contract(req.goal)
    adapter = registry.get_for_goal(goal)
    # G1: 检索历史反思经验注入 prompt(Reflexion: 失败教训回灌)
    # Voyager: 检索验证通过的成功结构注入 few-shot(能力复利,无灾难遗忘)
    try:
        lessons = _retrieve_lessons(goal.goal, get_knowledge_store())
        if lessons:
            goal.metadata["prior_lessons"] = lessons
        patterns = _retrieve_patterns(goal.goal, get_knowledge_store())
        if patterns:
            goal.metadata["prior_patterns"] = patterns
    except Exception:  # noqa: BLE001 — 知识库不可用不阻断起草
        pass
    # PR-ROUND-1/B-06: goal 赋值 + draft + 图更新 + 保存整体持 per-session 锁,
    # 避免与并发 /refine(已持锁) 竞争修改同一 session
    loop = asyncio.get_running_loop()

    # P1-3: 使用公共 session_lock CM(含 session_id 校验),不再访问私有 _get_session_lock
    def _draft_with_lock() -> NodeGraph:
        with mgr.session_lock(session_id):
            s.goal = goal
            graph = adapter.draft(goal)
            s.update_graph(graph)
            s.phase = SessionPhase.VISUALIZE
            mgr.save(s)
            return graph

    METRICS["llm_calls_total"] += 1
    try:
        graph = await loop.run_in_executor(None, _draft_with_lock)
    except Exception:
        METRICS["llm_errors_total"] += 1
        raise
    return NodeGraphModel(**graph.to_dict())


@router.post("/refine", response_model=RatchetResponse)
async def refine(
    session_id: str,
    req: RefineRequest,
    mgr: SessionManager = Depends(get_session_manager),
    registry: AdapterRegistry = Depends(get_adapter_registry),
    llm: BaseLLMProvider | None = Depends(get_llm_provider),
) -> RatchetResponse:
    """精化补丁 — 自动验证 + 棘轮门控

    流程:
      1. 保存快照 (refine 前的 graph + score)
      2. adapter.refine() 生成 new_graph
      3. adapter.validate(new_graph) 得到 new_score
      4. ratchet.judge(old_score, new_score) → decision
      5. KEEP: 保留 new_graph; REVERT: 恢复快照
    """
    s = _get_session(mgr, session_id)
    if not s.graph or not s.goal:
        raise HTTPException(400, "Session has no graph/goal, call /draft first")
    # R3-07: 会话状态机 phase 合法转换校验(refine 允许从 VISUALIZE/REFINE/VERIFY 进入)
    if s.phase not in (SessionPhase.VISUALIZE, SessionPhase.REFINE, SessionPhase.VERIFY):
        raise HTTPException(409, f"Cannot refine in phase {s.phase.value}")

    adapter = registry.get_for_goal(s.goal)
    patch = _to_patch(req.patch)
    patch_sum = _patch_summary(patch)

    # 1. 保存快照
    old_score = s.current_score

    # PR-M: 同步 LLM 调用 + 棘轮判定 + 状态更新整体移入线程池并持 per-session 锁,
    # 保证 refine 全流程原子性(避免并发 refine 互相覆盖快照/分数)
    # R3-08: 统一使用 asyncio.get_running_loop()
    loop = asyncio.get_running_loop()

    # P1-3: 使用公共 session_lock CM
    def _refine_with_lock() -> tuple[Any, NodeGraph, float]:
        with mgr.session_lock(session_id):
            assert s.graph is not None  # endpoint 顶部已 400 校验,此处收窄类型
            graph_before_dict = s.graph.to_dict()  # 数据飞轮: 补丁前图快照
            s.save_snapshot(patch_summary=patch_sum)
            new_graph = adapter.refine(s.graph, patch)
            report = adapter.validate(new_graph)

            # G2 双评估器: 评估器A=规则(adapter.validate),评估器B=LLM 独立评审。
            # llm 由端点 DI 注入;不可用/评审失败 → 仅规则报告(与原单评估器路径一致)。
            judge_report = None
            if llm is not None and _dual_eval_enabled():
                judge_report = LLMJudgeEvaluator(llm).evaluate(
                    new_graph, goal_text=s.goal.goal if s.goal else ""
                )
            reports = [report] if judge_report is None else [report, judge_report]

            decision, merged = s.ratchet.judge_reports(
                reports, old_score, new_graph, patch_summary=patch_sum
            )
            # 分差过大 → 第三评审仲裁(LLM tiebreaker,失败回退规则报告),
            # 取三份评审的中位数进棘轮
            if decision == RatchetDecision.ARBITRATE and judge_report is not None:
                third = None
                if llm is not None:
                    third = LLMJudgeEvaluator(llm, persona="tiebreaker").evaluate(
                        new_graph, goal_text=s.goal.goal if s.goal else ""
                    )
                third_report = third if third is not None else report
                decision, merged = s.ratchet.arbitrate(
                    new_graph,
                    lambda _g: third_report,
                    reports,
                    old_score,
                    patch_summary=patch_sum,
                )
            new_score = merged.score
            final_graph = s.apply_ratchet_decision(decision, new_graph, new_score)
            s.phase = SessionPhase.REFINE
            s.add_dialog("user", f"{patch.op.value}: {patch.target_node_id or ''} {patch.slots}")
            # R3-09: dialog role 统一为 assistant(与 WS 一致)
            s.add_dialog(
                "assistant", f"棘轮判定: {decision.value} (Δ={new_score - old_score:+.1f})"
            )
            # 方案 #2 数据飞轮: 沉淀 refine 判定样本(best-effort,不阻断主流程)
            try:
                from goal_studio_server.routes.execution import record_refine_outcome

                record_refine_outcome(
                    session_id, s, graph_before_dict, new_graph.to_dict(),
                    patch, decision.value, old_score, new_score,
                )
            except Exception:  # noqa: BLE001
                pass
            # G1: 失败反思沉淀(Reflexion) — REVERT/EARLY_STOP 时写入 KnowledgeStore
            # P1: LLM 生成教训优先,失败降级确定性模板
            if decision.value in ("revert", "early_stop"):
                try:
                    goal_text = s.goal.goal if s.goal else ""
                    reflection = _build_reflection_llm(
                        llm, goal_text, patch_sum, decision.value,
                        old_score, new_score, merged.issues,
                    ) or _build_reflection(
                        goal_text=goal_text,
                        patch_summary=patch_sum,
                        decision=decision.value,
                        old_score=old_score,
                        new_score=new_score,
                        issues=merged.issues,
                    )
                    refl_meta: dict[str, Any] = {
                        "session_id": session_id, "decision": decision.value,
                    }
                    _prov = _get_embed()
                    if _prov is not None:
                        try:
                            refl_meta["embedding"] = _prov.embed([reflection])[0]
                        except Exception:  # noqa: BLE001
                            pass
                    get_knowledge_store().put(KnowledgeEntry(
                        id=uuid.uuid4().hex[:12],
                        category="experience",
                        key=f"reflection:{session_id}:{len(s.ratchet_history)}",
                        value=reflection,
                        source="auto",
                        confidence=0.6,
                        tags=sorted(
                            _goal_tokens((s.goal.goal if s.goal else "") + " " + patch_sum)
                        )[:8],
                        metadata=refl_meta,
                    ))
                except Exception:  # noqa: BLE001 — 沉淀失败不影响 refine 主流程
                    pass
            mgr.save(s)
            return decision, final_graph, new_score

    METRICS["llm_calls_total"] += 1
    try:
        decision, final_graph, new_score = await loop.run_in_executor(
            None, _refine_with_lock
        )
    except Exception:
        METRICS["llm_errors_total"] += 1
        raise

    # 构造响应
    messages = {
        RatchetDecision.KEEP: f"保留修改 (分数 {old_score:.1f} → {new_score:.1f})",
        RatchetDecision.REVERT: f"回滚修改 (分数下降 {old_score:.1f} → {new_score:.1f})",
        RatchetDecision.EARLY_STOP: f"早停 (连续2轮 Δ<{s.ratchet.early_stop_delta}),恢复最佳版本",
        RatchetDecision.ARBITRATE: "双评审分差过大,需仲裁",
    }

    return RatchetResponse(
        decision=decision.value,
        old_score=old_score,
        new_score=new_score,
        delta=new_score - old_score,
        best_score=s.ratchet.best_score,
        current_score=s.current_score,
        reverted=decision == RatchetDecision.REVERT,
        early_stopped=decision == RatchetDecision.EARLY_STOP,
        message=messages.get(decision, ""),
        graph=NodeGraphModel(**final_graph.to_dict()),
    )


@router.post("/validate", response_model=ReportModel)
async def validate(
    session_id: str,
    mgr: SessionManager = Depends(get_session_manager),
    registry: AdapterRegistry = Depends(get_adapter_registry),
) -> ReportModel:
    """验证 — 调用 Adapter.validate() + 更新棘轮状态"""
    s = _get_session(mgr, session_id)
    if not s.graph or not s.goal:
        raise HTTPException(400, "Session has no graph/goal")
    # R3-07: 会话状态机 phase 合法转换校验(validate 允许从 VISUALIZE/REFINE/VERIFY 进入)
    if s.phase not in (SessionPhase.VISUALIZE, SessionPhase.REFINE, SessionPhase.VERIFY):
        raise HTTPException(409, f"Cannot validate in phase {s.phase.value}")
    adapter = registry.get_for_goal(s.goal)
    # PR-M: 同步 LLM 调用移入线程池,避免阻塞事件循环
    # R3-08: 统一使用 asyncio.get_running_loop()
    loop = asyncio.get_running_loop()
    METRICS["llm_calls_total"] += 1
    try:
        report = await loop.run_in_executor(None, partial(adapter.validate, s.graph))
    except Exception:
        METRICS["llm_errors_total"] += 1
        raise
    # Voyager: 验证 PASS 的结构沉淀为可复用技能(失败静默,不阻断验证)
    try:
        _store_pattern(session_id, s, report)
    except Exception:  # noqa: BLE001
        pass
    # R3-06: 状态更新纳入 per-session 锁,避免并发修改同一 session
    with mgr.session_lock(session_id):
        s.phase = SessionPhase.VERIFY
        s.current_score = report.score
        # 更新 best (如果当前分数更高)
        if report.score > s.ratchet.best_score:
            s.ratchet.best_score = report.score
            # PR-ROUND-2/C-03: 深拷贝,避免后续 s.graph 修改污染 best_graph
            s.ratchet.best_graph = NodeGraph.from_dict(s.graph.to_dict())
            s.best_score = report.score
            s.best_graph = NodeGraph.from_dict(s.graph.to_dict())
        s.ratchet_history = [r.to_dict() for r in s.ratchet.history]
        mgr.save(s)
    return ReportModel(
        passed=report.passed,
        score=report.score,
        dimensions=report.dimensions,
        issues=report.issues,
        suggestions=report.suggestions,
        evaluator=report.evaluator,
    )


@router.post("/build", response_model=ArtifactModel)
async def build(
    session_id: str,
    mgr: SessionManager = Depends(get_session_manager),
    registry: AdapterRegistry = Depends(get_adapter_registry),
) -> ArtifactModel:
    """构建产物 — 使用 best_graph (历史最佳) 而非 current graph

    保证最终产物质量是历史最高分的版本。
    """
    s = _get_session(mgr, session_id)
    if not s.graph or not s.goal:
        raise HTTPException(400, "Session has no graph/goal")
    # R3-07: 会话状态机 phase 合法转换校验(build 允许从 VERIFY/BUILD 进入)
    if s.phase not in (SessionPhase.VERIFY, SessionPhase.BUILD):
        raise HTTPException(409, f"Cannot build in phase {s.phase.value}")
    adapter = registry.get_for_goal(s.goal)
    # 优先使用 best_graph,回退到 current graph
    # PR-ROUND-2/C-04: 传深拷贝给 adapter,避免 adapter 修改污染 session 状态
    source_graph = s.ratchet.best_graph or s.graph
    build_graph = NodeGraph.from_dict(source_graph.to_dict())
    # PR-M: 同步 LLM 调用移入线程池,避免阻塞事件循环
    loop = asyncio.get_running_loop()
    METRICS["llm_calls_total"] += 1
    try:
        artifact: Artifact = await loop.run_in_executor(None, partial(adapter.build, build_graph))
    except Exception:
        METRICS["llm_errors_total"] += 1
        raise
    # PR-S3: 产物持久化 + 记录 best_artifact_id,供删除会话时级联清理
    store = get_artifact_store()
    artifact_id = await loop.run_in_executor(
        None, partial(store.save, artifact, f"session={session_id}")
    )
    # R3-06: 状态更新纳入 per-session 锁,避免并发修改同一 session
    with mgr.session_lock(session_id):
        s.phase = SessionPhase.BUILD
        s.best_artifact_id = artifact_id
        if not s.metadata.get("artifact_ids"):
            s.metadata["artifact_ids"] = []
        if artifact_id not in s.metadata["artifact_ids"]:
            s.metadata["artifact_ids"].append(artifact_id)
        mgr.save(s)
    # Voyager 深化: 产物骨架合并进已沉淀技能(best-effort,不阻断 build)
    try:
        await loop.run_in_executor(
            None, partial(_enrich_pattern_with_artifact, session_id, artifact)
        )
    except Exception:  # noqa: BLE001
        pass
    return ArtifactModel(
        product_type=ModelProductType(artifact.product_type),
        content=artifact.content,
        files=artifact.files,
        metadata=artifact.metadata,
        graph_snapshot=artifact.graph_snapshot,
        version=artifact.version,
    )


# === 棘轮门控专用端点 ===

@router.post("/ratchet/undo", response_model=UndoResponse)
async def ratchet_undo(
    session_id: str,
    mgr: SessionManager = Depends(get_session_manager),
) -> UndoResponse:
    """撤销最近一次 refine — 恢复到上一个快照

    G3: 返回剩余快照数,前端据此维持/禁用撤销按钮。
    """
    s = _get_session(mgr, session_id)
    # R3-06: 状态更新纳入 per-session 锁,避免并发修改
    with mgr.session_lock(session_id):
        restored = s.undo()
        if restored is None:
            raise HTTPException(400, "No snapshot to undo")
        s.add_dialog("system", "用户撤销最近一次修改")
        mgr.save(s)
        remaining = len(s.ratchet.snapshots)
        phase = s.phase.value
    return UndoResponse(
        graph=NodeGraphModel(**restored.to_dict()),
        snapshots_remaining=remaining,
        phase=phase,
    )


@router.get("/ratchet/history", response_model=RatchetHistoryResponse)
async def ratchet_history(
    session_id: str,
    mgr: SessionManager = Depends(get_session_manager),
) -> RatchetHistoryResponse:
    """获取棘轮门控历史"""
    s = _get_session(mgr, session_id)
    return RatchetHistoryResponse(
        history=[
            RatchetHistoryItem(**r.to_dict())
            for r in s.ratchet.history
        ],
        best_score=s.ratchet.best_score,
        current_score=s.current_score,
        snapshot_count=len(s.ratchet.snapshots),
    )


@router.post("/ratchet/restore_best", response_model=NodeGraphModel)
async def ratchet_restore_best(
    session_id: str,
    mgr: SessionManager = Depends(get_session_manager),
) -> NodeGraphModel:
    """恢复到历史最佳版本"""
    s = _get_session(mgr, session_id)
    # R3-06: 状态更新纳入 per-session 锁,避免并发修改
    with mgr.session_lock(session_id):
        restored = s.restore_best()
        if restored is None:
            raise HTTPException(400, "No best graph to restore")
        s.add_dialog("system", f"恢复到最佳版本 (分数 {s.ratchet.best_score:.1f})")
        mgr.save(s)
    return NodeGraphModel(**restored.to_dict())


# === G4: 产物导出与安装(Web 最后一公里) ===

def _load_session_artifact(session_id: str, mgr: SessionManager) -> tuple[Session, Any]:
    """加载会话最佳产物,无产物时抛 400"""
    s = _get_session(mgr, session_id)
    if not s.best_artifact_id:
        raise HTTPException(400, "Session has no artifact, call /build first")
    store = get_artifact_store()
    artifact = store.load(s.best_artifact_id)
    if artifact is None:
        raise HTTPException(404, f"Artifact {s.best_artifact_id} not found")
    return s, artifact


@router.post("/export", response_model=ExportResponse)
async def export_artifact(
    session_id: str,
    req: ExportRequest,
    mgr: SessionManager = Depends(get_session_manager),
    registry: AdapterRegistry = Depends(get_adapter_registry),
) -> ExportResponse:
    """导出产物到文件系统(G4: Web build 后的最后一公里)

    target 为空时默认导出到产物库同级的 goal-studio-exports/{session_id}/。
    导出成功后会话进入 DONE 阶段(G5)。
    """
    s, artifact = _load_session_artifact(session_id, mgr)
    if not s.goal:
        raise HTTPException(400, "Session has no goal")
    adapter = registry.get_for_goal(s.goal)

    if req.target:
        target = req.target
    else:
        store_root = Path(str(getattr(get_artifact_store(), "root", Path.cwd() / "artifacts")))
        export_dir = store_root.parent / "goal-studio-exports" / session_id
        target = export_dir.resolve().as_uri()

    loop = asyncio.get_running_loop()
    location = await loop.run_in_executor(None, partial(adapter.export, artifact, target))

    # G5: 交付完成 → DONE
    with mgr.session_lock(session_id):
        s.phase = SessionPhase.DONE
        s.add_dialog("system", f"产物已导出: {location}")
        mgr.save(s)
    return ExportResponse(location=location, phase=s.phase.value)


@router.post("/install", response_model=InstallResponse)
async def install_artifact(
    session_id: str,
    req: InstallRequest,
    mgr: SessionManager = Depends(get_session_manager),
    registry: AdapterRegistry = Depends(get_adapter_registry),
) -> InstallResponse:
    """安装产物到运行环境(G4: file / maxfun 两种目标)

    maxfun 目标通过 plugins/maxfun-adapter 动态加载(源码部署可用;
    wheel 安装无 plugins 目录时返回 400 及安装指引)。
    安装成功后会话进入 DONE 阶段(G5)。
    """
    s, artifact = _load_session_artifact(session_id, mgr)

    if req.target == "maxfun":
        def _install_maxfun() -> str:
            import os as _os

            plugin_env = _os.getenv("GOAL_STUDIO_PLUGINS_DIR")
            candidates = []
            if plugin_env:
                candidates.append(Path(plugin_env) / "maxfun-adapter")
            candidates.append(
                Path(__file__).resolve().parents[5] / "plugins" / "maxfun-adapter"
            )
            for plugin_path in candidates:
                if (plugin_path / "maxfun_bridge.py").exists():
                    if str(plugin_path) not in sys.path:
                        sys.path.insert(0, str(plugin_path))
                    try:
                        from maxfun_bridge import detect_maxfun_dir, install_to_maxfun
                    except ImportError:
                        raise HTTPException(
                            400,
                            "maxfun-adapter 加载失败,请检查 plugins/maxfun-adapter 安装",
                        ) from None

                    mf_dir = req.skill_dir or detect_maxfun_dir() or str(
                        Path.home() / ".maxfun" / "skills"
                    )
                    Path(mf_dir).mkdir(parents=True, exist_ok=True)
                    skill_name = req.name or artifact.metadata.get("skill_name", session_id)
                    return str(install_to_maxfun(artifact.files, skill_name, mf_dir))
            raise HTTPException(
                400,
                "maxfun-adapter 不可用: 源码部署请设置 GOAL_STUDIO_PLUGINS_DIR "
                "指向仓库 plugins/ 目录,或改用 target=file",
            )

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _install_maxfun)
    else:
        if not s.goal:
            raise HTTPException(400, "Session has no goal")
        adapter = registry.get_for_goal(s.goal)
        skill_dir = req.skill_dir or "./output"
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, partial(adapter.install, artifact, {"skill_dir": skill_dir})
        )

    # G5: 安装交付 → DONE
    with mgr.session_lock(session_id):
        s.phase = SessionPhase.DONE
        s.add_dialog("system", f"产物已安装({req.target}): {result}")
        mgr.save(s)
    return InstallResponse(result=result, phase=s.phase.value)
