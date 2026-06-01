"""
Agent Orchestrator for Insurance Adjudication System
Manages agent workflows, parallel execution, and state management
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Callable, Awaitable
from uuid import UUID, uuid4
from enum import Enum

from pydantic import BaseModel, Field

from .base import (
    BaseAgent, AgentContext, AgentResult, AgentTask,
    agent_registry
)
from ..models.claim import (
    Claim, WorkflowState, ClaimStatus, AdjudicationDecision
)
from ..config.settings import settings


logger = logging.getLogger(__name__)


class WorkflowStep(str, Enum):
    """Steps in the adjudication workflow"""
    INITIALIZE = "initialize"
    DOCUMENT_EXTRACTION = "document_extraction"
    POLICY_ANALYSIS = "policy_analysis"
    FRAUD_DETECTION = "fraud_detection"
    FINAL_DECISION = "final_decision"
    HUMAN_REVIEW = "human_review"
    COMPLETE = "complete"
    FAILED = "failed"


class WorkflowTransition(BaseModel):
    """Defines a transition between workflow steps"""
    from_step: WorkflowStep
    to_step: WorkflowStep
    condition: Optional[str] = None
    priority: int = 0


class WorkflowConfig(BaseModel):
    """Configuration for the adjudication workflow"""
    name: str = "insurance_adjudication"
    version: str = "1.0.0"
    
    steps: List[WorkflowStep] = Field(default_factory=lambda: [
        WorkflowStep.INITIALIZE,
        WorkflowStep.DOCUMENT_EXTRACTION,
        WorkflowStep.POLICY_ANALYSIS,
        WorkflowStep.FRAUD_DETECTION,
        WorkflowStep.FINAL_DECISION,
        WorkflowStep.COMPLETE,
    ])
    
    parallel_steps: List[List[WorkflowStep]] = Field(default_factory=lambda: [
        [WorkflowStep.POLICY_ANALYSIS, WorkflowStep.FRAUD_DETECTION],
    ])
    
    step_agents: Dict[str, str] = Field(default_factory=lambda: {
        WorkflowStep.DOCUMENT_EXTRACTION.value: "document_extraction",
        WorkflowStep.POLICY_ANALYSIS.value: "policy_analysis",
        WorkflowStep.FRAUD_DETECTION.value: "fraud_detection",
        WorkflowStep.FINAL_DECISION.value: "decision_maker",
    })
    
    step_timeouts: Dict[str, int] = Field(default_factory=lambda: {
        WorkflowStep.DOCUMENT_EXTRACTION.value: 60,
        WorkflowStep.POLICY_ANALYSIS.value: 45,
        WorkflowStep.FRAUD_DETECTION.value: 45,
        WorkflowStep.FINAL_DECISION.value: 30,
    })


class OrchestrationResult(BaseModel):
    """Result of workflow orchestration"""
    workflow_id: UUID
    claim_id: UUID
    status: str
    
    decision: Optional[AdjudicationDecision] = None
    
    steps_completed: List[str] = Field(default_factory=list)
    steps_failed: List[str] = Field(default_factory=list)
    
    total_execution_time_ms: float = 0.0
    agent_results: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    
    requires_human_review: bool = False
    human_review_reasons: List[str] = Field(default_factory=list)
    
    error_message: Optional[str] = None
    
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None


class AgentOrchestrator:
    """
    Orchestrates the execution of agents in the adjudication workflow.
    
    Features:
    - Sequential and parallel agent execution
    - State management across agents
    - Error handling and recovery
    - Timeout management
    - Human escalation routing
    - Observability and logging
    """
    
    def __init__(
        self,
        config: Optional[WorkflowConfig] = None,
        llm_client: Any = None,
    ):
        self.config = config or WorkflowConfig()
        self.llm_client = llm_client
        self._active_workflows: Dict[UUID, WorkflowState] = {}
        self._hooks: Dict[str, List[Callable]] = {
            "pre_step": [],
            "post_step": [],
            "on_error": [],
            "on_complete": [],
        }
        self._logger = logging.getLogger("orchestrator")
    
    def register_hook(
        self,
        hook_type: str,
        callback: Callable[..., Awaitable[None]]
    ) -> None:
        """Register a lifecycle hook"""
        if hook_type in self._hooks:
            self._hooks[hook_type].append(callback)
    
    async def _trigger_hooks(self, hook_type: str, **kwargs) -> None:
        """Trigger all hooks of a given type"""
        for hook in self._hooks.get(hook_type, []):
            try:
                await hook(**kwargs)
            except Exception as e:
                self._logger.warning(f"Hook {hook_type} failed: {e}")
    
    async def process_claim(self, claim: Claim) -> OrchestrationResult:
        """
        Process a claim through the full adjudication workflow.
        """
        workflow_id = uuid4()
        start_time = datetime.now(timezone.utc)
        
        self._logger.info(f"Starting workflow {workflow_id} for claim {claim.id}")
        
        workflow_state = WorkflowState(
            id=workflow_id,
            claim_id=claim.id,
            current_step=WorkflowStep.INITIALIZE.value,
            pending_steps=[s.value for s in self.config.steps[1:]],
        )
        self._active_workflows[workflow_id] = workflow_state
        
        context = AgentContext(
            claim=claim,
            workflow_state=workflow_state,
            max_iterations=settings.agent.max_iterations,
        )
        
        result = OrchestrationResult(
            workflow_id=workflow_id,
            claim_id=claim.id,
            status="in_progress",
            started_at=start_time,
        )
        
        try:
            claim.status = ClaimStatus.AGENT_PROCESSING
            claim.processing_started_at = start_time
            
            await self._execute_workflow(context, result)
            
            if result.requires_human_review:
                result.status = "pending_human_review"
                claim.status = ClaimStatus.HUMAN_REVIEW_REQUIRED
            elif result.steps_failed:
                result.status = "failed"
                claim.status = ClaimStatus.UNDER_REVIEW
            else:
                result.status = "completed"
                claim.status = (
                    ClaimStatus.APPROVED if result.decision and 
                    result.decision.decision == "approved"
                    else ClaimStatus.DENIED if result.decision and
                    result.decision.decision == "denied"
                    else ClaimStatus.PARTIALLY_APPROVED
                )
            
            await self._trigger_hooks("on_complete", result=result)
            
        except Exception as e:
            self._logger.error(f"Workflow failed: {e}", exc_info=True)
            result.status = "failed"
            result.error_message = str(e)
            claim.status = ClaimStatus.UNDER_REVIEW
            # Track error on claim for audit trail
            claim.last_error_message = str(e)
            claim.last_error_at = datetime.now(timezone.utc)
            claim.workflow_errors.append({
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "workflow_id": str(workflow_id),
                "step": workflow_state.current_step,
            })
            await self._trigger_hooks("on_error", error=e, result=result)
        
        finally:
            result.completed_at = datetime.now(timezone.utc)
            result.total_execution_time_ms = (
                (result.completed_at - result.started_at).total_seconds() * 1000
            )
            claim.processing_completed_at = result.completed_at
            del self._active_workflows[workflow_id]
        
        return result
    
    async def _execute_workflow(
        self,
        context: AgentContext,
        result: OrchestrationResult
    ) -> None:
        """Execute the workflow steps"""
        
        workflow_state = context.workflow_state
        steps_to_execute = [
            s for s in self.config.steps 
            if s not in [WorkflowStep.INITIALIZE, WorkflowStep.COMPLETE]
        ]
        
        i = 0
        while i < len(steps_to_execute):
            current_step = steps_to_execute[i]
            
            parallel_group = self._get_parallel_group(current_step)
            
            if parallel_group and all(s in steps_to_execute[i:] for s in parallel_group):
                await self._execute_parallel_steps(context, parallel_group, result)
                i += len(parallel_group)
            else:
                await self._execute_step(context, current_step, result)
                i += 1
            
            if result.requires_human_review:
                self._logger.info("Stopping workflow for human review")
                break
            
            if current_step.value in result.steps_failed:
                self._logger.warning(f"Step {current_step.value} failed")
                break
    
    def _get_parallel_group(self, step: WorkflowStep) -> Optional[List[WorkflowStep]]:
        """Get the parallel group for a step"""
        for group in self.config.parallel_steps:
            if step in group:
                return group
        return None
    
    async def _execute_parallel_steps(
        self,
        context: AgentContext,
        steps: List[WorkflowStep],
        result: OrchestrationResult
    ) -> None:
        """Execute multiple steps in parallel"""
        
        self._logger.info(f"Executing parallel: {[s.value for s in steps]}")
        
        tasks = [
            self._execute_step_internal(context, step, result)
            for step in steps
        ]
        
        try:
            max_timeout = max(
                self.config.step_timeouts.get(s.value, 60) for s in steps
            )
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=max_timeout
            )
        except asyncio.TimeoutError:
            self._logger.error("Parallel execution timed out")
            for step in steps:
                if step.value not in result.steps_completed:
                    result.steps_failed.append(step.value)
    
    async def _execute_step(
        self,
        context: AgentContext,
        step: WorkflowStep,
        result: OrchestrationResult
    ) -> None:
        """Execute a single workflow step"""
        
        timeout = self.config.step_timeouts.get(step.value, 60)
        
        try:
            await asyncio.wait_for(
                self._execute_step_internal(context, step, result),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            self._logger.error(f"Step {step.value} timed out")
            result.steps_failed.append(step.value)
    
    async def _execute_step_internal(
        self,
        context: AgentContext,
        step: WorkflowStep,
        result: OrchestrationResult
    ) -> None:
        """Internal step execution"""
        
        workflow_state = context.workflow_state
        workflow_state.current_step = step.value
        
        await self._trigger_hooks("pre_step", step=step, context=context)
        
        self._logger.info(f"Executing step: {step.value}")
        
        agent_name = self.config.step_agents.get(step.value)
        if not agent_name:
            result.steps_completed.append(step.value)
            return
        
        agent = agent_registry.get(agent_name)
        if not agent:
            self._logger.error(f"Agent {agent_name} not found")
            result.steps_failed.append(step.value)
            return
        
        task = AgentTask(
            task_type=step.value,
            claim_id=context.claim.id,
            agent_name=agent_name,
        )
        
        try:
            agent_result = await agent.execute(context, task)
            result.agent_results[agent_name] = agent_result.model_dump()
            
            if agent_result.success:
                result.steps_completed.append(step.value)
                workflow_state.completed_steps.append(step.value)
                
                if agent_result.requires_human_review:
                    result.requires_human_review = True
                    result.human_review_reasons.extend(agent_result.human_review_reasons)
                
                if step == WorkflowStep.FINAL_DECISION:
                    decision_data = agent_result.output.get("decision")
                    if decision_data and isinstance(decision_data, dict):
                        result.decision = AdjudicationDecision(**decision_data)
            else:
                result.steps_failed.append(step.value)
        
        except Exception as e:
            self._logger.error(f"Error executing {agent_name}: {e}")
            result.steps_failed.append(step.value)
            # Track step failure on claim
            context.claim.failed_steps.append(step.value)
            context.claim.workflow_errors.append({
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "step": step.value,
                "agent": agent_name,
            })
        
        finally:
            if step.value in workflow_state.pending_steps:
                workflow_state.pending_steps.remove(step.value)
            await self._trigger_hooks("post_step", step=step, context=context)
    
    async def process_batch(
        self,
        claims: List[Claim],
        max_concurrent: Optional[int] = None
    ) -> List[OrchestrationResult]:
        """Process multiple claims in parallel"""
        
        max_concurrent = max_concurrent or settings.agent.parallel_agents
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def process_with_semaphore(claim: Claim) -> OrchestrationResult:
            async with semaphore:
                return await self.process_claim(claim)
        
        self._logger.info(f"Processing batch of {len(claims)} claims")
        
        results = await asyncio.gather(
            *[process_with_semaphore(claim) for claim in claims],
            return_exceptions=True
        )
        
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                final_results.append(OrchestrationResult(
                    workflow_id=uuid4(),
                    claim_id=claims[i].id,
                    status="failed",
                    error_message=str(result),
                ))
            else:
                final_results.append(result)
        
        return final_results
    
    def get_workflow_status(self, workflow_id: UUID) -> Optional[WorkflowState]:
        """Get status of an active workflow"""
        return self._active_workflows.get(workflow_id)
    
    def get_active_workflows(self) -> List[WorkflowState]:
        """Get all active workflows"""
        return list(self._active_workflows.values())


class AdaptiveOrchestrator(AgentOrchestrator):
    """
    Extended orchestrator with adaptive capabilities
    """
    
    def __init__(
        self,
        config: Optional[WorkflowConfig] = None,
        llm_client: Any = None,
    ):
        super().__init__(config, llm_client)
        self._claim_metrics: Dict[str, Dict[str, Any]] = {}
    
    async def process_claim(self, claim: Claim) -> OrchestrationResult:
        """Process with adaptive workflow selection"""
        
        workflow_config = await self._select_optimal_workflow(claim)
        self.config = workflow_config
        
        result = await super().process_claim(claim)
        self._record_metrics(claim, result)
        
        return result
    
    async def _select_optimal_workflow(self, claim: Claim) -> WorkflowConfig:
        """Select optimal workflow based on claim characteristics"""
        
        config = WorkflowConfig()
        
        # High-value claims get more thorough processing
        if claim.total_amount_claimed > 50000:
            config.step_timeouts = {
                WorkflowStep.DOCUMENT_EXTRACTION.value: 90,
                WorkflowStep.POLICY_ANALYSIS.value: 60,
                WorkflowStep.FRAUD_DETECTION.value: 90,
                WorkflowStep.FINAL_DECISION.value: 45,
            }
        
        # Many documents need more extraction time
        if len(claim.documents) > 10:
            config.step_timeouts[WorkflowStep.DOCUMENT_EXTRACTION.value] = 120
        
        # High-risk claimants get sequential processing
        if claim.claimant and claim.claimant.risk_score and claim.claimant.risk_score > 0.7:
            config.parallel_steps = []
        
        return config
    
    def _record_metrics(self, claim: Claim, result: OrchestrationResult) -> None:
        """Record metrics for continuous improvement"""
        
        claim_type = claim.claim_type.value
        
        if claim_type not in self._claim_metrics:
            self._claim_metrics[claim_type] = {
                "total_processed": 0,
                "avg_processing_time_ms": 0,
                "approval_rate": 0,
                "human_review_rate": 0,
            }
        
        metrics = self._claim_metrics[claim_type]
        n = metrics["total_processed"]
        
        metrics["total_processed"] = n + 1
        metrics["avg_processing_time_ms"] = (
            (metrics["avg_processing_time_ms"] * n + result.total_execution_time_ms) 
            / (n + 1)
        )
        
        if result.decision:   
            is_approved = result.decision.decision in ["approved", "partially_approved"]
            metrics["approval_rate"] = (
                (metrics["approval_rate"] * n + (1 if is_approved else 0)) / (n + 1)
            )
        
        metrics["human_review_rate"] = (
            (metrics["human_review_rate"] * n + (1 if result.requires_human_review else 0))
            / (n + 1)
        )
    
    def get_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Get accumulated metrics"""
        return self._claim_metrics.copy()
