from alembic import context

from src.daily_planning.database import get_engine
from src.daily_planning.models import metadata


def run_migrations():
    with get_engine().connect() as connection:
        context.configure(connection=connection, target_metadata=metadata,
                          include_schemas=True, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    context.configure(dialect_name='postgresql', target_metadata=metadata,
                      literal_binds=True, include_schemas=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    run_migrations()
