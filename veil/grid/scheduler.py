"""
Distributed Execution Grid — Orchestrate multiple browser instances across machines for parallel task execution.
Enables horizontal scaling of automation workloads with load balancing and fault tolerance.
"""

import asyncio
import uuid
import time
import logging
from typing import Dict, List, Optional, Any, Callable, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
import json
from concurrent.futures import ThreadPoolExecutor
import hashlib

# Import existing modules
from veil.actor.page import Page
from veil.actor.utils import ActorUtils
from veil.agent.service import AgentService
from veil.agent.views import AgentResult
from veil.cloud_events import CloudEventService

logger = logging.getLogger(__name__)


class WorkerStatus(Enum):
    """Status of a worker node."""
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"
    ERROR = "error"


class TaskStatus(Enum):
    """Status of a task in the grid."""
    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LoadBalancingStrategy(Enum):
    """Load balancing strategies for task distribution."""
    ROUND_ROBIN = "round_robin"
    LEAST_CONNECTIONS = "least_connections"
    RESOURCE_BASED = "resource_based"
    SESSION_AFFINITY = "session_affinity"


@dataclass
class WorkerNode:
    """Represents a worker node in the grid."""
    id: str
    host: str
    port: int
    status: WorkerStatus = WorkerStatus.IDLE
    capabilities: Dict[str, Any] = field(default_factory=dict)
    current_tasks: Set[str] = field(default_factory=set)
    max_concurrent_tasks: int = 5
    last_heartbeat: float = field(default_factory=time.time)
    session_affinity_map: Dict[str, str] = field(default_factory=dict)  # session_id -> worker_id
    performance_metrics: Dict[str, float] = field(default_factory=dict)
    
    @property
    def load_factor(self) -> float:
        """Calculate current load factor (0.0 to 1.0)."""
        if self.max_concurrent_tasks == 0:
            return 1.0
        return len(self.current_tasks) / self.max_concurrent_tasks
    
    @property
    def is_available(self) -> bool:
        """Check if worker can accept new tasks."""
        return (
            self.status in [WorkerStatus.IDLE, WorkerStatus.BUSY] and
            len(self.current_tasks) < self.max_concurrent_tasks
        )


@dataclass
class GridTask:
    """Represents a task to be executed on the grid."""
    id: str
    session_id: Optional[str] = None
    task_type: str = "browser_automation"
    payload: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    timeout: int = 300  # seconds
    retry_count: int = 0
    max_retries: int = 3
    status: TaskStatus = TaskStatus.PENDING
    assigned_worker: Optional[str] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GridConfig:
    """Configuration for the execution grid."""
    master_host: str = "localhost"
    master_port: int = 8080
    heartbeat_interval: int = 30  # seconds
    task_timeout: int = 300  # seconds
    max_retries: int = 3
    load_balancing: LoadBalancingStrategy = LoadBalancingStrategy.LEAST_CONNECTIONS
    enable_session_affinity: bool = True
    enable_fault_tolerance: bool = True
    health_check_interval: int = 60  # seconds
    result_aggregation_timeout: int = 60  # seconds


class SessionAffinityManager:
    """Manages session affinity for consistent task routing."""
    
    def __init__(self):
        self.session_map: Dict[str, str] = {}  # session_id -> worker_id
        self.worker_sessions: Dict[str, Set[str]] = {}  # worker_id -> set of session_ids
    
    def assign_session(self, session_id: str, worker_id: str) -> None:
        """Assign a session to a specific worker."""
        self.session_map[session_id] = worker_id
        if worker_id not in self.worker_sessions:
            self.worker_sessions[worker_id] = set()
        self.worker_sessions[worker_id].add(session_id)
    
    def get_worker_for_session(self, session_id: str) -> Optional[str]:
        """Get the worker assigned to a session."""
        return self.session_map.get(session_id)
    
    def remove_session(self, session_id: str) -> None:
        """Remove session assignment."""
        worker_id = self.session_map.pop(session_id, None)
        if worker_id and worker_id in self.worker_sessions:
            self.worker_sessions[worker_id].discard(session_id)
    
    def get_sessions_for_worker(self, worker_id: str) -> Set[str]:
        """Get all sessions assigned to a worker."""
        return self.worker_sessions.get(worker_id, set())
    
    def clear_worker_sessions(self, worker_id: str) -> None:
        """Clear all sessions for a worker."""
        sessions = self.get_sessions_for_worker(worker_id)
        for session_id in sessions:
            self.session_map.pop(session_id, None)
        self.worker_sessions.pop(worker_id, None)


