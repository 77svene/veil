"""Distributed Execution Grid Worker — Browser automation workload executor."""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass, field, asdict
from enum import Enum
import aiohttp
from aiohttp import web
import websockets
from websockets.exceptions import ConnectionClosed

from veil.actor.page import Page
from veil.agent.service import AgentService
from veil.agent.views import AgentResult
from veil.actor.utils import create_browser_context

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkerStatus(Enum):
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"
    DRAINING = "draining"


@dataclass
class Task:
    """Represents an automation task to be executed."""
    id: str
    session_id: str
    type: str  # "agent", "actor", "script"
    payload: Dict[str, Any]
    priority: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    timeout: int = 300  # seconds
    max_retries: int = 3
    retry_count: int = 0
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    worker_id: Optional[str] = None
    affinity_key: Optional[str] = None  # For session affinity


@dataclass
class WorkerInfo:
    """Information about a worker node."""
    id: str
    host: str
    port: int
    capabilities: Dict[str, Any]
    status: WorkerStatus = WorkerStatus.IDLE
    current_tasks: int = 0
    max_tasks: int = 5
    last_heartbeat: datetime = field(default_factory=datetime.utcnow)
    tags: List[str] = field(default_factory=list)
    browser_pool_size: int = 3


@dataclass
class SessionAffinity:
    """Maintains session affinity for consistent routing."""
    session_id: str
    worker_id: str
    last_used: datetime = field(default_factory=datetime.utcnow)
    context_id: Optional[str] = None


class BrowserInstancePool:
    """Manages a pool of browser instances for parallel execution."""
    
    def __init__(self, max_instances: int = 5, browser_type: str = "chromium"):
        self.max_instances = max_instances
        self.browser_type = browser_type
        self.instances: Dict[str, Dict[str, Any]] = {}  # instance_id -> {context, pages}
        self.available_instances: asyncio.Queue = asyncio.Queue()
        self.instance_locks: Dict[str, asyncio.Lock] = {}
        self._initialized = False
        
    async def initialize(self):
        """Initialize the browser pool."""
        if self._initialized:
            return
            
        for i in range(self.max_instances):
            instance_id = f"browser_{uuid.uuid4().hex[:8]}"
            try:
                context = await create_browser_context(
                    headless=True,
                    browser_type=self.browser_type
                )
                self.instances[instance_id] = {
                    "context": context,
                    "pages": {},
                    "created_at": datetime.utcnow(),
                    "last_used": datetime.utcnow()
                }
                self.instance_locks[instance_id] = asyncio.Lock()
                await self.available_instances.put(instance_id)
                logger.info(f"Initialized browser instance {instance_id}")
            except Exception as e:
                logger.error(f"Failed to initialize browser instance {i}: {e}")
                
        self._initialized = True
        logger.info(f"Browser pool initialized with {len(self.instances)} instances")
        
    async def acquire_instance(self, timeout: float = 30.0) -> Optional[str]:
        """Acquire an available browser instance."""
        try:
            instance_id = await asyncio.wait_for(
                self.available_instances.get(),
                timeout=timeout
            )
            self.instances[instance_id]["last_used"] = datetime.utcnow()
            return instance_id
        except asyncio.TimeoutError:
            logger.warning("Timeout acquiring browser instance")
            return None
            
    async def release_instance(self, instance_id: str):
        """Release a browser instance back to the pool."""
        if instance_id in self.instances:
            await self.available_instances.put(instance_id)
            
    async def create_page(self, instance_id: str, session_id: Optional[str] = None) -> Optional[Page]:
        """Create a new page in the specified browser instance."""
        if instance_id not in self.instances:
            return None
            
        async with self.instance_locks[instance_id]:
            context = self.instances[instance_id]["context"]
            page_id = f"page_{uuid.uuid4().hex[:8]}"
            
            try:
                playwright_page = await context.new_page()
                page = Page(playwright_page)
                self.instances[instance_id]["pages"][page_id] = {
                    "page": page,
                    "session_id": session_id,
                    "created_at": datetime.utcnow()
                }
                return page
            except Exception as e:
                logger.error(f"Failed to create page in instance {instance_id}: {e}")
                return None
                
    async def close_page(self, instance_id: str, page_id: str):
        """Close a page in the browser instance."""
        if instance_id in self.instances and page_id in self.instances[instance_id]["pages"]:
            page_info = self.instances[instance_id]["pages"][page_id]
            try:
                await page_info["page"].close()
            except Exception as e:
                logger.error(f"Error closing page {page_id}: {e}")
            finally:
                del self.instances[instance_id]["pages"][page_id]
                
    async def cleanup(self):
        """Cleanup all browser instances."""
        for instance_id, instance_info in self.instances.items():
            try:
                # Close all pages
                for page_id in list(instance_info["pages"].keys()):
                    await self.close_page(instance_id, page_id)
                    
                # Close context
                await instance_info["context"].close()
                logger.info(f"Closed browser instance {instance_id}")
            except Exception as e:
                logger.error(f"Error cleaning up instance {instance_id}: {e}")
                
        self.instances.clear()
        self.instance_locks.clear()


