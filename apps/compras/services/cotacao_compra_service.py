"""Orquestracao e persistencia da analise de custo de compra."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from apps.cadastros.models import Fornecedor
from apps.cadastros.services.replicacao_service import ReplicacaoCadastrosService
from apps.compras.models import (
    CalculoTributarioCompra, CotacaoCompra, CotacaoCompraFornecedor,
    CotacaoCompraItem, CotacaoCompraPreco, EntradaNF, ItemEntradaNF,
)
from apps.compras.services.purchase_tax_service import PurchaseTaxService
from apps.core.constants.tributacao import (
    normalizar_regimes_cotacao, regime_ibs_cbs_padrao,
)
from apps.core.services.exceptions import DadosInvalidosError
from apps.core.tenant_context import tenant_atomic
from apps.produtos.models import Produto


REGIMES_EMPRESARIAIS = {'mei', 'simples_nacional', 'lucro_presumido', 'lucro_real'}
REGIMES_IBS_CBS = {'mei', 'simples', 'regular'}


def _decimal(valor, campo, *, minimo=Decimal('0')):
    try:
        numero = Decimal(str(valor).replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError):
        raise DadosInvalidosError(f'{campo} invalido.')
    if numero < minimo:
        raise DadosInvalidosError(f'{campo} deve ser maior ou igual a {minimo}.')
    return numero


class CotacaoCompraService:
    @staticmethod
    def _validar_regimes(regime, regime_ibs_cbs, sujeito):
        if regime not in REGIMES_EMPRESARIAIS:
            raise DadosInvalidosError(f'Informe o regime tributario de {sujeito}.')
        if regime_ibs_cbs not in REGIMES_IBS_CBS:
            raise DadosInvalidosError(f'Informe o regime IBS/CBS de {sujeito}.')

    @classmethod
    @tenant_atomic
    def criar_e_analisar(
        cls, *, filial, usuario, dados,
        pode_editar_fornecedor=False, pode_criar_fornecedor=False,
    ):
        produtos_dados = dados.get('products') or []
        fornecedores_dados = dados.get('suppliers') or []
        precos_dados = dados.get('prices') or []
        if not produtos_dados:
            raise DadosInvalidosError('Selecione pelo menos um produto.')
        if len(fornecedores_dados) < 2:
            raise DadosInvalidosError('Selecione ou adicione pelo menos dois fornecedores.')

        regime_comprador_informado = dados.get('buyer_regime', '')
        regime_comprador, regime_ibs_comprador = normalizar_regimes_cotacao(
            regime_comprador_informado,
            dados.get('buyer_ibs_cbs', ''),
        )
        cls._validar_regimes(regime_comprador, regime_ibs_comprador, 'sua empresa')
        try:
            data_referencia = date.fromisoformat(
                dados.get('reference_date') or timezone.localdate().isoformat()
            )
        except ValueError:
            raise DadosInvalidosError('Data de referencia invalida.')

        produtos_ids = [item.get('id') for item in produtos_dados]
        produtos = {
            str(produto.pk): produto
            for produto in Produto.objects.for_filial(filial).filter(
                pk__in=produtos_ids, ativo=True,
            ).select_related('unidade_medida', 'classe_fiscal')
        }
        if len(produtos_ids) != len(set(map(str, produtos_ids))):
            raise DadosInvalidosError('Nao repita produtos na mesma cotacao.')
        if len(produtos) != len(produtos_ids):
            raise DadosInvalidosError('Um dos produtos nao pertence a filial ativa.')

        ultimas_compras = cls._ultimas_compras(
            filial=filial,
            produtos_ids=produtos_ids,
            data_referencia=data_referencia,
        )

        cotacao = CotacaoCompra.objects.create(
            filial=filial,
            numero=f'ACI-{timezone.localtime():%Y%m%d%H%M%S%f}',
            usuario=usuario,
            data_referencia=data_referencia,
            regime_comprador=regime_comprador,
            regime_ibs_cbs_comprador=regime_ibs_comprador,
            empresa_snapshot={
                'empresa_id': filial.empresa_id,
                'razao_social': filial.empresa.razao_social,
                'nome_fantasia': filial.empresa.nome_fantasia,
                'cnpj': filial.empresa.cnpj,
                'filial_id': filial.pk,
                'filial_nome': filial.nome_fantasia or filial.razao_social,
                'regime_tributario': regime_comprador,
                'regime_ibs_cbs': regime_ibs_comprador,
                'regime_informado': regime_comprador_informado,
            },
            observacao=str(dados.get('observation') or '')[:2000],
        )

        itens = {}
        for posicao, item_dado in enumerate(produtos_dados, start=1):
            produto = produtos[str(item_dado.get('id'))]
            quantidade = _decimal(item_dado.get('quantity'), f'Quantidade de {produto}', minimo=Decimal('0.001'))
            item = CotacaoCompraItem.objects.create(
                cotacao=cotacao,
                produto=produto,
                produto_descricao=produto.descricao,
                produto_codigo=produto.codigo,
                produto_ncm=produto.ncm,
                ultima_compra_snapshot=ultimas_compras.get(produto.pk, {}),
                quantidade=quantidade,
                unidade_sigla=getattr(produto.unidade_medida, 'sigla', '') or '',
                produto_fiscal_snapshot={
                    'ncm': produto.ncm,
                    'classe_fiscal_id': produto.classe_fiscal_id,
                    'cst_cbs': produto.cst_cbs,
                    'classificacao_tributaria_cbs': produto.classificacao_tributaria_cbs,
                    'aliquota_cbs': str(produto.aliquota_cbs),
                    'cst_ibs': produto.cst_ibs,
                    'classificacao_tributaria_ibs': produto.classificacao_tributaria_ibs,
                    'aliquota_ibs_uf': str(produto.aliquota_ibs_uf),
                    'aliquota_ibs_municipal': str(produto.aliquota_ibs_municipal),
                },
            )
            itens[str(produto.pk)] = item

        fornecedores = {}
        fornecedores_cadastrados = set()
        for fornecedor_dado in fornecedores_dados:
            fornecedor_dado = dict(fornecedor_dado)
            regime_informado = fornecedor_dado.get('regime', '')
            regime_normalizado, ibs_normalizado = normalizar_regimes_cotacao(
                regime_informado,
                fornecedor_dado.get('ibs_cbs', ''),
            )
            fornecedor_dado['regime'] = regime_normalizado
            fornecedor_dado['ibs_cbs'] = ibs_normalizado
            chave = str(fornecedor_dado.get('key') or '')
            if not chave or chave in fornecedores:
                raise DadosInvalidosError('Fornecedor repetido ou sem identificador.')
            fornecedor, manual, salvo = cls._resolver_fornecedor(
                filial, fornecedor_dado, pode_criar_fornecedor,
            )
            if fornecedor and fornecedor.pk in fornecedores_cadastrados:
                raise DadosInvalidosError('Nao repita fornecedores na mesma cotacao.')
            if fornecedor:
                fornecedores_cadastrados.add(fornecedor.pk)
            regime = fornecedor_dado.get('regime') or (
                fornecedor.regime_tributario if fornecedor else ''
            ) or ('simples_nacional' if fornecedor and fornecedor.optante_simples else '')
            regime_ibs = fornecedor_dado.get('ibs_cbs') or (
                fornecedor.regime_ibs_cbs if fornecedor else ''
            ) or regime_ibs_cbs_padrao(regime)
            cls._validar_regimes(regime, regime_ibs, f'fornecedor {fornecedor_dado.get("name") or fornecedor}')

            if fornecedor and fornecedor_dado.get('update_registration'):
                if not pode_editar_fornecedor:
                    raise DadosInvalidosError('Sem permissao para atualizar o cadastro do fornecedor.')
                fornecedor.regime_tributario = regime
                fornecedor.regime_ibs_cbs = regime_ibs
                fornecedor.optante_simples = regime in {'mei', 'simples_nacional'}
                fornecedor.save(update_fields=[
                    'regime_tributario', 'regime_ibs_cbs', 'optante_simples', 'updated_at',
                ])
                ReplicacaoCadastrosService.sincronizar_fornecedor(fornecedor)

            nome = (fornecedor_dado.get('name') or (str(fornecedor) if fornecedor else '')).strip()
            cnpj = ''.join(filter(str.isdigit, fornecedor_dado.get('cnpj') or (
                fornecedor.cpf_cnpj if fornecedor else ''
            )))
            participante = CotacaoCompraFornecedor.objects.create(
                cotacao=cotacao,
                fornecedor=fornecedor,
                manual_supplier=manual,
                supplier_name=nome,
                supplier_cnpj=cnpj,
                supplier_tax_regime=regime,
                supplier_tax_ibs_cbs=regime_ibs,
                salvo_no_cadastro=salvo,
                supplier_snapshot={
                    'manualSupplier': manual,
                    'supplierId': fornecedor.pk if fornecedor else None,
                    'supplierName': nome,
                    'supplierCNPJ': cnpj,
                    'supplierTaxRegime': regime,
                    'supplierTaxIBSCBS': regime_ibs,
                    'supplierTaxRegimeInformado': regime_informado,
                    'observacao': str(fornecedor_dado.get('notes') or '')[:500],
                },
            )
            fornecedores[chave] = participante

        calculos_por_item = {item.pk: [] for item in itens.values()}
        pares_precos = set()
        for preco_dado in precos_dados:
            item = itens.get(str(preco_dado.get('product_id')))
            participante = fornecedores.get(str(preco_dado.get('supplier_key')))
            if not item or not participante or preco_dado.get('unit_price') in (None, ''):
                continue
            par = (item.pk, participante.pk)
            if par in pares_precos:
                raise DadosInvalidosError('Nao repita o preco de um produto para o mesmo fornecedor.')
            pares_precos.add(par)
            valor_unitario = _decimal(preco_dado.get('unit_price'), 'Preco unitario', minimo=Decimal('0.0001'))
            frete = _decimal(preco_dado.get('freight') or 0, 'Frete')
            desconto = _decimal(preco_dado.get('discount') or 0, 'Desconto')
            preco = CotacaoCompraPreco.objects.create(
                item=item,
                fornecedor=participante,
                valor_unitario=valor_unitario,
                frete_nao_recuperavel=frete,
                desconto=desconto,
                condicoes_comerciais=preco_dado.get('commercial_terms') or {},
            )
            resultado = PurchaseTaxService.calcular(
                empresa=filial.empresa,
                data_referencia=data_referencia,
                produto=item.produto,
                quantidade=item.quantidade,
                valor_unitario=valor_unitario,
                frete_nao_recuperavel=frete,
                desconto=desconto,
                regime_comprador=regime_comprador,
                regime_ibs_cbs_comprador=regime_ibs_comprador,
                regime_fornecedor=participante.supplier_tax_regime,
                regime_ibs_cbs_fornecedor=participante.supplier_tax_ibs_cbs,
            )
            calculo = CalculoTributarioCompra.objects.create(
                preco=preco,
                regra=resultado.regra,
                valor_bruto=resultado.valor_bruto,
                custos_nao_recuperaveis=resultado.custos_nao_recuperaveis,
                credito_ibs=resultado.credito_ibs,
                credito_cbs=resultado.credito_cbs,
                outros_creditos=resultado.outros_creditos,
                credito_total=resultado.credito_total,
                custo_efetivo=resultado.custo_efetivo,
                regra_snapshot=resultado.regra_snapshot,
            )
            calculos_por_item[item.pk].append(calculo)

        if any(not calculos for calculos in calculos_por_item.values()):
            raise DadosInvalidosError('Informe pelo menos um preco para cada produto.')

        vencedores = []
        for calculos in calculos_por_item.values():
            for posicao, calculo in enumerate(sorted(calculos, key=lambda obj: (obj.custo_efetivo, obj.preco.valor_unitario)), start=1):
                calculo.posicao = posicao
                calculo.vencedor = posicao == 1
                calculo.save(update_fields=['posicao', 'vencedor', 'updated_at'])
                if posicao == 1:
                    vencedores.append(calculo)

        cotacao.valor_nominal_total = sum((c.valor_bruto + c.custos_nao_recuperaveis - c.preco.desconto for c in vencedores), Decimal('0'))
        cotacao.creditos_estimados_total = sum((c.credito_total for c in vencedores), Decimal('0'))
        cotacao.custo_efetivo_total = sum((c.custo_efetivo for c in vencedores), Decimal('0'))
        cls._calcular_comparacao_fornecedor_unico(cotacao, calculos_por_item, fornecedores)
        cotacao.save(update_fields=[
            'valor_nominal_total', 'creditos_estimados_total', 'custo_efetivo_total',
            'economia_estimada', 'fornecedor_base_nome', 'updated_at',
        ])
        return cotacao

    @staticmethod
    def _ultimas_compras(*, filial, produtos_ids, data_referencia):
        """Retorna um snapshot da ultima entrada efetivada de cada produto."""
        itens_entrada = (
            ItemEntradaNF.objects
            .filter(
                entrada__filial=filial,
                entrada__status=EntradaNF.Status.EFETIVADA,
                entrada__data_emissao_nf__lte=data_referencia,
                produto_id__in=produtos_ids,
            )
            .select_related('entrada__fornecedor')
            .order_by(
                'produto_id',
                '-entrada__data_emissao_nf',
                '-entrada__data_entrada',
                '-pk',
            )
        )
        snapshots = {}
        for item in itens_entrada:
            if item.produto_id in snapshots:
                continue
            custo_unitario = item.custo_unitario_total or item.valor_unitario
            snapshots[item.produto_id] = {
                'entrada_id': item.entrada_id,
                'numero_nf': item.entrada.numero_nf,
                'serie_nf': item.entrada.serie_nf,
                'data_compra': item.entrada.data_emissao_nf.isoformat(),
                'data_entrada': item.entrada.data_entrada.isoformat(),
                'fornecedor_id': item.entrada.fornecedor_id,
                'fornecedor_nome': str(item.entrada.fornecedor),
                'custo_unitario': str(custo_unitario),
                'valor_unitario_nf': str(item.valor_unitario),
            }
        return snapshots

    @staticmethod
    def _resolver_fornecedor(filial, dados, pode_criar_fornecedor):
        if dados.get('type') == 'registered':
            fornecedor = Fornecedor.objects.for_filial(filial).filter(pk=dados.get('id'), ativo=True).first()
            if not fornecedor:
                raise DadosInvalidosError('Um dos fornecedores nao pertence a filial ativa.')
            return fornecedor, False, False

        nome = str(dados.get('name') or '').strip()
        if not nome:
            raise DadosInvalidosError('Informe o nome do fornecedor manual.')
        cnpj = ''.join(filter(str.isdigit, str(dados.get('cnpj') or '')))
        if cnpj and len(cnpj) != 14:
            raise DadosInvalidosError(f'CNPJ invalido para {nome}.')
        if not dados.get('save_registration'):
            return None, True, False
        if not pode_criar_fornecedor:
            raise DadosInvalidosError('Sem permissao para salvar o fornecedor no cadastro.')
        existente = Fornecedor.objects.for_filial(filial).filter(cpf_cnpj=cnpj).first() if cnpj else None
        if existente:
            return existente, False, False
        fornecedor = Fornecedor.objects.create(
            filial=filial,
            tipo_pessoa='J',
            razao_social=nome,
            nome_fantasia=nome,
            cpf_cnpj=cnpj,
            regime_tributario=dados.get('regime', ''),
            regime_ibs_cbs=dados.get('ibs_cbs', ''),
            optante_simples=dados.get('regime') in {'mei', 'simples_nacional'},
            observacao=str(dados.get('notes') or '')[:500],
        )
        ReplicacaoCadastrosService.sincronizar_fornecedor(fornecedor)
        return fornecedor, False, True

    @staticmethod
    def _calcular_comparacao_fornecedor_unico(cotacao, calculos_por_item, fornecedores):
        total_itens = len(calculos_por_item)
        totais = {}
        for participante in fornecedores.values():
            calculos = [
                calculo
                for lista in calculos_por_item.values()
                for calculo in lista
                if calculo.preco.fornecedor_id == participante.pk
            ]
            if len(calculos) == total_itens:
                totais[participante.pk] = sum((calculo.custo_efetivo for calculo in calculos), Decimal('0'))
        if not totais:
            return
        fornecedor_id, total = min(totais.items(), key=lambda par: par[1])
        participante = next(item for item in fornecedores.values() if item.pk == fornecedor_id)
        cotacao.fornecedor_base_nome = participante.supplier_name
        cotacao.economia_estimada = max(total - cotacao.custo_efetivo_total, Decimal('0'))
