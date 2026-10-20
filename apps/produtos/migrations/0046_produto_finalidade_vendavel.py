from django.db import migrations, models


def criar_categoria_insumos(apps, schema_editor):
    alias = schema_editor.connection.alias
    Empresa = apps.get_model('core', 'Empresa')
    Filial = apps.get_model('core', 'Filial')
    Categoria = apps.get_model('produtos', 'CategoriaProduto')
    CategoriaFilial = apps.get_model('produtos', 'CategoriaProdutoFilial')

    for empresa in Empresa.objects.using(alias).all().iterator():
        categoria = (
            Categoria.objects.using(alias)
            .filter(
                empresa_id=empresa.pk,
                categoria_pai__isnull=True,
                nome__iexact='Insumos',
            )
            .order_by('pk')
            .first()
        )
        if categoria is None:
            categoria = Categoria.objects.using(alias).create(
                empresa_id=empresa.pk,
                filial_id=None,
                nome='Insumos',
                descricao='Matérias-primas, componentes e embalagens usados na produção.',
                ativo=True,
            )
        elif not categoria.ativo:
            categoria.ativo = True
            categoria.save(update_fields=['ativo'])
        for filial_id in (
            Filial.objects.using(alias)
            .filter(empresa_id=empresa.pk)
            .values_list('pk', flat=True)
        ):
            CategoriaFilial.objects.using(alias).update_or_create(
                categoria_id=categoria.pk,
                filial_id=filial_id,
                defaults={'ativo': True},
            )


class Migration(migrations.Migration):

    dependencies = [
        ('produtos', '0045_imagens_hortifruti_ited'),
    ]

    operations = [
        migrations.AddField(
            model_name='produto',
            name='finalidade',
            field=models.CharField(
                choices=[
                    ('revenda', 'Produto para revenda'),
                    ('materia_prima', 'Matéria-prima / insumo'),
                    ('embalagem', 'Embalagem / rótulo'),
                    ('intermediario', 'Produto intermediário'),
                    ('acabado', 'Produto acabado'),
                    ('servico', 'Serviço'),
                ],
                db_index=True,
                default='revenda',
                help_text='Define o uso operacional do cadastro no estoque e na produção.',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='produto',
            name='permite_venda',
            field=models.BooleanField(
                db_index=True,
                default=True,
                help_text=(
                    'Desmarque para insumos, embalagens e intermediários. O produto '
                    'continua disponível em compras, XML, estoque e produção.'
                ),
                verbose_name='Pode ser vendido',
            ),
        ),
        migrations.AddIndex(
            model_name='produto',
            index=models.Index(
                fields=['filial', 'permite_venda'],
                name='produtos_filial_vendavel_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='produto',
            index=models.Index(
                fields=['filial', 'finalidade'],
                name='produtos_filial_finalidade_idx',
            ),
        ),
        migrations.RunPython(criar_categoria_insumos, migrations.RunPython.noop),
    ]
