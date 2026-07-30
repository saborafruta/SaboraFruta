"""
Casa os textos legados de Rota com os cadastros reais de Motorista e Veículo.

`motorista_padrao` e `veiculo_padrao` guardavam nome/placa digitados à mão.
Esta migration tenta resolvê-los para as novas FKs.

O que NÃO casar é deixado como está, de propósito: pode ser um motorista
terceirizado, um veículo já baixado ou simplesmente um erro de digitação.
Apagar o texto nesses casos destruiria a única informação existente — as
properties `Rota.motorista_nome`/`veiculo_placa` continuam exibindo o texto
quando não há FK.

Idempotente: só preenche FK que esteja nula.
"""
from django.db import migrations


def _normalizar(texto):
    return ' '.join((texto or '').strip().split()).lower()


def _so_alfanumerico(texto):
    return ''.join(ch for ch in (texto or '') if ch.isalnum()).lower()


def vincular(apps, schema_editor):
    Rota = apps.get_model('cadastros', 'Rota')
    Motorista = apps.get_model('cadastros', 'Motorista')
    Veiculo = apps.get_model('cadastros', 'Veiculo')

    rotas = list(
        Rota.objects.filter(motorista__isnull=True, veiculo__isnull=True)
        | Rota.objects.filter(motorista__isnull=True).exclude(motorista_padrao='')
        | Rota.objects.filter(veiculo__isnull=True).exclude(veiculo_padrao='')
    )
    if not rotas:
        return

    filiais = {r.filial_id for r in rotas}

    # Índices em memória por filial: as tabelas são pequenas e isso evita uma
    # query por rota.
    motoristas = {}
    for m in Motorista.objects.filter(filial_id__in=filiais):
        motoristas.setdefault(m.filial_id, {}).setdefault(_normalizar(m.nome), m.pk)

    veiculos = {}
    for v in Veiculo.objects.filter(filial_id__in=filiais):
        veiculos.setdefault(v.filial_id, {}).setdefault(_so_alfanumerico(v.placa), v.pk)

    casadas = 0
    for rota in rotas:
        campos = []

        if rota.motorista_id is None and rota.motorista_padrao:
            pk = motoristas.get(rota.filial_id, {}).get(_normalizar(rota.motorista_padrao))
            if pk:
                rota.motorista_id = pk
                campos.append('motorista')

        if rota.veiculo_id is None and rota.veiculo_padrao:
            pk = veiculos.get(rota.filial_id, {}).get(_so_alfanumerico(rota.veiculo_padrao))
            if pk:
                rota.veiculo_id = pk
                campos.append('veiculo')

        if campos:
            rota.save(update_fields=campos)
            casadas += 1

    print(f'  [rotas] {casadas} de {len(rotas)} rota(s) vinculadas a cadastros reais')


def desvincular(apps, schema_editor):
    """Reverso: solta as FKs. O texto legado nunca foi apagado, então nada se perde."""
    Rota = apps.get_model('cadastros', 'Rota')
    Rota.objects.update(motorista=None, veiculo=None)


class Migration(migrations.Migration):

    dependencies = [
        ('cadastros', '0013_praca_territorio_rota_fks'),
    ]

    operations = [
        migrations.RunPython(vincular, desvincular),
    ]
