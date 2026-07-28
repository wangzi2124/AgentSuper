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

    def __init__(self):
        self._crew_tools: List = []

    def _get_tools(self) -> List:
        """Get CrewAI tools from the app's plugin/skill system."""
        if not self._crew_tools:
            from app.crew.tools import create_crewai_tools
            from app.config import settings as _settings
            from app.plugins.loader import PluginLoader
            from app.skills.loader import SkillLoader

            plugin_loader = PluginLoader(_settings.plugins_dir)
            plugin_loader.load_all()
            skill_loader = SkillLoader(_settings.skills_dir)
            skill_loader.load_all()

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
    ) -> Dict[str, Any]:
        """Execute a CrewAI workflow.

        Args:
            task_type: One of "research", "analysis", "orchestrated", or "custom"
            input_data: Input variables for tasks
            custom_tasks: For task_type="custom"

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
        agents = {}
        for role in config["agents"]:
            agents[role] = create_agent(role, tools)

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
        roles_needed = set(spec.get("agent_role", "researcher") for spec in task_specs)
        agents = {role: create_agent(role, tools) for role in roles_needed}
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
