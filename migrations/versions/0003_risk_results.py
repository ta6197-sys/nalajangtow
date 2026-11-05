"""Isolated immutable Risk analysis, scenario and vessel outcomes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('risk_analysis',
        sa.Column('analysis_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('planning_run.run_id'), nullable=False),
        sa.Column('input_snapshot_hash', sa.Text(), nullable=False),
        sa.Column('analysis_fingerprint', sa.Text(), nullable=False, unique=True),
        sa.Column('result_hash', sa.Text(), nullable=False),
        sa.Column('replay_version', sa.Text(), nullable=False),
        sa.Column('contract_rule_version', sa.Text(), nullable=False),
        sa.Column('model_id', sa.Text(), nullable=False),
        sa.Column('scenario_config', postgresql.JSONB(), nullable=False),
        sa.Column('candidate_refs', postgresql.JSONB(), nullable=False),
        sa.Column('summary', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()))
    op.create_index('ix_risk_analysis_run', 'risk_analysis', ['run_id'])
    op.create_table('risk_scenario',
        sa.Column('risk_scenario_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('analysis_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('risk_analysis.analysis_id'), nullable=False),
        sa.Column('scenario_id', sa.Text(), nullable=False),
        sa.Column('payload', postgresql.JSONB(), nullable=False),
        sa.Column('content_hash', sa.Text(), nullable=False),
        sa.UniqueConstraint('analysis_id', 'scenario_id', name='uq_risk_scenario'))
    op.create_table('risk_candidate_outcome',
        sa.Column('outcome_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('analysis_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('scenario_id', sa.Text(), nullable=False),
        sa.Column('candidate_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('schedule_candidate.candidate_id'), nullable=False),
        sa.Column('label', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('cost_cents', sa.BigInteger()),
        sa.Column('failure_class', sa.Text()),
        sa.Column('payload', postgresql.JSONB(), nullable=False),
        sa.Column('content_hash', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['analysis_id', 'scenario_id'],
                                ['risk_scenario.analysis_id', 'risk_scenario.scenario_id']),
        sa.UniqueConstraint('analysis_id', 'scenario_id', 'candidate_id', name='uq_risk_outcome'),
        sa.CheckConstraint("(status = 'SUCCEEDED' AND cost_cents IS NOT NULL AND cost_cents >= 0) OR "
                           "(status IN ('FAILED','MISSING') AND cost_cents IS NULL AND failure_class IS NOT NULL)",
                           name='ck_risk_outcome_status'))
    op.create_index('ix_risk_outcome_analysis_label', 'risk_candidate_outcome',
                    ['analysis_id', 'label'])
    op.create_table('risk_vessel_result',
        sa.Column('vessel_result_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('outcome_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('risk_candidate_outcome.outcome_id'), nullable=False),
        sa.Column('call_id', sa.Text(), nullable=False),
        sa.Column('payload', postgresql.JSONB(), nullable=False),
        sa.Column('content_hash', sa.Text(), nullable=False),
        sa.UniqueConstraint('outcome_id', 'call_id', name='uq_risk_vessel_result'))


def downgrade():
    op.drop_table('risk_vessel_result')
    op.drop_index('ix_risk_outcome_analysis_label', table_name='risk_candidate_outcome')
    op.drop_table('risk_candidate_outcome')
    op.drop_table('risk_scenario')
    op.drop_index('ix_risk_analysis_run', table_name='risk_analysis')
    op.drop_table('risk_analysis')