class LoadBalancer:
    """Handles load balancing across worker nodes."""
    
    def __init__(self, strategy: LoadBalancingStrategy = LoadBalancingStrategy.LEAST_CONNECTIONS):
        self.strategy = strategy
        self.round_robin_index = 0
    
    def select_worker(
        self,
        workers: Dict[str, WorkerNode],
        task: GridTask,
        session_affinity_manager: SessionAffinityManager
    ) -> Optional[WorkerNode]:
        """Select the best worker for a task based on the load balancing strategy."""
        available_workers = [
            w for w in workers.values()
            if w.is_available and w.status != WorkerStatus.OFFLINE
        ]
        
        if not available_workers:
            return None
        
        # Check session affinity first if enabled
        if task.session_id:
            worker_id = session_affinity_manager.get_worker_for_session(task.session_id)
            if worker_id and worker_id in workers:
                worker = workers[worker_id]
                if worker.is_available:
                    return worker
        
        # Apply load balancing strategy
        if self.strategy == LoadBalancingStrategy.ROUND_ROBIN:
            return self._round_robin_select(available_workers)
        elif self.strategy == LoadBalancingStrategy.LEAST_CONNECTIONS:
            return self._least_connections_select(available_workers)
        elif self.strategy == LoadBalancingStrategy.RESOURCE_BASED:
            return self._resource_based_select(available_workers)
        else:
            return available_workers[0]
    
    def _round_robin_select(self, workers: List[WorkerNode]) -> WorkerNode:
        """Select worker using round-robin strategy."""
        if not workers:
            raise ValueError("No workers available")
        
        worker = workers[self.round_robin_index % len(workers)]
        self.round_robin_index += 1
        return worker
    
    def _least_connections_select(self, workers: List[WorkerNode]) -> WorkerNode:
        """Select worker with least active connections."""
        return min(workers, key=lambda w: len(w.current_tasks))
    
    def _resource_based_select(self, workers: List[WorkerNode]) -> WorkerNode:
        """Select worker based on resource availability and performance metrics."""
        def score_worker(worker: WorkerNode) -> float:
            # Lower score is better
            load_score = worker.load_factor * 0.6
            performance_score = worker.performance_metrics.get("avg_response_time", 1.0) * 0.4
            return load_score + performance_score
        
        return min(workers, key=score_worker)


class ResultAggregator:
    """Aggregates results from multiple task executions."""
    
    def __init__(self, timeout: int = 60):
        self.timeout = timeout
        self.pending_aggregations: Dict[str, Dict[str, Any]] = {}
    
    async def aggregate_results(
        self,
        aggregation_id: str,
        tasks: List[GridTask],
        aggregation_func: Optional[Callable] = None
    ) -> Any:
        """Aggregate results from multiple tasks."""
        self.pending_aggregations[aggregation_id] = {
            "tasks": {task.id: task for task in tasks},
            "results": {},
            "completed": set(),
            "failed": set(),
            "start_time": time.time()
        }
        
        try:
            # Wait for all tasks to complete or timeout
            while True:
                await asyncio.sleep(0.1)
                
                aggregation_data = self.pending_aggregations[aggregation_id]
                completed_count = len(aggregation_data["completed"])
                failed_count = len(aggregation_data["failed"])
                total_count = len(tasks)
                
                # Check if all tasks are done
                if completed_count + failed_count >= total_count:
                    break
                
                # Check timeout
                if time.time() - aggregation_data["start_time"] > self.timeout:
                    logger.warning(f"Aggregation timeout for {aggregation_id}")
                    break
            
            # Collect results
            aggregation_data = self.pending_aggregations[aggregation_id]
            results = []
            
            for task_id, task in aggregation_data["tasks"].items():
                if task.status == TaskStatus.COMPLETED:
                    results.append(task.result)
                elif task.status == TaskStatus.FAILED:
                    results.append({"error": task.error, "task_id": task_id})
            
            # Apply custom aggregation function if provided
            if aggregation_func:
                return aggregation_func(results)
            
            return results
            
        finally:
            self.pending_aggregations.pop(aggregation_id, None)
    
    def update_task_result(self, aggregation_id: str, task_id: str, result: Any, success: bool) -> None:
        """Update task result in aggregation."""
        if aggregation_id in self.pending_aggregations:
            aggregation_data = self.pending_aggregations[aggregation_id]
            aggregation_data["results"][task_id] = result
            if success:
                aggregation_data["completed"].add(task_id)
            else:
                aggregation_data["failed"].add(task_id)


