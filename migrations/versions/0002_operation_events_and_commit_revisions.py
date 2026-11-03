"""record input/administrator changes and release superseded berth occupancy"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('operation_event',
        sa.Column('event_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('experiment_key', sa.Text(), nullable=False),
        sa.Column('call_id', sa.Text(), sa.ForeignKey('port_call.call_id'), nullable=False),
        sa.Column('run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('planning_run.run_id')),
        sa.Column('event_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('event_kind', sa.Text(), nullable=False),
        sa.Column('actor', sa.Text(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('before_value', postgresql.JSONB(), nullable=False),
        sa.Column('after_value', postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("event_kind IN ('CALL_UPDATED', 'CALL_CANCELLED', 'ADMIN_RESCHEDULE', 'ADMIN_DEFER')",
                           name='ck_operation_event_kind'),
        sa.CheckConstraint("length(trim(reason)) > 0", name='ck_operation_event_reason'))
    op.execute('ALTER TABLE schedule_commit DROP CONSTRAINT ex_commit_berth_overlap')
    op.execute("""ALTER TABLE schedule_commit ADD CONSTRAINT ex_commit_berth_overlap
        EXCLUDE USING gist (experiment_key WITH =, berth_id WITH =,
        tstzrange(locked_from_at, locked_until_at, '[)') WITH &&)
        WHERE (status IN ('LOCKED', 'IN_PROGRESS', 'COMPLETED'))""")


def downgrade():
    op.execute('ALTER TABLE schedule_commit DROP CONSTRAINT ex_commit_berth_overlap')
    op.execute("""ALTER TABLE schedule_commit ADD CONSTRAINT ex_commit_berth_overlap
        EXCLUDE USING gist (experiment_key WITH =, berth_id WITH =,
        tstzrange(locked_from_at, locked_until_at, '[)') WITH &&)""")
    op.drop_table('operation_event')
