"""MockLLMProvider: 本地 Mock,无需 API key

根据消息内容返回预设或生成的响应,用于:
  - 无 API key 环境的开发
  - 单元测试
  - 离线演示

通过关键词路由到不同的 mock 响应生成器:
  - "起草" / "draft" / "DAG" → mock_draft_dag
  - "解析" / "intent" / "parse" → mock_parse_intent
  - "精化" / "refine" / "patch" → mock_refine
  - 其他 → mock_generic
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from goal_studio.llm.base import BaseLLMProvider, LLMMessage, LLMResponse


class MockLLMProvider(BaseLLMProvider):
    """本地 Mock LLM Provider"""

    def __init__(self, model: str = "mock-llm") -> None:
        self._model = model

    @property
    def name(self) -> str:
        return "mock"

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        # 路由优先级: system prompt > user prompt
        # system prompt 更可靠地标识任务类型,避免 user prompt 中的画布摘要干扰
        system_text = " ".join(
            m.content for m in messages if m.role == "system"
        ).lower()
        user_text = "\n".join(
            m.content for m in messages if m.role == "user"
        ).lower()
        combined = system_text + " " + user_text

        # 优先按 system prompt 路由 (避免画布摘要中的关键词干扰)
        # 顺序很重要: build 必须在 draft 之前 (build prompt 也含 DAG/SKILL)
        if "意图解析" in system_text or "intent_parser" in system_text or "意图解析器" in system_text:
            content = self._mock_parse_intent(messages)
        elif "代码生成助手" in system_text or ("code" in system_text and "files" in system_text):
            content = self._mock_build_code(messages)
        elif "文档生成助手" in system_text or ("doc" in system_text and "markdown" in system_text):
            content = self._mock_build_doc(messages)
        elif "工作流生成助手" in system_text or ("workflow" in system_text and "steps" in system_text):
            content = self._mock_build_workflow(messages)
        elif "技能生成助手" in system_text or "skill.md" in system_text:
            content = self._mock_build_skill(messages)
        elif "代码项目架构助手" in system_text or ("代码项目" in system_text and "dag" in system_text):
            content = self._mock_draft_code_dag(messages)
        elif "文档创作助手" in system_text or ("文档" in system_text and "dag" in system_text and "创作" in system_text):
            content = self._mock_draft_doc_dag(messages)
        elif "工作流设计助手" in system_text or ("工作流" in system_text and "dag" in system_text and "设计" in system_text):
            content = self._mock_draft_workflow_dag(messages)
        elif "技能创作助手" in system_text or ("skill" in system_text and "dag" in system_text and "生成" not in system_text):
            content = self._mock_draft_dag(messages)
        elif "节点精化助手" in system_text:
            content = self._mock_refine(messages)
        elif "仲裁评审官" in system_text:
            content = self._mock_judge(messages, persona="tiebreaker")
        elif "独立评审官" in system_text:
            content = self._mock_judge(messages, persona="reviewer")
        elif "复盘" in system_text:
            content = self._mock_reflection(messages)
        elif any(kw in combined for kw in ["解析", "intent", "parse", "意图", "指令"]):
            content = self._mock_parse_intent(messages)
        elif any(kw in combined for kw in ["起草", "draft", "dag", "三层", "规划"]):
            content = self._mock_draft_dag(messages)
        elif any(kw in combined for kw in ["精化", "refine", "patch", "补丁"]):
            content = self._mock_refine(messages)
        elif any(kw in combined for kw in ["验证", "validate", "评估", "score"]):
            content = self._mock_validate(messages)
        else:
            content = self._mock_generic(messages)

        return LLMResponse(
            content=content,
            usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
            model=self._model,
            finish_reason="stop",
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        resp = self.complete(messages, temperature=temperature, max_tokens=max_tokens, **kwargs)
        # 模拟流式: 按 chunk 返回
        chunk_size = 10
        for i in range(0, len(resp.content), chunk_size):
            yield resp.content[i:i + chunk_size]

    def _mock_draft_dag(self, messages: list[LLMMessage]) -> str:
        """Mock 起草三层 DAG 的 JSON 响应"""
        # 提取目标
        goal = ""
        for m in messages:
            if m.role == "user" and "目标" in m.content:
                # 尝试从 prompt 提取目标
                match = re.search(r"目标[:：]\s*(.+?)(?:\n|$)", m.content)
                if match:
                    goal = match.group(1).strip()
                    break
        if not goal:
            goal = "用户目标"

        # 根据 goal 生成三层 DAG: L1 任务 + L2 Agent + L3 精化
        dag = {
            "nodes": [
                # L1 任务规划层
                {
                    "id": "l1-req",
                    "layer": 1,
                    "title": "需求分析",
                    "description": f"解析目标「{goal}」,提取四要素",
                    "node_type": "task",
                    "parent_id": None,
                    "slots": {"goal": goal},
                },
                {
                    "id": "l1-design",
                    "layer": 1,
                    "title": "方案设计",
                    "description": "设计技能结构与工作流",
                    "node_type": "task",
                    "parent_id": None,
                },
                {
                    "id": "l1-author",
                    "layer": 1,
                    "title": "技能体编写",
                    "description": "起草 SKILL.md 主体与 references",
                    "node_type": "task",
                    "parent_id": None,
                },
                {
                    "id": "l1-test",
                    "layer": 1,
                    "title": "测试验证",
                    "description": "设计测试 prompt,9 维评估",
                    "node_type": "task",
                    "parent_id": None,
                },
                {
                    "id": "l1-build",
                    "layer": 1,
                    "title": "固化产物",
                    "description": "生成最终技能包并导出",
                    "node_type": "task",
                    "parent_id": None,
                },
                # L2 Agent 分配层
                {
                    "id": "l2-req-analyst",
                    "layer": 2,
                    "title": "需求分析师",
                    "description": "解析用户目标,提取四要素并输出 GoalContract",
                    "node_type": "agent",
                    "parent_id": "l1-req",
                },
                {
                    "id": "l2-design-arch",
                    "layer": 2,
                    "title": "架构设计师",
                    "description": "设计技能的章节结构、工作流和节点分工",
                    "node_type": "agent",
                    "parent_id": "l1-design",
                },
                {
                    "id": "l2-author-writer",
                    "layer": 2,
                    "title": "技能撰写者",
                    "description": "起草 SKILL.md 的命令入口、工作流、规则、失败模式",
                    "node_type": "agent",
                    "parent_id": "l1-author",
                },
                {
                    "id": "l2-test-runner",
                    "layer": 2,
                    "title": "测试执行器",
                    "description": "运行 9 维评估并收集结果,生成 Report",
                    "node_type": "agent",
                    "parent_id": "l1-test",
                },
                {
                    "id": "l2-build-packer",
                    "layer": 2,
                    "title": "产物打包器",
                    "description": "生成最终技能包,导出到 skill 目录",
                    "node_type": "agent",
                    "parent_id": "l1-build",
                },
                # L3 节点精化层 (关键 L2 下的精化节点,带 slots)
                {
                    "id": "l3-req-extract",
                    "layer": 3,
                    "title": "四要素提取器",
                    "description": "从目标文本提取 goal/prerequisites/constraints/success_criteria",
                    "node_type": "tool",
                    "parent_id": "l2-req-analyst",
                    "slots": {
                        "prompt_template": "从目标中提取 goal/prerequisites/constraints/success_criteria",
                        "output_format": "json",
                    },
                },
                {
                    "id": "l3-author-skeleton",
                    "layer": 3,
                    "title": "章节骨架生成",
                    "description": "生成 SKILL.md 的章节结构骨架",
                    "node_type": "tool",
                    "parent_id": "l2-author-writer",
                    "slots": {
                        "sections": ["命令入口", "工作流", "关键规则", "失败模式"],
                        "template": "skill-v1",
                    },
                },
                {
                    "id": "l3-test-9dim",
                    "layer": 3,
                    "title": "9维评估器",
                    "description": "执行 9 维度质量评估,输出分数和建议",
                    "node_type": "tool",
                    "parent_id": "l2-test-runner",
                    "slots": {
                        "dimensions": ["清晰度", "可执行性", "完整性", "失败覆盖", "资源整合", "架构", "性能", "反例", "检查点"],
                        "pass_threshold": 70,
                    },
                },
            ],
            "edges": [
                {"source": "l1-req", "target": "l1-design"},
                {"source": "l1-design", "target": "l1-author"},
                {"source": "l1-author", "target": "l1-test"},
                {"source": "l1-test", "target": "l1-build"},
            ],
            "root_id": "l1-req",
        }
        return json.dumps(dag, ensure_ascii=False, indent=2)

    def _mock_parse_intent(self, messages: list[LLMMessage]) -> str:
        """Mock 意图解析的 JSON 响应"""
        user_text = ""
        for m in messages:
            if m.role == "user":
                user_text = m.content
                break

        # 简单关键词匹配
        if any(kw in user_text for kw in ["删除", "删掉", "remove", "delete"]):
            intent = "delete"
        elif any(kw in user_text for kw in ["增加", "添加", "新建", "add", "create"]):
            intent = "create"
        elif any(kw in user_text for kw in ["连接", "接上", "connect"]):
            intent = "connect"
        elif any(kw in user_text for kw in ["查看", "查询", "query", "show"]):
            intent = "query"
        else:
            intent = "modify"

        # 提取目标节点引用
        ref = "pronoun"
        ref_value = "这个"
        # 模式 1: "把 l1-xxx 改成" / "把 l1-xxx 加" / "删除 l1-xxx"
        id_match = re.search(r"(?:把|将|删除|删掉|移除)\s+(l\d+-[a-zA-Z0-9_-]+)", user_text)
        if id_match:
            ref = "id"
            ref_value = id_match.group(1)
        elif re.search(r"节点[:：]\s*\S+", user_text):
            match = re.search(r"节点[:：]\s*(\S+)", user_text)
            if match:
                ref = "id"
                ref_value = match.group(1)
        elif re.search(r"(.*?)节点", user_text):
            name_match = re.search(r"把(.+?)节点", user_text)
            if name_match:
                ref = "name"
                ref_value = name_match.group(1)
        else:
            # 模式 2: 直接出现 l1-xxx / l2-xxx / l3-xxx
            id_match2 = re.search(r"(l[123]-[a-zA-Z0-9_-]+)", user_text)
            if id_match2:
                ref = "id"
                ref_value = id_match2.group(1)

        # 提取槽位
        slots: dict[str, Any] = {}

        # connect 意图: 提取第二个节点作为 target_node_id
        if intent == "connect":
            connect_match = re.search(
                r"(?:连接|连|接上|connect)\s*(l\d+-[\w-]+)\s*(?:和|到|与|to|→|->)\s*(l\d+-[\w-]+)",
                user_text,
            )
            if connect_match:
                # target_ref 已指向第一个节点,第二个放入 slots
                slots["target_node_id"] = connect_match.group(2)
                ref = "id"
                ref_value = connect_match.group(1)

        if "异步" in user_text:
            slots["execution_mode"] = "async"
        if "重试" in user_text:
            match = re.search(r"重试.*?(\d+).*?次", user_text)
            if match:
                slots["retry_count"] = int(match.group(1))
            else:
                slots["retry_enabled"] = True
        if "超时" in user_text:
            match = re.search(r"超时.*?(\d+)", user_text)
            if match:
                slots["timeout"] = int(match.group(1))

        # 提取描述(整个用户输入作为 description)
        desc_match = re.search(r"[:：]\s*(.+)$", user_text)
        if desc_match and not slots:
            slots["description"] = desc_match.group(1).strip()

        result = {
            "intent": intent,
            "target_ref": {
                "type": ref,
                "value": ref_value,
                "confidence": 0.85,
            },
            "slots": slots,
            "ambiguity_flag": False,
            "clarification_options": [],
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    def _mock_refine(self, messages: list[LLMMessage]) -> str:
        """Mock 精化响应"""
        return json.dumps({
            "patch": {
                "op": "update_node",
                "target_node_id": None,
                "slots": {"description": "refined by mock"},
            },
            "rationale": "Mock refine",
        }, ensure_ascii=False, indent=2)

    def _mock_judge(self, messages: list[LLMMessage], persona: str = "reviewer") -> str:
        """Mock 独立评审响应(G2 双评估器;tiebreaker 仲裁分略偏中性)"""
        score = 84.0 if persona == "tiebreaker" else 82.0
        return json.dumps({
            "score": score,
            "dimensions": {
                "completeness": 82,
                "structure": 84,
                "executability": 80,
                "alignment": 82,
            },
            "issues": [],
            "suggestions": ["建议为关键 L3 节点补充可执行参数"],
        }, ensure_ascii=False)

    def _mock_reflection(self, messages: list[LLMMessage]) -> str:
        """Mock 复盘反思响应(G1 LLM 反思升级)"""
        return json.dumps({
            "lesson": "本次修改未通过棘轮门控:应先核对评估问题清单逐项修复,"
                      "再提交精化,避免同类目标重复此类缺陷。"
        }, ensure_ascii=False)

    def _mock_validate(self, messages: list[LLMMessage]) -> str:
        """Mock 验证响应"""
        return json.dumps({
            "passed": True,
            "score": 78.5,
            "dimensions": {
                "workflow_clarity": 85,
                "executability": 80,
                "completeness": 75,
                "failure_coverage": 70,
                "resource_integration": 75,
                "architecture": 80,
                "actual_performance": 78,
                "anti_patterns": 72,
                "checkpoint_design": 80,
            },
            "issues": [],
            "suggestions": ["建议补充失败模式编码"],
        }, ensure_ascii=False, indent=2)

    def _mock_draft_code_dag(self, messages: list[LLMMessage]) -> str:
        """Mock 代码项目三层 DAG"""
        goal = self._extract_goal(messages)
        dag = {
            "nodes": [
                {"id": "l1-req", "layer": 1, "title": "需求分析", "description": f"解析目标「{goal}」,提取功能需求", "node_type": "task", "parent_id": None},
                {"id": "l1-arch", "layer": 1, "title": "架构设计", "description": "设计项目结构和技术栈", "node_type": "task", "parent_id": None},
                {"id": "l1-impl", "layer": 1, "title": "核心实现", "description": "实现核心业务逻辑", "node_type": "task", "parent_id": None},
                {"id": "l1-test", "layer": 1, "title": "测试编写", "description": "编写单元测试和集成测试", "node_type": "task", "parent_id": None},
                {"id": "l1-deploy", "layer": 1, "title": "部署配置", "description": "配置构建和部署环境", "node_type": "task", "parent_id": None},
                {"id": "l2-req-analyst", "layer": 2, "title": "需求分析师", "description": "提取用户故事和验收标准", "node_type": "agent", "parent_id": "l1-req"},
                {"id": "l2-arch-architect", "layer": 2, "title": "架构师", "description": "设计目录结构和技术栈", "node_type": "agent", "parent_id": "l1-arch"},
                {"id": "l2-impl-dev", "layer": 2, "title": "开发工程师", "description": "实现核心模块代码", "node_type": "agent", "parent_id": "l1-impl"},
                {"id": "l2-test-qa", "layer": 2, "title": "测试工程师", "description": "编写测试用例", "node_type": "agent", "parent_id": "l1-test"},
                {"id": "l2-deploy-devops", "layer": 2, "title": "DevOps工程师", "description": "配置CI/CD和Docker", "node_type": "agent", "parent_id": "l1-deploy"},
                {"id": "l3-arch-stack", "layer": 3, "title": "技术栈选型器", "description": "选择语言和框架", "node_type": "tool", "parent_id": "l2-arch-architect", "slots": {"language": "python", "framework": "fastapi", "db": "sqlite"}},
                {"id": "l3-impl-skeleton", "layer": 3, "title": "代码骨架生成", "description": "生成项目骨架", "node_type": "tool", "parent_id": "l2-impl-dev", "slots": {"entry": "src/main.py", "modules": ["api", "core", "models"]}},
                {"id": "l3-test-framework", "layer": 3, "title": "测试框架配置", "description": "配置pytest", "node_type": "tool", "parent_id": "l2-test-qa", "slots": {"framework": "pytest", "coverage_target": 80}},
            ],
            "edges": [
                {"source": "l1-req", "target": "l1-arch"},
                {"source": "l1-arch", "target": "l1-impl"},
                {"source": "l1-impl", "target": "l1-test"},
                {"source": "l1-test", "target": "l1-deploy"},
            ],
            "root_id": "l1-req",
        }
        return json.dumps(dag, ensure_ascii=False, indent=2)

    def _mock_draft_doc_dag(self, messages: list[LLMMessage]) -> str:
        """Mock 文档三层 DAG"""
        goal = self._extract_goal(messages)
        dag = {
            "nodes": [
                {"id": "l1-research", "layer": 1, "title": "主题调研", "description": f"调研「{goal}」,收集资料", "node_type": "task", "parent_id": None},
                {"id": "l1-outline", "layer": 1, "title": "大纲设计", "description": "设计文档章节结构", "node_type": "task", "parent_id": None},
                {"id": "l1-draft", "layer": 1, "title": "内容起草", "description": "按大纲撰写各章节", "node_type": "task", "parent_id": None},
                {"id": "l1-review", "layer": 1, "title": "审校修订", "description": "审校内容质量", "node_type": "task", "parent_id": None},
                {"id": "l1-finalize", "layer": 1, "title": "定稿导出", "description": "格式化并导出", "node_type": "task", "parent_id": None},
                {"id": "l2-research-analyst", "layer": 2, "title": "研究员", "description": "收集资料和参考文献", "node_type": "agent", "parent_id": "l1-research"},
                {"id": "l2-outline-designer", "layer": 2, "title": "大纲设计师", "description": "设计章节层级", "node_type": "agent", "parent_id": "l1-outline"},
                {"id": "l2-draft-writer", "layer": 2, "title": "撰稿人", "description": "撰写各章节正文", "node_type": "agent", "parent_id": "l1-draft"},
                {"id": "l2-review-editor", "layer": 2, "title": "审校编辑", "description": "校对事实和语言", "node_type": "agent", "parent_id": "l1-review"},
                {"id": "l2-finalize-formatter", "layer": 2, "title": "排版师", "description": "格式化和导出", "node_type": "agent", "parent_id": "l1-finalize"},
                {"id": "l3-research-sources", "layer": 3, "title": "资料源配置", "description": "配置资料来源", "node_type": "tool", "parent_id": "l2-research-analyst", "slots": {"sources": ["官方文档", "论文"], "min_refs": 5}},
                {"id": "l3-outline-structure", "layer": 3, "title": "章节结构生成", "description": "生成章节骨架", "node_type": "tool", "parent_id": "l2-outline-designer", "slots": {"sections": ["引言", "背景", "主体", "结论"], "depth": 3}},
                {"id": "l3-review-checklist", "layer": 3, "title": "审校清单", "description": "审校检查项", "node_type": "decision", "parent_id": "l2-review-editor", "slots": {"checklist": ["事实准确", "逻辑连贯", "语言通顺"]}},
            ],
            "edges": [
                {"source": "l1-research", "target": "l1-outline"},
                {"source": "l1-outline", "target": "l1-draft"},
                {"source": "l1-draft", "target": "l1-review"},
                {"source": "l1-review", "target": "l1-finalize"},
            ],
            "root_id": "l1-research",
        }
        return json.dumps(dag, ensure_ascii=False, indent=2)

    def _mock_draft_workflow_dag(self, messages: list[LLMMessage]) -> str:
        """Mock 工作流三层 DAG"""
        goal = self._extract_goal(messages)
        dag = {
            "nodes": [
                {"id": "l1-trigger", "layer": 1, "title": "触发分析", "description": f"分析「{goal}」的触发条件", "node_type": "task", "parent_id": None},
                {"id": "l1-orchestrate", "layer": 1, "title": "步骤编排", "description": "编排工作流步骤", "node_type": "task", "parent_id": None},
                {"id": "l1-branch", "layer": 1, "title": "条件分支", "description": "设计分支逻辑", "node_type": "task", "parent_id": None},
                {"id": "l1-error", "layer": 1, "title": "异常处理", "description": "设计重试和降级", "node_type": "task", "parent_id": None},
                {"id": "l1-deploy", "layer": 1, "title": "部署执行", "description": "配置执行环境", "node_type": "task", "parent_id": None},
                {"id": "l2-trigger-analyst", "layer": 2, "title": "触发分析师", "description": "分析触发类型", "node_type": "agent", "parent_id": "l1-trigger"},
                {"id": "l2-orchestrate-designer", "layer": 2, "title": "流程设计师", "description": "设计步骤序列", "node_type": "agent", "parent_id": "l1-orchestrate"},
                {"id": "l2-branch-designer", "layer": 2, "title": "分支设计师", "description": "设计条件判断", "node_type": "agent", "parent_id": "l1-branch"},
                {"id": "l2-error-engineer", "layer": 2, "title": "异常工程师", "description": "设计重试策略", "node_type": "agent", "parent_id": "l1-error"},
                {"id": "l2-deploy-devops", "layer": 2, "title": "部署工程师", "description": "配置监控", "node_type": "agent", "parent_id": "l1-deploy"},
                {"id": "l3-trigger-config", "layer": 3, "title": "触发器配置", "description": "配置webhook触发", "node_type": "tool", "parent_id": "l2-trigger-analyst", "slots": {"type": "webhook", "endpoint": "/api/trigger", "method": "POST"}},
                {"id": "l3-orchestrate-steps", "layer": 3, "title": "步骤定义生成", "description": "生成步骤定义", "node_type": "tool", "parent_id": "l2-orchestrate-designer", "slots": {"step_count": 5, "parallel": False, "timeout_per_step": 60}},
                {"id": "l3-error-retry", "layer": 3, "title": "重试策略配置", "description": "配置重试策略", "node_type": "decision", "parent_id": "l2-error-engineer", "slots": {"max_retries": 3, "backoff": "exponential", "base_delay": 5}},
            ],
            "edges": [
                {"source": "l1-trigger", "target": "l1-orchestrate"},
                {"source": "l1-orchestrate", "target": "l1-branch"},
                # P2 条件边: 校验通过直接部署,失败走异常处理返工
                {"source": "l1-branch", "target": "l1-deploy",
                 "edge_type": "condition", "condition": "校验通过 == true"},
                {"source": "l1-branch", "target": "l1-error",
                 "edge_type": "condition", "condition": "校验通过 == false"},
                {"source": "l1-error", "target": "l1-deploy"},
            ],
            "root_id": "l1-trigger",
        }
        return json.dumps(dag, ensure_ascii=False, indent=2)

    def _mock_build_code(self, messages: list[LLMMessage]) -> str:
        """Mock 代码项目骨架生成"""
        return json.dumps({
            "files": {
                "README.md": "# Generated Project\n\nAuto-generated by goal-studio.\n\n## 模块\n- **需求分析**: 解析目标\n- **架构设计**: 技术栈选型\n- **核心实现**: 业务逻辑\n- **测试编写**: 单元测试\n- **部署配置**: Docker\n\n技术栈: python / fastapi",
                "src/main.py": '"""项目主入口"""\n\n\ndef main() -> None:\n    """主函数"""\n    print("Hello from goal-studio")\n\n\nif __name__ == "__main__":\n    main()\n',
                "tests/test_main.py": '"""测试文件"""\n\n\ndef test_main() -> None:\n    pass\n',
                "requirements.txt": "# fastapi\n# pytest\n",
            },
            "entry_point": "src/main.py",
            "dependencies": ["fastapi", "pytest"],
        }, ensure_ascii=False, indent=2)

    def _mock_build_doc(self, messages: list[LLMMessage]) -> str:
        """Mock 文档生成"""
        return """# 生成文档