class FaultToleranceManager:
    """Manages fault tolerance and automatic failover."""
    
    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self.failed_tasks: Dict[str, GridTask] = {}
        self.retry_queue: asyncio.Queue = asyncio.Queue()
    
    async def handle_worker_failure(
        self,
        worker_id: str,
        tasks: Dict[str, GridTask],
        workers: Dict[str, WorkerNode],
        session_affinity_manager: SessionAffinityManager
    ) -> List[GridTask]:
        """Handle worker failure and reassign its tasks."""
        failed_tasks = []
        
        for task_id, task in tasks.items():
            if task.assigned_worker == worker_id and task.status in [
                TaskStatus.ASSIGNED, TaskStatus.RUNNING
            ]:
                # Mark task for retry
                task.retry_count += 1
                if task.retry_count <= self.max_retries:
                    task.status = TaskStatus.PENDING
                    task.assigned_worker = None
                    task.error = f"Worker {worker_id} failed"
                    failed_tasks.append(task)
                    logger.info(f"Task {task_id} queued for retry (attempt {task.retry_count})")
                else:
                    task.status = TaskStatus.FAILED
                    task.error = f"Max retries exceeded after worker failure"
                    logger.error(f"Task {task_id} failed after {self.max_retries} retries")
        
        # Clear session affinity for failed worker
        session_affinity_manager.clear_worker_sessions(worker_id)
        
        return failed_tasks
    
    async def monitor_workers(
        self,
        workers: Dict[str, WorkerNode],
        heartbeat_interval: int = 30
    ) -> List[str]:
        """Monitor worker health and detect failures."""
        failed_workers = []
        current_time = time.time()
        
        for worker_id, worker in workers.items():
            if worker.status == WorkerStatus.OFFLINE:
                continue
            
            # Check heartbeat timeout (2x heartbeat interval)
            if current_time - worker.last_heartbeat > heartbeat_interval * 2:
                logger.warning(f"Worker {worker_id} heartbeat timeout")
                worker.status = WorkerStatus.OFFLINE
                failed_workers.append(worker_id)
        
        return failed_tasks


