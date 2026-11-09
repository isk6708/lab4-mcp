"""The agent loop: what makes this assistant "agentic".

A plain chatbot answers in one LLM call. An agent works in a loop (the ReAct
pattern: reason -> act -> observe -> repeat):

    1. the LLM looks at the goal and the history and either answers, or
    2. asks for one or more tool calls (act),
    3. the host runs them through MCP and feeds the results back (observe),
    4. back to 1, until it answers or hits the step budget.

Guardrails kept on the host side, not left to the model:
    - a step budget (max_steps), after which the model must answer without tools
    - human approval for tools that change data (APPROVAL_TOOLS)
    - bad tool arguments are returned to the model as an error so it can correct itself

This module has no web or MCP imports so it can be unit-tested with fakes.
"""
import json
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, List, Optional

SYSTEM_PROMPT = """You are the Lab 4 Platform Assistant for a product catalogue. Products can have PDF specification sheets whose text is stored as page-tagged chunks.

Rules:
- Use the tools to get facts. Never invent products, ids, prices or specification details.
- If you need an id, call list_products or get_product first.
- For questions about specifications, call search_product_specs with one or two short keywords (for example "warranty"), and read more with get_attachment_chunks if needed. Cite sources as (filename, page N).
- Tools that change data need the user's approval; the system asks them for you. If an action is rejected, do not retry it; say it was not done.
- When you have what you need, answer briefly in plain language. Use Markdown lists or tables when they help."""

CHAT_ONLY_PROMPT = """You are the Lab 4 Platform Assistant. Tools are switched off in this mode, so you cannot see the product catalogue or its specification sheets. Answer from general knowledge and say so when a question needs catalogue data."""

ToolCaller = Callable[[str, dict], Awaitable[str]]


@dataclass
class Conversation:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    messages: List[dict] = field(default_factory=list)  # OpenAI chat format, without the system prompt
    pending: List[dict] = field(default_factory=list)   # tool calls requested by the model, not yet run
    steps: int = 0                                      # LLM calls used in the current turn

    def snapshot(self) -> tuple:
        return (json.loads(json.dumps(self.messages)), json.loads(json.dumps(self.pending)), self.steps)

    def restore(self, snap: tuple) -> None:
        self.messages, self.pending, self.steps = snap

    def abandon_pending(self, reason: str) -> None:
        for call in self.pending:
            self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": reason})
        self.pending = []
        self.steps = 0


class Agent:
    def __init__(self, llm, model: str, call_tool: Optional[ToolCaller], tools: List[dict],
                 max_steps: int = 6, approval_tools: Iterable[str] = (), use_tools: bool = True,
                 temperature: Optional[float] = None, supports_tool_choice: bool = True):
        self.llm = llm
        self.model = model
        self.call_tool = call_tool
        self.tools = tools or []
        self.max_steps = max(1, max_steps)
        self.approval_tools = set(approval_tools)
        self.use_tools = use_tools and bool(self.tools)
        self.temperature = temperature
        self.supports_tool_choice = supports_tool_choice

    # -- LLM ---------------------------------------------------------------
    async def _complete(self, conv: Conversation, allow_tools: bool):
        system = SYSTEM_PROMPT if self.use_tools else CHAT_ONLY_PROMPT
        kwargs = {"model": self.model, "messages": [{"role": "system", "content": system}] + conv.messages}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if allow_tools and self.use_tools:
            kwargs["tools"] = self.tools
            if self.supports_tool_choice:
                kwargs["tool_choice"] = "auto"
        elif self.tools and self.supports_tool_choice:
            # keep tool definitions (history may contain tool calls) but forbid new calls
            kwargs["tools"] = self.tools
            kwargs["tool_choice"] = "none"
        resp = await self.llm.chat.completions.create(**kwargs)
        return resp.choices[0].message

    @staticmethod
    def _assistant_entry(msg) -> dict:
        entry = {"role": "assistant", "content": msg.content or ""}
        calls = []
        for tc in (msg.tool_calls or []):
            args = tc.function.arguments
            if not isinstance(args, str):
                args = json.dumps(args or {})
            calls.append({"id": tc.id or f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                          "function": {"name": tc.function.name, "arguments": args or "{}"}})
        if calls:
            entry["tool_calls"] = calls
        return entry

    # -- tools ---------------------------------------------------------------
    async def _drain(self, conv: Conversation, decision: Optional[str], trace: list) -> Optional[dict]:
        """Run queued tool calls. Returns a 'needs_approval' result if one must wait for the user."""
        while conv.pending:
            call = conv.pending[0]
            name = call["function"]["name"]
            raw = call["function"]["arguments"]
            try:
                args = json.loads(raw) if raw else {}
                if not isinstance(args, dict):
                    raise ValueError("arguments must be a JSON object")
            except ValueError:
                args = None
            gated = name in self.approval_tools

            if args is None:
                result = (f"ERROR: the arguments for {name} were not a valid JSON object: {raw!r}. "
                          "Call the tool again with valid JSON.")
                trace.append({"type": "error", "tool": name, "text": result})
            elif gated and decision is None:
                trace.append({"type": "tool_call", "tool": name, "arguments": args, "needs_approval": True})
                return {"status": "needs_approval", "approval": {"tool": name, "arguments": args, "call_id": call["id"]}}
            elif gated and decision == "reject":
                decision = None
                trace.append({"type": "approval", "tool": name, "decision": "rejected"})
                result = "The user REJECTED this action, so it was not run. Do not retry it."
            else:
                if gated:
                    decision = None
                    trace.append({"type": "approval", "tool": name, "decision": "approved"})
                else:
                    trace.append({"type": "tool_call", "tool": name, "arguments": args, "needs_approval": False})
                try:
                    result = await self.call_tool(name, args)
                except Exception as exc:  # report back to the model instead of crashing the loop
                    result = f"ERROR: {exc}"

            trace.append({"type": "observation", "tool": name, "text": result})
            conv.messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            conv.pending.pop(0)
        return None

    # -- loop ----------------------------------------------------------------
    async def run(self, conv: Conversation, decision: Optional[str] = None) -> dict:
        trace: list = []

        paused = await self._drain(conv, decision, trace)
        if paused:
            return {**paused, "trace": trace, "steps": conv.steps}

        while True:
            if conv.steps >= self.max_steps:
                msg = await self._complete(conv, allow_tools=False)
                answer = msg.content or (f"I stopped after {self.max_steps} steps without a final answer. "
                                         "Try a more specific request or raise the step limit.")
                conv.messages.append({"role": "assistant", "content": answer})
                used, conv.steps = conv.steps, 0
                trace.append({"type": "limit", "text": f"Step budget of {self.max_steps} reached; answered without tools."})
                return {"status": "stopped", "answer": answer, "trace": trace, "steps": used}

            conv.steps += 1
            msg = await self._complete(conv, allow_tools=True)
            entry = self._assistant_entry(msg)
            conv.messages.append(entry)

            if "tool_calls" not in entry:
                used, conv.steps = conv.steps, 0
                return {"status": "done", "answer": entry["content"], "trace": trace, "steps": used}

            if entry["content"]:
                trace.append({"type": "thought", "text": entry["content"]})
            conv.pending = list(entry["tool_calls"])
            paused = await self._drain(conv, None, trace)
            if paused:
                return {**paused, "trace": trace, "steps": conv.steps}
