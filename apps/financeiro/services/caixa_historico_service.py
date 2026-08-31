"""Importação validada e imutável do arquivo histórico, sem efeitos financeiros."""
import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Sum
from django.core.paginator import Paginator
from apps.core.models import Filial
from apps.financeiro.models.caixa_historico import (
    DiaCaixaHistorico, LoteCaixaHistorico, MovimentoCaixaHistorico,
)

CENTAVO = Decimal('0.01')


def _dinheiro(valor):
    try:
        numero = Decimal(str(valor))
        if not numero.is_finite() or abs(numero) >= Decimal('100000000000000'):
            raise ValueError('Valor fora do limite.')
        return numero.quantize(CENTAVO, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError('Valor monetário inválido.') from exc


def validar_historico(payload, inicio, fim):
    if inicio > fim:
        raise ValueError('Período inválido.')
    if payload.get('versao') != 1 or not isinstance(payload.get('dias'), list):
        raise ValueError('Formato de histórico inválido.')
    if not re.fullmatch(r'[0-9a-f]{64}', payload.get('arquivo_sha256', '')):
        raise ValueError('SHA-256 do arquivo obrigatório.')
    if not isinstance(payload.get('arquivo'), str) or not 1 <= len(payload['arquivo']) <= 255:
        raise ValueError('Nome de arquivo inválido.')
    datas, dias = set(), []
    for original in payload['dias']:
        data = date.fromisoformat(original['data'])
        if not inicio <= data <= fim:
            raise ValueError(f'Data fora do período autorizado: {data}.')
        if data in datas:
            raise ValueError(f'Data duplicada: {data}.')
        datas.add(data)
        aba = original['aba']
        if not isinstance(aba, str) or not 1 <= len(aba) <= 100:
            raise ValueError('Aba de origem inválida.')
        if not isinstance(original.get('observacoes', []), list):
            raise ValueError('Observações devem ser uma lista.')
        itens, celulas = [], set()
        totais = {'entrada': Decimal('0'), 'saida': Decimal('0')}
        for item in original['movimentos']:
            tipo = item['tipo']
            celula = item['celula']
            if tipo not in totais or not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,6}', celula):
                raise ValueError(f'Movimento inválido em {aba}.')
            if celula in celulas:
                raise ValueError(f'Célula duplicada: {aba}!{celula}.')
            celulas.add(celula)
            bruto = str(item['valor_original'])
            valor = _dinheiro(bruto)
            if valor <= 0 or len(bruto) > 100 or not isinstance(item['descricao'], str):
                raise ValueError(f'Valor/descrição inválido: {aba}!{celula}.')
            totais[tipo] += valor
            itens.append(dict(tipo=tipo, descricao=item['descricao'], valor=valor,
                              valor_original=bruto, celula_origem=celula,
                              ordem=int(re.search(r'\d+', celula).group())))
        for tipo, campo in [('entrada', 'total_entradas'), ('saida', 'total_saidas')]:
            if totais[tipo] != _dinheiro(original[campo]):
                raise ValueError(f'Total de {tipo} divergente em {aba}.')
        canonico = dict(original, movimentos=sorted(original['movimentos'], key=lambda x: x['celula']))
        digest = hashlib.sha256(json.dumps(canonico, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        dias.append(dict(data=data, aba_origem=aba, conteudo_sha256=digest,
                         saldo_anterior_informado=_dinheiro(original['saldo_anterior']),
                         saldo_final_informado=_dinheiro(original['saldo_final']),
                         total_entradas=totais['entrada'], total_saidas=totais['saida'],
                         observacoes=original.get('observacoes', []), movimentos=itens))
    if not dias:
        raise ValueError('Nenhum dia selecionado.')
    return sorted(dias, key=lambda x: x['data'])


@transaction.atomic
def importar_historico(payload, *, filial_id, cnpj, inicio, fim, aplicar=False):
    dias = validar_historico(payload, inicio, fim)
    # Serializa importações da filial. Valida tudo antes de criar o lote.
    filial = Filial.objects.select_for_update().get(pk=filial_id)
    if re.sub(r'\D', '', filial.cnpj) != re.sub(r'\D', '', cnpj):
        raise ValueError('CNPJ não corresponde à filial de destino.')
    existentes = {d.data: d for d in DiaCaixaHistorico.objects.for_filial(filial).filter(
        data__in=[d['data'] for d in dias])}
    novos, repetidos = [], 0
    for dia in dias:
        existente = existentes.get(dia['data'])
        if existente:
            if existente.conteudo_sha256 != dia['conteudo_sha256']:
                raise ValueError(f'Histórico diferente já existe em {dia["data"]}; nenhuma substituição realizada.')
            repetidos += 1
        else:
            novos.append(dia)
    resultado = dict(aplicado=aplicar, filial=filial.pk, dias_novos=len(novos),
                     dias_ja_importados=repetidos,
                     movimentos_novos=sum(len(d['movimentos']) for d in novos),
                     inicio=str(dias[0]['data']), fim=str(dias[-1]['data']))
    if aplicar and novos:
        lote = LoteCaixaHistorico.objects.create(filial=filial, arquivo=payload['arquivo'],
                                                arquivo_sha256=payload['arquivo_sha256'])
        for dados in novos:
            movimentos = dados.pop('movimentos')
            dia = DiaCaixaHistorico.objects.create(filial=filial, lote=lote, **dados)
            MovimentoCaixaHistorico.objects.bulk_create([
                MovimentoCaixaHistorico(dia=dia, **item) for item in movimentos
            ])
        resultado['lote'] = str(lote.pk)
    return resultado


def consultar_historico(filial, inicio, fim, pagina=1):
    consulta = DiaCaixaHistorico.objects.for_filial(filial).filter(
        data__range=(inicio, fim)).order_by('data')
    totais = consulta.aggregate(entradas=Sum('total_entradas'), saidas=Sum('total_saidas'))
    paginador = Paginator(consulta.prefetch_related('movimentos'), 31)
    pagina = paginador.get_page(pagina)
    dias = list(pagina.object_list)
    for dia in dias:
        itens = list(dia.movimentos.all())
        dia.entradas = [i for i in itens if i.tipo == 'entrada']
        dia.saidas = [i for i in itens if i.tipo == 'saida']
    return {
        'dias': dias,
        'pagina': pagina,
        'quantidade_dias': paginador.count,
        'total_entradas': totais['entradas'] or Decimal('0'),
        'total_saidas': totais['saidas'] or Decimal('0'),
        # Nunca somar os fechamentos de dias distintos nem projetar datas ausentes.
        'ultimo_dia': consulta.last(),
    }
