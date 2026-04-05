"""add cascade delete

Revision ID: 3d398b4bf5d7
Revises: 68d0f30fec0e
Create Date: 2026-03-15 00:14:05.416111
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '3d398b4bf5d7'
down_revision = '68d0f30fec0e'
branch_labels = None
depends_on = None

def upgrade():
    # Ajusta foreign keys para usar ON DELETE CASCADE

    # Respostas → Matriculas
    op.drop_constraint('respostas_matricula_id_fkey', 'respostas', type_='foreignkey')
    op.create_foreign_key(
        'respostas_matricula_id_fkey',
        'respostas', 'matriculas',
        ['matricula_id'], ['id'],
        ondelete='CASCADE'
    )

    # Respostas → Turmas
    op.drop_constraint('respostas_turma_id_fkey', 'respostas', type_='foreignkey')
    op.create_foreign_key(
        'respostas_turma_id_fkey',
        'respostas', 'turmas',
        ['turma_id'], ['id'],
        ondelete='CASCADE'
    )

    # Matriculas → Turmas
    op.drop_constraint('matriculas_turma_id_fkey', 'matriculas', type_='foreignkey')
    op.create_foreign_key(
        'matriculas_turma_id_fkey',
        'matriculas', 'turmas',
        ['turma_id'], ['id'],
        ondelete='CASCADE'
    )

def downgrade():
    # Reverte para constraints sem cascade

    op.drop_constraint('respostas_matricula_id_fkey', 'respostas', type_='foreignkey')
    op.create_foreign_key(
        'respostas_matricula_id_fkey',
        'respostas', 'matriculas',
        ['matricula_id'], ['id']
    )

    op.drop_constraint('respostas_turma_id_fkey', 'respostas', type_='foreignkey')
    op.create_foreign_key(
        'respostas_turma_id_fkey',
        'respostas', 'turmas',
        ['turma_id'], ['id']
    )

    op.drop_constraint('matriculas_turma_id_fkey', 'matriculas', type_='foreignkey')
    op.create_foreign_key(
        'matriculas_turma_id_fkey',
        'matriculas', 'turmas',
        ['turma_id'], ['id']
    )
