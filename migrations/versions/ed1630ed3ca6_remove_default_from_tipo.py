"""Remove default from tipo

Revision ID: ed1630ed3ca6
Revises: 2fe28b648fda
Create Date: 2026-03-19 14:46:11.278267

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ed1630ed3ca6'
down_revision = '2fe28b648fda'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('respostas', schema=None) as batch_op:
        batch_op.alter_column(
            'tipo',
            existing_type=sa.String(length=20),
            nullable=False,
            server_default=None
        )


def downgrade():
    with op.batch_alter_table('respostas', schema=None) as batch_op:
        batch_op.alter_column(
            'tipo',
            existing_type=sa.String(length=20),
            nullable=False,
            server_default='livre'
        )