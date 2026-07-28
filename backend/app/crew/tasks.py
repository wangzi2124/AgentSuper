"""CrewAI Task definitions for common multi-agent workflows."""

from typing import Dict, List

from crewai import Agent, Task


def create_research_tasks(topic: str, researcher: Agent, writer: Agent) -> List[Task]:
    """Research + Write workflow: researcher gathers info, writer produces report."""
    research_task = Task(
        description=(
            f"Conduct thorough research on the following topic:\n\n"
            f"**Topic:** {topic}\n\n"
            "Requirements:\n"
            "- Gather comprehensive information from available tools\n"
            "- Identify key facts, statistics, and expert opinions\n"
            "- Note all sources for citation\n"
            "- Organize findings by subtopic"
        ),
        expected_output=(
            "A structured research summary with:\n"
            "- Key findings organized by subtopic\n"
            "- Supporting evidence and data points\n"
            "- Source citations\n"
            "- Areas of uncertainty or conflicting information"
        ),
        agent=researcher,
    )

    writing_task = Task(
        description=(
            "Based on the research findings, write a comprehensive report on the topic. "
            "The report should be well-structured, engaging, and accessible to a general audience."
        ),
        expected_output=(
            "A well-formatted report in Markdown with:\n"
            "- Clear title and introduction\n"
            "- Organized sections with headings\n"
            "- Key findings and analysis\n"
            "- Conclusion with actionable insights\n"
            "- References section"
        ),
        agent=writer,
        context=[research_task],
    )

    return [research_task, writing_task]


def create_analysis_tasks(
    data_description: str, analyst: Agent, writer: Agent
) -> List[Task]:
    """Analyze + Report workflow: analyst processes data, writer formats report."""
    analysis_task = Task(
        description=(
            f"Analyze the following data and generate insights:\n\n"
            f"**Data Description:** {data_description}\n\n"
            "Requirements:\n"
            "- Identify patterns, trends, and anomalies\n"
            "- Generate quantitative insights where possible\n"
            "- Provide actionable recommendations\n"
            "- Highlight areas requiring further investigation"
        ),
        expected_output=(
            "A structured analysis with:\n"
            "- Executive summary of key findings\n"
            "- Detailed analysis by category\n"
            "- Trend identification\n"
            "- Actionable recommendations\n"
            "- Risk assessment"
        ),
        agent=analyst,
    )

    report_task = Task(
        description=(
            "Transform the analysis into a polished, presentable report. "
            "Ensure the report is clear, well-formatted, and suitable for stakeholders."
        ),
        expected_output=(
            "A professional report in Markdown with:\n"
            "- Executive summary\n"
            "- Analysis sections with clear headings\n"
            "- Key metrics and visualizations descriptions\n"
            "- Recommendations and next steps"
        ),
        agent=writer,
        context=[analysis_task],
    )

    return [analysis_task, report_task]


def create_custom_tasks(
    task_specs: List[Dict], agents: Dict[str, Agent]
) -> List[Task]:
    """Create tasks from custom specifications.

    task_specs: list of dicts with keys:
        - description: str
        - expected_output: str
        - agent_role: str (key in agents dict)
        - context_indices: list of int (indices into returned task list)
    """
    tasks = []
    for spec in task_specs:
        agent = agents[spec["agent_role"]]
        context_tasks = [
            tasks[i] for i in spec.get("context_indices", []) if i < len(tasks)
        ]
        task = Task(
            description=spec["description"],
            expected_output=spec["expected_output"],
            agent=agent,
            context=context_tasks if context_tasks else None,
        )
        tasks.append(task)
    return tasks
