"""add subject namespace

Revision ID: 202606120003
Revises: 202606120002
Create Date: 2026-06-12 00:03:00
"""

import re
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "202606120003"
down_revision: str | None = "202606120002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().casefold()).strip("-")
    return slug or "general"


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE quiz_status ADD VALUE IF NOT EXISTS 'archived'")

    quiz_type = sa.Enum("standard", "review", "generated", name="quiz_type")
    question_status = sa.Enum("draft", "active", "archived", name="question_status")
    quiz_type.create(bind, checkfirst=True)
    question_status.create(bind, checkfirst=True)

    op.create_table(
        "subjects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_subjects_slug"),
    )

    subjects = sa.table(
        "subjects",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("slug", sa.String),
        sa.column("description", sa.Text),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    quizzes = sa.table(
        "quizzes",
        sa.column("id", sa.Integer),
        sa.column("topic", sa.String),
        sa.column("primary_subject_id", sa.Integer),
        sa.column("quiz_type", sa.String),
    )
    concepts = sa.table(
        "concepts",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("topic", sa.String),
        sa.column("subject_id", sa.Integer),
        sa.column("slug", sa.String),
    )
    questions = sa.table(
        "questions",
        sa.column("id", sa.Integer),
        sa.column("subject_id", sa.Integer),
        sa.column("difficulty", sa.String),
        sa.column("status", sa.String),
    )
    question_concepts = sa.table(
        "question_concepts",
        sa.column("question_id", sa.Integer),
        sa.column("concept_id", sa.Integer),
        sa.column("is_primary", sa.Boolean),
        sa.column("created_at", sa.DateTime),
    )
    attempt_answers = sa.table(
        "attempt_answers",
        sa.column("id", sa.Integer),
        sa.column("question_id", sa.Integer),
        sa.column("selected_choice_id", sa.Integer),
        sa.column("question_stem_snapshot", sa.Text),
        sa.column("selected_choice_text_snapshot", sa.Text),
        sa.column("correct_choice_text_snapshot", sa.Text),
        sa.column("explanation_snapshot", sa.Text),
    )

    topic_names = {"General"}
    for (topic,) in bind.execute(sa.text("SELECT DISTINCT topic FROM quizzes")):
        if topic and topic.strip():
            topic_names.add(topic.strip())
    for (topic,) in bind.execute(sa.text("SELECT DISTINCT topic FROM concepts")):
        if topic and topic.strip():
            topic_names.add(topic.strip())

    subject_ids_by_slug: dict[str, int] = {}
    for name in sorted(topic_names, key=str.casefold):
        bind.execute(
            subjects.insert().values(
                name=name,
                slug=slugify(name),
                description="",
                created_at=now,
                updated_at=now,
            )
        )
        subject_ids_by_slug[slugify(name)] = int(
            bind.execute(
                sa.text("SELECT id FROM subjects WHERE slug = :slug"),
                {"slug": slugify(name)},
            ).scalar_one()
        )
    general_subject_id = subject_ids_by_slug["general"]

    op.add_column("quizzes", sa.Column("primary_subject_id", sa.Integer(), nullable=True))
    op.add_column(
        "quizzes",
        sa.Column("quiz_type", quiz_type, nullable=False, server_default="standard"),
    )
    if bind.dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_quizzes_primary_subject_id_subjects",
            "quizzes",
            "subjects",
            ["primary_subject_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.add_column("concepts", sa.Column("subject_id", sa.Integer(), nullable=True))
    op.add_column("concepts", sa.Column("slug", sa.String(length=180), nullable=True))
    if bind.dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_concepts_subject_id_subjects",
            "concepts",
            "subjects",
            ["subject_id"],
            ["id"],
            ondelete="CASCADE",
        )
    op.add_column("questions", sa.Column("subject_id", sa.Integer(), nullable=True))
    op.add_column(
        "questions",
        sa.Column(
            "difficulty",
            sa.Enum("beginner", "intermediate", "advanced", name="difficulty"),
            nullable=False,
            server_default="beginner",
        ),
    )
    op.add_column(
        "questions",
        sa.Column("status", question_status, nullable=False, server_default="active"),
    )
    if bind.dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_questions_subject_id_subjects",
            "questions",
            "subjects",
            ["subject_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.add_column(
        "question_concepts",
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "question_concepts",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default="1970-01-01 00:00:00",
        ),
    )
    op.add_column(
        "attempt_answers",
        sa.Column("question_stem_snapshot", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "attempt_answers",
        sa.Column("selected_choice_text_snapshot", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "attempt_answers",
        sa.Column("correct_choice_text_snapshot", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "attempt_answers",
        sa.Column("explanation_snapshot", sa.Text(), nullable=False, server_default=""),
    )
    bind.execute(question_concepts.update().values(created_at=now))

    for quiz_id, topic in bind.execute(sa.text("SELECT id, topic FROM quizzes")):
        subject_id = subject_ids_by_slug.get(slugify(topic or ""), general_subject_id)
        bind.execute(
            quizzes.update().where(quizzes.c.id == quiz_id).values(primary_subject_id=subject_id)
        )

    used_concept_slugs: set[tuple[int, str]] = set()
    for concept_id, name, topic in bind.execute(sa.text("SELECT id, name, topic FROM concepts")):
        subject_id = subject_ids_by_slug.get(slugify(topic or ""), general_subject_id)
        slug = slugify(name or "")
        if (subject_id, slug) in used_concept_slugs:
            slug = f"{slug}-{concept_id}"
        used_concept_slugs.add((subject_id, slug))
        bind.execute(
            concepts.update()
            .where(concepts.c.id == concept_id)
            .values(subject_id=subject_id, slug=slug)
        )

    subject_by_concept_id = {
        concept_id: subject_id
        for concept_id, subject_id in bind.execute(sa.text("SELECT id, subject_id FROM concepts"))
    }
    primary_subject_by_quiz_id = {
        quiz_id: subject_id
        for quiz_id, subject_id in bind.execute(
            sa.text("SELECT id, primary_subject_id FROM quizzes")
        )
    }

    for (question_id,) in bind.execute(sa.text("SELECT id FROM questions")):
        concept_subject_ids = {
            subject_by_concept_id[concept_id]
            for (concept_id,) in bind.execute(
                sa.text(
                    "SELECT concept_id FROM question_concepts WHERE question_id = :question_id"
                ),
                {"question_id": question_id},
            )
            if concept_id in subject_by_concept_id
        }
        if len(concept_subject_ids) == 1:
            subject_id = next(iter(concept_subject_ids))
        else:
            quiz_subject_ids = {
                primary_subject_by_quiz_id[quiz_id]
                for (quiz_id,) in bind.execute(
                    sa.text("SELECT quiz_id FROM quiz_questions WHERE question_id = :question_id"),
                    {"question_id": question_id},
                )
                if primary_subject_by_quiz_id.get(quiz_id)
            }
            subject_id = (
                next(iter(quiz_subject_ids)) if len(quiz_subject_ids) == 1 else general_subject_id
            )

        bind.execute(
            questions.update().where(questions.c.id == question_id).values(subject_id=subject_id)
        )

        first_concept_id = bind.execute(
            sa.text(
                """
                SELECT concept_id
                FROM question_concepts
                WHERE question_id = :question_id
                ORDER BY concept_id
                LIMIT 1
                """
            ),
            {"question_id": question_id},
        ).scalar()
        if first_concept_id is not None:
            bind.execute(
                question_concepts.update()
                .where(question_concepts.c.question_id == question_id)
                .where(question_concepts.c.concept_id == first_concept_id)
                .values(is_primary=True)
            )

    for answer_id, question_id, selected_choice_id in bind.execute(
        sa.text("SELECT id, question_id, selected_choice_id FROM attempt_answers")
    ):
        question_row = bind.execute(
            sa.text("SELECT stem, explanation FROM questions WHERE id = :question_id"),
            {"question_id": question_id},
        ).first()
        selected_text = ""
        if selected_choice_id is not None:
            selected_text = (
                bind.execute(
                    sa.text("SELECT text FROM choices WHERE id = :choice_id"),
                    {"choice_id": selected_choice_id},
                ).scalar()
                or ""
            )
        correct_text = (
            bind.execute(
                sa.text(
                    """
                    SELECT text
                    FROM choices
                    WHERE question_id = :question_id AND is_correct = true
                    """
                ),
                {"question_id": question_id},
            ).scalar()
            or ""
        )
        bind.execute(
            attempt_answers.update()
            .where(attempt_answers.c.id == answer_id)
            .values(
                question_stem_snapshot=question_row.stem if question_row else "",
                selected_choice_text_snapshot=selected_text,
                correct_choice_text_snapshot=correct_text,
                explanation_snapshot=question_row.explanation if question_row else "",
            )
        )

    with op.batch_alter_table("concepts") as batch_op:
        batch_op.alter_column("subject_id", nullable=False)
        batch_op.alter_column("slug", nullable=False)
        batch_op.drop_constraint("uq_concepts_name", type_="unique")
        batch_op.create_unique_constraint("uq_concepts_subject_slug", ["subject_id", "slug"])
        batch_op.drop_column("topic")

    with op.batch_alter_table("questions") as batch_op:
        batch_op.alter_column("subject_id", nullable=False)

    with op.batch_alter_table("quizzes") as batch_op:
        batch_op.drop_column("topic")


def downgrade() -> None:
    op.add_column(
        "quizzes",
        sa.Column("topic", sa.String(length=120), nullable=False, server_default=""),
    )
    op.add_column(
        "concepts",
        sa.Column("topic", sa.String(length=120), nullable=False, server_default=""),
    )
    op.execute(
        """
        UPDATE quizzes
        SET topic = COALESCE((
            SELECT subjects.name FROM subjects WHERE subjects.id = quizzes.primary_subject_id
        ), '')
        """
    )
    op.execute(
        """
        UPDATE concepts
        SET topic = COALESCE((
            SELECT subjects.name FROM subjects WHERE subjects.id = concepts.subject_id
        ), '')
        """
    )

    with op.batch_alter_table("concepts") as batch_op:
        batch_op.drop_constraint("uq_concepts_subject_slug", type_="unique")
        batch_op.create_unique_constraint("uq_concepts_name", ["name"])
        batch_op.drop_column("slug")
        batch_op.drop_column("subject_id")

    with op.batch_alter_table("questions") as batch_op:
        batch_op.drop_column("status")
        batch_op.drop_column("difficulty")
        batch_op.drop_column("subject_id")

    with op.batch_alter_table("quizzes") as batch_op:
        batch_op.drop_column("quiz_type")
        batch_op.drop_column("primary_subject_id")

    with op.batch_alter_table("question_concepts") as batch_op:
        batch_op.drop_column("created_at")
        batch_op.drop_column("is_primary")

    with op.batch_alter_table("attempt_answers") as batch_op:
        batch_op.drop_column("explanation_snapshot")
        batch_op.drop_column("correct_choice_text_snapshot")
        batch_op.drop_column("selected_choice_text_snapshot")
        batch_op.drop_column("question_stem_snapshot")

    op.drop_table("subjects")
    sa.Enum(name="question_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="quiz_type").drop(op.get_bind(), checkfirst=True)
