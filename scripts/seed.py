from collections.abc import Mapping
from typing import cast

from app.database import get_session_factory
from app.models import (
    Choice,
    Concept,
    Difficulty,
    Question,
    QuestionConcept,
    QuestionStatus,
    Quiz,
    QuizQuestion,
    QuizStatus,
)
from app.services.concept_service import normalize_concept_name
from app.services.quiz_service import get_or_create_subject, slugify

QUIZZES = [
    {
        "title": "AWS Foundations Drill",
        "description": "Core cloud concepts across compute, storage, networking, and reliability.",
        "topic": "AWS",
        "difficulty": Difficulty.BEGINNER,
        "questions": [
            (
                "Which AWS service provides object storage for files and backups?",
                ["Amazon S3", "Amazon EC2", "Amazon RDS", "AWS Lambda"],
                0,
                "Amazon S3 stores objects in buckets and is used for durable file storage.",
                "aws/storage.md#s3",
            ),
            (
                "What does an EC2 security group primarily control?",
                [
                    "Instance CPU size",
                    "Inbound and outbound traffic",
                    "AMI ownership",
                    "Billing tags",
                ],
                1,
                "Security groups act as virtual firewalls around network interfaces.",
                "aws/networking.md#security-groups",
            ),
            (
                "Which managed database service is designed for relational workloads?",
                ["Amazon RDS", "Amazon SQS", "Amazon CloudFront", "AWS IAM"],
                0,
                "RDS manages relational engines such as PostgreSQL, MySQL, and MariaDB.",
                "aws/databases.md#rds",
            ),
            (
                "What is the main purpose of IAM policies?",
                [
                    "Compress logs",
                    "Grant or deny permissions",
                    "Scale containers",
                    "Cache static assets",
                ],
                1,
                "IAM policies define which actions are allowed or denied on AWS resources.",
                "aws/security.md#iam-policies",
            ),
            (
                "Which service is commonly used as a CDN in AWS?",
                ["Amazon CloudFront", "Amazon DynamoDB", "AWS Step Functions", "Amazon EBS"],
                0,
                "CloudFront distributes content through edge locations for lower latency.",
                "aws/networking.md#cloudfront",
            ),
        ],
    },
    {
        "title": "Linux Command Line Check",
        "description": "Practical shell, permissions, process, and filesystem questions.",
        "topic": "Linux",
        "difficulty": Difficulty.INTERMEDIATE,
        "questions": [
            (
                "Which command shows running processes interactively?",
                ["tar", "top", "chmod", "df"],
                1,
                "top refreshes process and system resource information in the terminal.",
                "linux/processes.md#top",
            ),
            (
                "What does chmod 600 file.txt allow?",
                ["Owner read/write only", "Everyone execute", "Group write only", "No permissions"],
                0,
                "Mode 600 grants read and write to the owner, and nothing to group or others.",
                "linux/permissions.md#modes",
            ),
            (
                "Which command prints disk filesystem usage?",
                ["du", "df", "ps", "grep"],
                1,
                "df reports filesystem capacity and available space.",
                "linux/storage.md#df",
            ),
            (
                "What does the pipe operator do in a shell command?",
                [
                    "Runs as root",
                    "Sends stdout to another command",
                    "Deletes a file",
                    "Starts a subshell only",
                ],
                1,
                "A pipe connects the output of one command to the input of the next.",
                "linux/shell.md#pipes",
            ),
            (
                "Which file commonly defines systemd service units?",
                [
                    "/etc/systemd/system/*.service",
                    "/etc/hosts",
                    "/var/log/auth.log",
                    "/boot/grub/grub.cfg",
                ],
                0,
                "Custom systemd unit files are often placed under /etc/systemd/system.",
                "linux/systemd.md#units",
            ),
        ],
    },
    {
        "title": "Python Runtime Basics",
        "description": "Language fundamentals for packaging, data structures, errors, and typing.",
        "topic": "Python",
        "difficulty": Difficulty.BEGINNER,
        "questions": [
            (
                "Which Python data type stores key-value pairs?",
                ["list", "tuple", "dict", "set"],
                2,
                "dict maps keys to values and preserves insertion order in modern Python.",
                "python/data-structures.md#dict",
            ),
            (
                "What keyword starts exception handling for risky code?",
                ["try", "with", "yield", "lambda"],
                0,
                "try begins a block that can be paired with except, else, and finally.",
                "python/errors.md#try-except",
            ),
            (
                "Which file is the modern standard place for Python project metadata?",
                ["requirements.lock", "pyproject.toml", "setup.cache", "Pipfile.env"],
                1,
                "pyproject.toml stores standardized build-system and project metadata.",
                "python/packaging.md#pyproject",
            ),
            (
                "What does a list comprehension produce?",
                ["A new list", "A database cursor", "A module import", "A virtual environment"],
                0,
                "List comprehensions build lists from an expression and iterable.",
                "python/syntax.md#comprehensions",
            ),
            (
                "Which annotation means a value can be a string or None in Python 3.10+?",
                ["str?", "OptionalOnly[str]", "str | None", "Maybe[str]"],
                2,
                "The pipe union syntax can express nullable values as str | None.",
                "python/typing.md#unions",
            ),
        ],
    },
]


