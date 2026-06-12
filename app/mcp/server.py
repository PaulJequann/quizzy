from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_session_factory
from app.mcp import question_first_tools
from app.models import Difficulty, Question, Quiz, QuizStatus, SourceDocument, Subject
from app.services import (
    concept_service,
    generation_service,
    settings_service,
    source_service,
)
from app.services.concept_service import MergeConceptsInput
from app.services.generation_service import GeneratedQuestionInput, GeneratedQuizCreateInput
from app.services.quiz_service import slugify

GENERATION_GUIDANCE = """
Quizzy does not perform AI inference. The MCP client/agent is responsible for reading
source documents, deciding subject/concept/difficulty metadata, creating questions, and
choosing which questions belong in a quiz.

Required question-writing contract:
- Every question must be grounded in an ingested source document or explicit source reference.
- Each multiple-choice question must have exactly four choices and exactly one correct answer.
- Distractor Control: distractors must represent known common misconceptions, adjacent
  confusions, or plausible mistakes about the same concept. Do not use random answers,
  joke answers, absurd options, or factually unrelated items outside the domain context.
- Distractors should be similar in type, specificity, and style to the correct answer so
  the question tests understanding instead of pattern matching.
- Explanations should explain why the correct answer is right and, when useful, why the
  most tempting distractor is wrong.

Concept granularity rules:
- Concepts are durable learning objectives, not one-off facts, edge cases, protocols, timings,
  or question titles.
- Reuse an existing concept whenever the question is testing the same skill in narrower wording.
- A single source note should usually produce 3-6 concepts total, even when it yields more
  questions. A question should normally tag 1 primary concept and never more than 2 concepts.
- Create a new concept only when it is likely to support at least 3 future questions or reviews.
- Prefer "Route 53 health checks" over narrower labels like "HTTP health checks",
  "endpoint health check timing", or "HTTPS certificate gotcha" unless the narrower concept is
  independently reusable across notes.

Recommended workflow:
1. Ingest Obsidian notes with ingest_obsidian_note or ingest_obsidian_directory.
2. Use prepare_generation_context to inspect app state, existing concepts, and concept policy.
3. Read relevant notes with get_source_document.
4. Decide subject, concepts, difficulty, source_reference, stems, correct answers, and distractors.
5. Write durable bank questions with create_generated_questions.
6. Optionally assemble a disposable practice session from the bank.
7. Resolve app-created question review items when maintaining the bank.
""".strip()

CONCEPT_POLICY = {
    "concept_definition": "A durable learning objective reused across questions and notes.",
    "reuse_first": True,
    "concepts_per_source_note": {"usual_min": 3, "usual_max": 6},
    "concepts_per_question": {"usual": 1, "maximum": 2},
    "new_concept_threshold": (
        "Create only when it is likely to support at least 3 future questions."
    ),
    "avoid": [
        "one-off facts",
        "question titles",
        "exam gotchas",
        "individual protocol variants",
        "timing or threshold details unless independently reusable",
    ],
    "prefer_examples": [
        {
            "prefer": "Route 53 health checks",
            "instead_of": [
                "HTTP health checks",
                "endpoint health check timing",
                "HTTPS certificate gotcha",
            ],
        }
    ],
}


def build_server() -> FastMCP:
    server = FastMCP(
        name="quizzy",
        instructions=GENERATION_GUIDANCE,
    )
    _register_read_tools(server)
    _register_source_tools(server)
    _register_write_tools(server)
    return server


def run_stdio(server: FastMCP) -> None:
    server.run(transport="stdio")


def register_tool(
    server: FastMCP,
    name: str,
    fn: Callable[..., Any],
    *,
    annotations: dict[str, Any] | None = None,
) -> None:
    tool_annotations = ToolAnnotations(**annotations) if annotations else None
    server.add_tool(Tool.from_function(fn, name=name, annotations=tool_annotations))


@contextmanager
def _session_scope():
    session_factory = get_session_factory()
    db = session_factory()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _source_document_for_context(
    db: Session, source_document_id: int | None
) -> SourceDocument | None:
    if source_document_id is None:
        return None
    return source_service.get_source_document(db, source_document_id)


def _context_subject_name(
    subject_name: str | None,
    document: SourceDocument | None,
) -> str | None:
    if subject_name and subject_name.strip():
        return subject_name.strip()
    if document and document.subject:
        return document.subject.name
    return None