class TaskExecutor:
    """Executes automation tasks using browser instances."""
    
    def __init__(self, browser_pool: BrowserInstancePool):
        self.browser_pool = browser_pool
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.task_results: Dict[str, AgentResult] = {}
        
    async def execute_task(self, task: Task) -> Dict[str, Any]:
        """Execute a single automation task."""
        logger.info(f"Executing task {task.id} of type {task.type}")
        
        try:
            # Acquire browser instance
            instance_id = await self.browser_pool.acquire_instance()
            if not instance_id:
                raise RuntimeError("No browser instances available")
                
            try:
                # Create page for the task
                page = await self.browser_pool.create_page(instance_id, task.session_id)
                if not page:
                    raise RuntimeError("Failed to create browser page")
                    
                # Execute based on task type
                result = None
                if task.type == "agent":
                    result = await self._execute_agent_task(task, page)
                elif task.type == "actor":
                    result = await self._execute_actor_task(task, page)
                elif task.type == "script":
                    result = await self._execute_script_task(task, page)
                else:
                    raise ValueError(f"Unknown task type: {task.type}")
                    
                return {
                    "status": "completed",
                    "result": result,
                    "instance_id": instance_id,
                    "completed_at": datetime.utcnow().isoformat()
                }
                
            finally:
                # Release browser instance
                await self.browser_pool.release_instance(instance_id)
                
        except Exception as e:
            logger.error(f"Task {task.id} execution failed: {e}")
            return {
                "status": "failed",
                "error": str(e),
                "completed_at": datetime.utcnow().isoformat()
            }
            
    async def _execute_agent_task(self, task: Task, page: Page) -> Dict[str, Any]:
        """Execute an agent-based task."""
        agent_config = task.payload.get("agent_config", {})
        goal = task.payload.get("goal", "")
        
        # Create agent service
        agent_service = AgentService(
            page=page,
            goal=goal,
            **agent_config
        )
        
        # Run agent
        result = await agent_service.run()
        return asdict(result) if hasattr(result, '__dataclass_fields__') else result
        
    async def _execute_actor_task(self, task: Task, page: Page) -> Dict[str, Any]:
        """Execute an actor-based task (direct page interactions)."""
        actions = task.payload.get("actions", [])
        results = []
        
        for action in actions:
            action_type = action.get("type")
            params = action.get("params", {})
            
            if action_type == "goto":
                await page.goto(params.get("url"))
            elif action_type == "click":
                await page.click(params.get("selector"))
            elif action_type == "fill":
                await page.fill(params.get("selector"), params.get("value"))
            elif action_type == "screenshot":
                screenshot = await page.screenshot()
                results.append({"type": "screenshot", "data": screenshot})
            # Add more action types as needed
            
        return {"actions_executed": len(actions), "results": results}
        
    async def _execute_script_task(self, task: Task, page: Page) -> Dict[str, Any]:
        """Execute a JavaScript script in the page."""
        script = task.payload.get("script", "")
        result = await page.evaluate(script)
        return {"script_result": result}


