"""
Character Analysis Plugin

Analyzes characters and dialogues from documents in the knowledge base.
"""
from collections import Counter
from typing import Optional

PLUGIN_NAME = "character-analysis"
PLUGIN_VERSION = "0.1.0"
PLUGIN_DESCRIPTION = "Analyze characters and dialogues from documents in the knowledge base"


def _get_vector_store():
    from app.rag.plugin_bridge import get_vector_store
    return get_vector_store()


def tool_list_characters(document_id: Optional[str] = None) -> str:
    """List all characters (speakers) found in the knowledge base.

    Use this tool to get a summary of all characters who have dialogues
    in the documents. Returns character names and their dialogue counts.

    Parameters:
    - document_id: optional document ID to filter by specific document
    """
    vector_store = _get_vector_store()
    if not vector_store:
        return "Error: vector store not available (KB not initialized)"
    if vector_store.count == 0:
        return "Error: knowledge base is empty — upload documents first"

    all_docs, all_meta = vector_store.get_all()

    speaker_counts = Counter()
    for meta in all_meta:
        if meta.get("is_dialogue"):
            if document_id and meta.get("document_id") != document_id:
                continue
            speaker = meta.get("speaker", "")
            if speaker:
                speaker_counts[speaker] += 1

    if not speaker_counts:
        return "No characters with dialogues found in the knowledge base."

    lines = ["Characters found in knowledge base:\n"]
    for speaker, count in speaker_counts.most_common():
        lines.append(f"- {speaker}: {count} dialogues")

    lines.append(f"\nTotal: {len(speaker_counts)} characters")
    return "\n".join(lines)


def tool_get_character_dialogues(
    character_name: str,
    document_id: Optional[str] = None,
    limit: int = 20
) -> str:
    """Get all dialogues spoken by a specific character.

    Use this tool to retrieve all lines spoken by a character.
    Returns the character's dialogues with chapter context.

    Parameters:
    - character_name: name of the character to search for
    - document_id: optional document ID to filter by specific document
    - limit: maximum number of dialogues to return (default: 20)
    """
    vector_store = _get_vector_store()
    if not vector_store:
        return "Error: vector store not available (KB not initialized)"
    if vector_store.count == 0:
        return "Error: knowledge base is empty — upload documents first"

    all_docs, all_meta = vector_store.get_all()

    dialogues = []
    for doc, meta in zip(all_docs, all_meta):
        if meta.get("is_dialogue") and meta.get("speaker") == character_name:
            if document_id and meta.get("document_id") != document_id:
                continue
            dialogues.append({
                "text": meta.get("dialogue", ""),
                "chapter": meta.get("chapter_title", ""),
                "source": meta.get("filename", ""),
            })

    if not dialogues:
        return f"No dialogues found for character: {character_name}"

    lines = [f"Dialogues for '{character_name}' ({len(dialogues)} found):\n"]
    for i, d in enumerate(dialogues[:limit], 1):
        chapter_info = f" [{d['chapter']}]" if d['chapter'] else ""
        lines.append(f"{i}.{chapter_info} \"{d['text']}\"")

    if len(dialogues) > limit:
        lines.append(f"\n... and {len(dialogues) - limit} more dialogues")

    return "\n".join(lines)


def tool_analyze_character_interactions(
    character_name: str,
    limit: int = 10
) -> str:
    """Analyze which characters interact with a specific character.

    Use this tool to find characters who appear in the same chapters
    as the specified character, suggesting potential interactions.

    Parameters:
    - character_name: name of the character to analyze
    - limit: maximum number of related characters to return (default: 10)
    """
    vector_store = _get_vector_store()
    if not vector_store:
        return "Error: vector store not available (KB not initialized)"
    if vector_store.count == 0:
        return "Error: knowledge base is empty — upload documents first"

    all_docs, all_meta = vector_store.get_all()

    target_chapters = set()
    for meta in all_meta:
        if meta.get("is_dialogue") and meta.get("speaker") == character_name:
            chapter = meta.get("chapter_title")
            if chapter:
                target_chapters.add(chapter)

    if not target_chapters:
        return f"Character '{character_name}' not found or has no dialogues."

    related_speakers = Counter()
    for meta in all_meta:
        if meta.get("is_dialogue") and meta.get("chapter_title") in target_chapters:
            speaker = meta.get("speaker", "")
            if speaker and speaker != character_name:
                related_speakers[speaker] += 1

    lines = [
        f"Characters appearing in same chapters as '{character_name}':\n",
        f"Chapters with {character_name}: {len(target_chapters)}\n"
    ]
    for speaker, count in related_speakers.most_common(limit):
        lines.append(f"- {speaker}: {count} dialogues in shared chapters")

    return "\n".join(lines)