def _subject_for_context(db: Session, subject_name: str | None) -> Subject | None:
    if not subject_name:
        return None
    subjects = generation_service.list_subjects(db)
    subject_id = next(
        (row["id"] for row in subjects if row["slug"] == slugify(subject_name)),
        None,
    )
    if subject_id is None:
        return None
    return db.get(Subject, subject_id)


def _subject_to_context(subject: Subject | None, requested_name: str | None) -> dict[str, Any]:
    if subject is None:
        return {
            "id": None,
            "name": requested_name,
            "slug": slugify(requested_name or "") if requested_name else None,
            "exists": False,
        }
    return {
        "id": subject.id,
        "name": subject.name,
        "slug": subject.slug,
        "description": subject.description,
        "exists": True,
    }


def _source_document_to_context(document: SourceDocument | None) -> dict[str, Any] | None:
    if document is None:
        return None
    return source_service.source_document_to_dict(document, include_content=False)


def get_generation_guidance() -> dict[str, str]:
    """Return Quizzy's agent workflow and question-writing rules."""
    return {"guidance": GENERATION_GUIDANCE}


def get_status() -> dict[str, Any]:
    """Snapshot of the Quizzy database and MCP generation defaults."""
    with _session_scope() as db:
        return {
            "subjects": db.scalar(select(func.count(Subject.id))),
            "source_documents": db.scalar(select(func.count(SourceDocument.id))),
            "quizzes": db.scalar(select(func.count(Quiz.id))),
            "questions": db.scalar(select(func.count(Question.id))),
            "default_difficulty": generation_service.default_difficulty(db).value,
        }


def list_subjects() -> dict[str, Any]:
    """List subject namespaces. Use this before assigning notes or questions to a subject."""
    with _session_scope() as db:
        return {"subjects": generation_service.list_subjects(db)}


def list_concepts(
    subject_name: Annotated[
        str | None,
        Field(description="Optional subject name used to scope concepts."),
    ] = None,
) -> dict[str, Any]:
    """List existing concepts, optionally scoped to one subject."""
    with _session_scope() as db:
        return {"concepts": generation_service.list_concepts(db, subject_name=subject_name)}


def prepare_generation_context(
    subject_name: Annotated[
        str | None,
        Field(description="Optional subject name used to scope existing concepts."),
    ] = None,
    source_document_id: Annotated[
        int | None,
        Field(description="Optional source document id the quiz will be generated from."),
    ] = None,
) -> dict[str, Any]:
    """Read app state needed before generating a quiz or creating concepts."""
    with _session_scope() as db:
        document = _source_document_for_context(db, source_document_id)
        resolved_subject_name = _context_subject_name(subject_name, document)
        subject = _subject_for_context(db, resolved_subject_name)
        return {
            "generation_settings": {
                "default_difficulty": generation_service.default_difficulty(db).value,
            },
            "subject": _subject_to_context(subject, resolved_subject_name),
            "source_document": _source_document_to_context(document),
            "existing_concepts": generation_service.list_concepts(
                db, subject_name=subject.name if subject else resolved_subject_name
            ),
            "concept_policy": CONCEPT_POLICY,
            "guidance": GENERATION_GUIDANCE,
        }


def list_quizzes() -> dict[str, Any]:
    """List quizzes with their subject, status, type, and difficulty."""
    with _session_scope() as db:
        rows = db.scalars(select(Quiz).order_by(Quiz.created_at.desc())).all()
        return {
            "quizzes": [
                {
                    "id": quiz.id,
                    "title": quiz.title,
                    "subject": quiz.primary_subject.name if quiz.primary_subject else None,
                    "difficulty": quiz.difficulty.value,
                    "quiz_type": quiz.quiz_type.value,
                    "status": quiz.status.value,
                }
                for quiz in rows
            ]
        }


def get_quiz(quiz_id: Annotated[int, Field(description="Quiz id to read.")]) -> dict[str, Any]:
    """Read a quiz and its generated-question metadata."""
    with _session_scope() as db:
        return generation_service.generated_quiz_to_dict(db, quiz_id)


