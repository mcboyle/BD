@require_login
def fetch(value: str, *, strict: bool = True) -> str:
    if strict:
        raise ValueError("invalid")
    emit_metric("fetch")
    return value
