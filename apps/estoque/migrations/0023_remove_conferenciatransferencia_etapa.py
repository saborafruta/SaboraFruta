# Repara o revert do modulo de equalizacao de estoque: a migration
# 0019_conferenciatransferencia_etapa.py (apagada junto com o resto do
# modulo) ja tinha rodado em producao e deixado a coluna "etapa" fisicamente
# na tabela transferencias_conferencias, NOT NULL e sem default no banco
# (o "default" era so' do Django, aplicado uma vez no backfill). Como o
# campo saiu do model, todo INSERT feito pela ORM parou de enviar essa
# coluna -- e o Postgres rejeita com NotNullViolation. Sem esta migration
# a coluna orfa quebra qualquer criacao de ConferenciaTransferencia.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("estoque", "0022_movimentacao_apresentacao"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE transferencias_conferencias "
                "DROP COLUMN IF EXISTS etapa;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
