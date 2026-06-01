"""
Base Agent Class for Insurance Adjudication System
Provides common functionality for all specialized agents
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Type, TypeVar, Generic
from uuid import UUID, uuid4
from contextlib import asynccontextmanager

from pydantic import BaseModel, Field

from ..config.settings import settings
from ..models.claim import (
    Claim, AgentTask, AgentMessage, WorkflowState,
    ClaimStatus, AdjudicationDecision
)


logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class AgentContext(BaseModel):
    """Context passed to agents during execution"""
    claim: Claim
    workflow_state: WorkflowState
    shared_memory: Dict[str, Any] = Field(default_factory=dict)
    parent_task_id: Optional[UUID] = None
    correlation_id: UUID = Field(default_factory=uuid4)
    max_iterations: int = Field(default=10)
    current_iteration: int = 0
    # Lock for thread-safe shared memory access
    _memory_lock: Optional[asyncio.Lock] = None

    class Config:
        arbitrary_types_allowed = True

    def get_memory_lock(self) -> asyncio.Lock:
        """Get or create the memory lock for thread-safe access"""
        if self._memory_lock is None:
            self._memory_lock = asyncio.Lock()
        return self._memory_lock


class AgentResult(BaseModel):
    """Result returned by an agent"""
    success: bool
    task_id: UUID
    agent_name: str
    
    output: Dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""
    confidence: float = 0.0
    
    next_actions: List[str] = Field(default_factory=list)
    requires_human_review: bool = False
    human_review_reasons: List[str] = Field(default_factory=list)
    
    error_message: Optional[str] = None
    execution_time_ms: float = 0.0
    
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentCapability(BaseModel):
    """Describes an agent's capability"""
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    examples: List[Dict[str, Any]] = Field(default_factory=list)


