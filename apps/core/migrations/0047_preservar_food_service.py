"""
Preserva o Food Service para quem já o usa.

O módulo deixou de ser universal e passou a ser concedido pelos segmentos
Padarias e Indústria Alimentícia. Sem esta migração, toda empresa sem
segmento — que hoje são todas — perderia mesas, comandas, KDS e cardápio
digital no dia do deploy, sem aviso.

A regra usada é "tem dado, então usa": empresa com Mesa ou Comanda
cadastrada recebe `food_service` em `modulos_extras`, que é a habilitação
manual. Assim ela continua exatamente como estava, e o admin pode tirar
depois se quiser.

Não é reversível de forma automática: desfazer removeria a habilitação
manual de quem talvez a tenha marcado à mão depois. O reverso é no-op.
"""
from django.db import migrations

CHAVE = 'food_service'


def preservar(apps, schema_editor):
    Empresa = apps.get_model('core', 'Empresa')
    Filial = apps.get_model('core', 'Filial')

    try:
        Mesa = apps.get_model('food_service', 'Mesa')
        Comanda = apps.get_model('food_service', 'Comanda')
    except LookupError:  # pragma: no cover
        # App ausente numa instalação enxuta: nada a preservar.
        return

    # Filiais com qualquer vestígio de uso do módulo.
    filiais_em_uso = set(
        Mesa.objects.values_list('filial_id', flat=True).distinct()
    ) | set(
        Comanda.objects.values_list('filial_id', flat=True).distinct()
    )
    if not filiais_em_uso:
        return

    empresas_em_uso = set(
        Filial.objects
        .filter(id__in=filiais_em_uso)
        .values_list('empresa_id', flat=True)
        .distinct()
    )

    for empresa in Empresa.objects.filter(id__in=empresas_em_uso):
        extras = list(empresa.modulos_extras or [])
        if CHAVE not in extras:
            extras.append(CHAVE)
            empresa.modulos_extras = sorted(extras)
            empresa.save(update_fields=['modulos_extras'])


def nao_desfazer(apps, schema_editor):
    """Reverso vazio — ver o docstring do módulo."""


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0046_permissao_modulo_moda'),
        ('food_service', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(preservar, nao_desfazer),
    ]