class GridScheduler:
    """
    Distributed Execution Grid Scheduler
    
    Orchestrates multiple browser instances across machines for parallel task execution.
    Implements master-worker architecture with load balancing and fault tolerance.
    """
    
    def __init__(self, config: Optional[GridConfig] = None):
        self.config = config or GridConfig()
        self.workers: Dict[str, WorkerNode] = {}
        self.tasks: Dict[str, GridTask] = {}
        self.session_affinity = SessionAffinityManager()
        self.load_balancer = LoadBalancer(self.config.load_balancing)
        self.result_aggregator = ResultAggregator(self.config.result_aggregation_timeout)
        self.fault_tolerance = FaultToleranceManager(self.config.max_retries)
        self.cloud_events = CloudEventService()
        
        # Internal state
        self._running = False
        self._scheduler_task: Optional[asyncio.Task] = None
        self._health_check_task: Optional[asyncio.Task] = None
        self._task_queue: asyncio.Queue = asyncio.Queue()
        self._result_callbacks: Dict[str, Callable] = {}
        self._executor = ThreadPoolExecutor(max_workers=10)
        
        logger.info(f"GridScheduler initialized with config: {self.config}")
    
    async def start(self) -> None:
        """Start the grid scheduler."""
        if self._running:
            return
        
        self._running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        
        if self.config.enable_fault_tolerance:
            self._health_check_task = asyncio.create_task(self._health_check_loop())
        
        logger.info("GridScheduler started")
        await self.cloud_events.emit("grid.scheduler.started", {
            "scheduler_id": id(self),
            "config": self.config.__dict__
        })
    
    async def stop(self) -> None:
        """Stop the grid scheduler."""
        self._running = False
        
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        
        # Cancel all pending tasks
        for task_id, task in self.tasks.items():
            if task.status in [TaskStatus.PENDING, TaskStatus.ASSIGNED, TaskStatus.RUNNING]:
                task.status = TaskStatus.CANCELLED
        
        self._executor.shutdown(wait=False)
        logger.info("GridScheduler stopped")
        await self.cloud_events.emit("grid.scheduler.stopped", {
            "scheduler_id": id(self)
        })
    
    async def register_worker(
        self,
        worker_id: str,
        host: str,
        port: int,
        capabilities: Optional[Dict[str, Any]] = None,
        max_concurrent_tasks: int = 5
    ) -> WorkerNode:
        """Register a new worker node with the grid."""
        if worker_id in self.workers:
            raise ValueError(f"Worker {worker_id} already registered")
        
        worker = WorkerNode(
            id=worker_id,
            host=host,
            port=port,
            capabilities=capabilities or {},
            max_concurrent_tasks=max_concurrent_tasks
        )
        
        self.workers[worker_id] = worker
        logger.info(f"Worker {worker_id} registered at {host}:{port}")
        
        await self.cloud_events.emit("grid.worker.registered", {
            "worker_id": worker_id,
            "host": host,
            "port": port,
            "capabilities": capabilities
        })
        
        return worker
    
    async def unregister_worker(self, worker_id: str) -> None:
        """Unregister a worker node from the grid."""
        if worker_id not in self.workers:
            raise ValueError(f"Worker {worker_id} not found")
        
        # Handle worker failure before removal
        if self.config.enable_fault_tolerance:
            failed_tasks = await self.fault_tolerance.handle_worker_failure(
                worker_id,
                self.tasks,
                self.workers,
                self.session_affinity
            )
            
            # Requeue failed tasks
            for task in failed_tasks:
                await self._task_queue.put(task)
        
        del self.workers[worker_id]
        logger.info(f"Worker {worker_id} unregistered")
        
        await self.cloud_events.emit("grid.worker.unregistered", {
            "worker_id": worker_id
        })
    
    async def submit_task(
        self,
        task_type: str,
        payload: Dict[str, Any],
        session_id: Optional[str] = None,
        priority: int = 0,
        timeout: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        result_callback: Optional[Callable] = None
    ) -> str:
        """Submit a task to the grid for execution."""
        task_id = str(uuid.uuid4())
        
        task = GridTask(
            id=task_id,
            session_id=session_id,
            task_type=task_type,
            payload=payload,
            priority=priority,
            timeout=timeout or self.config.task_timeout,
            metadata=metadata or {}
        )
        
        self.tasks[task_id] = task
        
        if result_callback:
            self._result_callbacks[task_id] = result_callback
        
        await self._task_queue.put(task)
        logger.info(f"Task {task_id} submitted (type: {task_type}, session: {session_id})")
        
        await self.cloud_events.emit("grid.task.submitted", {
            "task_id": task_id,
            "task_type": task_type,
            "session_id": session_id,
            "priority": priority
        })
        
        return task_id
    
    async def submit_batch(
        self,
        tasks: List[Dict[str, Any]],
        aggregation_id: Optional[str] = None,
        aggregation_func: Optional[Callable] = None
    ) -> Tuple[List[str], Optional[str]]:
        """Submit multiple tasks as a batch with optional result aggregation."""
        task_ids = []
        grid_tasks = []
        
        for task_data in tasks:
            task_id = await self.submit_task(**task_data)
            task_ids.append(task_id)
            grid_tasks.append(self.tasks[task_id])
        
        aggregation_result = None
        if aggregation_id:
            # Start aggregation in background
            asyncio.create_task(
                self._aggregate_batch_results(
                    aggregation_id,
                    grid_tasks,
                    aggregation_func
                )
            )
        
        return task_ids, aggregation_id
    
    async def _aggregate_batch_results(
        self,
        aggregation_id: str,
        tasks: List[GridTask],
        aggregation_func: Optional[Callable]
    ) -> None:
        """Aggregate results from a batch of tasks."""
        try:
            result = await self.result_aggregator.aggregate_results(
                aggregation_id,
                tasks,
                aggregation_func
            )
            
            await self.cloud_events.emit("grid.batch.aggregated", {
                "aggregation_id": aggregation_id,
                "result": result,
                "task_count": len(tasks)
            })
            
        except Exception as e:
            logger.error(f"Batch aggregation failed for {aggregation_id}: {e}")
            await self.cloud_events.emit("grid.batch.aggregation_failed", {
                "aggregation_id": aggregation_id,
                "error": str(e)
            })
    
    async def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Get the status of a task."""
        if task_id not in self.tasks:
            raise ValueError(f"Task {task_id} not found")
        
        task = self.tasks[task_id]
        return {
            "task_id": task.id,
            "status": task.status.value,
            "assigned_worker": task.assigned_worker,
            "result": task.result,
            "error": task.error,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "retry_count": task.retry_count
        }
    
    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending or running task."""
        if task_id not in self.tasks:
            return False
        
        task = self.tasks[task_id]
        
        if task.status in [TaskStatus.PENDING, TaskStatus.ASSIGNED]:
            task.status = TaskStatus.CANCELLED
            logger.info(f"Task {task_id} cancelled")
            
            await self.cloud_events.emit("grid.task.cancelled", {
                "task_id": task_id
            })
            return True
        
        elif task.status == TaskStatus.RUNNING:
            # Notify worker to cancel the task
            # This would require a cancellation protocol with workers
            task.status = TaskStatus.CANCELLED
            logger.info(f"Task {task_id} marked for cancellation")
            return True
        
        return False
    
    async def get_grid_stats(self) -> Dict[str, Any]:
        """Get statistics about the grid."""
        total_workers = len(self.workers)
        available_workers = sum(1 for w in self.workers.values() if w.is_available)
        
        task_stats = {}
        for status in TaskStatus:
            task_stats[status.value] = sum(
                1 for t in self.tasks.values() if t.status == status
            )
        
        worker_loads = {
            worker_id: worker.load_factor
            for worker_id, worker in self.workers.items()
        }
        
        return {
            "total_workers": total_workers,
            "available_workers": available_workers,
            "task_stats": task_stats,
            "worker_loads": worker_loads,
            "queue_size": self._task_queue.qsize(),
            "session_affinity_map_size": len(self.session_affinity.session_map)
        }
    
    async def _scheduler_loop(self) -> None:
        """Main scheduler loop that assigns tasks to workers."""
        while self._running:
            try:
                # Get next task from queue
                task = await asyncio.wait_for(
                    self._task_queue.get(),
                    timeout=1.0
                )
                
                # Skip if task was cancelled
                if task.status == TaskStatus.CANCELLED:
                    continue
                
                # Select worker for task
                worker = self.load_balancer.select_worker(
                    self.workers,
                    task,
                    self.session_affinity
                )
                
                if worker:
                    await self._assign_task_to_worker(task, worker)
                else:
                    # No workers available, requeue with delay
                    await asyncio.sleep(0.1)
                    await self._task_queue.put(task)
                    
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
                await asyncio.sleep(1)
    
    async def _assign_task_to_worker(self, task: GridTask, worker: WorkerNode) -> None:
        """Assign a task to a specific worker."""
        task.status = TaskStatus.ASSIGNED
        task.assigned_worker = worker.id
        task.started_at = time.time()
        
        worker.current_tasks.add(task.id)
        worker.status = WorkerStatus.BUSY if len(worker.current_tasks) >= worker.max_concurrent_tasks else WorkerStatus.IDLE
        
        # Update session affinity
        if task.session_id and self.config.enable_session_affinity:
            self.session_affinity.assign_session(task.session_id, worker.id)
        
        logger.info(f"Task {task.id} assigned to worker {worker.id}")
        
        await self.cloud_events.emit("grid.task.assigned", {
            "task_id": task.id,
            "worker_id": worker.id,
            "session_id": task.session_id
        })
        
        # Execute task on worker (in background)
        asyncio.create_task(self._execute_task_on_worker(task, worker))
    
    async def _execute_task_on_worker(self, task: GridTask, worker: WorkerNode) -> None:
        """Execute a task on a worker node."""
        try:
            task.status = TaskStatus.RUNNING
            
            # Here we would actually send the task to the worker
            # For now, we'll simulate execution with a mock
            result = await self._simulate_task_execution(task, worker)
            
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.completed_at = time.time()
            
            # Update worker metrics
            execution_time = task.completed_at - task.started_at
            worker.performance_metrics["avg_response_time"] = (
                worker.performance_metrics.get("avg_response_time", execution_time) * 0.7 +
                execution_time * 0.3
            )
            
            logger.info(f"Task {task.id} completed on worker {worker.id}")
            
            # Trigger result callback if registered
            if task.id in self._result_callbacks:
                callback = self._result_callbacks.pop(task.id)
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(task.result)
                    else:
                        callback(task.result)
                except Exception as e:
                    logger.error(f"Result callback failed for task {task.id}: {e}")
            
            await self.cloud_events.emit("grid.task.completed", {
                "task_id": task.id,
                "worker_id": worker.id,
                "execution_time": execution_time,
                "result_summary": str(result)[:100] if result else None
            })
            
        except asyncio.TimeoutError:
            task.status = TaskStatus.FAILED
            task.error = "Task execution timeout"
            task.completed_at = time.time()
            
            logger.error(f"Task {task.id} timed out on worker {worker.id}")
            
            await self.cloud_events.emit("grid.task.timeout", {
                "task_id": task.id,
                "worker_id": worker.id,
                "timeout": task.timeout
            })
            
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = time.time()
            
            logger.error(f"Task {task.id} failed on worker {worker.id}: {e}")
            
            await self.cloud_events.emit("grid.task.failed", {
                "task_id": task.id,
                "worker_id": worker.id,
                "error": str(e)
            })
            
            # Handle retry if enabled
            if self.config.enable_fault_tolerance and task.retry_count < task.max_retries:
                task.retry_count += 1
                task.status = TaskStatus.PENDING
                task.assigned_worker = None
                await self._task_queue.put(task)
                logger.info(f"Task {task.id} queued for retry (attempt {task.retry_count})")
        
        finally:
            # Clean up worker state
            worker.current_tasks.discard(task.id)
            if not worker.current_tasks:
                worker.status = WorkerStatus.IDLE
    
    async def _simulate_task_execution(self, task: GridTask, worker: WorkerNode) -> Any:
        """Simulate task execution (replace with actual worker communication)."""
        # This is a placeholder - in production, this would send the task to the actual worker
        # and wait for the result via HTTP, WebSocket, or message queue
        
        # Simulate processing time
        processing_time = min(task.timeout, 2.0)  # Simulate 2 seconds max
        await asyncio.sleep(processing_time)
        
        # Simulate occasional failures for testing fault tolerance
        if task.retry_count > 0 and task.retry_count % 2 == 0:
            raise Exception(f"Simulated failure on retry {task.retry_count}")
        
        # Return mock result based on task type
        if task.task_type == "browser_automation":
            return {
                "success": True,
                "data": f"Executed on worker {worker.id}",
                "execution_time": processing_time,
                "task_id": task.id
            }
        elif task.task_type == "page_navigation":
            return {
                "url": task.payload.get("url", "https://example.com"),
                "status_code": 200,
                "title": "Example Domain"
            }
        else:
            return {"result": "completed", "worker": worker.id}
    
    async def _health_check_loop(self) -> None:
        """Periodic health check for workers."""
        while self._running:
            try:
                await asyncio.sleep(self.config.health_check_interval)
                
                # Check for failed workers
                failed_workers = await self.fault_tolerance.monitor_workers(
                    self.workers,
                    self.config.heartbeat_interval
                )
                
                # Handle failed workers
                for worker_id in failed_workers:
                    logger.warning(f"Worker {worker_id} detected as failed")
                    
                    if self.config.enable_fault_tolerance:
                        failed_tasks = await self.fault_tolerance.handle_worker_failure(
                            worker_id,
                            self.tasks,
                            self.workers,
                            self.session_affinity
                        )
                        
                        # Requeue failed tasks
                        for task in failed_tasks:
                            await self._task_queue.put(task)
                    
                    await self.cloud_events.emit("grid.worker.failed", {
                        "worker_id": worker_id,
                        "timestamp": time.time()
                    })
                
            except Exception as e:
                logger.error(f"Health check loop error: {e}")
                await asyncio.sleep(5)
    
    async def update_worker_heartbeat(self, worker_id: str, metrics: Optional[Dict[str, Any]] = None) -> None:
        """Update worker heartbeat and metrics."""
        if worker_id not in self.workers:
            raise ValueError(f"Worker {worker_id} not found")
        
        worker = self.workers[worker_id]
        worker.last_heartbeat = time.time()
        
        if metrics:
            worker.performance_metrics.update(metrics)
        
        # Update status based on current load
        if worker.current_tasks:
            worker.status = WorkerStatus.BUSY
        else:
            worker.status = WorkerStatus.IDLE
    
    async def get_worker_info(self, worker_id: str) -> Dict[str, Any]:
        """Get information about a specific worker."""
        if worker_id not in self.workers:
            raise ValueError(f"Worker {worker_id} not found")
        
        worker = self.workers[worker_id]
        return {
            "id": worker.id,
            "host": worker.host,
            "port": worker.port,
            "status": worker.status.value,
            "current_tasks": list(worker.current_tasks),
            "max_concurrent_tasks": worker.max_concurrent_tasks,
            "load_factor": worker.load_factor,
            "capabilities": worker.capabilities,
            "performance_metrics": worker.performance_metrics,
            "last_heartbeat": worker.last_heartbeat,
            "session_count": len(self.session_affinity.get_sessions_for_worker(worker_id))
        }


