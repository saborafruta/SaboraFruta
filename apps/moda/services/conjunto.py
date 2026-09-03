"""Configuração transacional do conjunto esportivo (camisa + calção)."""
from __future__ import annotations

import json

from ..models import Grade, ItemGrade, Tamanho
from .op2_estrutura import campo_multisselecao


COMPONENTES_CONJUNTO = (
    ('camisa', 'Camisa'),
    ('calcao', 'Calção'),
)


def _lista(valor):
    if isinstance(valor, list):
        return [str(item).strip() for item in valor if str(item).strip()]
    return [str(valor).strip()] if str(valor or '').strip() else []


def validar_configuracao_conjunto(valor, grupos, filial=None):
    """Valida e normaliza as duas fichas sem confiar no JSON do navegador."""
    if isinstance(valor, str):
        try:
            valor = json.loads(valor or '{}')
        except json.JSONDecodeError as erro:
            raise ValueError('Conjunto: os dados enviados estão inválidos.') from erro
    if not isinstance(valor, dict):
        raise ValueError('Conjunto: informe os dados da camisa e do calção.')

    resultado = {}
    totais = {}
    ids_grades = set()
    ids_tamanhos = set()
    for componente, label in COMPONENTES_CONJUNTO:
        origem = valor.get(componente) or {}
        grupo = grupos.get(componente) or {}
        estrutura_origem = origem.get('estrutura') or {}
        estrutura = {}
        for campo, opcoes in (grupo.get('campos') or {}).items():
            escolhas = _lista(estrutura_origem.get(campo))
            if not escolhas:
                raise ValueError(
                    f'{label} · {campo.replace("_", " ").capitalize()}: '
                    'selecione uma opção ou N/A.'
                )
            if not campo_multisselecao(campo) and len(escolhas) > 1:
                raise ValueError(f'{label}: selecione somente uma opção em {campo}.')
            if any(escolha not in opcoes for escolha in escolhas):
                raise ValueError(f'{label}: existe uma opção inválida em {campo}.')
            if len(escolhas) > 1 and 'N/A' in escolhas:
                raise ValueError(f'{label}: N/A não pode ser combinado em {campo}.')
            estrutura[campo] = escolhas if campo_multisselecao(campo) else escolhas[0]

        cor_personalizada = str(origem.get('cor_personalizada') or '').strip()
        if estrutura.get('cor') == 'COR PERSONALIZADA' and not cor_personalizada:
            raise ValueError(f'{label}: informe a cor personalizada.')
        outros = {
            str(campo): str(texto or '').strip()[:500]
            for campo, texto in (origem.get('outros') or {}).items()
            if str(texto or '').strip()
        }
        observacoes_campos = {
            str(campo): str(texto or '').strip()[:500]
            for campo, texto in (origem.get('observacoes_campos') or {}).items()
            if str(texto or '').strip()
        }
        for campo, escolha in estrutura.items():
            if 'OUTRO' in _lista(escolha) and not outros.get(campo):
                raise ValueError(
                    f'{label} · {campo.replace("_", " ").capitalize()}: '
                    'descreva a opção “Outro”.'
                )

        grades = []
        grade_por_grade = {}
        for grade_id in origem.get('grades') or []:
            grade_id = str(grade_id)
            if not grade_id.isdigit() or grade_id in grades:
                continue
            grades.append(grade_id)
            ids_grades.add(int(grade_id))
            quantidades = {}
            for tamanho_id, quantidade in (origem.get('gradePorGrade') or {}).get(grade_id, {}).items():
                tamanho_id = str(tamanho_id)
                if not tamanho_id.isdigit():
                    continue
                try:
                    quantidade = int(quantidade or 0)
                except (TypeError, ValueError):
                    raise ValueError(f'{label}: informe somente números na grade.')
                if quantidade < 0:
                    raise ValueError(f'{label}: a quantidade da grade não pode ser negativa.')
                quantidades[tamanho_id] = quantidade
                ids_tamanhos.add(int(tamanho_id))
            grade_por_grade[grade_id] = quantidades
        total = sum(sum(mapa.values()) for mapa in grade_por_grade.values())
        totais[componente] = total
        resultado[componente] = {
            'estrutura': estrutura,
            'cor_personalizada': cor_personalizada,
            'outros': outros,
            'observacoes_campos': observacoes_campos,
            'grades': grades,
            'gradePorGrade': grade_por_grade,
            'observacoes': str(origem.get('observacoes') or '').strip()[:2000],
        }

    possui_alguma_grade = any(
        resultado[componente]['grades'] for componente, _ in COMPONENTES_CONJUNTO
    )
    if possui_alguma_grade and (
        not resultado['camisa']['grades']
        or not resultado['calcao']['grades']
        or totais['camisa'] < 1
        or totais['calcao'] < 1
    ):
        raise ValueError(
            'Conjunto: selecione uma grade e informe as quantidades para camisa e calção.'
        )
    if totais['camisa'] != totais['calcao']:
        raise ValueError(
            'Conjunto: camisa e calção precisam ter a mesma quantidade total '
            f'({totais["camisa"]} camisa(s) e {totais["calcao"]} calção(ões)).'
        )
    if filial is not None:
        if Grade.objects.for_filial(filial).filter(pk__in=ids_grades, ativo=True).count() != len(ids_grades):
            raise ValueError('Conjunto: uma das grades não está disponível nesta filial.')
        if Tamanho.objects.for_filial(filial).filter(pk__in=ids_tamanhos, ativo=True).count() != len(ids_tamanhos):
            raise ValueError('Conjunto: um dos tamanhos não está disponível nesta filial.')
        pares_validos = set(ItemGrade.objects.filter(
            grade_id__in=ids_grades,
            tamanho_id__in=ids_tamanhos,
        ).values_list('grade_id', 'tamanho_id'))
        for componente, label in COMPONENTES_CONJUNTO:
            for grade_id, mapa in resultado[componente]['gradePorGrade'].items():
                if any((int(grade_id), int(tamanho_id)) not in pares_validos for tamanho_id in mapa):
                    raise ValueError(f'{label}: existe um tamanho que não pertence à grade selecionada.')
    return resultado