def _register_read_tools(server: FastMCP) -> None:
    ro = {"readOnlyHint": True, "idempotentHint": True}
    register_tool(server, "get_generation_guidance", get_generation_guidance, annotations=ro)
    register_tool(server, "get_status", get_status, annotations=ro)
    register_tool(server, "list_subjects", list_subjects, annotations=ro)
    register_tool(server, "list_concepts", list_concepts, annotations=ro)
    register_tool(server, "prepare_generation_context", prepare_generation_context, annotations=ro)
    register_tool(
        server,
        "prepare_question_generation_context",
        prepare_generation_context,
        annotations=ro,
    )
    register_tool(server, "list_quizzes", list_quizzes, annotations=ro)
    register_tool(server, "get_quiz", get_quiz, annotations=ro)


def _register_source_tools(server: FastMCP) -> None:
    _register_source_ingest_tools(server)
    _register_source_read_tools(server)


def _register_source_ingest_tools(server: FastMCP) -> None:
    def ingest_obsidian_note(
        path: Annotated[str, Field(description="Absolute path to an Obsidian Markdown note.")],
        subject_name: Annotated[
            str | None,
            Field(description="Subject namespace to attach the note to. The agent decides this."),
        ] = None,
        vault_name: Annotated[
            str | None,
            Field(description="Optional Obsidian vault name used to build obsidian:// links."),
        ] = None,
    ) -> dict[str, Any]:
        """Ingest or update one Obsidian Markdown note as a source document."""
        with _session_scope() as db:
            report = source_service.ingest_obsidian_note(
                db, path, subject_name=subject_name, vault_name=vault_name
            )
            return {
                "created": report.created,
                "document": source_service.source_document_to_dict(report.document),
            }

    def ingest_obsidian_directory(
        path: Annotated[str, Field(description="Absolute path to an Obsidian notes directory.")],
        subject_name: Annotated[
            str | None,
            Field(description="Optional subject applied to every note in this ingest batch."),
        ] = None,
        glob: Annotated[
            str,
            Field(description="Glob used to select notes. Defaults to all Markdown recursively."),
        ] = "**/*.md",
        vault_name: Annotated[
            str | None,
            Field(description="Optional Obsidian vault name used to build obsidian:// links."),
        ] = None,
    ) -> dict[str, Any]:
        """Ingest every matching Markdown note in an Obsidian directory."""
        with _session_scope() as db:
            return source_service.ingest_obsidian_directory(
                db,
                source_service.ObsidianDirectoryIngestInput(
                    path=path,
                    subject_name=subject_name,
                    glob=glob,
                    vault_name=vault_name,
                ),
            )

    write = {"destructiveHint": False, "idempotentHint": True}
    register_tool(server, "ingest_obsidian_note", ingest_obsidian_note, annotations=write)
    register_tool(server, "ingest_obsidian_directory", ingest_obsidian_directory, annotations=write)


def _register_source_read_tools(server: FastMCP) -> None:
    def list_source_documents(
        subject_name: Annotated[
            str | None,
            Field(description="Optional subject name used to filter source documents."),
        ] = None,
        limit: Annotated[int, Field(description="Maximum number of source documents.")] = 100,
    ) -> dict[str, Any]:
        """List ingested source documents without returning full note content."""
        with _session_scope() as db:
            return {
                "documents": [
                    source_service.source_document_to_dict(document)
                    for document in source_service.list_source_documents(
                        db, subject_name=subject_name, limit=limit
                    )
                ]
            }

    def find_source_documents(
        query: Annotated[str, Field(description="Text to search in title, path, or content.")],
        limit: Annotated[int, Field(description="Maximum number of matching documents.")] = 20,
    ) -> dict[str, Any]:
        """Search ingested source documents."""
        with _session_scope() as db:
            return {
                "documents": [
                    source_service.source_document_to_dict(document)
                    for document in source_service.find_source_documents(db, query, limit=limit)
                ]
            }

    def get_source_document(
        document_id: Annotated[int, Field(description="Source document id to read.")],
    ) -> dict[str, Any]:
        """Read one ingested source document, including full Markdown content."""
        with _session_scope() as db:
            document = source_service.get_source_document(db, document_id)
            return source_service.source_document_to_dict(document, include_content=True)

    ro = {"readOnlyHint": True, "idempotentHint": True}
    register_tool(server, "list_source_documents", list_source_documents, annotations=ro)
    register_tool(server, "find_source_documents", find_source_documents, annotations=ro)
    register_tool(server, "get_source_document", get_source_document, annotations=ro)