class BaseAgent(ABC):
    """
    Abstract base class for all agents in the system.
    Provides common functionality for:
    - LLM interaction
    - Tool execution
    - State management
    - Logging and observability
    - Error handling and retries
    """
    
    def __init__(
        self,
        name: str,
        description: str,
        llm_client: Any = None,
        tools: Optional[List[Any]] = None,
    ):
        self.name = name
        self.description = description
        self.llm_client = llm_client
        self.tools = tools or []
        self.capabilities: List[AgentCapability] = []
        
        self._is_initialized = False
        self._execution_history: List[AgentResult] = []
        self._logger = logging.getLogger(f"agent.{name}")
    
    async def initialize(self) -> None:
        """Initialize agent resources"""
        if self._is_initialized:
            return
        
        self._logger.info(f"Initializing agent: {self.name}")
        await self._setup()
        self._is_initialized = True
    
    async def _setup(self) -> None:
        """Override for custom initialization"""
        pass
    
    async def shutdown(self) -> None:
        """Cleanup agent resources"""
        self._logger.info(f"Shutting down agent: {self.name}")
        await self._cleanup()
        self._is_initialized = False
    
    async def _cleanup(self) -> None:
        """Override for custom cleanup"""
        pass
    
    @abstractmethod
    async def execute(self, context: AgentContext, task: AgentTask) -> AgentResult:
        """
        Main execution method - must be implemented by subclasses.
        
        Args:
            context: Execution context with claim and workflow state
            task: The specific task to execute
            
        Returns:
            AgentResult with the outcome of execution
        """
        pass
    
    async def think(self, context: AgentContext, prompt: str) -> str:
        """
        Send a prompt to the LLM and get a response.
        Includes automatic retry and error handling.
        """
        if not self.llm_client:
            raise RuntimeError(f"Agent {self.name} has no LLM client configured")
        
        for attempt in range(settings.agent.retry_attempts):
            try:
                system_prompt = self._build_system_prompt(context)
                response = await self.llm_client.generate(
                    system=system_prompt,
                    prompt=prompt,
                    max_tokens=settings.llm.max_tokens,
                    temperature=settings.llm.temperature,
                )
                return response
            except Exception as e:
                self._logger.warning(
                    f"LLM call failed (attempt {attempt + 1}): {e}"
                )
                if attempt == settings.agent.retry_attempts - 1:
                    raise
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
        
        return ""
    
    def _build_system_prompt(self, context: AgentContext) -> str:
        """Build the system prompt for this agent"""
        return f"""You are {self.name}, an AI agent specialized in insurance claim adjudication.

Role: {self.description}

Guidelines:
1. Always base decisions on policy terms and coverage
2. Be thorough but efficient in analysis
3. Flag any uncertainties or edge cases
4. Provide clear reasoning for all conclusions
5. Identify potential fraud indicators
6. Ensure regulatory compliance

Current claim context:
- Claim ID: {context.claim.id}
- Claim Type: {context.claim.claim_type}
- Amount: ${context.claim.total_amount_claimed}
- Status: {context.claim.status}

You have access to the following capabilities:
{self._format_capabilities()}
"""
    
    def _format_capabilities(self) -> str:
        """Format capabilities for system prompt"""
        if not self.capabilities:
            return "No specific capabilities defined."
        
        lines = []
        for cap in self.capabilities:
            lines.append(f"- {cap.name}: {cap.description}")
        return "\n".join(lines)
    
    async def use_tool(self, tool_name: str, **kwargs) -> Dict[str, Any]:
        """Execute a tool and return results"""
        tool = next((t for t in self.tools if t.name == tool_name), None)
        if not tool:
            raise ValueError(f"Tool '{tool_name}' not found for agent {self.name}")
        
        self._logger.debug(f"Executing tool: {tool_name} with args: {kwargs}")
        
        try:
            result = await tool.execute(**kwargs)
            return {"success": True, "result": result}
        except Exception as e:
            self._logger.error(f"Tool execution failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def delegate(
        self,
        target_agent: "BaseAgent",
        context: AgentContext,
        task_data: Dict[str, Any]
    ) -> AgentResult:
        """Delegate a task to another agent"""
        task = AgentTask(
            task_type=f"delegated_from_{self.name}",
            claim_id=context.claim.id,
            input_data=task_data,
            agent_name=target_agent.name,
        )
        
        self._logger.info(f"Delegating task to {target_agent.name}")
        return await target_agent.execute(context, task)
    
    async def update_shared_memory(
        self,
        context: AgentContext,
        key: str,
        value: Any
    ) -> None:
        """Update shared memory accessible to all agents (thread-safe)"""
        lock = context.get_memory_lock()
        async with lock:
            context.shared_memory[key] = value
            context.shared_memory[f"{key}_updated_by"] = self.name
            context.shared_memory[f"{key}_updated_at"] = datetime.now(timezone.utc).isoformat()

    async def get_from_shared_memory_async(
        self,
        context: AgentContext,
        key: str,
        default: Any = None
    ) -> Any:
        """Retrieve value from shared memory (thread-safe)"""
        lock = context.get_memory_lock()
        async with lock:
            return context.shared_memory.get(key, default)

    def get_from_shared_memory(
        self,
        context: AgentContext,
        key: str,
        default: Any = None
    ) -> Any:
        """Retrieve value from shared memory (synchronous - use for non-concurrent access)"""
        return context.shared_memory.get(key, default)
    
    def should_escalate_to_human(
        self,
        confidence: float,
        fraud_indicators: Optional[List[Any]] = None
    ) -> tuple[bool, List[str]]:
        """
        Determine if the case should be escalated to human review.
        
        Returns:
            Tuple of (should_escalate, reasons)
        """
        reasons = []
        
        # Low confidence
        if confidence < settings.agent.human_review_threshold:
            reasons.append(
                f"Confidence score ({confidence:.2f}) below threshold "
                f"({settings.agent.human_review_threshold})"
            )
        
        # Fraud indicators
        if fraud_indicators:
            high_severity = [f for f in fraud_indicators if f.severity == "high"]
            if high_severity:
                reasons.append(
                    f"{len(high_severity)} high-severity fraud indicator(s) detected"
                )
        
        return len(reasons) > 0, reasons
    
    def log_reasoning(self, step: str, reasoning: str, data: Optional[Dict] = None):
        """Log a reasoning step for audit trail"""
        self._logger.info(f"[{self.name}] {step}: {reasoning}")
        if data:
            self._logger.debug(f"[{self.name}] Data: {data}")
    
    @asynccontextmanager
    async def execution_context(self, task: AgentTask):
        """Context manager for task execution with timing and error handling"""
        start_time = datetime.now(timezone.utc)
        task.status = "in_progress"
        task.started_at = start_time
        
        try:
            yield
            task.status = "completed"
        except Exception as e:
            task.status = "failed"
            task.error_message = str(e)
            raise
        finally:
            task.completed_at = datetime.now(timezone.utc)
    
    def create_result(
        self,
        task: AgentTask,
        success: bool,
        output: Dict[str, Any],
        reasoning: str,
        confidence: float,
        **kwargs
    ) -> AgentResult:
        """Helper to create standardized results"""
        return AgentResult(
            success=success,
            task_id=task.id,
            agent_name=self.name,
            output=output,
            reasoning=reasoning,
            confidence=confidence,
            execution_time_ms=(
                (task.completed_at - task.started_at).total_seconds() * 1000
                if task.completed_at and task.started_at else 0
            ),
            **kwargs
        )


class AgentRegistry:
    """Registry for managing agent instances"""
    
    _instance: Optional["AgentRegistry"] = None
    _agents: Dict[str, BaseAgent] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def register(self, agent: BaseAgent) -> None:
        """Register an agent"""
        self._agents[agent.name] = agent
        logger.info(f"Registered agent: {agent.name}")
    
    def get(self, name: str) -> Optional[BaseAgent]:
        """Get an agent by name"""
        return self._agents.get(name)
    
    def get_all(self) -> List[BaseAgent]:
        """Get all registered agents"""
        return list(self._agents.values())
    
    async def initialize_all(self) -> None:
        """Initialize all registered agents"""
        for agent in self._agents.values():
            await agent.initialize()
    
    async def shutdown_all(self) -> None:
        """Shutdown all registered agents"""
        for agent in self._agents.values():
            await agent.shutdown()


# Global registry instance
agent_registry = AgentRegistry()