class Worker:
    """Distributed grid worker that executes browser automation tasks."""
    
    def __init__(
        self,
        master_url: str,
        worker_id: Optional[str] = None,
        host: str = "localhost",
        port: int = 8081,
        max_tasks: int = 5,
        browser_pool_size: int = 3,
        tags: Optional[List[str]] = None
    ):
        self.master_url = master_url
        self.worker_id = worker_id or f"worker_{uuid.uuid4().hex[:8]}"
        self.host = host
        self.port = port
        self.max_tasks = max_tasks
        self.browser_pool_size = browser_pool_size
        self.tags = tags or []
        
        # Components
        self.browser_pool = BrowserInstancePool(max_instances=browser_pool_size)
        self.task_executor = TaskExecutor(self.browser_pool)
        
        # State
        self.status = WorkerStatus.IDLE
        self.current_tasks: Dict[str, Task] = {}
        self.completed_tasks: Dict[str, Task] = {}
        self.session_affinities: Dict[str, SessionAffinity] = {}
        
        # Communication
        self.ws_connection = None
        self.http_session = None
        self.heartbeat_task = None
        self.task_poll_task = None
        
        # Configuration
        self.heartbeat_interval = 30  # seconds
        self.task_poll_interval = 5   # seconds
        self.max_task_age = timedelta(hours=1)
        
    async def start(self):
        """Start the worker."""
        logger.info(f"Starting worker {self.worker_id}")
        
        try:
            # Initialize browser pool
            await self.browser_pool.initialize()
            
            # Create HTTP session
            self.http_session = aiohttp.ClientSession()
            
            # Register with master
            await self.register()
            
            # Start background tasks
            self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            self.task_poll_task = asyncio.create_task(self._task_poll_loop())
            
            # Start WebSocket connection for real-time updates
            await self._connect_websocket()
            
        except Exception as e:
            logger.error(f"Failed to start worker: {e}")
            await self.stop()
            raise
            
    async def stop(self):
        """Stop the worker gracefully."""
        logger.info(f"Stopping worker {self.worker_id}")
        
        # Update status
        self.status = WorkerStatus.OFFLINE
        
        # Cancel background tasks
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
        if self.task_poll_task:
            self.task_poll_task.cancel()
            
        # Wait for current tasks to complete (with timeout)
        if self.current_tasks:
            logger.info(f"Waiting for {len(self.current_tasks)} tasks to complete...")
            await asyncio.sleep(10)  # Give tasks time to finish
            
        # Close WebSocket connection
        if self.ws_connection:
            await self.ws_connection.close()
            
        # Cleanup browser pool
        await self.browser_pool.cleanup()
        
        # Close HTTP session
        if self.http_session:
            await self.http_session.close()
            
        logger.info(f"Worker {self.worker_id} stopped")
        
    async def register(self):
        """Register with the master node."""
        worker_info = WorkerInfo(
            id=self.worker_id,
            host=self.host,
            port=self.port,
            capabilities={
                "browser_types": ["chromium", "firefox", "webkit"],
                "max_concurrent_tasks": self.max_tasks,
                "browser_pool_size": self.browser_pool_size,
                "supported_task_types": ["agent", "actor", "script"],
                "version": "1.0.0"
            },
            tags=self.tags,
            browser_pool_size=self.browser_pool_size
        )
        
        try:
            async with self.http_session.post(
                f"{self.master_url}/api/workers/register",
                json=asdict(worker_info)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"Registered with master: {data}")
                    return True
                else:
                    logger.error(f"Registration failed: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Registration error: {e}")
            return False
            
    async def _connect_websocket(self):
        """Establish WebSocket connection to master for real-time communication."""
        ws_url = self.master_url.replace("http", "ws") + f"/ws/workers/{self.worker_id}"
        
        try:
            self.ws_connection = await websockets.connect(ws_url)
            logger.info(f"WebSocket connected to {ws_url}")
            
            # Start listening for messages
            asyncio.create_task(self._websocket_listener())
            
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            # Fall back to polling
            self.ws_connection = None
            
    async def _websocket_listener(self):
        """Listen for messages from master via WebSocket."""
        try:
            async for message in self.ws_connection:
                try:
                    data = json.loads(message)
                    await self._handle_websocket_message(data)
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON received: {message}")
                except Exception as e:
                    logger.error(f"Error handling WebSocket message: {e}")
        except ConnectionClosed:
            logger.info("WebSocket connection closed")
            self.ws_connection = None
            # Attempt reconnection
            await asyncio.sleep(5)
            await self._connect_websocket()
            
    async def _handle_websocket_message(self, data: Dict[str, Any]):
        """Handle incoming WebSocket messages."""
        message_type = data.get("type")
        
        if message_type == "task_assignment":
            task_data = data.get("task")
            if task_data:
                task = Task(**task_data)
                await self._execute_assigned_task(task)
                
        elif message_type == "cancel_task":
            task_id = data.get("task_id")
            if task_id in self.current_tasks:
                await self._cancel_task(task_id)
                
        elif message_type == "drain":
            await self._drain_worker()
            
        elif message_type == "ping":
            await self._send_pong()
            
    async def _heartbeat_loop(self):
        """Send periodic heartbeats to master."""
        while True:
            try:
                await asyncio.sleep(self.heartbeat_interval)
                await self._send_heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                
    async def _send_heartbeat(self):
        """Send heartbeat to master."""
        heartbeat_data = {
            "worker_id": self.worker_id,
            "status": self.status.value,
            "current_tasks": len(self.current_tasks),
            "timestamp": datetime.utcnow().isoformat(),
            "metrics": {
                "browser_instances": len(self.browser_pool.instances),
                "available_instances": self.browser_pool.available_instances.qsize()
            }
        }
        
        try:
            async with self.http_session.post(
                f"{self.master_url}/api/workers/heartbeat",
                json=heartbeat_data
            ) as response:
                if response.status != 200:
                    logger.warning(f"Heartbeat failed: {response.status}")
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")
            
    async def _task_poll_loop(self):
        """Poll master for new tasks (fallback when WebSocket not available)."""
        while True:
            try:
                await asyncio.sleep(self.task_poll_interval)
                
                # Only poll if we have capacity and WebSocket is not connected
                if (len(self.current_tasks) < self.max_tasks and 
                    self.status == WorkerStatus.IDLE and 
                    not self.ws_connection):
                    await self._poll_for_tasks()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Task poll error: {e}")
                
    async def _poll_for_tasks(self):
        """Poll master for available tasks."""
        try:
            async with self.http_session.get(
                f"{self.master_url}/api/tasks/poll",
                params={"worker_id": self.worker_id, "limit": self.max_tasks - len(self.current_tasks)}
            ) as response:
                if response.status == 200:
                    tasks_data = await response.json()
                    for task_data in tasks_data.get("tasks", []):
                        task = Task(**task_data)
                        await self._execute_assigned_task(task)
        except Exception as e:
            logger.error(f"Task poll error: {e}")
            
    async def _execute_assigned_task(self, task: Task):
        """Execute a task assigned by master."""
        if len(self.current_tasks) >= self.max_tasks:
            logger.warning(f"Worker at capacity, rejecting task {task.id}")
            await self._report_task_rejected(task.id, "Worker at capacity")
            return
            
        logger.info(f"Starting task {task.id}")
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.utcnow()
        task.worker_id = self.worker_id
        
        # Store task
        self.current_tasks[task.id] = task
        
        # Update worker status
        if len(self.current_tasks) >= self.max_tasks:
            self.status = WorkerStatus.BUSY
            
        # Execute task asynchronously
        asyncio.create_task(self._run_task(task))
        
    async def _run_task(self, task: Task):
        """Run a task and report results."""
        try:
            # Check session affinity
            if task.affinity_key and task.affinity_key in self.session_affinities:
                affinity = self.session_affinities[task.affinity_key]
                affinity.last_used = datetime.utcnow()
                # Could reuse browser context here if needed
                
            # Execute task
            result = await self.task_executor.execute_task(task)
            
            # Update task
            task.status = TaskStatus.COMPLETED if result["status"] == "completed" else TaskStatus.FAILED
            task.result = result.get("result")
            task.error = result.get("error")
            task.completed_at = datetime.utcnow()
            
            # Report result to master
            await self._report_task_result(task)
            
            # Update session affinity
            if task.affinity_key:
                self.session_affinities[task.affinity_key] = SessionAffinity(
                    session_id=task.session_id,
                    worker_id=self.worker_id,
                    last_used=datetime.utcnow()
                )
                
        except Exception as e:
            logger.error(f"Task {task.id} execution failed: {e}")
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = datetime.utcnow()
            await self._report_task_result(task)
            
        finally:
            # Clean up
            if task.id in self.current_tasks:
                del self.current_tasks[task.id]
                
            # Update worker status
            if len(self.current_tasks) < self.max_tasks:
                self.status = WorkerStatus.IDLE
                
            # Move to completed tasks (with cleanup)
            self.completed_tasks[task.id] = task
            self._cleanup_old_tasks()
            
    async def _report_task_result(self, task: Task):
        """Report task result to master."""
        result_data = {
            "task_id": task.id,
            "worker_id": self.worker_id,
            "status": task.status.value,
            "result": task.result,
            "error": task.error,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "duration": (task.completed_at - task.started_at).total_seconds() if task.completed_at and task.started_at else None
        }
        
        try:
            async with self.http_session.post(
                f"{self.master_url}/api/tasks/{task.id}/result",
                json=result_data
            ) as response:
                if response.status != 200:
                    logger.error(f"Failed to report task result: {response.status}")
        except Exception as e:
            logger.error(f"Error reporting task result: {e}")
            
    async def _report_task_rejected(self, task_id: str, reason: str):
        """Report task rejection to master."""
        try:
            async with self.http_session.post(
                f"{self.master_url}/api/tasks/{task_id}/reject",
                json={"worker_id": self.worker_id, "reason": reason}
            ) as response:
                if response.status != 200:
                    logger.error(f"Failed to report task rejection: {response.status}")
        except Exception as e:
            logger.error(f"Error reporting task rejection: {e}")
            
    async def _cancel_task(self, task_id: str):
        """Cancel a running task."""
        if task_id in self.current_tasks:
            logger.info(f"Cancelling task {task_id}")
            # Task cancellation would be implemented here
            # For now, just mark as cancelled
            task = self.current_tasks[task_id]
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.utcnow()
            await self._report_task_result(task)
            del self.current_tasks[task_id]
            
    async def _drain_worker(self):
        """Drain worker (stop accepting new tasks)."""
        logger.info("Draining worker")
        self.status = WorkerStatus.DRAINING
        
    async def _send_pong(self):
        """Send pong response to master."""
        if self.ws_connection:
            try:
                await self.ws_connection.send(json.dumps({
                    "type": "pong",
                    "worker_id": self.worker_id,
                    "timestamp": datetime.utcnow().isoformat()
                }))
            except Exception as e:
                logger.error(f"Error sending pong: {e}")
                
    def _cleanup_old_tasks(self):
        """Clean up old completed tasks."""
        cutoff = datetime.utcnow() - self.max_task_age
        to_remove = []
        
        for task_id, task in self.completed_tasks.items():
            if task.completed_at and task.completed_at < cutoff:
                to_remove.append(task_id)
                
        for task_id in to_remove:
            del self.completed_tasks[task_id]
            
    async def get_status(self) -> Dict[str, Any]:
        """Get worker status information."""
        return {
            "worker_id": self.worker_id,
            "status": self.status.value,
            "current_tasks": len(self.current_tasks),
            "completed_tasks": len(self.completed_tasks),
            "browser_pool": {
                "total_instances": len(self.browser_pool.instances),
                "available_instances": self.browser_pool.available_instances.qsize()
            },
            "session_affinities": len(self.session_affinities),
            "uptime": (datetime.utcnow() - self.start_time).total_seconds() if hasattr(self, 'start_time') else 0
        }


