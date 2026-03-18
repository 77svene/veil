"""
veil/grid/orchestrator.py

Distributed Execution Grid - Orchestrate multiple browser instances across machines for parallel task execution.
Enables horizontal scaling of automation workloads with load balancing and fault tolerance.
"""

import asyncio
import json
import logging
import uuid
import time
import hashlib
import pickle
from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Dict, List, Optional, Callable, Any, Set, Tuple
from collections import defaultdict
import aiohttp
from aiohttp import web
import websockets
from websockets.exceptions import ConnectionClosed

from ..actor.page import Page
from ..agent.service import AgentService
from ..agent.views import AgentTask, AgentResult

logger = logging.getLogger(__name__)


class WorkerStatus(Enum):
    """Status of a worker node."""
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"
    ERROR = "error"


class TaskPriority(Enum):
    """Task priority levels."""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


class TaskStatus(Enum):
    """Task execution status."""
    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class WorkerNode:
    """Represents a worker node in the grid."""
    worker_id: str
    host: str
    port: int
    capabilities: Dict[str, Any]
    status: WorkerStatus = WorkerStatus.IDLE
    current_tasks: Set[str] = field(default_factory=set)
    last_heartbeat: float = field(default_factory=time.time)
    browser_count: int = 1
    performance_score: float = 1.0
    session_affinity: Dict[str, str] = field(default_factory=dict)  # session_id -> worker_id
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data['status'] = self.status.value
        data['current_tasks'] = list(self.current_tasks)
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'WorkerNode':
        """Create from dictionary."""
        data['status'] = WorkerStatus(data['status'])
        data['current_tasks'] = set(data.get('current_tasks', []))
        return cls(**data)


@dataclass
class GridTask:
    """Represents a task to be executed on the grid."""
    task_id: str
    task_type: str
    payload: Dict[str, Any]
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    assigned_worker: Optional[str] = None
    session_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    timeout: float = 300.0  # 5 minutes default
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data['priority'] = self.priority.value
        data['status'] = self.status.value
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'GridTask':
        """Create from dictionary."""
        data['priority'] = TaskPriority(data['priority'])
        data['status'] = TaskStatus(data['status'])
        return cls(**data)


class LoadBalancer:
    """Load balancing strategies for task distribution."""
    
    @staticmethod
    def round_robin(workers: List[WorkerNode], task: GridTask) -> Optional[WorkerNode]:
        """Round-robin load balancing."""
        available = [w for w in workers if w.status == WorkerStatus.IDLE]
        if not available:
            return None
        # Simple round-robin based on worker index
        return available[hash(task.task_id) % len(available)]
    
    @staticmethod
    def least_connections(workers: List[WorkerNode], task: GridTask) -> Optional[WorkerNode]:
        """Assign to worker with fewest active tasks."""
        available = [w for w in workers if w.status in [WorkerStatus.IDLE, WorkerStatus.BUSY]]
        if not available:
            return None
        return min(available, key=lambda w: len(w.current_tasks))
    
    @staticmethod
    def session_affinity(workers: List[WorkerNode], task: GridTask) -> Optional[WorkerNode]:
        """Assign to worker with session affinity if exists."""
        if task.session_id:
            for worker in workers:
                if task.session_id in worker.session_affinity:
                    return worker
        # Fall back to least connections
        return LoadBalancer.least_connections(workers, task)
    
    @staticmethod
    def performance_based(workers: List[WorkerNode], task: GridTask) -> Optional[WorkerNode]:
        """Assign based on worker performance score."""
        available = [w for w in workers if w.status in [WorkerStatus.IDLE, WorkerStatus.BUSY]]
        if not available:
            return None
        # Weight by performance score and inverse of current tasks
        return max(available, 
                  key=lambda w: w.performance_score / (len(w.current_tasks) + 1))