def _register_write_tools(server: FastMCP) -> None:
    _register_settings_tools(server)
    _register_subject_write_tools(server)
    _register_concept_maintenance_tools(server)
    _register_generated_quiz_tools(server)
    _register_generated_question_tools(server)
    question_first_tools.register_question_first_tools(server, register_tool, _session_scope)


def _register_settings_tools(server: FastMCP) -> None:
    def get_generation_settings() -> dict[str, str]:
        """Read generation settings from app state."""
        with _session_scope() as db:
            return {
                "default_difficulty": settings_service.get_default_difficulty(db).value,
            }

    def set_default_difficulty(
        difficulty: Annotated[
            Difficulty,
            Field(description="Default difficulty for generated quizzes."),
        ],
    ) -> dict[str, str]:
        """Update the default difficulty for generated quizzes."""
        with _session_scope() as db:
            return settings_service.set_default_difficulty(db, difficulty)

    read = {"readOnlyHint": True, "idempotentHint": True}
    write = {"destructiveHint": False, "idempotentHint": True}
    register_tool(server, "get_generation_settings", get_generation_settings, annotations=read)
    register_tool(server, "set_default_difficulty", set_default_difficulty, annotations=write)


def _register_subject_write_tools(server: FastMCP) -> None:
    def create_subject(
        name: Annotated[str, Field(description="Subject display name.")],
        description: Annotated[str, Field(description="Optional subject description.")] = "",
    ) -> dict[str, Any]:
        """Create or update a subject namespace."""
        with _session_scope() as db:
            return generation_service.create_subject(db, name=name, description=description)

    write = {"destructiveHint": False, "idempotentHint": False}
    register_tool(server, "create_subject", create_subject, annotations=write)


def _register_concept_maintenance_tools(server: FastMCP) -> None:
    def suggest_concept_merges(
        subject_name: Annotated[str, Field(description="Subject namespace to inspect.")],
    ) -> dict[str, Any]:
        """Suggest low-use concepts that may be candidates for consolidation."""
        with _session_scope() as db:
            return concept_service.suggest_concept_merges(db, subject_name=subject_name)

    def merge_concepts(
        subject_name: Annotated[str, Field(description="Subject namespace containing concepts.")],
        target_concept_name: Annotated[
            str,
            Field(description="Concept that should receive question links."),
        ],
        source_concept_names: Annotated[
            list[str],
            Field(description="Concepts to merge into the target concept."),
        ],
        dry_run: Annotated[
            bool,
            Field(description="When true, report affected links without mutating data."),
        ] = True,
    ) -> dict[str, Any]:
        """Merge source concepts into a target concept while preserving question history."""
        with _session_scope() as db:
            return concept_service.merge_concepts(
                db,
                MergeConceptsInput(
                    subject_name=subject_name,
                    target_concept_name=target_concept_name,
                    source_concept_names=source_concept_names,
                    dry_run=dry_run,
                ),
            )

    read = {"readOnlyHint": True, "idempotentHint": True}
    write = {"destructiveHint": True, "idempotentHint": False}
    register_tool(server, "suggest_concept_merges", suggest_concept_merges, annotations=read)
    register_tool(server, "merge_concepts", merge_concepts, annotations=write)


def _register_generated_quiz_tools(server: FastMCP) -> None:
    def create_generated_quiz(
        title: Annotated[str, Field(description="Quiz title.")],
        subject_name: Annotated[str, Field(description="Subject namespace chosen by the agent.")],
        description: Annotated[str, Field(description="Quiz description.")] = "",
        difficulty: Annotated[
            Difficulty | None,
            Field(description="Quiz difficulty. Omit to use the app's current default difficulty."),
        ] = None,
        status: Annotated[
            QuizStatus,
            Field(description="Initial quiz status. Draft is the recommended default."),
        ] = QuizStatus.DRAFT,
    ) -> dict[str, Any]:
        """Create an empty generated quiz. Use add_questions_to_quiz afterward."""
        with _session_scope() as db:
            quiz = generation_service.create_generated_quiz(
                db,
                GeneratedQuizCreateInput(
                    title=title,
                    subject_name=subject_name,
                    description=description,
                    difficulty=difficulty,
                    status=status,
                ),
            )
            return generation_service.generated_quiz_to_dict(db, quiz.id)

    write = {"destructiveHint": False, "idempotentHint": False}
    register_tool(server, "create_generated_quiz", create_generated_quiz, annotations=write)


