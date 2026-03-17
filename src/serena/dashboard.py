"""Serena Dashboard API — WebSocket real-time updates via flask-socketio.

All dashboard data flows through one bidirectional WebSocket connection:
- Server→Client: task lifecycle, log messages, tool stats, config changes
- Client→Server: memory operations, task cancellation, config changes

REST endpoints kept for: static files, heartbeat, and the hooks HTTP bridge
(serena_session.py uses /save_memory, /get_memory, /heartbeat).
"""

import os
import queue
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from flask import Flask, Response, request, send_from_directory
from flask_socketio import SocketIO, emit
from pydantic import BaseModel
from sensai.util import logging

from serena.analytics import ToolUsageStats
from serena.config.serena_config import SerenaConfig, SerenaPaths
from serena.constants import SERENA_DASHBOARD_DIR
from serena.task_executor import TaskExecutor
from serena.util.logging import MemoryLogHandler

if TYPE_CHECKING:
    from serena.agent import SerenaAgent

log = logging.getLogger(__name__)

# disable Werkzeug's logging to avoid cluttering the output
logging.getLogger("werkzeug").setLevel(logging.WARNING)


class RequestLog(BaseModel):
    start_idx: int = 0


class ResponseLog(BaseModel):
    messages: list[str]
    max_idx: int
    active_project: str | None = None


class ResponseToolNames(BaseModel):
    tool_names: list[str]


class ResponseToolStats(BaseModel):
    stats: dict[str, dict[str, int]]


class ResponseConfigOverview(BaseModel):
    active_project: dict[str, str | None]
    context: dict[str, str]
    modes: list[dict[str, str]]
    active_tools: list[str]
    tool_stats_summary: dict[str, dict[str, int]]
    registered_projects: list[dict[str, str | bool]]
    available_tools: list[dict[str, str | bool]]
    available_modes: list[dict[str, str | bool]]
    available_contexts: list[dict[str, str | bool]]
    available_memories: list[str] | None
    jetbrains_mode: bool
    languages: list[str]
    encoding: str | None
    current_client: str | None


class ResponseAvailableLanguages(BaseModel):
    languages: list[str]


class RequestAddLanguage(BaseModel):
    language: str


class RequestRemoveLanguage(BaseModel):
    language: str


class RequestGetMemory(BaseModel):
    memory_name: str


class ResponseGetMemory(BaseModel):
    content: str
    memory_name: str


class RequestSaveMemory(BaseModel):
    memory_name: str
    content: str


class RequestDeleteMemory(BaseModel):
    memory_name: str


class RequestRenameMemory(BaseModel):
    old_name: str
    new_name: str


class ResponseGetSerenaConfig(BaseModel):
    content: str


class RequestSaveSerenaConfig(BaseModel):
    content: str


class RequestCancelTaskExecution(BaseModel):
    task_id: int


class QueuedExecution(BaseModel):
    task_id: int
    is_running: bool
    name: str
    finished_successfully: bool
    logged: bool

    @classmethod
    def from_task_info(cls, task_info: TaskExecutor.TaskInfo) -> Self:
        return cls(
            task_id=task_info.task_id,
            is_running=task_info.is_running,
            name=task_info.name,
            finished_successfully=task_info.finished_successfully(),
            logged=task_info.logged,
        )