Auto-generated by goal-studio.

## 引言
本文档由 goal-studio 自动生成,基于用户目标和约束条件。

## 背景
根据调研结果,梳理项目背景和上下文。

## 主体
### 需求分析
解析用户目标,提取关键需求。

### 方案设计
设计整体方案和技术路线。

### 实现路径
按步骤实现核心功能。

## 结论
通过本工作流,实现了用户目标的完整覆盖。

## 参考文献
- [1] 官方文档
- [2] 学术论文
"""

    def _mock_build_workflow(self, messages: list[LLMMessage]) -> str:
        """Mock 工作流定义生成"""
        return json.dumps({
            "name": "generated-workflow",
            "version": "1.0.0",
            "description": "Auto-generated by goal-studio",
            "trigger": {"type": "webhook", "config": {"endpoint": "/api/trigger", "method": "POST"}},
            "steps": [
                {"id": "step-1", "name": "触发分析", "action": "分析触发条件", "inputs": {}, "outputs": {}, "next": "step-2", "on_failure": "retry"},
                {"id": "step-2", "name": "步骤编排", "action": "编排步骤", "inputs": {}, "outputs": {}, "next": "step-3", "on_failure": "retry"},
                # P2 条件分支示例: when/default 路由
                {"id": "step-3", "name": "条件分支", "action": "判断分支", "inputs": {}, "outputs": {},
                 "next_branches": [
                     {"when": "校验通过 == true", "next": "step-5"},
                     {"default": "step-4"},
                 ], "on_failure": "skip"},
                {"id": "step-4", "name": "异常处理", "action": "重试或降级", "inputs": {}, "outputs": {}, "next": "step-5", "on_failure": "abort"},
                {"id": "step-5", "name": "部署执行", "action": "执行部署", "inputs": {}, "outputs": {}, "next": None, "on_failure": "abort"},
            ],
            "error_handling": {
                "retry_policy": {"max_retries": 3, "backoff": "exponential"},
                "timeout": 300,
                "fallback": "通知管理员",
            },
        }, ensure_ascii=False, indent=2)

    def _mock_build_skill(self, messages: list[LLMMessage]) -> str:
        """Mock SKILL.md 生成"""
        return """---
name: generated-skill
version: 0.1.0
description: Auto-generated by goal-studio (mock)
---
# generated-skill — 技能

## §1 命令入口
| 命令 | 说明 |
|------|------|
| /run | 执行技能 |

## §2 工作流
按 DAG 节点顺序执行。

## §3 关键规则
- 遵循节点 slots 配置
- 失败时重试

## §4 失败模式
- LLM 调用失败: 降级到模板
- JSON 解析失败: 返回错误
"""

    def _extract_goal(self, messages: list[LLMMessage]) -> str:
        """从 messages 提取目标文本"""
        for m in messages:
            if m.role == "user" and "目标" in m.content:
                match = re.search(r"目标[:：]\s*(.+?)(?:\n|$)", m.content)
                if match:
                    return match.group(1).strip()
        return "用户目标"

    def _mock_generic(self, messages: list[LLMMessage]) -> str:
        """通用 Mock 响应"""
        user_msg = next((m.content for m in messages if m.role == "user"), "")
        return f"[Mock LLM] 收到: {user_msg[:100]}"