def _register_generated_question_tools(server: FastMCP) -> None:
    _register_question_bank_tools(server)
    _register_add_questions_tool(server)
    _register_generated_quiz_with_questions_tool(server)


def _register_question_bank_tools(server: FastMCP) -> None:
    def create_generated_questions(
        subject_name: Annotated[str, Field(description="Subject namespace chosen by the agent.")],
        questions: Annotated[
            list[GeneratedQuestionInput],
            Field(
                description=(
                    "Durable question-bank questions. The agent must provide exactly four "
                    "choices, one correct answer, concepts, difficulty when needed, and source "
                    "grounding."
                )
            ),
        ],
        source_document_id: Annotated[
            int | None,
            Field(description="Optional source document shared by every question in the batch."),
        ] = None,
        notes: Annotated[str, Field(description="Optional batch notes.")] = "",
    ) -> dict[str, Any]:
        """Create durable generated questions without creating a quiz."""
        with _session_scope() as db:
            return generation_service.create_generated_questions(
                db,
                generation_service.QuestionBankCreateInput(
                    subject_name=subject_name,
                    questions=questions,
                    source_document_id=source_document_id,
                    batch_metadata=generation_service.BatchMetadata(notes=notes),
                ),
            )

    def list_question_bank(
        subject_name: Annotated[
            str | None,
            Field(description="Optional subject name used to filter the bank."),
        ] = None,
    ) -> dict[str, Any]:
        """List durable questions independent of quiz containers."""
        with _session_scope() as db:
            subject = _subject_for_context(db, subject_name)
            return {
                "questions": generation_service.list_question_bank(
                    db,
                    subject_id=subject.id if subject else None,
                )
            }

    write = {"destructiveHint": False, "idempotentHint": False}
    read = {"readOnlyHint": True, "idempotentHint": True}
    register_tool(
        server,
        "create_generated_questions",
        create_generated_questions,
        annotations=write,
    )
    register_tool(server, "list_question_bank", list_question_bank, annotations=read)


def _register_add_questions_tool(server: FastMCP) -> None:
    def add_questions_to_quiz(
        quiz_id: Annotated[int, Field(description="Quiz id to append questions to.")],
        questions: Annotated[
            list[GeneratedQuestionInput],
            Field(
                description=(
                    "Generated questions. The agent must provide exactly four choices, one "
                    "correct answer, concepts, difficulty when needed, and source grounding."
                )
            ),
        ],
    ) -> dict[str, Any]:
        """Append generated questions to an existing quiz."""
        with _session_scope() as db:
            return generation_service.add_questions_to_quiz(
                db, quiz_id=quiz_id, questions=questions
            )

    write = {"destructiveHint": False, "idempotentHint": False}
    register_tool(server, "add_questions_to_quiz", add_questions_to_quiz, annotations=write)


def _register_generated_quiz_with_questions_tool(server: FastMCP) -> None:
    def create_generated_quiz_with_questions(
        title: Annotated[str, Field(description="Quiz title.")],
        subject_name: Annotated[str, Field(description="Subject namespace chosen by the agent.")],
        questions: Annotated[
            list[GeneratedQuestionInput],
            Field(
                description=(
                    "Questions generated by the agent. Distractors must be plausible common "
                    "misconceptions about the same concept, not random false statements."
                )
            ),
        ],
        description: Annotated[str, Field(description="Quiz description.")] = "",
        difficulty: Annotated[
            Difficulty | None,
            Field(description="Quiz difficulty. Omit to use the app's current default difficulty."),
        ] = None,
        status: Annotated[
            QuizStatus,
            Field(description="Initial quiz status. Draft is the recommended default."),
        ] = QuizStatus.DRAFT,
    ) -> dict[str, Any]:
        """Create a generated quiz and questions in one call."""
        with _session_scope() as db:
            return generation_service.create_generated_quiz_with_questions(
                db,
                GeneratedQuizCreateInput(
                    title=title,
                    subject_name=subject_name,
                    description=description,
                    difficulty=difficulty,
                    status=status,
                ),
                questions=questions,
            )

    write = {"destructiveHint": False, "idempotentHint": False}
    register_tool(
        server,
        "create_generated_quiz_with_questions",
        create_generated_quiz_with_questions,
        annotations=write,
    )