def total_componente(configuracao, componente):
    dados = (configuracao or {}).get(componente) or {}
    return sum(
        int(qtd or 0)
        for mapa in (dados.get('gradePorGrade') or {}).values()
        for qtd in mapa.values()
    )


def quantidades_agregadas(configuracao, componente):
    resultado = {}
    dados = (configuracao or {}).get(componente) or {}
    for mapa in (dados.get('gradePorGrade') or {}).values():
        for tamanho_id, quantidade in mapa.items():
            chave = int(tamanho_id)
            resultado[chave] = resultado.get(chave, 0) + int(quantidade or 0)
    return resultado


def componentes_conjunto_exibicao(item):
    """Entrega estrutura e matrizes de grade prontas para tela e PDFs."""
    configuracao = item.configuracao_conjunto or {}
    grade_ids = {
        int(grade_id)
        for dados in configuracao.values()
        for grade_id in (dados.get('grades') or [])
        if str(grade_id).isdigit()
    }
    tamanho_ids = {
        int(tamanho_id)
        for dados in configuracao.values()
        for mapa in (dados.get('gradePorGrade') or {}).values()
        for tamanho_id in mapa
        if str(tamanho_id).isdigit()
    }
    grades = Grade.objects.in_bulk(grade_ids)
    tamanhos = Tamanho.objects.in_bulk(tamanho_ids)
    componentes = []
    for slug, label in COMPONENTES_CONJUNTO:
        dados = configuracao.get(slug) or {}
        estrutura = []
        for campo, valor in (dados.get('estrutura') or {}).items():
            valores = _lista(valor)
            if not valores or valores == ['N/A']:
                continue
            if campo == 'cor' and valores == ['COR PERSONALIZADA']:
                valores = [dados.get('cor_personalizada') or valores[0]]
            if 'OUTRO' in valores:
                outro = (dados.get('outros') or {}).get(campo) or 'OUTRO'
                valores = [outro if item == 'OUTRO' else item for item in valores]
            estrutura.append((campo.replace('_', ' ').title(), ' + '.join(valores)))
            observacao = (dados.get('observacoes_campos') or {}).get(campo)
            if observacao:
                estrutura.append((f'Observação de {campo.replace("_", " ").title()}', observacao))
        matrizes = []
        for grade_id in dados.get('grades') or []:
            mapa = (dados.get('gradePorGrade') or {}).get(str(grade_id), {})
            linhas = []
            for tamanho_id, quantidade in mapa.items():
                quantidade = int(quantidade or 0)
                if not quantidade:
                    continue
                tamanho = tamanhos.get(int(tamanho_id)) if str(tamanho_id).isdigit() else None
                linhas.append({
                    'id': str(tamanho_id),
                    'sigla': tamanho.sigla if tamanho else str(tamanho_id),
                    'quantidade': quantidade,
                    'ordem': tamanho.ordem if tamanho else 999,
                })
            linhas.sort(key=lambda linha: (linha['ordem'], linha['sigla']))
            grade = grades.get(int(grade_id)) if str(grade_id).isdigit() else None
            matrizes.append({
                'id': str(grade_id), 'nome': grade.nome if grade else f'Grade {grade_id}',
                'tamanhos': linhas, 'total': sum(linha['quantidade'] for linha in linhas),
            })
        componentes.append({
            'slug': slug, 'label': label, 'estrutura': estrutura,
            'grades': matrizes, 'total': sum(g['total'] for g in matrizes),
            'observacoes': dados.get('observacoes') or '',
        })
    return componentes


def resumo_conjunto(configuracao):
    """Texto compacto compatível com as telas legadas e buscas da OP."""
    linhas = ['Tipo de peça: Conjunto']
    for componente in componentes_conjunto_exibicao(
        type('ItemConjunto', (), {'configuracao_conjunto': configuracao})()
    ):
        linhas.append(f'{componente["label"]}:')
        linhas.extend(f'- {rotulo}: {valor}' for rotulo, valor in componente['estrutura'])
        if componente['observacoes']:
            linhas.append(f'- Observações: {componente["observacoes"]}')
    return '\n'.join(linhas)
