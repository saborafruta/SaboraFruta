"""Regras de tabela de preço, markup e custo médio."""
from __future__ import annotations

from decimal import Decimal
import unicodedata

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.produtos.models import DIAS_SEMANA_TODOS, ItemTabelaPreco, Produto, ProdutoFilial, TabelaPreco


class PrecoService:
    """Calculos e consultas de preco."""

    @staticmethod
    def tabela_cliente_vigente(cliente=None, filial=None, tabela=None):
        """Retorna somente uma tabela ativa, vigente e vinculada a filial."""
        tabela = tabela or getattr(cliente, 'tabela_preco', None)
        if not tabela or not tabela.ativo:
            return None

        hoje = timezone.localdate()
        if tabela.data_inicio and tabela.data_inicio > hoje:
            return None
        if tabela.data_fim and tabela.data_fim < hoje:
            return None
        if filial and not TabelaPreco.objects.for_filial(filial).filter(
            pk=tabela.pk,
            ativo=True,
        ).exists():
            return None
        return tabela

    @classmethod
    def preco_cliente_detalhado(
        cls,
        produto: Produto,
        quantidade: Decimal,
        *,
        cliente=None,
        tabela=None,
        filial=None,
        validar_promocoes: bool = True,
    ) -> dict:
        """
        Resolve o preco comercial do cliente, com fallback para a regra padrao.
        """
        quantidade = Decimal(str(quantidade or '0'))
        preco_padrao = cls.melhor_preco_produto_detalhado(
            produto,
            usar_promocoes=validar_promocoes,
            filial=filial,
            quantidade=quantidade,
            data=timezone.localdate(),
        )
        tabela = cls.tabela_cliente_vigente(
            cliente=cliente,
            filial=filial,
            tabela=tabela,
        )
        if not tabela:
            return preco_padrao

        item = (
            ItemTabelaPreco.objects
            .filter(
                tabela=tabela,
                produto=produto,
                quantidade_minima__lte=quantidade,
            )
            .order_by('-quantidade_minima')
            .first()
        )
        if not item:
            return preco_padrao

        # valor_final = preço unitário já com o desconto em R$ abatido.
        preco = item.valor_final
        if tabela.acrescimo_percentual:
            preco *= Decimal('1') + (tabela.acrescimo_percentual / Decimal('100'))
        return {
            'preco': preco,
            'tipo': 'tabela_cliente',
            'origem': tabela.descricao,
            'detalhe': (
                f'Tabela de preco "{tabela.descricao}" '
                f'(a partir de {item.quantidade_minima} unidade(s)).'
            ),
            'tabela_preco_id': tabela.pk,
            'item_tabela_preco_id': item.pk,
        }

    @staticmethod
    def promocao_produto_contexto(produto: Produto, filial=None):
        """Retorna a regra promocional da filial, com fallback legado no produto."""
        if filial is not None and getattr(produto, 'pk', None):
            vinculo_cache = getattr(produto, '_promocao_filial_cache', {})
            if getattr(filial, 'pk', None) in vinculo_cache:
                vinculo = vinculo_cache[filial.pk]
            else:
                vinculo = (
                    ProdutoFilial.objects
                    .filter(produto=produto, filial=filial, ativo=True)
                    .first()
                )
            if vinculo:
                return vinculo
        return produto

    @staticmethod
    def _dias_semana_set(dias: str | None) -> set[str]:
        """Normaliza dias de semana preservando o padrão de todos os dias."""
        dias_validos = set(DIAS_SEMANA_TODOS.split(','))
        return {dia for dia in (dias or DIAS_SEMANA_TODOS).split(',') if dia in dias_validos}

    @staticmethod
    def _cumpre_minimo_dias_semana(dias: str | None, minimo_dias_semana: int | None = None) -> bool:
        if minimo_dias_semana is None:
            return True
        return len(PrecoService._dias_semana_set(dias)) >= int(minimo_dias_semana)

    @staticmethod
    def produto_tem_promocao_vigente(
        produto: Produto,
        filial=None,
        data=None,
        validar_dia_semana: bool = True,
        minimo_dias_semana: int | None = None,
    ) -> bool:
        """Retorna se o preco promocional do produto esta vigente na data informada."""
        data = data or timezone.localdate()
        promocao = PrecoService.promocao_produto_contexto(produto, filial)
        if not getattr(promocao, 'preco_promocional_ativo', True):
            return False
        preco_promocional = promocao.preco_promocional or Decimal('0')
        if preco_promocional <= 0:
            return False
        if promocao.promocao_inicio and promocao.promocao_inicio > data:
            return False
        if promocao.promocao_fim and promocao.promocao_fim < data:
            return False
        if not PrecoService._cumpre_minimo_dias_semana(promocao.promocao_dias_semana, minimo_dias_semana):
            return False
        dias = PrecoService._dias_semana_set(promocao.promocao_dias_semana)
        if validar_dia_semana and str(data.weekday()) not in dias:
            return False
        return True

    @staticmethod
    def preco_promocional_vigente(
        produto: Produto,
        filial=None,
        data=None,
        validar_dia_semana: bool = True,
        minimo_dias_semana: int | None = None,
    ) -> Decimal | None:
        """Retorna o preco promocional vigente ou None quando a promocao nao vale."""
        if PrecoService.produto_tem_promocao_vigente(
            produto,
            filial=filial,
            data=data,
            validar_dia_semana=validar_dia_semana,
            minimo_dias_semana=minimo_dias_semana,
        ):
            return PrecoService.calcular_preco_promocional(produto, filial=filial)
        return None

    @staticmethod
    def calcular_preco_promocional(produto: Produto, filial=None) -> Decimal:
        """Calcula o preco promocional vivo a partir do preco de venda atual."""
        promocao = PrecoService.promocao_produto_contexto(produto, filial)
        base = produto.preco_venda or Decimal('0')
        tipo = getattr(promocao, 'promocao_tipo_desconto', 'preco_final') or 'preco_final'
        valor = getattr(promocao, 'promocao_valor_desconto', None)
        if tipo == 'percentual':
            valor = valor or Decimal('0')
            preco = base * (Decimal('1') - (valor / Decimal('100')))
        elif tipo == 'valor':
            valor = valor or Decimal('0')
            preco = base - valor
        else:
            preco = valor if valor not in (None, Decimal('0')) else (promocao.preco_promocional or Decimal('0'))
        if preco < 0:
            return Decimal('0')
        return preco

    @staticmethod
    def aplicar_regra_desconto(preco_base: Decimal, tipo: str, valor: Decimal) -> Decimal:
        """Aplica uma regra viva de desconto percentual, valor ou preco final."""
        preco_base = preco_base or Decimal('0')
        valor = valor or Decimal('0')
        if tipo == 'percentual':
            preco = preco_base * (Decimal('1') - (valor / Decimal('100')))
        elif tipo == 'valor':
            preco = preco_base - valor
        else:
            preco = valor
        return max(Decimal('0'), preco)

    @staticmethod
    def _preco_base_categoria(
        produto: Produto,
        desconto=None,
        filial=None,
        data=None,
        validar_dia_semana: bool = True,
        minimo_dias_semana: int | None = None,
    ) -> tuple[Decimal, str]:
        preco_normal = produto.preco_venda or Decimal('0')
        if not getattr(desconto, 'permite_preco_promocional', False):
            return preco_normal, 'preco de venda'

        preco_promocional = PrecoService.preco_promocional_vigente(
            produto,
            filial=filial,
            data=data,
            validar_dia_semana=validar_dia_semana,
            minimo_dias_semana=minimo_dias_semana,
        )
        if preco_promocional is not None and preco_promocional < preco_normal:
            return preco_promocional, 'promocao individual'
        return preco_normal, 'preco de venda'

    @staticmethod
    def _fmt_decimal(valor: Decimal, casas: int = 2) -> str:
        valor = Decimal(valor or '0')
        quant = Decimal('1').scaleb(-casas)
        return f'{valor.quantize(quant):f}'.replace('.', ',')

    @staticmethod
    def _fmt_money(valor: Decimal) -> str:
        return f'R$ {PrecoService._fmt_decimal(valor, 2)}'

    @staticmethod
    def _resumo_desconto(tipo: str, valor: Decimal) -> str:
        if tipo == 'percentual':
            return f'{PrecoService._fmt_decimal(valor, 2)}%'
        if tipo == 'valor':
            return PrecoService._fmt_money(valor)
        return f'preco final {PrecoService._fmt_money(valor)}'

    @staticmethod
    def _fmt_date(valor) -> str:
        return valor.strftime('%d/%m/%Y') if valor else ''

    @staticmethod
    def _vigencia_texto(inicio=None, fim=None) -> str:
        if fim:
            return f'Validade: até {PrecoService._fmt_date(fim)}.'
        if inicio:
            return f'Validade: a partir de {PrecoService._fmt_date(inicio)}.'
        return 'Validade: sem prazo de término.'

    @staticmethod
    def preco_vivo_produto(
        produto: Produto,
        usar_preco_promocional: bool = True,
        filial=None,
        quantidade: Decimal | int | str = Decimal('1'),
        data=None,
        validar_dia_semana: bool = True,
        minimo_dias_semana: int | None = None,
    ) -> Decimal:
        """Resolve o preco vivo do produto para kits, combos, PDV e listagens."""
        return PrecoService.melhor_preco_produto(
            produto,
            usar_promocoes=usar_preco_promocional,
            filial=filial,
            quantidade=quantidade,
            data=data,
            validar_dia_semana=validar_dia_semana,
            minimo_dias_semana=minimo_dias_semana,
        )

    @staticmethod
    def melhor_preco_produto(
        produto: Produto,
        usar_promocoes: bool = True,
        filial=None,
        quantidade: Decimal | int | str = Decimal('1'),
        data=None,
        validar_dia_semana: bool = True,
        minimo_dias_semana: int | None = None,
    ) -> Decimal:
        """Escolhe o melhor preco base vigente sem acumular descontos paralelos."""
        preco_normal = produto.preco_venda or Decimal('0')
        if not usar_promocoes:
            return preco_normal

        candidatos = [preco_normal]
        preco_promocional = PrecoService.preco_promocional_vigente(
            produto,
            filial=filial,
            data=data,
            validar_dia_semana=validar_dia_semana,
            minimo_dias_semana=minimo_dias_semana,
        )
        if preco_promocional is not None:
            candidatos.append(preco_promocional)
        candidatos.extend(
            PrecoService.precos_categoria_vigentes(
                produto,
                filial=filial,
                quantidade=quantidade,
                data=data,
                validar_dia_semana=validar_dia_semana,
                minimo_dias_semana=minimo_dias_semana,
            )
        )
        candidatos.extend(
            PrecoService.precos_combo_quantidade_vigentes(
                produto,
                filial=filial,
                quantidade=quantidade,
                data=data,
                validar_dia_semana=validar_dia_semana,
                minimo_dias_semana=minimo_dias_semana,
            )
        )
        return min(candidatos)

    @staticmethod
    def melhor_preco_produto_detalhado(
        produto: Produto,
        usar_promocoes: bool = True,
        filial=None,
        quantidade: Decimal | int | str = Decimal('1'),
        data=None,
        validar_dia_semana: bool = True,
        minimo_dias_semana: int | None = None,
    ) -> dict:
        """Resolve o menor preco vigente e informa de qual regra ele veio."""
        preco_normal = produto.preco_venda or Decimal('0')
        candidatos = [{
            'preco': preco_normal,
            'tipo': 'normal',
            'origem': 'Preco de venda',
            'detalhe': 'Preco de venda cadastrado no produto.',
        }]
        if usar_promocoes:
            preco_promocional = PrecoService.preco_promocional_vigente(
                produto,
                filial=filial,
                data=data,
                validar_dia_semana=validar_dia_semana,
                minimo_dias_semana=minimo_dias_semana,
            )
            if preco_promocional is not None:
                promocao = PrecoService.promocao_produto_contexto(produto, filial)
                tipo = getattr(promocao, 'promocao_tipo_desconto', 'preco_final') or 'preco_final'
                valor = getattr(promocao, 'promocao_valor_desconto', None) or preco_promocional
                nome_produto = getattr(produto, 'descricao_curta', '') or getattr(produto, 'descricao', '') or 'produto'
                resumo = PrecoService._resumo_desconto(tipo, valor)
                candidatos.append({
                    'preco': preco_promocional,
                    'tipo': 'promocional',
                    'origem': 'Promoção individual',
                    'detalhe': (
                        f'Promoção individual do produto "{nome_produto}". '
                        f'{PrecoService._vigencia_texto(promocao.promocao_inicio, promocao.promocao_fim)}'
                    ),
                })
            candidatos.extend(
                PrecoService.precos_categoria_vigentes_detalhados(
                    produto,
                    filial=filial,
                    quantidade=quantidade,
                    data=data,
                    validar_dia_semana=validar_dia_semana,
                    minimo_dias_semana=minimo_dias_semana,
                )
            )
            candidatos.extend(
                PrecoService.precos_combo_quantidade_vigentes_detalhados(
                    produto,
                    filial=filial,
                    quantidade=quantidade,
                    data=data,
                    validar_dia_semana=validar_dia_semana,
                    minimo_dias_semana=minimo_dias_semana,
                )
            )
        melhor = min(candidatos, key=lambda item: item['preco'])
        return melhor

    @staticmethod
    def desconto_categoria_vigente(
        desconto,
        data=None,
        validar_dia_semana: bool = True,
        minimo_dias_semana: int | None = None,
    ) -> bool:
        """Confere status, periodo e dia da semana de um desconto por categoria."""
        data = data or timezone.localdate()
        if not getattr(desconto, 'ativo', True):
            return False
        if desconto.data_inicio and desconto.data_inicio > data:
            return False
        if desconto.data_fim and desconto.data_fim < data:
            return False
        if not PrecoService._cumpre_minimo_dias_semana(desconto.dias_semana, minimo_dias_semana):
            return False
        dias = PrecoService._dias_semana_set(desconto.dias_semana)
        return not validar_dia_semana or str(data.weekday()) in dias

    @staticmethod
    def _categorias_equivalentes(categoria, alvo) -> bool:
        """Compara categorias diretas e clones replicados por id_externo."""
        if not categoria or not alvo:
            return False
        if categoria.pk and alvo.pk and categoria.pk == alvo.pk:
            return True
        categoria_externo = getattr(categoria, 'id_externo', '') or ''
        alvo_externo = getattr(alvo, 'id_externo', '') or ''
        if categoria_externo and alvo_externo and categoria_externo == alvo_externo:
            return True
        categoria_chaves = PrecoService._categoria_nome_chaves(categoria)
        alvo_chaves = PrecoService._categoria_nome_chaves(alvo)
        return bool(categoria_chaves and alvo_chaves and categoria_chaves.intersection(alvo_chaves))

    @staticmethod
    def _categoria_nome_chave(categoria) -> str:
        """Normaliza nome/caminho para cobrir categorias antigas sem id_externo."""
        if not categoria:
            return ''
        if hasattr(categoria, 'full_path'):
            texto = categoria.full_path()
        else:
            nomes = [getattr(categoria, 'nome', '')]
            parent = getattr(categoria, 'categoria_pai', None)
            while parent:
                nomes.insert(0, getattr(parent, 'nome', ''))
                parent = getattr(parent, 'categoria_pai', None)
            texto = ' / '.join(nome for nome in nomes if nome)
        texto = unicodedata.normalize('NFKD', texto or '').encode('ascii', 'ignore').decode('ascii')
        return ' '.join(texto.lower().split())

    @staticmethod
    def _categoria_nome_chaves(categoria) -> set[str]:
        """Gera chaves tolerantes para categorias antigas sem vinculo externo."""
        if not categoria:
            return set()
        chaves = {PrecoService._categoria_nome_chave(categoria)}
        nome = getattr(categoria, 'nome', '')
        nome = unicodedata.normalize('NFKD', nome or '').encode('ascii', 'ignore').decode('ascii')
        nome = ' '.join(nome.lower().split())
        if nome:
            chaves.add(nome)
        normalizadas = {PrecoService._remover_plural_simples(chave) for chave in chaves if chave}
        chaves.update(normalizadas)
        chaves.discard('')
        return chaves

    @staticmethod
    def _remover_plural_simples(texto: str) -> str:
        """Aproxima plurais simples: Polpas de Fruta == Polpa de Fruta."""
        tokens = []
        for token in (texto or '').split():
            if len(token) > 3 and token.endswith('s'):
                token = token[:-1]
            tokens.append(token)
        return ' '.join(tokens)

    @staticmethod
    def regra_categoria_aplica(regra, produto: Produto, quantidade: Decimal) -> bool:
        """Retorna se a regra de categoria/subcategoria vale para o produto."""
        quantidade_minima = regra.quantidade_minima or Decimal('0')
        if quantidade < quantidade_minima:
            return False
        produto_categoria = getattr(produto, 'categoria', None)
        produto_subcategoria = getattr(produto, 'subcategoria', None)
        if regra.subcategoria_id:
            return (
                PrecoService._categorias_equivalentes(regra.subcategoria, produto_subcategoria)
                or PrecoService._categorias_equivalentes(regra.subcategoria, produto_categoria)
            )
        if regra.categoria_id:
            produto_categoria_pai = getattr(produto_categoria, 'categoria_pai', None)
            produto_subcategoria_pai = getattr(produto_subcategoria, 'categoria_pai', None)
            return (
                PrecoService._categorias_equivalentes(regra.categoria, produto_categoria)
                or PrecoService._categorias_equivalentes(regra.categoria, produto_subcategoria)
                or PrecoService._categorias_equivalentes(regra.categoria, produto_categoria_pai)
                or PrecoService._categorias_equivalentes(regra.categoria, produto_subcategoria_pai)
            )
        return True

    @staticmethod
    def precos_categoria_vigentes(
        produto: Produto,
        filial=None,
        quantidade: Decimal | int | str = Decimal('1'),
        data=None,
        validar_dia_semana: bool = True,
        minimo_dias_semana: int | None = None,
    ) -> list[Decimal]:
        """Calcula candidatos de preco por descontos de categoria vigentes."""
        from apps.produtos.models import KitCategoria

        filial = filial or getattr(produto, 'filial', None)
        if not filial or not getattr(filial, 'pk', None):
            return []

        quantidade = Decimal(str(quantidade or '1'))
        descontos = (
            KitCategoria.objects.for_filial(filial)
            .filter(ativo=True)
            .prefetch_related('regras__categoria__categoria_pai', 'regras__subcategoria__categoria_pai')
        )
        candidatos = []
        for desconto in descontos:
            if not PrecoService.desconto_categoria_vigente(
                desconto,
                data=data,
                validar_dia_semana=validar_dia_semana,
                minimo_dias_semana=minimo_dias_semana,
            ):
                continue
            preco_base, _ = PrecoService._preco_base_categoria(
                produto,
                desconto=desconto,
                filial=filial,
                data=data,
                validar_dia_semana=validar_dia_semana,
                minimo_dias_semana=minimo_dias_semana,
            )
            for regra in desconto.regras.all():
                if PrecoService.regra_categoria_aplica(regra, produto, quantidade):
                    candidatos.append(
                        PrecoService.aplicar_regra_desconto(
                            preco_base,
                            regra.tipo_desconto,
                            regra.valor_desconto,
                        )
                    )
        return candidatos

    @staticmethod
    def precos_categoria_vigentes_detalhados(
        produto: Produto,
        filial=None,
        quantidade: Decimal | int | str = Decimal('1'),
        data=None,
        validar_dia_semana: bool = True,
        minimo_dias_semana: int | None = None,
    ) -> list[dict]:
        """Calcula candidatos de desconto por categoria com origem legivel."""
        from apps.produtos.models import KitCategoria

        filial = filial or getattr(produto, 'filial', None)
        if not filial or not getattr(filial, 'pk', None):
            return []

        quantidade = Decimal(str(quantidade or '1'))
        descontos = (
            KitCategoria.objects.for_filial(filial)
            .filter(ativo=True)
            .prefetch_related('regras__categoria__categoria_pai', 'regras__subcategoria__categoria_pai')
        )
        candidatos = []
        for desconto in descontos:
            if not PrecoService.desconto_categoria_vigente(
                desconto,
                data=data,
                validar_dia_semana=validar_dia_semana,
                minimo_dias_semana=minimo_dias_semana,
            ):
                continue
            preco_base, _ = PrecoService._preco_base_categoria(
                produto,
                desconto=desconto,
                filial=filial,
                data=data,
                validar_dia_semana=validar_dia_semana,
                minimo_dias_semana=minimo_dias_semana,
            )
            for regra in desconto.regras.all():
                if not PrecoService.regra_categoria_aplica(regra, produto, quantidade):
                    continue
                preco = PrecoService.aplicar_regra_desconto(
                    preco_base,
                    regra.tipo_desconto,
                    regra.valor_desconto,
                )
                alvo = regra.categoria.nome if regra.categoria else 'todos os produtos'
                if regra.subcategoria:
                    alvo = f'{alvo} / {regra.subcategoria.nome}'
                resumo = PrecoService._resumo_desconto(regra.tipo_desconto, regra.valor_desconto)
                candidatos.append({
                    'preco': preco,
                    'tipo': 'categoria',
                    'origem': 'Desconto por categoria',
                    'detalhe': (
                        f'Desconto por categoria "{desconto.nome}" para {alvo}. '
                        f'{PrecoService._vigencia_texto(desconto.data_inicio, desconto.data_fim)}'
                    ),
                    'kit_categoria_id': desconto.pk,
                    'regra_id': regra.pk,
                    'quantidade_minima': regra.quantidade_minima,
                })
        return candidatos

    @staticmethod
    def combo_quantidade_vigente(
        promocao,
        data=None,
        validar_dia_semana: bool = True,
        minimo_dias_semana: int | None = None,
    ) -> bool:
        """Confere status, periodo e dia da semana de combo por quantidade."""
        data = data or timezone.localdate()
        if not getattr(promocao, 'ativo', True):
            return False
        if promocao.data_inicio and promocao.data_inicio > data:
            return False
        if promocao.data_fim and promocao.data_fim < data:
            return False
        if not PrecoService._cumpre_minimo_dias_semana(promocao.dias_semana, minimo_dias_semana):
            return False
        dias = PrecoService._dias_semana_set(promocao.dias_semana)
        return not validar_dia_semana or str(data.weekday()) in dias

    @staticmethod
    def precos_combo_quantidade_vigentes(
        produto: Produto,
        filial=None,
        quantidade: Decimal | int | str = Decimal('1'),
        data=None,
        validar_dia_semana: bool = True,
        minimo_dias_semana: int | None = None,
    ) -> list[Decimal]:
        """Calcula candidatos de preco por combo de quantidade vigente."""
        return [
            item['preco']
            for item in PrecoService.precos_combo_quantidade_vigentes_detalhados(
                produto,
                filial=filial,
                quantidade=quantidade,
                data=data,
                validar_dia_semana=validar_dia_semana,
                minimo_dias_semana=minimo_dias_semana,
            )
        ]

    @staticmethod
    def precos_combo_quantidade_vigentes_detalhados(
        produto: Produto,
        filial=None,
        quantidade: Decimal | int | str = Decimal('1'),
        data=None,
        validar_dia_semana: bool = True,
        minimo_dias_semana: int | None = None,
    ) -> list[dict]:
        """Calcula candidatos de combo por quantidade com origem legivel."""
        from apps.produtos.models import PromocaoQuantidade

        filial = filial or getattr(produto, 'filial', None)
        if not filial or not getattr(filial, 'pk', None):
            return []

        quantidade = Decimal(str(quantidade or '1'))
        promocoes = (
            PromocaoQuantidade.objects.for_filial(filial)
            .filter(produto=produto, ativo=True)
            .prefetch_related('faixas')
        )
        candidatos = []
        for promocao in promocoes:
            if not PrecoService.combo_quantidade_vigente(
                promocao,
                data=data,
                validar_dia_semana=validar_dia_semana,
                minimo_dias_semana=minimo_dias_semana,
            ):
                continue
            preco_base = produto.preco_venda or Decimal('0')
            if promocao.usar_preco_promocional:
                candidatos_base = [preco_base]
                preco_promocional = PrecoService.preco_promocional_vigente(
                    produto,
                    filial=filial,
                    data=data,
                    validar_dia_semana=validar_dia_semana,
                    minimo_dias_semana=minimo_dias_semana,
                )
                if preco_promocional is not None:
                    candidatos_base.append(preco_promocional)
                candidatos_base.extend(
                    PrecoService.precos_categoria_vigentes(
                        produto,
                        filial=filial,
                        quantidade=Decimal('1'),
                        data=data,
                        validar_dia_semana=validar_dia_semana,
                        minimo_dias_semana=minimo_dias_semana,
                    )
                )
                preco_base = min(candidatos_base)
            for faixa in promocao.faixas.all():
                if not faixa.aplica_para_quantidade(quantidade):
                    continue
                preco = PrecoService.aplicar_regra_desconto(
                    preco_base,
                    faixa.tipo_desconto,
                    faixa.valor,
                )
                resumo = PrecoService._resumo_desconto(faixa.tipo_desconto, faixa.valor)
                candidatos.append({
                    'preco': preco,
                    'tipo': 'combo',
                    'origem': 'Combo por quantidade',
                    'detalhe': (
                        f'Combo "{promocao.nome}" com {resumo} para quantidade '
                        f'{PrecoService._fmt_decimal(faixa.quantidade_minima, 3)}. '
                        f'{PrecoService._vigencia_texto(promocao.data_inicio, promocao.data_fim)}'
                    ),
                    'promocao_id': promocao.pk,
                    'faixa_id': faixa.pk,
                })
        return candidatos

    @staticmethod
    def preco_para_cliente(
        produto: Produto, quantidade: Decimal, tabela: TabelaPreco | None = None,
    ) -> Decimal:
        """
        Retorna preço aplicável considerando:
        - Tabela de preço (se fornecida)
        - Preço escalonado (quantidade mínima maior tem preço menor)
        - Vigência da tabela
        - Fallback para Produto.preco_atual (promocao ativa ou preco de venda)
        """
        hoje = timezone.localdate()
        if tabela and tabela.ativo:
            if tabela.data_inicio and tabela.data_inicio > hoje:
                tabela = None
            elif tabela.data_fim and tabela.data_fim < hoje:
                tabela = None

        if not tabela:
            return produto.preco_atual

        item = ItemTabelaPreco.objects.filter(
            tabela=tabela, produto=produto,
            quantidade_minima__lte=quantidade,
        ).order_by('-quantidade_minima').first()

        if not item:
            return produto.preco_atual

        # valor_final = preço unitário já com o desconto em R$ abatido.
        preco = item.valor_final
        if tabela.acrescimo_percentual:
            preco = preco * (1 + tabela.acrescimo_percentual / 100)
        return preco

    @staticmethod
    @transaction.atomic
    def recalcular_custo_medio(
        produto: Produto, quantidade_entrada: Decimal, custo_entrada: Decimal,
    ) -> Decimal:
        """
        Recalcula custo médio ponderado:
        ((custo_atual * qtd_atual) + (custo_entrada * qtd_entrada)) / (qtd_total)
        """
        from apps.estoque.models import Estoque
        try:
            estoque = Estoque.objects.select_for_update().get(
                produto=produto, filial=produto.filial,
            )
            qtd_atual = estoque.quantidade_atual
        except Estoque.DoesNotExist:
            qtd_atual = Decimal('0')

        qtd_total = qtd_atual + quantidade_entrada
        if qtd_total <= 0:
            novo_custo = custo_entrada
        else:
            novo_custo = (
                (produto.preco_custo_medio * qtd_atual) + (custo_entrada * quantidade_entrada)
            ) / qtd_total
            novo_custo = novo_custo.quantize(Decimal('0.0001'))

        produto.preco_custo_medio = novo_custo
        produto.calcular_margem()
        produto.save(update_fields=['preco_custo_medio', 'margem_lucro', 'updated_at'])
        return novo_custo

    @staticmethod
    def aplicar_desconto(preco: Decimal, percentual: Decimal, max_permitido: Decimal) -> Decimal:
        """Aplica desconto validando contra o teto permitido."""
        if percentual > max_permitido:
            raise DadosInvalidosError(
                f'Desconto de {percentual}% excede máximo permitido ({max_permitido}%).'
            )
        return preco * (1 - percentual / 100)
