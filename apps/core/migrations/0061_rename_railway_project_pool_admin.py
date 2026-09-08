from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0060_railway_project_pool'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='railwayprojectpool',
            options={
                'db_table': 'railway_project_pools',
                'ordering': ['prioridade', 'nome'],
                'verbose_name': 'Projeto da Gestão Railway',
                'verbose_name_plural': 'Gestão Railway',
            },
        ),
    ]
