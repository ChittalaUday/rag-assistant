import re


def sqlalchemy_url(url: str) -> str:
    """libpq-style URL -> SQLAlchemy URL using the psycopg (v3) driver."""
    return re.sub(r"^postgres(ql)?://", "postgresql+psycopg://", url, count=1)
