import pytest

from app.auth.provision import FamilySetup, provision_family

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("parent_password", "learner_password"),
    [("1234567", "12345678"), ("12345678", "1234567")],
)
def test_provisioning_rejects_either_password_below_eight_characters(
    parent_password: str, learner_password: str
) -> None:
    setup = FamilySetup(
        "admin@example.com", parent_password, "learner@example.com", learner_password, "Learner"
    )
    with pytest.raises(ValueError, match="at least 8 characters"):
        provision_family(setup)