async def create_worker_app(worker: Worker) -> web.Application:
    """Create aiohttp application for worker HTTP API."""
    app = web.Application()
    
    routes = web.RouteTableDef()
    
    @routes.get('/health')
    async def health_check(request):
        status = await worker.get_status()
        return web.json_response(status)
        
    @routes.post('/tasks/submit')
    async def submit_task(request):
        """Submit a task directly to this worker (for testing)."""
        try:
            data = await request.json()
            task = Task(**data)
            await worker._execute_assigned_task(task)
            return web.json_response({"status": "accepted", "task_id": task.id})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
            
    @routes.get('/tasks/{task_id}')
    async def get_task_status(request):
        """Get status of a specific task."""
        task_id = request.match_info['task_id']
        
        if task_id in worker.current_tasks:
            task = worker.current_tasks[task_id]
        elif task_id in worker.completed_tasks:
            task = worker.completed_tasks[task_id]
        else:
            return web.json_response({"error": "Task not found"}, status=404)
            
        return web.json_response(asdict(task))
        
    app.add_routes(routes)
    return app


async def run_worker(
    master_url: str,
    worker_id: Optional[str] = None,
    host: str = "0.0.0.0",
    port: int = 8081,
    max_tasks: int = 5,
    browser_pool_size: int = 3,
    tags: Optional[List[str]] = None
):
    """Run a worker node."""
    worker = Worker(
        master_url=master_url,
        worker_id=worker_id,
        host=host,
        port=port,
        max_tasks=max_tasks,
        browser_pool_size=browser_pool_size,
        tags=tags
    )
    
    worker.start_time = datetime.utcnow()
    
    # Create HTTP app for worker API
    app = await create_worker_app(worker)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    
    logger.info(f"Worker HTTP API started on {host}:{port}")
    
    try:
        # Start worker
        await worker.start()
        
        # Keep running
        while True:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        await worker.stop()
        await runner.cleanup()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Browser Grid Worker")
    parser.add_argument("--master-url", required=True, help="Master node URL")
    parser.add_argument("--worker-id", help="Worker ID (auto-generated if not provided)")
    parser.add_argument("--host", default="0.0.0.0", help="Worker host")
    parser.add_argument("--port", type=int, default=8081, help="Worker port")
    parser.add_argument("--max-tasks", type=int, default=5, help="Maximum concurrent tasks")
    parser.add_argument("--browser-pool-size", type=int, default=3, help="Browser instance pool size")
    parser.add_argument("--tags", nargs="+", help="Worker tags")
    
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.INFO)
    
    asyncio.run(run_worker(
        master_url=args.master_url,
        worker_id=args.worker_id,
        host=args.host,
        port=args.port,
        max_tasks=args.max_tasks,
        browser_pool_size=args.browser_pool_size,
        tags=args.tags
    ))