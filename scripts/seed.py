from app.database import get_session_factory
from app.models import Choice, Concept, Difficulty, Question, Quiz, QuizStatus

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


def main() -> None:
    session_factory = get_session_factory()
    with session_factory() as db:
        existing_titles = {title for (title,) in db.query(Quiz.title).all()}
        created = 0
        for quiz_data in QUIZZES:
            if quiz_data["title"] in existing_titles:
                continue
            quiz = Quiz(
                title=quiz_data["title"],
                description=quiz_data["description"],
                topic=quiz_data["topic"],
                difficulty=quiz_data["difficulty"],
                status=QuizStatus.PUBLISHED,
            )
            for question_index, (stem, choices, correct_index, explanation, source) in enumerate(
                quiz_data["questions"], start=1
            ):
                question = Question(
                    stem=stem,
                    explanation=explanation,
                    source_reference=source,
                    order_index=question_index,
                )
                concept_name = source.rsplit("#", maxsplit=1)[-1].replace("-", " ")
                question.concepts = [Concept(name=concept_name, topic=quiz_data["topic"])]
                for choice_index, choice_text in enumerate(choices, start=1):
                    question.choices.append(
                        Choice(
                            text=choice_text,
                            is_correct=choice_index - 1 == correct_index,
                            order_index=choice_index,
                        )
                    )
                quiz.questions.append(question)
            db.add(quiz)
            created += 1
        db.commit()
        print(f"Seed complete. Created {created} quizzes.")


if __name__ == "__main__":
    main()
