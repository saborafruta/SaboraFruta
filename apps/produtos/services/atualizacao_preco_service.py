"""Servicos da rotina de atualizacao de preco de venda."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.core.tenant_context import tenant_atomic
from apps.compras.models import EntradaNF
from apps.compras.services.entrada_custo_service import EntradaCustoService
from apps.estoque.models import Estoque
from apps.produtos.models import AtualizacaoPrecoItem, AtualizacaoPrecoLote, Produto


CENTAVOS = Decimal('0.01')
QUATRO_CASAS = Decimal('0.0001')


@dataclass
class LinhaAtualizacaoPreco:
    produto: Produto
    fornecedor_nome: str
    custo_atual: Decimal
    custo_base: Decimal
    variacao_custo: Decimal
    preco_atual: Decimal
    preco_sugerido: Decimal
    margem_atual: Decimal
    margem_projetada: Decimal
    markup_atual: Decimal
    markup_novo: Decimal
    estoque_atual: Decimal
    status: str
    motivo_bloqueio: str = ''
    origem_item: str = ''

    @property
    def revisar(self) -> bool:
        return self.status in {'revisar', 'novo', 'bloqueado'}

    @property
    def variacao_abs(self) -> Decimal:
        return abs(self.variacao_custo)


class AtualizacaoPrecoService:
    @staticmethod
    def decimal(valor, padrao=Decimal('0')) -> Decimal:
        if valor in (None, ''):
            return padrao
        if isinstance(valor, Decimal):
            return valor
        texto = str(valor).strip().replace(' ', '')
        if ',' in texto:
            texto = texto.replace('.', '').replace(',', '.')
        try:
            return Decimal(texto)
        except (InvalidOperation, ValueError):
            return padrao

    @classmethod
    def centavos(cls, valor) -> Decimal:
        return cls.decimal(valor).quantize(CENTAVOS, rounding=ROUND_HALF_UP)

    @classmethod
    def quatro_casas(cls, valor) -> Decimal:
        return cls.decimal(valor).quantize(QUATRO_CASAS, rounding=ROUND_HALF_UP)

    @staticmethod
    def margem(preco: Decimal, custo: Decimal) -> Decimal:
        if preco <= 0:
            return Decimal('0.00')
        return (((preco - custo) / preco) * Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @staticmethod
    def markup(preco: Decimal, custo: Decimal) -> Decimal:
        if custo <= 0:
            return Decimal('0.0000')
        return (preco / custo).quantize(QUATRO_CASAS, rounding=ROUND_HALF_UP)

    @staticmethod
    def variacao(novo: Decimal, anterior: Decimal) -> Decimal:
        if anterior <= 0:
            return Decimal('0.00')
        return (((novo - anterior) / anterior) * Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @classmethod
    def aplicar_arredondamento(cls, valor: Decimal, tipo='centavos') -> Decimal:
        valor = cls.centavos(valor)
        if tipo == 'multiplo_005':
            return (valor / Decimal('0.05')).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * Decimal('0.05')
        if tipo == 'multiplo_010':
            return (valor / Decimal('0.10')).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * Decimal('0.10')
        if tipo in {'final_990', 'final_999'} and valor >= Decimal('1'):
            inteiro = int(valor)
            final = Decimal('0.90') if tipo == 'final_990' else Decimal('0.99')
            candidato = Decimal(inteiro) + final
            if candidato < valor:
                candidato += Decimal('1.00')
            return candidato.quantize(CENTAVOS)
        return valor

    @classmethod
    def calcular_novo_preco(cls, produto: Produto, custo: Decimal, regra: dict) -> Decimal:
        tipo = regra.get('tipo') or 'percentual'
        valor = cls.decimal(regra.get('valor'), Decimal('0'))
        preco_atual = cls.decimal(getattr(produto, 'preco_venda', 0))
        if tipo == 'markup':
            novo = custo * valor
        elif tipo == 'margem':
            margem = min(max(valor, Decimal('0')), Decimal('95'))
            novo = custo / (Decimal('1') - (margem / Decimal('100'))) if margem < Decimal('100') else preco_atual
        elif tipo == 'novo_preco':
            novo = valor
        elif tipo == 'valor_fixo':
            direcao = regra.get('direcao') or 'aumentar'
            novo = preco_atual - valor if direcao == 'reduzir' else preco_atual + valor
        else:
            direcao = regra.get('direcao') or 'aumentar'
            fator = valor / Decimal('100')
            novo = preco_atual * (Decimal('1') - fator if direcao == 'reduzir' else Decimal('1') + fator)
        return cls.aplicar_arredondamento(novo, regra.get('arredondamento') or 'centavos')

    @classmethod
    def _produto_base_qs(cls, filial, filtros=None):
        filtros = filtros or {}
        estoque_atual = Estoque.objects.filter(
            produto=OuterRef('pk'),
            filial=filial,
        ).values('quantidade_atual')[:1]
        qs = Produto.objects.for_filial(filial).select_related(
            'categoria', 'marca', 'fornecedor', 'unidade_medida',
        ).annotate(
            estoque_atual_preco=Coalesce(
                Subquery(estoque_atual, output_field=DecimalField(max_digits=12, decimal_places=3)),
                Value(Decimal('0')),
                output_field=DecimalField(max_digits=12, decimal_places=3),
            ),
        )
        busca = (filtros.get('q') or '').strip()
        if busca:
            filtro_busca = Q(descricao__icontains=busca) | Q(codigo__icontains=busca) | Q(codigo_barras__icontains=busca)
            if busca.lstrip('0').isdigit():
                filtro_busca |= Q(pk=int(busca.lstrip('0'))) | Q(id_externo=f'produto:{int(busca.lstrip("0"))}')
            qs = qs.filter(filtro_busca)
        for campo in ('categoria', 'marca', 'fornecedor'):
            valor = filtros.get(campo)
            if valor:
                qs = qs.filter(**{f'{campo}_id': valor})
        if filtros.get('com_estoque'):
            qs = qs.filter(estoque_atual_preco__gt=0)
        if filtros.get('margem_baixa'):
            qs = qs.filter(margem_lucro__lt=F('margem_desejada'))
        return qs.order_by('descricao')

    @classmethod
    def linhas_avulsas(cls, filial, filtros=None, limite=80) -> list[LinhaAtualizacaoPreco]:
        produtos = list(cls._produto_base_qs(filial, filtros)[:limite])
        return [cls._linha_produto(produto, produto.preco_custo_medio or produto.preco_custo) for produto in produtos]

    @classmethod
    def linhas_xml(cls, entrada: EntradaNF, limite=120) -> list[LinhaAtualizacaoPreco]:
        custo_por_item = {}
        try:
            composicao = EntradaCustoService.compor(
                entrada=entrada,
                metodo_rateio=entrada.custo_rateio_metodo,
                incluir_ipi=entrada.custo_incluir_ipi,
                incluir_icms_st=entrada.custo_incluir_icms_st,
                incluir_icms=entrada.custo_incluir_icms,
                custo_financeiro=entrada.custo_financeiro or Decimal('0'),
                usar_apenas_valor_nota=entrada.custo_usar_apenas_valor_nota,
            )
            custo_por_item = {linha.item_id if hasattr(linha, 'item_id') else linha.item.pk: linha.custo_unitario for linha in composicao['linhas']}
        except Exception:
            custo_por_item = {}

        linhas = []
        itens = entrada.itens.select_related('produto', 'produto__fornecedor').prefetch_related(
            'produtos_gerados', 'produtos_gerados__produto',
        ).order_by('numero_item', 'pk')
        for item in itens:
            custo_item = cls.quatro_casas(custo_por_item.get(item.pk) or item.custo_unitario_total or item.valor_unitario)
            if item.produtos_gerados.exists():
                for gerado in item.produtos_gerados.all():
                    custo = cls.quatro_casas(gerado.custo_unitario_manual or custo_item)
                    linhas.append(cls._linha_produto(gerado.produto, custo, origem_item=f'Item {item.numero_item}'))
            elif item.produto_id:
                linhas.append(cls._linha_produto(item.produto, custo_item, origem_item=f'Item {item.numero_item}'))
            if len(linhas) >= limite:
                break
        return linhas

    @classmethod
    def _linha_produto(cls, produto: Produto, custo_base, origem_item='') -> LinhaAtualizacaoPreco:
        custo_base = cls.quatro_casas(custo_base or produto.preco_custo_medio or produto.preco_custo)
        custo_atual = cls.quatro_casas(produto.preco_custo_medio or produto.preco_custo)
        preco_atual = cls.centavos(produto.preco_venda)
        margem_atual = cls.margem(preco_atual, custo_atual)
        margem_meta = produto.margem_desejada or Decimal('30')
        preco_sugerido = cls.aplicar_arredondamento(
            custo_base / (Decimal('1') - (min(margem_meta, Decimal('90')) / Decimal('100')))
            if custo_base > 0 and margem_meta > 0 else preco_atual,
            'centavos',
        )
        margem_projetada = cls.margem(preco_sugerido, custo_base)
        variacao = cls.variacao(custo_base, custo_atual)
        status = 'sem_impacto'
        motivo = ''
        if produto.rascunho_comercial or preco_atual <= 0:
            status = 'novo'
        elif custo_base <= 0:
            status = 'bloqueado'
            motivo = 'Produto sem custo base.'
        elif abs(variacao) >= Decimal('3') or margem_projetada < margem_meta:
            status = 'revisar'
        return LinhaAtualizacaoPreco(
            produto=produto,
            fornecedor_nome=str(produto.fornecedor or ''),
            custo_atual=custo_atual,
            custo_base=custo_base,
            variacao_custo=variacao,
            preco_atual=preco_atual,
            preco_sugerido=preco_sugerido,
            margem_atual=margem_atual,
            margem_projetada=margem_projetada,
            markup_atual=cls.markup(preco_atual, custo_atual),
            markup_novo=cls.markup(preco_sugerido, custo_base),
            estoque_atual=cls.decimal(getattr(produto, 'estoque_atual_preco', 0)),
            status=status,
            motivo_bloqueio=motivo,
            origem_item=origem_item,
        )

    @classmethod
    def resumo(cls, linhas: list[LinhaAtualizacaoPreco]) -> dict:
        total = len(linhas)
        if not total:
            return {
                'total': 0, 'custo_maior': 0, 'custo_menor': 0, 'novos': 0,
                'variacao_media': Decimal('0'), 'margem_media': Decimal('0'),
                'impacto_estoque': Decimal('0'), 'revisar': 0,
            }
        return {
            'total': total,
            'custo_maior': sum(1 for linha in linhas if linha.variacao_custo > 0),
            'custo_menor': sum(1 for linha in linhas if linha.variacao_custo < 0),
            'novos': sum(1 for linha in linhas if linha.status == 'novo'),
            'revisar': sum(1 for linha in linhas if linha.revisar),
            'variacao_media': (sum((linha.variacao_custo for linha in linhas), Decimal('0')) / total).quantize(Decimal('0.01')),
            'margem_media': (sum((linha.margem_atual for linha in linhas), Decimal('0')) / total).quantize(Decimal('0.01')),
            'impacto_estoque': sum((linha.preco_sugerido * linha.estoque_atual for linha in linhas), Decimal('0')).quantize(CENTAVOS),
        }

    @classmethod
    def simular_cenario(cls, linhas: list[LinhaAtualizacaoPreco], regra: dict) -> dict:
        simuladas = []
        bloqueados = 0
        for linha in linhas:
            novo = cls.calcular_novo_preco(linha.produto, linha.custo_base, regra)
            margem_nova = cls.margem(novo, linha.custo_base)
            motivo = ''
            if linha.custo_base > 0 and novo <= linha.custo_base:
                motivo = 'Novo preco ficaria abaixo do custo.'
                bloqueados += 1
                novo = linha.preco_atual
                margem_nova = linha.margem_atual
            simuladas.append({**linha.__dict__, 'preco_novo': novo, 'margem_nova': margem_nova, 'motivo_bloqueio': motivo})
        total = len(simuladas) or 1
        novo_preco_medio = (sum((item['preco_novo'] for item in simuladas), Decimal('0')) / total).quantize(CENTAVOS)
        margem_media = (sum((item['margem_nova'] for item in simuladas), Decimal('0')) / total).quantize(Decimal('0.01'))
        impacto = sum((item['preco_novo'] * item['estoque_atual'] for item in simuladas), Decimal('0')).quantize(CENTAVOS)
        ganho = sum(((item['preco_novo'] - item['custo_base']) * max(item['estoque_atual'], Decimal('1')) for item in simuladas), Decimal('0')).quantize(CENTAVOS)
        risco = 'Baixo' if bloqueados == 0 and margem_media >= Decimal('30') else ('Medio' if bloqueados <= 3 else 'Alto')
        return {
            'regra': regra,
            'linhas': simuladas,
            'produtos': len(simuladas),
            'novo_preco_medio': novo_preco_medio,
            'margem_media': margem_media,
            'impacto_estoque': impacto,
            'ganho_bruto': ganho,
            'bloqueados': bloqueados,
            'risco': risco,
        }

    @classmethod
    def cenarios(cls, linhas: list[LinhaAtualizacaoPreco]) -> list[dict]:
        configs = [
            {'nome': 'Cenario A', 'descricao': 'Repasse parcial 4%', 'tipo': 'percentual', 'valor': '4', 'direcao': 'aumentar', 'arredondamento': 'centavos'},
            {'nome': 'Cenario B', 'descricao': 'Markup 2,30', 'tipo': 'markup', 'valor': '2.30', 'arredondamento': 'centavos'},
            {'nome': 'Cenario C', 'descricao': 'Margem alvo 35%', 'tipo': 'margem', 'valor': '35', 'arredondamento': 'centavos'},
        ]
        cenarios = [cls.simular_cenario(linhas, config) for config in configs]
        if cenarios:
            melhor = sorted(cenarios, key=lambda item: (item['risco'] == 'Alto', -item['ganho_bruto']))[0]
            melhor['recomendado'] = True
        return cenarios

    @classmethod
    @tenant_atomic
    def aplicar_atualizacao(cls, *, request, entrada, linhas, regra: dict) -> AtualizacaoPrecoLote:
        lote = AtualizacaoPrecoLote.objects.create(
            filial=request.filial_ativa,
            usuario=request.user,
            origem=AtualizacaoPrecoLote.Origem.ENTRADA_XML if entrada else AtualizacaoPrecoLote.Origem.AVULSA,
            entrada=entrada,
            numero_nfe=getattr(entrada, 'numero_nf', '') or '',
            chave_nfe=getattr(entrada, 'chave_acesso_nf', '') or '',
            fornecedor_nome=getattr(entrada, 'fornecedor_nome_display', '') if entrada else '',
            status=AtualizacaoPrecoLote.Status.APLICADO,
            regra_tipo=regra.get('tipo') or AtualizacaoPrecoLote.RegraTipo.PERCENTUAL,
            regra_config=regra,
            filtros_config=dict(request.GET),
            total_produtos=len(linhas),
            data_aplicacao=timezone.now(),
        )
        for linha in linhas:
            novo = cls.calcular_novo_preco(linha.produto, linha.custo_base, regra)
            margem_nova = cls.margem(novo, linha.custo_base)
            status = AtualizacaoPrecoItem.Status.SIMULADO
            motivo = ''
            if linha.custo_base > 0 and novo <= linha.custo_base:
                status = AtualizacaoPrecoItem.Status.BLOQUEADO
                motivo = 'Novo preco ficaria abaixo do custo.'
                novo = linha.preco_atual
                margem_nova = linha.margem_atual
            else:
                linha.produto.preco_venda = novo
                linha.produto.calcular_margem()
                linha.produto.save(update_fields=['preco_venda', 'margem_lucro', 'markup', 'preco_sugerido', 'updated_at'])
                status = AtualizacaoPrecoItem.Status.APLICADO
            AtualizacaoPrecoItem.objects.create(
                lote=lote,
                produto=linha.produto,
                preco_anterior=linha.preco_atual,
                preco_novo=novo,
                custo_base=linha.custo_base,
                margem_anterior=linha.margem_atual,
                margem_nova=margem_nova,
                markup_anterior=linha.markup_atual,
                markup_novo=cls.markup(novo, linha.custo_base),
                status=status,
                motivo_bloqueio=motivo,
            )
        return lote

    @classmethod
    @tenant_atomic
    def aplicar_precos_manuais(cls, *, request, entrada, linhas, precos_por_produto: dict) -> AtualizacaoPrecoLote:
        linhas_para_gravar = [
            linha for linha in linhas
            if linha.produto.pk in precos_por_produto
        ]
        lote = AtualizacaoPrecoLote.objects.create(
            filial=request.filial_ativa,
            usuario=request.user,
            origem=AtualizacaoPrecoLote.Origem.ENTRADA_XML if entrada else AtualizacaoPrecoLote.Origem.AVULSA,
            entrada=entrada,
            numero_nfe=getattr(entrada, 'numero_nf', '') or '',
            chave_nfe=getattr(entrada, 'chave_acesso_nf', '') or '',
            fornecedor_nome=getattr(entrada, 'fornecedor_nome_display', '') if entrada else '',
            status=AtualizacaoPrecoLote.Status.APLICADO,
            regra_tipo=AtualizacaoPrecoLote.RegraTipo.NOVO_PRECO,
            regra_config={'tipo': 'manual_por_produto'},
            filtros_config=dict(request.GET),
            total_produtos=len(linhas_para_gravar),
            data_aplicacao=timezone.now(),
        )
        for linha in linhas_para_gravar:
            novo = cls.centavos(precos_por_produto.get(linha.produto.pk))
            margem_nova = cls.margem(novo, linha.custo_base)
            status = AtualizacaoPrecoItem.Status.SIMULADO
            motivo = ''
            if linha.custo_base > 0 and novo <= linha.custo_base:
                status = AtualizacaoPrecoItem.Status.BLOQUEADO
                motivo = 'Novo preco ficaria abaixo do custo.'
                novo = linha.preco_atual
                margem_nova = linha.margem_atual
            else:
                linha.produto.preco_venda = novo
                linha.produto.calcular_margem()
                linha.produto.save(update_fields=['preco_venda', 'margem_lucro', 'markup', 'preco_sugerido', 'updated_at'])
                status = AtualizacaoPrecoItem.Status.APLICADO
            AtualizacaoPrecoItem.objects.create(
                lote=lote,
                produto=linha.produto,
                preco_anterior=linha.preco_atual,
                preco_novo=novo,
                custo_base=linha.custo_base,
                margem_anterior=linha.margem_atual,
                margem_nova=margem_nova,
                markup_anterior=linha.markup_atual,
                markup_novo=cls.markup(novo, linha.custo_base),
                status=status,
                motivo_bloqueio=motivo,
            )
        return lote
