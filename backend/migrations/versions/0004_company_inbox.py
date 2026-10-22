"""Company inbox: inbound company emails; drive registration and student-list deadlines

Revision ID: 0004_company_inbox
Revises: 0003_outreach
Create Date: 2026-10-22 05:52:38.167172
"""
from alembic import op
import sqlalchemy as sa
import app.db.base


revision = '0004_company_inbox'
down_revision = '0003_outreach'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('inbound_emails',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('message_id', sa.String(length=300), nullable=False),
    sa.Column('from_address', sa.String(length=255), nullable=False),
    sa.Column('from_name', sa.String(length=200), nullable=True),
    sa.Column('to_address', sa.String(length=255), nullable=True),
    sa.Column('subject', sa.String(length=500), nullable=False),
    sa.Column('body_text', sa.Text(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('received_at', app.db.base.UTCDateTime(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('extraction', sa.JSON(), nullable=False),
    sa.Column('overrides', sa.JSON(), nullable=False),
    sa.Column('issues', sa.JSON(), nullable=False),
    sa.Column('drive_id', sa.Integer(), nullable=True),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('processed_by_id', sa.Integer(), nullable=True),
    sa.Column('processed_at', app.db.base.UTCDateTime(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', app.db.base.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['drive_id'], ['drives.id'], name=op.f('fk_inbound_emails_drive_id_drives'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['processed_by_id'], ['users.id'], name=op.f('fk_inbound_emails_processed_by_id_users')),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], name=op.f('fk_inbound_emails_run_id_agent_runs'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_inbound_emails')),
    sa.UniqueConstraint('message_id', name=op.f('uq_inbound_emails_message_id'))
    )
    with op.batch_alter_table('inbound_emails', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_inbound_emails_status'), ['status'], unique=False)

    with op.batch_alter_table('drives', schema=None) as batch_op:
        batch_op.add_column(sa.Column('registration_deadline', app.db.base.UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column('roster_due_at', app.db.base.UTCDateTime(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('drives', schema=None) as batch_op:
        batch_op.drop_column('roster_due_at')
        batch_op.drop_column('registration_deadline')

    with op.batch_alter_table('inbound_emails', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_inbound_emails_status'))

    op.drop_table('inbound_emails')
