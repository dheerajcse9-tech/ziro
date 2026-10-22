"""Agent crew: consent forms per drive, Google (Gmail) credentials, student self-reported values

Revision ID: 0005_agent_crew
Revises: 0004_company_inbox
Create Date: 2026-10-22 07:03:12.380344
"""
from alembic import op
import sqlalchemy as sa
import app.db.base


revision = '0005_agent_crew'
down_revision = '0004_company_inbox'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('oauth_credentials',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=40), nullable=False),
    sa.Column('account_email', sa.String(length=255), nullable=False),
    sa.Column('refresh_token_encrypted', sa.Text(), nullable=False),
    sa.Column('scopes', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('connected_by_id', sa.Integer(), nullable=True),
    sa.Column('connected_at', app.db.base.UTCDateTime(), nullable=False),
    sa.Column('last_used_at', app.db.base.UTCDateTime(), nullable=True),
    sa.Column('created_at', app.db.base.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['connected_by_id'], ['users.id'], name=op.f('fk_oauth_credentials_connected_by_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_oauth_credentials')),
    sa.UniqueConstraint('provider', name=op.f('uq_oauth_credentials_provider'))
    )
    op.create_table('consent_forms',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('drive_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('questions', sa.JSON(), nullable=False),
    sa.Column('audience', sa.String(length=20), nullable=False),
    sa.Column('closes_at', app.db.base.UTCDateTime(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('closed_at', app.db.base.UTCDateTime(), nullable=True),
    sa.Column('created_by', sa.String(length=120), nullable=False),
    sa.Column('created_at', app.db.base.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['drive_id'], ['drives.id'], name=op.f('fk_consent_forms_drive_id_drives'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_consent_forms')),
    sa.UniqueConstraint('drive_id', name=op.f('uq_consent_forms_drive_id'))
    )
    with op.batch_alter_table('students', schema=None) as batch_op:
        batch_op.add_column(sa.Column('self_reported', sa.JSON(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('students', schema=None) as batch_op:
        batch_op.drop_column('self_reported')

    op.drop_table('consent_forms')
    op.drop_table('oauth_credentials')
