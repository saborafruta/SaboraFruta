from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """
    Repara um bug de merge de branches: as migrations 0014, 0015 (rename),
    0016 e 0017 deveriam criar a tabela registros_auditoria, mas TODAS usam
    SeparateDatabaseAndState com database_operations=[] — cada uma
    presumindo que a outra branch ja havia criado a tabela de verdade.
    Resultado: o estado do Django ficou correto (o model RegistroAuditoria
    existe na app registry), mas a tabela nunca foi criada no banco,
    causando ProgrammingError: relation "registros_auditoria" does not
    exist.

    Esta migration so executa database_operations (CREATE TABLE + indices
    finais, ja com os nomes renomeados por 0017), sem tocar no estado —
    que ja esta correto e nao deve ser duplicado.
    """

    dependencies = [
        ('core', '0017_rename_registros_a_modulo'),
        ('core', '0029_alter_empresa_codigo_regime_tributario_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[],
            database_operations=[
                migrations.CreateModel(
                    name='RegistroAuditoria',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('modulo', models.CharField(choices=[('compras', 'Compras'), ('estoque', 'Estoque'), ('financeiro', 'Financeiro')], db_index=True, max_length=40)),
                        ('acao', models.CharField(choices=[('visualizar', 'Visualizar'), ('criar', 'Criar'), ('editar', 'Editar'), ('aprovar', 'Aprovar'), ('cancelar', 'Cancelar'), ('exportar', 'Exportar'), ('efetivar', 'Efetivar'), ('vincular', 'Vincular'), ('reprocessar', 'Reprocessar'), ('ajustar', 'Ajustar'), ('transferir', 'Transferir'), ('inventariar', 'Inventariar'), ('baixar_validade', 'Baixar validade')], db_index=True, max_length=40)),
                        ('objeto_tipo', models.CharField(db_index=True, max_length=80)),
                        ('objeto_id', models.BigIntegerField(db_index=True)),
                        ('objeto_descricao', models.CharField(blank=True, max_length=255)),
                        ('relacionado_tipo', models.CharField(blank=True, db_index=True, max_length=80)),
                        ('relacionado_id', models.BigIntegerField(blank=True, db_index=True, null=True)),
                        ('justificativa', models.TextField(blank=True)),
                        ('dados_anteriores', models.JSONField(blank=True, null=True)),
                        ('dados_novos', models.JSONField(blank=True, null=True)),
                        ('metadados', models.JSONField(blank=True, null=True)),
                        ('ip_acesso', models.GenericIPAddressField(blank=True, null=True)),
                        ('user_agent', models.TextField(blank=True)),
                        ('criado_em', models.DateTimeField(auto_now_add=True, db_index=True)),
                        ('filial', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='registros_auditoria', to='core.filial')),
                        ('usuario', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='registros_auditoria', to='core.usuario')),
                    ],
                    options={
                        'verbose_name': 'Registro de auditoria',
                        'verbose_name_plural': 'Registros de auditoria',
                        'db_table': 'registros_auditoria',
                        'ordering': ['-criado_em'],
                    },
                ),
                migrations.AddIndex(
                    model_name='registroauditoria',
                    index=models.Index(fields=['modulo', 'acao', '-criado_em'], name='registros_a_modulo_a33783_idx'),
                ),
                migrations.AddIndex(
                    model_name='registroauditoria',
                    index=models.Index(fields=['objeto_tipo', 'objeto_id', '-criado_em'], name='registros_a_objeto__d31499_idx'),
                ),
                migrations.AddIndex(
                    model_name='registroauditoria',
                    index=models.Index(fields=['relacionado_tipo', 'relacionado_id', '-criado_em'], name='registros_a_relacio_9886af_idx'),
                ),
                migrations.AddIndex(
                    model_name='registroauditoria',
                    index=models.Index(fields=['usuario', '-criado_em'], name='registros_a_usuario_d8fb67_idx'),
                ),
                migrations.AddIndex(
                    model_name='registroauditoria',
                    index=models.Index(fields=['filial', '-criado_em'], name='registros_a_filial__983dd1_idx'),
                ),
            ],
        ),
    ]