class SerenaDashboardAPI:
    log = logging.getLogger(__qualname__)

    def __init__(
        self,
        memory_log_handler: MemoryLogHandler,
        tool_names: list[str],
        agent: "SerenaAgent",
        shutdown_callback: Callable[[], None] | None = None,
        tool_usage_stats: ToolUsageStats | None = None,
    ) -> None:
        self._memory_log_handler = memory_log_handler
        self._tool_names = tool_names
        self._agent = agent
        self._shutdown_callback = shutdown_callback
        self._app = Flask(__name__)
        self._socketio = SocketIO(self._app, async_mode="threading", cors_allowed_origins="*")
        self._tool_usage_stats = tool_usage_stats

        # Non-blocking event broadcast queue — socketio.emit can block on Windows
        # when called from non-Flask threads, so we funnel all emits through a
        # dedicated daemon thread that is the only caller of socketio.emit.
        self._broadcast_queue: queue.Queue[tuple[str, dict]] = queue.Queue()
        self._broadcast_thread = threading.Thread(target=self._process_broadcast_queue, daemon=True)
        self._broadcast_thread.start()

        self._setup_routes()
        self._setup_socket_events()

        # Register callbacks for real-time push
        self._memory_log_handler.add_emit_callback(self._on_log_message)

    @property
    def memory_log_handler(self) -> MemoryLogHandler:
        return self._memory_log_handler

    @property
    def socketio(self) -> SocketIO:
        return self._socketio

    def broadcast_event(self, event_name: str, data: dict) -> None:
        """Broadcast an event to all connected WebSocket clients (non-blocking).

        Puts the event on a queue processed by a dedicated thread.
        This ensures socketio.emit() never blocks the caller, which prevents
        deadlocks with the TaskExecutor lock on Windows.
        """
        try:
            self._broadcast_queue.put_nowait((event_name, data))
        except queue.Full:
            pass

    def _process_broadcast_queue(self) -> None:
        """Dedicated thread that drains the broadcast queue and calls socketio.emit."""
        while True:
            try:
                event_name, data = self._broadcast_queue.get(timeout=1)
                try:
                    self._socketio.emit(event_name, data)
                except Exception:
                    pass
                self._broadcast_queue.task_done()
            except queue.Empty:
                continue

    def _on_log_message(self, message: str) -> None:
        """Callback from MemoryLogHandler — push log message to clients."""
        self.broadcast_event("log_message", {"message": message})

    def _on_task_event(self, event_data: dict) -> None:
        """Callback from TaskExecutor — push task lifecycle event to clients."""
        self.broadcast_event("task_update", event_data)
        self._broadcast_execution_state()

    def broadcast_full_state(self) -> None:
        """Broadcast all dashboard state to connected clients.

        Called after every tool call so all sections stay in sync.
        """
        self._broadcast_config()
        self._broadcast_execution_state()

    def _broadcast_config(self) -> None:
        """Push config + tool stats to all clients."""
        try:
            config = self._get_config_overview()
            self.broadcast_event("config_update", config.model_dump())
        except Exception:
            log.debug("Failed to broadcast config_update", exc_info=True)

    def _broadcast_execution_state(self) -> None:
        """Push execution queue + last execution to all clients."""
        try:
            current_tasks = self._agent.get_current_tasks()
            executions = [QueuedExecution.from_task_info(t).model_dump() for t in current_tasks]
            last = self._agent.get_last_executed_task()
            last_execution = QueuedExecution.from_task_info(last).model_dump() if last else None
            self.broadcast_event("execution_state", {
                "queued_executions": executions,
                "last_execution": last_execution,
            })
        except Exception:
            log.debug("Failed to broadcast execution_state", exc_info=True)

    def _setup_socket_events(self) -> None:
        """Register WebSocket event handlers for bidirectional communication."""

        @self._socketio.on("connect")
        def handle_connect():
            """On client connect, send full initial state."""
            try:
                # Send initial log messages
                all_logs = self._memory_log_handler.get_log_messages(from_idx=0)
                project = self._agent.get_active_project()
                project_name = project.project_name if project else None
                emit("initial_logs", {
                    "messages": all_logs.messages,
                    "max_idx": all_logs.max_idx,
                    "active_project": project_name,
                })

                # Send initial tool stats
                if self._tool_usage_stats is not None:
                    emit("tool_stats", {"stats": self._tool_usage_stats.get_tool_stats_dict()})

                # Send initial config
                try:
                    config = self._get_config_overview()
                    emit("config_update", config.model_dump())
                except Exception:
                    pass

                # Send current execution queue
                current_tasks = self._agent.get_current_tasks()
                executions = [QueuedExecution.from_task_info(t).model_dump() for t in current_tasks]
                last = self._agent.get_last_executed_task()
                last_execution = QueuedExecution.from_task_info(last).model_dump() if last else None
                emit("execution_state", {
                    "queued_executions": executions,
                    "last_execution": last_execution,
                })

                # Send tool names
                emit("tool_names", {"tool_names": self._tool_names})

            except Exception as e:
                self.log.error(f"Error sending initial state: {e}")

        @self._socketio.on("save_memory")
        def handle_save_memory(data):
            try:
                req = RequestSaveMemory.model_validate(data)
                self._save_memory(req)
                self.broadcast_full_state()
                emit("action_result", {"action": "save_memory", "status": "success",
                     "message": f"Memory {req.memory_name} saved"})
            except Exception as e:
                emit("action_result", {"action": "save_memory", "status": "error", "message": str(e)})

        @self._socketio.on("delete_memory")
        def handle_delete_memory(data):
            try:
                req = RequestDeleteMemory.model_validate(data)
                self._delete_memory(req)
                self.broadcast_full_state()
                emit("action_result", {"action": "delete_memory", "status": "success",
                     "message": f"Memory {req.memory_name} deleted"})
            except Exception as e:
                emit("action_result", {"action": "delete_memory", "status": "error", "message": str(e)})

        @self._socketio.on("rename_memory")
        def handle_rename_memory(data):
            try:
                req = RequestRenameMemory.model_validate(data)
                result = self._rename_memory(req)
                self.broadcast_full_state()
                emit("action_result", {"action": "rename_memory", "status": "success", "message": result})
            except Exception as e:
                emit("action_result", {"action": "rename_memory", "status": "error", "message": str(e)})

        @self._socketio.on("get_memory")
        def handle_get_memory(data):
            try:
                req = RequestGetMemory.model_validate(data)
                result = self._get_memory(req)
                emit("memory_content", result.model_dump())
            except Exception as e:
                emit("action_result", {"action": "get_memory", "status": "error", "message": str(e)})

        @self._socketio.on("cancel_task")
        def handle_cancel_task(data):
            try:
                task_id = data.get("task_id")
                for task in self._agent.get_current_tasks():
                    if task.task_id == task_id:
                        task.cancel()
                        emit("action_result", {"action": "cancel_task", "status": "success", "was_cancelled": True})
                        return
                emit("action_result", {"action": "cancel_task", "status": "success", "was_cancelled": False,
                     "message": f"Task {task_id} not found"})
            except Exception as e:
                emit("action_result", {"action": "cancel_task", "status": "error", "message": str(e)})

        @self._socketio.on("save_config")
        def handle_save_config(data):
            try:
                req = RequestSaveSerenaConfig.model_validate(data)
                self._save_serena_config(req)
                self.broadcast_full_state()
                emit("action_result", {"action": "save_config", "status": "success"})
            except Exception as e:
                emit("action_result", {"action": "save_config", "status": "error", "message": str(e)})

        @self._socketio.on("add_language")
        def handle_add_language(data):
            try:
                req = RequestAddLanguage.model_validate(data)
                self._add_language(req)
                self.broadcast_full_state()
                emit("action_result", {"action": "add_language", "status": "success",
                     "message": f"Language {req.language} added"})
            except Exception as e:
                emit("action_result", {"action": "add_language", "status": "error", "message": str(e)})

        @self._socketio.on("remove_language")
        def handle_remove_language(data):
            try:
                req = RequestRemoveLanguage.model_validate(data)
                self._remove_language(req)
                self.broadcast_full_state()
                emit("action_result", {"action": "remove_language", "status": "success",
                     "message": f"Language {req.language} removed"})
            except Exception as e:
                emit("action_result", {"action": "remove_language", "status": "error", "message": str(e)})

        @self._socketio.on("clear_logs")
        def handle_clear_logs():
            self._memory_log_handler.clear_log_messages()
            emit("action_result", {"action": "clear_logs", "status": "success"})

        @self._socketio.on("clear_tool_stats")
        def handle_clear_tool_stats():
            self._clear_tool_stats()
            emit("action_result", {"action": "clear_tool_stats", "status": "success"})

        @self._socketio.on("request_config")
        def handle_request_config():
            """Client requests fresh config (e.g., on tab switch)."""
            try:
                config = self._get_config_overview()
                emit("config_update", config.model_dump())
            except Exception as e:
                emit("action_result", {"action": "request_config", "status": "error", "message": str(e)})

        @self._socketio.on("request_tool_stats")
        def handle_request_tool_stats():
            """Client requests fresh tool stats."""
            if self._tool_usage_stats is not None:
                emit("tool_stats", {"stats": self._tool_usage_stats.get_tool_stats_dict()})

        @self._socketio.on("request_execution_state")
        def handle_request_execution_state():
            """Client requests fresh execution queue + last execution."""
            try:
                current_tasks = self._agent.get_current_tasks()
                executions = [QueuedExecution.from_task_info(t).model_dump() for t in current_tasks]
                last = self._agent.get_last_executed_task()
                last_execution = QueuedExecution.from_task_info(last).model_dump() if last else None
                emit("execution_state", {
                    "queued_executions": executions,
                    "last_execution": last_execution,
                })
            except Exception:
                log.debug("Failed to handle request_execution_state", exc_info=True)

        @self._socketio.on("mark_news_read")
        def handle_mark_news_read(data):
            try:
                news_snippet_id = int(data.get("news_snippet_id"))
                news_snippet_id_file = SerenaPaths().news_snippet_id_file
                with open(news_snippet_id_file, "w", encoding="utf-8") as f:
                    f.write(str(news_snippet_id))
                emit("action_result", {"action": "mark_news_read", "status": "success"})
            except Exception as e:
                emit("action_result", {"action": "mark_news_read", "status": "error", "message": str(e)})

    def _setup_routes(self) -> None:
        """HTTP routes — static files, heartbeat, and backward-compatible REST endpoints."""
        # Static files
        @self._app.route("/dashboard/<path:filename>")
        def serve_dashboard(filename: str) -> Response:
            return send_from_directory(SERENA_DASHBOARD_DIR, filename)

        @self._app.route("/dashboard/")
        def serve_dashboard_index() -> Response:
            return send_from_directory(SERENA_DASHBOARD_DIR, "index.html")

        @self._app.route("/heartbeat", methods=["GET"])
        def get_heartbeat() -> dict[str, Any]:
            return {"status": "alive"}

        # REST endpoints for hooks HTTP bridge (serena_session.py)

        @self._app.route("/shutdown", methods=["PUT"])
        def shutdown() -> dict[str, str]:
            self._shutdown()
            return {"status": "shutting down"}

        @self._app.route("/get_available_languages", methods=["GET"])
        def get_available_languages() -> dict[str, Any]:
            return self._get_available_languages().model_dump()

        @self._app.route("/add_language", methods=["POST"])
        def add_language() -> dict[str, str]:
            request_data = request.get_json()
            if not request_data:
                return {"status": "error", "message": "No data provided"}
            try:
                self._add_language(RequestAddLanguage.model_validate(request_data))
                return {"status": "success", "message": "Language added"}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        @self._app.route("/remove_language", methods=["POST"])
        def remove_language() -> dict[str, str]:
            request_data = request.get_json()
            if not request_data:
                return {"status": "error", "message": "No data provided"}
            try:
                self._remove_language(RequestRemoveLanguage.model_validate(request_data))
                return {"status": "success", "message": "Language removed"}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        @self._app.route("/get_memory", methods=["POST"])
        def get_memory() -> dict[str, Any]:
            request_data = request.get_json()
            if not request_data:
                return {"status": "error", "message": "No data provided"}
            try:
                result = self._get_memory(RequestGetMemory.model_validate(request_data))
                return result.model_dump()
            except Exception as e:
                return {"status": "error", "message": str(e)}

        @self._app.route("/save_memory", methods=["POST"])
        def save_memory() -> dict[str, str]:
            request_data = request.get_json()
            if not request_data:
                return {"status": "error", "message": "No data provided"}
            try:
                self._save_memory(RequestSaveMemory.model_validate(request_data))
                self.broadcast_full_state()
                return {"status": "success", "message": "Memory saved"}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        @self._app.route("/delete_memory", methods=["POST"])
        def delete_memory() -> dict[str, str]:
            request_data = request.get_json()
            if not request_data:
                return {"status": "error", "message": "No data provided"}
            try:
                self._delete_memory(RequestDeleteMemory.model_validate(request_data))
                self.broadcast_full_state()
                return {"status": "success", "message": "Memory deleted"}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        @self._app.route("/rename_memory", methods=["POST"])
        def rename_memory() -> dict[str, str]:
            request_data = request.get_json()
            if not request_data:
                return {"status": "error", "message": "No data provided"}
            try:
                result = self._rename_memory(RequestRenameMemory.model_validate(request_data))
                self.broadcast_full_state()
                return {"status": "success", "message": result}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        @self._app.route("/get_serena_config", methods=["GET"])
        def get_serena_config() -> dict[str, Any]:
            try:
                return self._get_serena_config().model_dump()
            except Exception as e:
                return {"status": "error", "message": str(e)}

        @self._app.route("/save_serena_config", methods=["POST"])
        def save_serena_config() -> dict[str, str]:
            request_data = request.get_json()
            if not request_data:
                return {"status": "error", "message": "No data provided"}
            try:
                self._save_serena_config(RequestSaveSerenaConfig.model_validate(request_data))
                self.broadcast_full_state()
                return {"status": "success", "message": "Config saved"}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        # /queued_task_executions, /cancel_task_execution, /last_execution
        # removed — WebSocket execution_state and cancel_task handle these

        @self._app.route("/news_snippet_ids", methods=["GET"])
        def get_news_snippet_ids() -> dict[str, str | list[int]]:
            try:
                all_news_files = (Path(SERENA_DASHBOARD_DIR) / "news").glob("*.html")
                all_news_ids = [int(f.stem) for f in all_news_files]
                config_date = SerenaConfig.get_config_file_creation_date()
                if config_date is None:
                    return {"news_snippet_ids": [], "status": "success"}
                config_date_int = int(config_date.strftime("%Y%m%d"))
                post_install = [nid for nid in all_news_ids if nid >= config_date_int]
                news_file = SerenaPaths().news_snippet_id_file
                if not os.path.exists(news_file):
                    return {"news_snippet_ids": post_install, "status": "success"}
                with open(news_file, encoding="utf-8") as f:
                    last_read = int(f.read().strip())
                unread = [nid for nid in post_install if nid > last_read]
                return {"news_snippet_ids": unread, "status": "success"}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        @self._app.route("/mark_news_snippet_as_read", methods=["POST"])
        def mark_news_snippet_as_read() -> dict[str, str]:
            try:
                request_data = request.get_json()
                news_id = int(request_data.get("news_snippet_id"))
                news_file = SerenaPaths().news_snippet_id_file
                with open(news_file, "w", encoding="utf-8") as f:
                    f.write(str(news_id))
                return {"status": "success"}
            except Exception as e:
                return {"status": "error", "message": str(e)}

    # --- Internal helper methods ---

    def _get_log_messages(self, request_log: RequestLog) -> ResponseLog:
        messages = self._memory_log_handler.get_log_messages(from_idx=request_log.start_idx)
        project = self._agent.get_active_project()
        project_name = project.project_name if project else None
        return ResponseLog(messages=messages.messages, max_idx=messages.max_idx, active_project=project_name)

    def _get_tool_names(self) -> ResponseToolNames:
        return ResponseToolNames(tool_names=self._tool_names)

    def _get_tool_stats(self) -> ResponseToolStats:
        if self._tool_usage_stats is not None:
            return ResponseToolStats(stats=self._tool_usage_stats.get_tool_stats_dict())
        return ResponseToolStats(stats={})

    def _clear_tool_stats(self) -> None:
        if self._tool_usage_stats is not None:
            self._tool_usage_stats.clear()

    def _get_config_overview(self) -> ResponseConfigOverview:
        from serena.config.context_mode import SerenaAgentContext, SerenaAgentMode
        from serena.tools.tools_base import Tool

        project = self._agent.get_active_project()
        active_project_name = project.project_name if project else None
        project_info = {
            "name": active_project_name,
            "language": ", ".join([lang.value for lang in project.project_config.languages]) if project else None,
            "path": str(project.project_root) if project else None,
        }

        context = self._agent.get_context()
        context_info = {
            "name": context.name,
            "description": context.description,
            "path": SerenaAgentContext.get_path(context.name, instance=context),
        }

        modes = self._agent.get_active_modes()
        modes_info = [
            {"name": mode.name, "description": mode.description, "path": SerenaAgentMode.get_path(mode.name, instance=mode)}
            for mode in modes
        ]
        active_mode_names = [mode.name for mode in modes]
        active_tools = self._agent.get_active_tool_names()

        registered_projects: list[dict[str, str | bool]] = []
        for proj in self._agent.serena_config.projects:
            registered_projects.append({
                "name": proj.project_name,
                "path": str(proj.project_root),
                "is_active": proj.project_name == active_project_name,
            })

        all_tool_names = sorted([tool.get_name_from_cls() for tool in self._agent._all_tools.values()])
        available_tools: list[dict[str, str | bool]] = [
            {"name": name, "is_active": False} for name in all_tool_names if name not in active_tools
        ]

        all_mode_names = SerenaAgentMode.list_registered_mode_names()
        available_modes: list[dict[str, str | bool]] = []
        for mode_name in all_mode_names:
            try:
                mode_path = SerenaAgentMode.get_path(mode_name)
            except FileNotFoundError:
                continue
            available_modes.append({"name": mode_name, "is_active": mode_name in active_mode_names, "path": mode_path})

        all_context_names = SerenaAgentContext.list_registered_context_names()
        available_contexts: list[dict[str, str | bool]] = []
        for context_name in all_context_names:
            try:
                context_path = SerenaAgentContext.get_path(context_name)
            except FileNotFoundError:
                continue
            available_contexts.append({"name": context_name, "is_active": context_name == context.name, "path": context_path})

        tool_stats_summary = {}
        if self._tool_usage_stats is not None:
            full_stats = self._tool_usage_stats.get_tool_stats_dict()
            tool_stats_summary = {name: {"num_calls": stats["num_times_called"]} for name, stats in full_stats.items()}

        available_memories = None
        if self._agent.tool_is_active("read_memory") and project is not None:
            available_memories = project.memories_manager.list_memories().get_full_list()

        languages = [lang.value for lang in project.project_config.languages] if project else []
        encoding = project.project_config.encoding if project else None

        return ResponseConfigOverview(
            active_project=project_info,
            context=context_info,
            modes=modes_info,
            active_tools=active_tools,
            tool_stats_summary=tool_stats_summary,
            registered_projects=registered_projects,
            available_tools=available_tools,
            available_modes=available_modes,
            available_contexts=available_contexts,
            available_memories=available_memories,
            jetbrains_mode=self._agent.get_language_backend().is_jetbrains(),
            languages=languages,
            encoding=encoding,
            current_client=Tool.get_last_tool_call_client_str(),
        )

    def _shutdown(self) -> None:
        log.info("Shutting down Serena")
        if self._shutdown_callback:
            self._shutdown_callback()
        else:
            os._exit(0)

    def _get_available_languages(self) -> ResponseAvailableLanguages:
        from solidlsp.ls_config import Language

        def run() -> ResponseAvailableLanguages:
            all_languages = [lang.value for lang in Language.iter_all(include_experimental=False)]
            project = self._agent.get_active_project()
            if project:
                current = [lang.value for lang in project.project_config.languages]
                available = [lang for lang in all_languages if lang not in current]
            else:
                available = all_languages
            return ResponseAvailableLanguages(languages=sorted(available))

        return self._agent.execute_task(run, logged=False)

    def _get_memory(self, request_get_memory: RequestGetMemory) -> ResponseGetMemory:
        def run() -> ResponseGetMemory:
            project = self._agent.get_active_project()
            if project is None:
                raise ValueError("No active project")
            content = project.memories_manager.load_memory(request_get_memory.memory_name)
            return ResponseGetMemory(content=content, memory_name=request_get_memory.memory_name)

        return self._agent.execute_task(run, logged=False)

    def _save_memory(self, request_save_memory: RequestSaveMemory) -> None:
        def run() -> None:
            project = self._agent.get_active_project()
            if project is None:
                raise ValueError("No active project")
            project.memories_manager.save_memory(request_save_memory.memory_name, request_save_memory.content, is_tool_context=False)

        self._agent.execute_task(run, logged=True, name="SaveMemory")

    def _delete_memory(self, request_delete_memory: RequestDeleteMemory) -> None:
        def run() -> None:
            project = self._agent.get_active_project()
            if project is None:
                raise ValueError("No active project")
            project.memories_manager.delete_memory(request_delete_memory.memory_name, is_tool_context=False)

        self._agent.execute_task(run, logged=True, name="DeleteMemory")

    def _rename_memory(self, request_rename_memory: RequestRenameMemory) -> str:
        def run() -> str:
            project = self._agent.get_active_project()
            if project is None:
                raise ValueError("No active project")
            return project.memories_manager.move_memory(
                request_rename_memory.old_name, request_rename_memory.new_name, is_tool_context=False
            )

        return self._agent.execute_task(run, logged=True, name="RenameMemory")

    def _get_serena_config(self) -> ResponseGetSerenaConfig:
        config_path = self._agent.serena_config.config_file_path
        if config_path is None or not os.path.exists(config_path):
            raise ValueError("Serena config file not found")
        with open(config_path, encoding="utf-8") as f:
            content = f.read()
        return ResponseGetSerenaConfig(content=content)

    def _save_serena_config(self, request_save_config: RequestSaveSerenaConfig) -> None:
        def run() -> None:
            config_path = self._agent.serena_config.config_file_path
            if config_path is None:
                raise ValueError("Serena config file path not set")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(request_save_config.content)

        self._agent.execute_task(run, logged=True, name="SaveSerenaConfig")

    def _add_language(self, request_add_language: RequestAddLanguage) -> None:
        from solidlsp.ls_config import Language
        language = Language(request_add_language.language)
        self._agent.add_language(language)

    def _remove_language(self, request_remove_language: RequestRemoveLanguage) -> None:
        from solidlsp.ls_config import Language
        language = Language(request_remove_language.language)
        self._agent.remove_language(language)

    @staticmethod
    def _find_first_free_port(start_port: int, host: str) -> int:
        port = start_port
        while port <= 65535:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.bind((host, port))
                    return port
            except OSError:
                port += 1
        raise RuntimeError(f"No free ports found starting from {start_port}")

    def run(self, host: str, port: int) -> int:
        """Runs the dashboard via SocketIO (WebSocket-enabled)."""
        from flask import cli
        cli.show_server_banner = lambda *args, **kwargs: None
        self._socketio.run(self._app, host=host, port=port, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
        return port

    def run_in_thread(self, host: str) -> tuple[threading.Thread, int]:
        port = self._find_first_free_port(0x5EDA, host)
        log.info("Starting dashboard (listen_address=%s, port=%d)", host, port)
        thread = threading.Thread(target=lambda: self.run(host=host, port=port), daemon=True)
        thread.start()
        return thread, port