class ResultAggregator:
    """Aggregates results from multiple tasks."""
    
    def __init__(self):
        self.results: Dict[str, Any] = {}
        self.partial_results: Dict[str, List[Any]] = defaultdict(list)
    
    def add_result(self, task_id: str, result: Any, is_partial: bool = False):
        """Add a task result."""
        if is_partial:
            self.partial_results[task_id].append(result)
        else:
            self.results[task_id] = result
    
    def get_aggregated_result(self, task_ids: List[str]) -> Dict[str, Any]:
        """Get aggregated results for multiple tasks."""
        aggregated = {}
        for task_id in task_ids:
            if task_id in self.results:
                aggregated[task_id] = self.results[task_id]
            elif task_id in self.partial_results:
                aggregated[task_id] = self.partial_results[task_id]
        return aggregated
    
    def clear(self, task_ids: Optional[List[str]] = None):
        """Clear results."""
        if task_ids:
            for task_id in task_ids:
                self.results.pop(task_id, None)
                self.partial_results.pop(task_id, None)
        else:
            self.results.clear()
            self.partial_results.clear()


class Orchestrator:
    """
    Master node that orchestrates browser instances across the grid.
    
    Features:
    - Task distribution with load balancing
    - Session affinity for stateful tasks
    - Automatic failover and retry
    - Result aggregation
    - Health monitoring
    """
    
    def __init__(self, 
                 host: str = "localhost", 
                 port: int = 8765,
                 load_balancer_strategy: str = "least_connections",
                 heartbeat_interval: float = 10.0,
                 task_timeout: float = 300.0):
        """
        Initialize the orchestrator.
        
        Args:
            host: Host to bind to
            port: Port to listen on
            load_balancer_strategy: Load balancing strategy (round_robin, least_connections, session_affinity, performance_based)
            heartbeat_interval: Interval for worker heartbeats in seconds
            task_timeout: Default task timeout in seconds
        """
        self.host = host
        self.port = port
        self.heartbeat_interval = heartbeat_interval
        self.task_timeout = task_timeout
        
        # Worker management
        self.workers: Dict[str, WorkerNode] = {}
        self.worker_sessions: Dict[str, web.WebSocketResponse] = {}
        
        # Task management
        self.tasks: Dict[str, GridTask] = {}
        self.task_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.result_aggregator = ResultAggregator()
        
        # Load balancer
        self.load_balancer = LoadBalancer()
        self.load_balancer_strategy = load_balancer_strategy
        
        # Statistics
        self.stats = {
            "tasks_submitted": 0,
            "tasks_completed": 0,
            "tasks_failed": 0,
            "total_execution_time": 0.0,
            "workers_registered": 0
        }
        
        # Event loop and server
        self.loop = asyncio.get_event_loop()
        self.app = web.Application()
        self._setup_routes()
        self.runner = None
        self.site = None
        
        # Background tasks
        self._background_tasks: List[asyncio.Task] = []
        
        logger.info(f"Orchestrator initialized with {load_balancer_strategy} load balancing")
    
    def _setup_routes(self):
        """Setup HTTP routes for the orchestrator API."""
        self.app.router.add_post('/api/tasks/submit', self.handle_submit_task)
        self.app.router.add_get('/api/tasks/{task_id}', self.handle_get_task)
        self.app.router.add_get('/api/tasks/{task_id}/result', self.handle_get_result)
        self.app.router.add_get('/api/workers', self.handle_list_workers)
        self.app.router.add_get('/api/stats', self.handle_get_stats)
        self.app.router.add_post('/api/workers/register', self.handle_register_worker)
        self.app.router.add_post('/api/workers/{worker_id}/heartbeat', self.handle_worker_heartbeat)
        self.app.router.add_post('/api/workers/{worker_id}/task_complete', self.handle_task_complete)
        self.app.router.add_websocket('/ws/worker/{worker_id}', self.handle_worker_websocket)
    
    async def start(self):
        """Start the orchestrator server."""
        logger.info(f"Starting orchestrator on {self.host}:{self.port}")
        
        # Start background tasks
        self._background_tasks.append(
            self.loop.create_task(self._task_dispatcher())
        )
        self._background_tasks.append(
            self.loop.create_task(self._health_monitor())
        )
        self._background_tasks.append(
            self.loop.create_task(self._cleanup_completed_tasks())
        )
        
        # Start HTTP server
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        
        logger.info(f"Orchestrator started on http://{self.host}:{self.port}")
    
    async def stop(self):
        """Stop the orchestrator server."""
        logger.info("Stopping orchestrator...")
        
        # Cancel background tasks
        for task in self._background_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Close all worker connections
        for worker_id, ws in self.worker_sessions.items():
            await ws.close()
        
        # Stop HTTP server
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        
        logger.info("Orchestrator stopped")
    
    async def submit_task(self, 
                         task_type: str, 
                         payload: Dict[str, Any],
                         priority: TaskPriority = TaskPriority.NORMAL,
                         session_id: Optional[str] = None,
                         timeout: Optional[float] = None) -> str:
        """
        Submit a task to the grid.
        
        Args:
            task_type: Type of task to execute
            payload: Task payload/data
            priority: Task priority
            session_id: Optional session ID for affinity
            timeout: Task timeout in seconds
            
        Returns:
            Task ID
        """
        task_id = str(uuid.uuid4())
        timeout = timeout or self.task_timeout
        
        task = GridTask(
            task_id=task_id,
            task_type=task_type,
            payload=payload,
            priority=priority,
            session_id=session_id,
            timeout=timeout
        )
        
        self.tasks[task_id] = task
        self.stats["tasks_submitted"] += 1
        
        # Add to priority queue (lower number = higher priority)
        await self.task_queue.put((-priority.value, time.time(), task_id))
        
        logger.info(f"Task {task_id} submitted with priority {priority.name}")
        return task_id
    
    async def submit_batch_tasks(self, 
                                tasks: List[Dict[str, Any]],
                                batch_id: Optional[str] = None) -> List[str]:
        """
        Submit multiple tasks as a batch.
        
        Args:
            tasks: List of task specifications
            batch_id: Optional batch identifier
            
        Returns:
            List of task IDs
        """
        task_ids = []
        batch_id = batch_id or str(uuid.uuid4())
        
        for i, task_spec in enumerate(tasks):
            task_spec.setdefault('session_id', f"{batch_id}_{i}")
            task_id = await self.submit_task(**task_spec)
            task_ids.append(task_id)
        
        logger.info(f"Batch {batch_id} submitted with {len(task_ids)} tasks")
        return task_ids
    
    async def get_task_result(self, task_id: str, wait: bool = False, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
        """
        Get result for a task.
        
        Args:
            task_id: Task ID
            wait: Whether to wait for result
            timeout: Wait timeout in seconds
            
        Returns:
            Task result or None
        """
        if task_id not in self.tasks:
            return None
        
        task = self.tasks[task_id]
        
        if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.TIMEOUT]:
            return task.to_dict()
        
        if wait:
            start_time = time.time()
            while time.time() - start_time < timeout:
                if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.TIMEOUT]:
                    return task.to_dict()
                await asyncio.sleep(0.1)
        
        return None
    
    async def get_batch_results(self, 
                               task_ids: List[str], 
                               wait: bool = False,
                               timeout: float = 60.0) -> Dict[str, Any]:
        """
        Get results for multiple tasks.
        
        Args:
            task_ids: List of task IDs
            wait: Whether to wait for all results
            timeout: Wait timeout in seconds
            
        Returns:
            Dictionary mapping task IDs to results
        """
        results = {}
        pending_tasks = set(task_ids)
        
        if wait:
            start_time = time.time()
            while pending_tasks and (time.time() - start_time < timeout):
                for task_id in list(pending_tasks):
                    result = await self.get_task_result(task_id, wait=False)
                    if result and result['status'] in [TaskStatus.COMPLETED.value, 
                                                      TaskStatus.FAILED.value, 
                                                      TaskStatus.TIMEOUT.value]:
                        results[task_id] = result
                        pending_tasks.remove(task_id)
                if pending_tasks:
                    await asyncio.sleep(0.1)
        else:
            for task_id in task_ids:
                result = await self.get_task_result(task_id, wait=False)
                if result:
                    results[task_id] = result
        
        return results
    
    async def _task_dispatcher(self):
        """Background task that dispatches tasks to workers."""
        logger.info("Task dispatcher started")
        
        while True:
            try:
                # Get next task from queue
                priority, timestamp, task_id = await self.task_queue.get()
                
                if task_id not in self.tasks:
                    continue
                
                task = self.tasks[task_id]
                
                if task.status != TaskStatus.PENDING:
                    continue
                
                # Find available worker
                worker = await self._find_worker_for_task(task)
                
                if worker:
                    # Assign task to worker
                    await self._assign_task_to_worker(task, worker)
                else:
                    # No worker available, put back in queue with delay
                    await asyncio.sleep(1.0)
                    await self.task_queue.put((priority, timestamp, task_id))
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in task dispatcher: {e}")
                await asyncio.sleep(1.0)
    
    async def _find_worker_for_task(self, task: GridTask) -> Optional[WorkerNode]:
        """Find the best worker for a task based on load balancing strategy."""
        available_workers = [
            w for w in self.workers.values() 
            if w.status in [WorkerStatus.IDLE, WorkerStatus.BUSY]
        ]
        
        if not available_workers:
            return None
        
        # Apply load balancing strategy
        if self.load_balancer_strategy == "round_robin":
            return self.load_balancer.round_robin(available_workers, task)
        elif self.load_balancer_strategy == "least_connections":
            return self.load_balancer.least_connections(available_workers, task)
        elif self.load_balancer_strategy == "session_affinity":
            return self.load_balancer.session_affinity(available_workers, task)
        elif self.load_balancer_strategy == "performance_based":
            return self.load_balancer.performance_based(available_workers, task)
        else:
            return self.load_balancer.least_connections(available_workers, task)
    
    async def _assign_task_to_worker(self, task: GridTask, worker: WorkerNode):
        """Assign a task to a worker."""
        task.status = TaskStatus.ASSIGNED
        task.assigned_worker = worker.worker_id
        task.started_at = time.time()
        
        worker.current_tasks.add(task.task_id)
        worker.status = WorkerStatus.BUSY
        
        # Update session affinity
        if task.session_id:
            worker.session_affinity[task.session_id] = worker.worker_id
        
        # Send task to worker via WebSocket
        if worker.worker_id in self.worker_sessions:
            ws = self.worker_sessions[worker.worker_id]
            try:
                await ws.send_json({
                    "type": "task_assignment",
                    "task": task.to_dict()
                })
                logger.info(f"Task {task.task_id} assigned to worker {worker.worker_id}")
            except Exception as e:
                logger.error(f"Failed to send task to worker {worker.worker_id}: {e}")
                await self._handle_worker_failure(worker.worker_id)
    
    async def _health_monitor(self):
        """Monitor worker health and handle failures."""
        logger.info("Health monitor started")
        
        while True:
            try:
                current_time = time.time()
                offline_workers = []
                
                for worker_id, worker in self.workers.items():
                    # Check heartbeat timeout (2x heartbeat interval)
                    if current_time - worker.last_heartbeat > self.heartbeat_interval * 2:
                        if worker.status != WorkerStatus.OFFLINE:
                            logger.warning(f"Worker {worker_id} missed heartbeat, marking as offline")
                            worker.status = WorkerStatus.OFFLINE
                            offline_workers.append(worker_id)
                
                # Handle offline workers
                for worker_id in offline_workers:
                    await self._handle_worker_failure(worker_id)
                
                await asyncio.sleep(self.heartbeat_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in health monitor: {e}")
                await asyncio.sleep(5.0)
    
    async def _handle_worker_failure(self, worker_id: str):
        """Handle worker failure and reassign tasks."""
        if worker_id not in self.workers:
            return
        
        worker = self.workers[worker_id]
        worker.status = WorkerStatus.OFFLINE
        
        # Reassign tasks from failed worker
        for task_id in list(worker.current_tasks):
            if task_id in self.tasks:
                task = self.tasks[task_id]
                if task.status in [TaskStatus.ASSIGNED, TaskStatus.RUNNING]:
                    # Reset task for reassignment
                    task.status = TaskStatus.PENDING
                    task.assigned_worker = None
                    task.retry_count += 1
                    
                    if task.retry_count <= task.max_retries:
                        # Put back in queue
                        await self.task_queue.put(
                            (-task.priority.value, time.time(), task_id)
                        )
                        logger.info(f"Task {task_id} reassigned after worker {worker_id} failure")
                    else:
                        # Max retries exceeded
                        task.status = TaskStatus.FAILED
                        task.error = f"Max retries exceeded after worker {worker_id} failure"
                        self.stats["tasks_failed"] += 1
        
        worker.current_tasks.clear()
        
        # Close WebSocket connection
        if worker_id in self.worker_sessions:
            await self.worker_sessions[worker_id].close()
            del self.worker_sessions[worker_id]
    
    async def _cleanup_completed_tasks(self):
        """Periodically clean up old completed tasks."""
        logger.info("Task cleanup started")
        
        while True:
            try:
                current_time = time.time()
                tasks_to_remove = []
                
                for task_id, task in self.tasks.items():
                    # Remove tasks completed more than 1 hour ago
                    if (task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.TIMEOUT] and
                        task.completed_at and current_time - task.completed_at > 3600):
                        tasks_to_remove.append(task_id)
                
                for task_id in tasks_to_remove:
                    del self.tasks[task_id]
                    self.result_aggregator.clear([task_id])
                
                if tasks_to_remove:
                    logger.info(f"Cleaned up {len(tasks_to_remove)} old tasks")
                
                await asyncio.sleep(300)  # Run every 5 minutes
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in task cleanup: {e}")
                await asyncio.sleep(60)
    
    # HTTP Handlers
    async def handle_submit_task(self, request: web.Request) -> web.Response:
        """Handle task submission via HTTP."""
        try:
            data = await request.json()
            task_id = await self.submit_task(**data)
            return web.json_response({"task_id": task_id, "status": "submitted"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
    
    async def handle_get_task(self, request: web.Request) -> web.Response:
        """Get task status."""
        task_id = request.match_info['task_id']
        result = await self.get_task_result(task_id, wait=False)
        
        if result:
            return web.json_response(result)
        else:
            return web.json_response({"error": "Task not found"}, status=404)
    
    async def handle_get_result(self, request: web.Request) -> web.Response:
        """Get task result with optional waiting."""
        task_id = request.match_info['task_id']
        wait = request.query.get('wait', 'false').lower() == 'true'
        timeout = float(request.query.get('timeout', '30.0'))
        
        result = await self.get_task_result(task_id, wait=wait, timeout=timeout)
        
        if result:
            return web.json_response(result)
        else:
            return web.json_response({"error": "Task not found or timeout"}, status=404)
    
    async def handle_list_workers(self, request: web.Request) -> web.Response:
        """List all workers."""
        workers_data = [worker.to_dict() for worker in self.workers.values()]
        return web.json_response({"workers": workers_data})
    
    async def handle_get_stats(self, request: web.Request) -> web.Response:
        """Get orchestrator statistics."""
        stats = self.stats.copy()
        stats.update({
            "active_workers": len([w for w in self.workers.values() 
                                 if w.status != WorkerStatus.OFFLINE]),
            "pending_tasks": len([t for t in self.tasks.values() 
                                if t.status == TaskStatus.PENDING]),
            "running_tasks": len([t for t in self.tasks.values() 
                                if t.status in [TaskStatus.ASSIGNED, TaskStatus.RUNNING]]),
            "total_tasks": len(self.tasks)
        })
        return web.json_response(stats)
    
    async def handle_register_worker(self, request: web.Request) -> web.Response:
        """Handle worker registration via HTTP."""
        try:
            data = await request.json()
            worker_id = data.get('worker_id', str(uuid.uuid4()))
            
            worker = WorkerNode(
                worker_id=worker_id,
                host=data.get('host', request.remote),
                port=data.get('port', 0),
                capabilities=data.get('capabilities', {}),
                browser_count=data.get('browser_count', 1)
            )
            
            self.workers[worker_id] = worker
            self.stats["workers_registered"] += 1
            
            logger.info(f"Worker {worker_id} registered")
            return web.json_response({"worker_id": worker_id, "status": "registered"})
            
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
    
    async def handle_worker_heartbeat(self, request: web.Request) -> web.Response:
        """Handle worker heartbeat."""
        worker_id = request.match_info['worker_id']
        
        if worker_id not in self.workers:
            return web.json_response({"error": "Worker not found"}, status=404)
        
        worker = self.workers[worker_id]
        worker.last_heartbeat = time.time()
        
        # Update worker status if it was offline
        if worker.status == WorkerStatus.OFFLINE:
            worker.status = WorkerStatus.IDLE
            logger.info(f"Worker {worker_id} back online")
        
        return web.json_response({"status": "ok"})
    
    async def handle_task_complete(self, request: web.Request) -> web.Response:
        """Handle task completion notification from worker."""
        worker_id = request.match_info['worker_id']
        
        if worker_id not in self.workers:
            return web.json_response({"error": "Worker not found"}, status=404)
        
        try:
            data = await request.json()
            task_id = data.get('task_id')
            result = data.get('result')
            error = data.get('error')
            
            if task_id and task_id in self.tasks:
                await self._handle_task_completion(task_id, result, error)
            
            return web.json_response({"status": "ok"})
            
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
    
    async def handle_worker_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket connection from worker."""
        worker_id = request.match_info['worker_id']
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        if worker_id not in self.workers:
            await ws.close(code=4004, message=b"Worker not registered")
            return ws
        
        self.worker_sessions[worker_id] = ws
        logger.info(f"Worker {worker_id} connected via WebSocket")
        
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self._handle_worker_message(worker_id, data)
                    except json.JSONDecodeError:
                        logger.error(f"Invalid JSON from worker {worker_id}")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error for worker {worker_id}: {ws.exception()}")
        except ConnectionClosed:
            logger.info(f"Worker {worker_id} WebSocket disconnected")
        finally:
            if worker_id in self.worker_sessions:
                del self.worker_sessions[worker_id]
            await self._handle_worker_failure(worker_id)
        
        return ws
    
    async def _handle_worker_message(self, worker_id: str, data: Dict[str, Any]):
        """Handle message from worker via WebSocket."""
        msg_type = data.get('type')
        
        if msg_type == 'task_started':
            task_id = data.get('task_id')
            if task_id in self.tasks:
                self.tasks[task_id].status = TaskStatus.RUNNING
                
        elif msg_type == 'task_progress':
            task_id = data.get('task_id')
            progress = data.get('progress')
            # Could store progress updates if needed
            
        elif msg_type == 'task_complete':
            task_id = data.get('task_id')
            result = data.get('result')
            error = data.get('error')
            await self._handle_task_completion(task_id, result, error)
            
        elif msg_type == 'heartbeat':
            if worker_id in self.workers:
                self.workers[worker_id].last_heartbeat = time.time()
    
    async def _handle_task_completion(self, task_id: str, result: Any, error: Optional[str]):
        """Handle task completion."""
        if task_id not in self.tasks:
            return
        
        task = self.tasks[task_id]
        worker_id = task.assigned_worker
        
        # Update task status
        if error:
            task.status = TaskStatus.FAILED
            task.error = error
            self.stats["tasks_failed"] += 1
        else:
            task.status = TaskStatus.COMPLETED
            task.result = result
            self.stats["tasks_completed"] += 1
        
        task.completed_at = time.time()
        
        # Update execution time stats
        if task.started_at:
            execution_time = task.completed_at - task.started_at
            self.stats["total_execution_time"] += execution_time
        
        # Update worker
        if worker_id and worker_id in self.workers:
            worker = self.workers[worker_id]
            worker.current_tasks.discard(task_id)
            
            if not worker.current_tasks:
                worker.status = WorkerStatus.IDLE
            
            # Update performance score based on execution time
            if task.started_at:
                execution_time = task.completed_at - task.started_at
                # Simple performance scoring: faster is better
                worker.performance_score = max(0.1, min(2.0, 10.0 / max(execution_time, 0.1)))
        
        # Store result in aggregator
        self.result_aggregator.add_result(task_id, task.to_dict())
        
        logger.info(f"Task {task_id} completed with status {task.status.value}")
    
    # Worker-side API methods (for workers to call)
    async def register_worker(self, 
                             host: str, 
                             port: int, 
                             capabilities: Dict[str, Any],
                             browser_count: int = 1) -> str:
        """
        Register a worker with the orchestrator.
        
        This is called by worker nodes to register themselves.
        """
        worker_id = str(uuid.uuid4())
        
        worker = WorkerNode(
            worker_id=worker_id,
            host=host,
            port=port,
            capabilities=capabilities,
            browser_count=browser_count
        )
        
        self.workers[worker_id] = worker
        self.stats["workers_registered"] += 1
        
        logger.info(f"Worker {worker_id} registered from {host}:{port}")
        return worker_id
    
    async def send_heartbeat(self, worker_id: str):
        """Send heartbeat from worker."""
        if worker_id in self.workers:
            self.workers[worker_id].last_heartbeat = time.time()
            return True
        return False
    
    async def report_task_completion(self, 
                                   worker_id: str, 
                                   task_id: str, 
                                   result: Any = None, 
                                   error: Optional[str] = None):
        """Report task completion from worker."""
        await self._handle_task_completion(task_id, result, error)


class GridWorker:
    """
    Worker node that executes tasks from the orchestrator.
    
    This class runs on worker machines and connects to the orchestrator.
    """
    
    def __init__(self, 
                 orchestrator_host: str,
                 orchestrator_port: int,
                 worker_host: str = "localhost",
                 worker_port: int = 0,
                 capabilities: Optional[Dict[str, Any]] = None,
                 browser_count: int = 1):
        """
        Initialize the grid worker.
        
        Args:
            orchestrator_host: Orchestrator host
            orchestrator_port: Orchestrator port
            worker_host: Worker host (for registration)
            worker_port: Worker port (for registration)
            capabilities: Worker capabilities
            browser_count: Number of browser instances
        """
        self.orchestrator_host = orchestrator_host
        self.orchestrator_port = orchestrator_port
        self.worker_host = worker_host
        self.worker_port = worker_port
        self.capabilities = capabilities or {}
        self.browser_count = browser_count
        
        self.worker_id: Optional[str] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        
        # Task execution
        self.current_tasks: Dict[str, asyncio.Task] = {}
        self.browser_pool: List[Page] = []
        
        logger.info(f"Grid worker initialized for orchestrator at {orchestrator_host}:{orchestrator_port}")
    
    async def start(self):
        """Start the worker and connect to orchestrator."""
        logger.info("Starting grid worker...")
        
        # Create HTTP session
        self.session = aiohttp.ClientSession()
        
        # Register with orchestrator
        await self._register()
        
        # Start WebSocket connection
        await self._connect_websocket()
        
        # Start heartbeat
        asyncio.create_task(self._send_heartbeats())
        
        logger.info(f"Grid worker {self.worker_id} started")
    
    async def stop(self):
        """Stop the worker."""
        logger.info("Stopping grid worker...")
        
        # Cancel all running tasks
        for task_id, task in self.current_tasks.items():
            task.cancel()
        
        # Close WebSocket
        if self.websocket:
            await self.websocket.close()
        
        # Close HTTP session
        if self.session:
            await self.session.close()
        
        logger.info("Grid worker stopped")
    
    async def _register(self):
        """Register with the orchestrator."""
        url = f"http://{self.orchestrator_host}:{self.orchestrator_port}/api/workers/register"
        
        data = {
            "host": self.worker_host,
            "port": self.worker_port,
            "capabilities": self.capabilities,
            "browser_count": self.browser_count
        }
        
        async with self.session.post(url, json=data) as response:
            if response.status == 200:
                result = await response.json()
                self.worker_id = result['worker_id']
                logger.info(f"Registered with orchestrator as {self.worker_id}")
            else:
                raise Exception(f"Failed to register: {response.status}")
    
    async def _connect_websocket(self):
        """Connect to orchestrator via WebSocket."""
        uri = f"ws://{self.orchestrator_host}:{self.orchestrator_port}/ws/worker/{self.worker_id}"
        
        try:
            self.websocket = await websockets.connect(uri)
            logger.info(f"Connected to orchestrator via WebSocket")
            
            # Start listening for messages
            asyncio.create_task(self._listen_websocket())
            
        except Exception as e:
            logger.error(f"Failed to connect WebSocket: {e}")
            raise
    
    async def _listen_websocket(self):
        """Listen for messages from orchestrator."""
        try:
            async for message in self.websocket:
                data = json.loads(message)
                await self._handle_orchestrator_message(data)
        except ConnectionClosed:
            logger.info("WebSocket connection closed")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
    
    async def _handle_orchestrator_message(self, data: Dict[str, Any]):
        """Handle message from orchestrator."""
        msg_type = data.get('type')
        
        if msg_type == 'task_assignment':
            task_data = data.get('task')
            if task_data:
                await self._execute_task(task_data)
    
    async def _execute_task(self, task_data: Dict[str, Any]):
        """Execute a task."""
        task_id = task_data['task_id']
        
        # Notify orchestrator that task started
        await self.websocket.send(json.dumps({
            "type": "task_started",
            "task_id": task_id
        }))
        
        # Create and start task execution
        task_coro = self._run_task(task_data)
        self.current_tasks[task_id] = asyncio.create_task(task_coro)
        
        logger.info(f"Started executing task {task_id}")
    
    async def _run_task(self, task_data: Dict[str, Any]):
        """Run the actual task."""
        task_id = task_data['task_id']
        
        try:
            # This is where you would integrate with the existing veil modules
            # For example, create a Page instance and execute the task
            
            # Simulate task execution
            task_type = task_data.get('task_type')
            payload = task_data.get('payload', {})
            
            # Example: Execute based on task type
            if task_type == "navigate":
                result = await self._execute_navigate_task(payload)
            elif task_type == "scrape":
                result = await self._execute_scrape_task(payload)
            elif task_type == "automate":
                result = await self._execute_automate_task(payload)
            else:
                result = {"status": "completed", "message": f"Executed {task_type} task"}
            
            # Report completion
            await self._report_task_completion(task_id, result)
            
        except asyncio.CancelledError:
            logger.info(f"Task {task_id} cancelled")
            await self._report_task_completion(task_id, None, "Task cancelled")
        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}")
            await self._report_task_completion(task_id, None, str(e))
        finally:
            self.current_tasks.pop(task_id, None)
    
    async def _execute_navigate_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a navigation task."""
        # Example integration with existing modules
        url = payload.get('url')
        if not url:
            raise ValueError("URL required for navigate task")
        
        # In a real implementation, you would use the Page class from veil.actor.page
        # For now, simulate navigation
        await asyncio.sleep(1)  # Simulate work
        
        return {
            "status": "completed",
            "url": url,
            "title": f"Page at {url}",
            "timestamp": time.time()
        }
    
    async def _execute_scrape_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a scraping task."""
        url = payload.get('url')
        selectors = payload.get('selectors', [])
        
        # Simulate scraping
        await asyncio.sleep(2)
        
        return {
            "status": "completed",
            "url": url,
            "data": {selector: f"Data for {selector}" for selector in selectors},
            "timestamp": time.time()
        }
    
    async def _execute_automate_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute an automation task."""
        # This would integrate with the AgentService
        task_description = payload.get('task')
        
        # Simulate automation
        await asyncio.sleep(3)
        
        return {
            "status": "completed",
            "task": task_description,
            "result": "Automation completed successfully",
            "timestamp": time.time()
        }
    
    async def _report_task_completion(self, task_id: str, result: Any = None, error: Optional[str] = None):
        """Report task completion to orchestrator."""
        message = {
            "type": "task_complete",
            "task_id": task_id,
            "result": result,
            "error": error
        }
        
        if self.websocket:
            await self.websocket.send(json.dumps(message))
    
    async def _send_heartbeats(self):
        """Send periodic heartbeats to orchestrator."""
        while True:
            try:
                if self.websocket:
                    await self.websocket.send(json.dumps({"type": "heartbeat"}))
                await asyncio.sleep(10)  # Send heartbeat every 10 seconds
            except Exception as e:
                logger.error(f"Failed to send heartbeat: {e}")
                await asyncio.sleep(5)


# Convenience functions for easy usage
async def create_orchestrator(host: str = "localhost", port: int = 8765, **kwargs) -> Orchestrator:
    """Create and start an orchestrator."""
    orchestrator = Orchestrator(host=host, port=port, **kwargs)
    await orchestrator.start()
    return orchestrator


async def create_worker(orchestrator_host: str, 
                       orchestrator_port: int,
                       **kwargs) -> GridWorker:
    """Create and start a grid worker."""
    worker = GridWorker(
        orchestrator_host=orchestrator_host,
        orchestrator_port=orchestrator_port,
        **kwargs
    )
    await worker.start()
    return worker


# Example usage
if __name__ == "__main__":
    async def main():
        # Start orchestrator
        orchestrator = await create_orchestrator()
        
        # Start a few workers
        workers = []
        for i in range(3):
            worker = await create_worker(
                orchestrator_host="localhost",
                orchestrator_port=8765,
                worker_host=f"worker-{i}",
                capabilities={"browser": True, "scraping": True}
            )
            workers.append(worker)
        
        # Submit some tasks
        task_ids = []
        for i in range(10):
            task_id = await orchestrator.submit_task(
                task_type="navigate",
                payload={"url": f"https://example.com/page/{i}"},
                priority=TaskPriority.NORMAL
            )
            task_ids.append(task_id)
        
        # Wait for results
        results = await orchestrator.get_batch_results(task_ids, wait=True, timeout=60)
        
        print(f"Completed {len(results)} tasks")
        
        # Cleanup
        for worker in workers:
            await worker.stop()
        await orchestrator.stop()
    
    asyncio.run(main())