# Factory function for easy instantiation
def create_grid_scheduler(
    master_host: str = "localhost",
    master_port: int = 8080,
    load_balancing: LoadBalancingStrategy = LoadBalancingStrategy.LEAST_CONNECTIONS,
    enable_session_affinity: bool = True,
    enable_fault_tolerance: bool = True,
    **kwargs
) -> GridScheduler:
    """Create a configured GridScheduler instance."""
    config = GridConfig(
        master_host=master_host,
        master_port=master_port,
        load_balancing=load_balancing,
        enable_session_affinity=enable_session_affinity,
        enable_fault_tolerance=enable_fault_tolerance,
        **kwargs
    )
    return GridScheduler(config)


# Example usage and demonstration
async def demonstrate_grid_usage():
    """Demonstrate how to use the GridScheduler."""
    # Create scheduler
    scheduler = create_grid_scheduler(
        load_balancing=LoadBalancingStrategy.SESSION_AFFINITY,
        enable_session_affinity=True
    )
    
    await scheduler.start()
    
    try:
        # Register workers
        await scheduler.register_worker(
            worker_id="worker-1",
            host="localhost",
            port=9001,
            capabilities={"browser": "chrome", "version": "120"},
            max_concurrent_tasks=3
        )
        
        await scheduler.register_worker(
            worker_id="worker-2",
            host="localhost",
            port=9002,
            capabilities={"browser": "firefox", "version": "115"},
            max_concurrent_tasks=5
        )
        
        # Submit individual tasks
        task_id = await scheduler.submit_task(
            task_type="browser_automation",
            payload={"action": "navigate", "url": "https://example.com"},
            session_id="user-session-123",
            priority=1
        )
        
        print(f"Submitted task: {task_id}")
        
        # Submit batch of tasks
        batch_tasks = [
            {
                "task_type": "page_navigation",
                "payload": {"url": f"https://example.com/page{i}"},
                "session_id": f"session-{i % 3}",
                "priority": i
            }
            for i in range(10)
        ]
        
        task_ids, aggregation_id = await scheduler.submit_batch(
            batch_tasks,
            aggregation_id="batch-123",
            aggregation_func=lambda results: {"total": len(results), "success_rate": 0.9}
        )
        
        print(f"Submitted batch with {len(task_ids)} tasks")
        
        # Monitor progress
        for _ in range(10):
            stats = await scheduler.get_grid_stats()
            print(f"Grid stats: {stats}")
            await asyncio.sleep(1)
        
        # Get task status
        status = await scheduler.get_task_status(task_id)
        print(f"Task status: {status}")
        
        # Get worker info
        worker_info = await scheduler.get_worker_info("worker-1")
        print(f"Worker info: {worker_info}")
        
    finally:
        await scheduler.stop()


if __name__ == "__main__":
    # Run demonstration
    asyncio.run(demonstrate_grid_usage())