type SeedQuestion = tuple[str, list[str], int, str, str]


def _concept_from_source(source: str, subject) -> Concept:
    concept_name = source.rsplit("#", maxsplit=1)[-1].replace("-", " ")
    return Concept(
        name=concept_name,
        slug=slugify(concept_name),
        normalized_name=normalize_concept_name(
            concept_name,
            subject_name=subject.name,
        ),
        subject=subject,
    )


def _question_from_seed(
    seed_question: SeedQuestion,
    *,
    question_index: int,
    subject,
    difficulty: Difficulty,
) -> tuple[Question, QuizQuestion]:
    stem, choices, correct_index, explanation, source = seed_question
    question = Question(
        subject=subject,
        stem=stem,
        explanation=explanation,
        difficulty=difficulty,
        status=QuestionStatus.ACTIVE,
        source_reference=source,
    )
    question.concept_links = [
        QuestionConcept(
            concept=_concept_from_source(source, subject),
            is_primary=True,
        )
    ]
    for choice_index, choice_text in enumerate(choices, start=1):
        question.choices.append(
            Choice(
                text=choice_text,
                is_correct=choice_index - 1 == correct_index,
                order_index=choice_index,
            )
        )
    return question, QuizQuestion(question=question, order_index=question_index)


def _quiz_from_seed(quiz_data: Mapping[str, object], subject) -> Quiz:
    title = cast(str, quiz_data["title"])
    description = cast(str, quiz_data["description"])
    difficulty = cast(Difficulty, quiz_data["difficulty"])
    questions = cast(list[SeedQuestion], quiz_data["questions"])
    quiz = Quiz(
        title=title,
        description=description,
        primary_subject=subject,
        difficulty=difficulty,
        status=QuizStatus.PUBLISHED,
    )
    for question_index, seed_question in enumerate(questions, start=1):
        _, quiz_question = _question_from_seed(
            seed_question,
            question_index=question_index,
            subject=subject,
            difficulty=difficulty,
        )
        quiz.question_links.append(quiz_question)
    return quiz


def main() -> None:
    session_factory = get_session_factory()
    with session_factory() as db:
        existing_titles = {title for (title,) in db.query(Quiz.title).all()}
        created = 0
        for quiz_data in QUIZZES:
            title = cast(str, quiz_data["title"])
            topic = cast(str, quiz_data["topic"])
            if title in existing_titles:
                continue
            subject = get_or_create_subject(db, topic)
            quiz = _quiz_from_seed(quiz_data, subject)
            db.add(quiz)
            created += 1
        db.commit()
        print(f"Seed complete. Created {created} quizzes.")


if __name__ == "__main__":
    main()
