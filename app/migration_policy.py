def include_schema_object(*comparison) -> bool:
    _object, name, type_, reflected, compare_to = comparison
    if not reflected or compare_to is not None:
        return True
    if type_ not in {"foreign_key_constraint", "unique_constraint"}:
        return True
    return not _migration_owned_constraint(name or "")


def _migration_owned_constraint(name: str) -> bool:
    if name in {"fk_answer_choice_question", "uq_choices_question_id"}:
        return True
    if name.startswith("fk_") and name.endswith("_workspace"):
        return True
    return name.startswith("uq_") and name.endswith("_workspace_id")
