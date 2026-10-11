import argparse
from dataclasses import dataclass
from getpass import getpass

from fastapi_users.password import PasswordHelper
from pydantic import EmailStr, TypeAdapter
from sqlalchemy import select

from app.auth.models import User
from app.database import get_session_factory


@dataclass(frozen=True)
class FamilySetup:
    parent_email: str
    parent_password: str
    learner_email: str
    learner_password: str
    learner_name: str


def provision_family(setup: FamilySetup) -> tuple[int, int]:
    _validate_setup(setup)
    emails = TypeAdapter(EmailStr)
    parent_email = str(emails.validate_python(setup.parent_email))
    learner_email = str(emails.validate_python(setup.learner_email))
    with get_session_factory()() as db, db.begin():
        parent = db.scalar(
            select(User).filter_by(email="legacy-parent@invalid.local").with_for_update()
        )
        if parent is None or parent.is_active:
            raise ValueError("Initial parent has already been provisioned")
        parent.email = parent_email
        parent.hashed_password = PasswordHelper().hash(setup.parent_password)
        parent.is_active = True
        learner = User(
            email=learner_email,
            hashed_password=PasswordHelper().hash(setup.learner_password),
            display_name=setup.learner_name.strip(),
            is_active=True,
            is_superuser=False,
        )
        db.add(learner)
        db.flush()
        return parent.id, learner.id


def _validate_setup(setup: FamilySetup) -> None:
    if min(len(setup.parent_password), len(setup.learner_password)) < 12:
        raise ValueError("Choose passwords with at least 12 characters")
    if setup.parent_email.casefold() == setup.learner_email.casefold():
        raise ValueError("Distinct emails are required")
    if not setup.learner_name.strip():
        raise ValueError("A learner name is required")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Activate the migrated parent and create a learner"
    )
    parser.add_argument("--parent-email", required=True)
    parser.add_argument("--learner-email", required=True)
    parser.add_argument("--learner-name", required=True)
    args = parser.parse_args()
    ids = provision_family(
        FamilySetup(
            args.parent_email,
            getpass("Parent password: "),
            args.learner_email,
            getpass("Learner password: "),
            args.learner_name,
        )
    )
    print(f"Parent workspace: {ids[0]}; learner workspace: {ids[1]}")


if __name__ == "__main__":
    main()
