"""CrewAI Agent definitions for multi-agent tasks."""

from typing import List, Optional

from crewai import Agent, LLM

from app.config import settings


def _get_llm() -> LLM:
    """Create LLM instance from project config."""
    return LLM(
        model=settings.llm_model,
        api_key=settings.llm_api_key or "",
        base_url=settings.llm_api_base,
    )


def create_researcher(tools: Optional[List] = None) -> Agent:
    """Research Analyst - deep topic research and information gathering."""
    return Agent(
        role="Research Analyst",
        goal=(
            "Conduct thorough research on the given topic, gather comprehensive "
            "information from available tools, and provide well-structured findings "
            "with sources."
        ),
        backstory=(
            "You are a senior research analyst with expertise in gathering and "
            "synthesizing information from multiple sources. You are methodical, "
            "detail-oriented, and always cite your sources."
        ),
        tools=tools or [],
        llm=_get_llm(),
        allow_delegation=False,
        verbose=True,
    )


def create_writer(tools: Optional[List] = None) -> Agent:
    """Content Writer - produce high-quality written content."""
    return Agent(
        role="Content Writer",
        goal=(
            "Transform research findings and raw data into well-structured, "
            "engaging, and accurate written content. Ensure clarity, proper "
            "formatting, and adherence to the requested style."
        ),
        backstory=(
            "You are a skilled content writer who excels at turning complex "
            "information into clear, accessible prose. You adapt your writing "
            "style to match the audience and purpose."
        ),
        tools=tools or [],
        llm=_get_llm(),
        allow_delegation=False,
        verbose=True,
    )


def create_analyst(tools: Optional[List] = None) -> Agent:
    """Data Analyst - analyze data and generate insights."""
    return Agent(
        role="Data Analyst",
        goal=(
            "Analyze provided data, identify patterns and trends, and generate "
            "actionable insights. Present findings in a clear, structured format."
        ),
        backstory=(
            "You are a data analyst with strong analytical skills. You excel at "
            "finding meaningful patterns in data and translating them into "
            "practical recommendations."
        ),
        tools=tools or [],
        llm=_get_llm(),
        allow_delegation=False,
        verbose=True,
    )


def create_coordinator(tools: Optional[List] = None) -> Agent:
    """Coordinator/Manager - orchestrate multi-agent workflows."""
    return Agent(
        role="Project Coordinator",
        goal=(
            "Coordinate the work of multiple agents, ensure task alignment, "
            "validate intermediate outputs, and synthesize final results."
        ),
        backstory=(
            "You are an experienced project coordinator who ensures all team "
            "members are aligned and tasks are completed accurately. You have "
            "the authority to delegate work and validate results."
        ),
        tools=tools or [],
        llm=_get_llm(),
        allow_delegation=True,
        verbose=True,
    )


AGENT_FACTORY = {
    "researcher": create_researcher,
    "writer": create_writer,
    "analyst": create_analyst,
    "coordinator": create_coordinator,
}


def create_agent(role: str, tools: Optional[List] = None) -> Agent:
    """Create an agent by role name."""
    factory = AGENT_FACTORY.get(role)
    if not factory:
        raise ValueError(f"Unknown agent role: {role}. Available: {list(AGENT_FACTORY.keys())}")
    return factory(tools)
