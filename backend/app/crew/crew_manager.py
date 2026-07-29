"""Crew execution manager — orchestrates CrewAI multi-agent workflows.

Can be used independently of the main RAG chat pipeline for complex tasks
like research, analysis, and report generation.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from crewai import Crew, Process

from app.crew.agents import create_agent
from app.crew.tasks import create_analysis_tasks, create_custom_tasks, create_research_tasks
from app.config import settings

logger = logging.getLogger(__name__)

TASK_CONFIGS = {
    "general": {
        "description": "General-purpose Q&A with researcher and writer",
        "agents": ["researcher", "writer"],
        "process": Process.sequential,
    },
    "research": {
        "description": "Deep research on a topic, followed by report writing",
        "agents": ["researcher", "writer"],
        "process": Process.sequential,
    },
    "analysis": {
        "description": "Data analysis with insight generation and reporting",
        "agents": ["analyst", "writer"],
        "process": Process.sequential,
    },
    "orchestrated": {
        "description": "Complex task with coordinator managing specialists",
        "agents": ["coordinator", "researcher", "analyst", "writer"],
        "process": Process.hierarchical,
    },
}


class CrewManager:
    """Manages CrewAI crew creation and execution for complex multi-agent tasks."""

    def __init__(self, plugin_loader=None, skill_loader=None):
        self._crew_tools: List = []
        self._plugin_loader = plugin_loader
        self._skill_loader = skill_loader

    def _get_tools(self) -> List:
        """Get CrewAI tools from the app's plugin/skill system."""
        if not self._crew_tools:
            from app.crew.tools import create_crewai_tools
            from app.runtime import get_plugin_loader, get_skill_loader

            plugin_loader = self._plugin_loader or get_plugin_loader()
            skill_loader = self._skill_loader or get_skill_loader()

            self._crew_tools = create_crewai_tools(
                plugin_loader=plugin_loader,
                skill_loader=skill_loader,
            )
        return self._crew_tools

    def refresh_tools(self):
        """Force refresh of bridged tools."""
        self._crew_tools = []

    async def run(
        self,
        task_type: str,
        input_data: Dict[str, Any],
        custom_tasks: Optional[List[Dict]] = None,
        event_queue: Optional[asyncio.Queue] = None,
    ) -> Dict[str, Any]:
        """Execute a CrewAI workflow.

        Args:
            task_type: One of "research", "analysis", "orchestrated", or "custom"
            input_data: Input variables for tasks
            custom_tasks: For task_type="custom"
            event_queue: Optional queue for SSE step progress events

        Returns:
            Dict with "result" and "metrics"
        """
        start = time.time()
        config = TASK_CONFIGS.get(task_type)

        if task_type == "custom" and custom_tasks:
            crew = self._build_custom_crew(custom_tasks, input_data)
        elif config:
            crew = self._build_predefined_crew(config, input_data)
        else:
            raise ValueError(
                f"Unknown task_type: {task_type}. "
                f"Available: {list(TASK_CONFIGS.keys()) + ['custom']}"
            )

        if event_queue:
            self._setup_task_event_callbacks(crew.tasks, event_queue)

        result = await asyncio.to_thread(crew.kickoff, inputs=input_data)

        elapsed = round(time.time() - start, 2)
        raw_output = result.raw if hasattr(result, "raw") else str(result)

        logger.info("Crew task '%s' completed in %.2fs", task_type, elapsed)
        return {
            "result": raw_output,
            "metrics": {
                "task_type": task_type,
                "elapsed_seconds": elapsed,
                "agents_used": config["agents"] if config else [t.get("agent_role") for t in custom_tasks or []],
            },
        }

    def _build_predefined_crew(self, config: Dict, input_data: Dict) -> Crew:
        tools = self._get_tools()
        model_override = input_data.get("model", input_data.get("model_override"))
        agents = {}
        for role in config["agents"]:
            # Manager agent in hierarchical process must NOT have tools
            agent_tools = tools if role != "coordinator" else []
            agents[role] = create_agent(role, agent_tools, model=model_override)

        # hierarchical process: coordinator is manager_agent, not in agents list
        is_hierarchical = config["process"] == Process.hierarchical
        if is_hierarchical:
            worker_roles = [r for r in config["agents"] if r != "coordinator"]
            agent_list = [agents[r] for r in worker_roles]
        else:
            agent_list = [agents[r] for r in config["agents"]]

        if "researcher" in agents and "writer" in agents and len(agent_list) == 2:
            tasks = create_research_tasks(
                input_data.get("topic", input_data.get("query", "")),
                agents["researcher"],
                agents["writer"],
            )
        elif "analyst" in agents and "writer" in agents and len(agent_list) == 2:
            tasks = create_analysis_tasks(
                input_data.get("data_description", input_data.get("query", "")),
                agents["analyst"],
                agents["writer"],
            )
        else:
            tasks = self._create_generic_tasks(agent_list, input_data)

        return Crew(
            agents=agent_list,
            tasks=tasks,
            process=config["process"],
            manager_agent=agents.get("coordinator"),
            verbose=True,
        )

    def _build_custom_crew(self, task_specs: List[Dict], input_data: Dict) -> Crew:
        tools = self._get_tools()
        model_override = input_data.get("model", input_data.get("model_override"))
        roles_needed = set(spec.get("agent_role", "researcher") for spec in task_specs)
        agents = {role: create_agent(role, tools, model=model_override) for role in roles_needed}
        tasks = create_custom_tasks(task_specs, agents)

        return Crew(
            agents=list(agents.values()),
            tasks=tasks,
            process=Process.sequential,
            verbose=True,
        )

    def _create_generic_tasks(self, agents: list, input_data: Dict) -> list:
        from crewai import Task

        query = input_data.get("topic", input_data.get("query", ""))
        tasks = []
        for i, agent in enumerate(agents):
            task = Task(
                description=f"Task {i+1}: Process the following query using your expertise:\n\n{query}",
                expected_output="Detailed analysis and findings related to the query.",
                agent=agent,
                context=tasks[-1:] if tasks else None,
            )
            tasks.append(task)
        return tasks

    def _setup_task_event_callbacks(self, tasks: list, event_queue: asyncio.Queue) -> None:
        """Chain task callbacks to emit step_start/step_end SSE events.

        Emits step_start for the first task immediately, then chains
        callbacks so each task's completion triggers step_end for itself
        and step_start for the next task.
        """
        start_times: Dict[int, float] = {}

        if not tasks:
            return

        for i, task in enumerate(tasks):
            next_task = tasks[i + 1] if i + 1 < len(tasks) else None
            task.callback = self._make_task_step_callback(
                i, task, next_task, event_queue, start_times
            )

        first_role = tasks[0].agent.role if tasks[0].agent else "unknown"
        start_times[0] = time.time()
        event_queue.put_nowait({
            "type": "step_start",
            "step_id": "crew_task_0",
            "name": f"Crew Agent: {first_role}",
            "status": "running",
            "detail": f"Starting {first_role} task",
        })

    @staticmethod
    def _make_task_step_callback(
        i: int,
        task: Any,
        next_task: Any,
        event_queue: asyncio.Queue,
        start_times: Dict[int, float],
    ):
        role = task.agent.role if task.agent else "unknown"
        name = f"Crew Agent: {role}"

        def callback(output):
            elapsed = round((time.time() - start_times.get(i, time.time())) * 1000, 1)
            detail = str(output.raw)[:200] if hasattr(output, "raw") else "Task completed"
            event_queue.put_nowait({
                "type": "step_end",
                "step_id": f"crew_task_{i}",
                "name": name,
                "status": "completed",
                "detail": detail,
                "duration_ms": elapsed,
            })
            if next_task:
                next_role = next_task.agent.role if next_task.agent else "unknown"
                next_name = f"Crew Agent: {next_role}"
                start_times[i + 1] = time.time()
                event_queue.put_nowait({
                    "type": "step_start",
                    "step_id": f"crew_task_{i + 1}",
                    "name": next_name,
                    "status": "running",
                    "detail": f"Starting {next_role} task",
                })

        return callback
