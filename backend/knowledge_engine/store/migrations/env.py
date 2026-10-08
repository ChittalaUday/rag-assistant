from alembic import context
from knowledge_engine.store.url import sqlalchemy_url
from sqlalchemy import create_engine

config = context.config
# Programmatic runs pass the URL via attributes (avoids ConfigParser '%' interpolation);
# the alembic CLI falls back to the application settings.
url = config.attributes.get("url")
if url is None:
    from knowledge_engine.config import Settings

    settings = Settings()
    url = settings.database_url
    config.attributes.setdefault("embedding_dimensions", settings.embedding_dimensions)
    config.attributes.setdefault("embedding_model", settings.embedding_model)

if context.is_offline_mode():
    context.configure(url=sqlalchemy_url(url), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(sqlalchemy_url(url))
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
