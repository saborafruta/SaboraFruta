"""
API REST de vendas do PDV.

Autenticacao: sessao do proprio ERP, mesmo padrao/motivo de
apps.produtos.api e apps.estoque.api (arquitetura multi-tenant sem login
JWT tenant-aware ainda). RBAC via `TemPermissaoPDV`
(`usuario.tem_permissao('pdv', acao)`).

Montada em `/api/pdv/`, no mesmo padrao de `/api/estoque/equalizacao/` e
`/api/produtos/`.

ESCOPO (decisao registrada): so venda de balcao/caixa (PDV, baixa
imediata), chamando `VendaPDVService.finalizar_venda` -- NAO o pedido B2B
de `apps.vendas` (reserva -> separacao -> faturamento). Investigacao
prévia mostrou dois dominios de venda bem diferentes, cada um com service
proprio ja testado; reimplementar a regra de negocio aqui (preco com
promocao/kit/tabela, FEFO, conta a receber) divergiria de producao. Esta
view e' uma casca fina: traduz o payload da API pros kwargs que o service
ja aceita, e devolve o resultado formatado.

APRESENTACAO NA VENDA: cada item pode vir com `apresentacao_id` +
`quantidade_comercial` em vez de `quantidade` direto. A CONVERSAO de
quantidade acontece aqui, na borda, via `ApresentacaoService` --
`quantidade` (unidade base) e' o que chega no `VendaPDVService`. O PRECO,
porem, e' resolvido pelo proprio `VendaPDVService`/`PrecoService`
(`apresentacao_id` e' passado adiante, nao so' a quantidade convertida) --
respeitando `ItemTabelaPreco.apresentacao` (preco por tabela/cliente/
filial especifico da apresentacao) com fallback pra
`ProdutoApresentacao.preco_venda`, sem empilhar promocao/desconto de
categoria do produto em cima (regra 5.8: preco de apresentacao e'
independente). Efeito colateral aceito: o `ItemVendaPDV` resultante
grava a quantidade so em unidade base (como sempre gravou) -- qual
apresentacao/fator foram usados na venda NAO fica registrado no item
para auditoria futura. Registrar isso no proprio ItemVendaPDV (colunas
novas, ou snapshot) continua fora do escopo desta fase.

IDEMPOTENCIA: o header opcional `Idempotency-Key` e' gravado junto com a
propria venda, antes de qualquer baixa de estoque. A constraint UNIQUE
decide corridas reais: a requisicao perdedora falha antes dos efeitos da
venda e recupera o registro vencedor. O mesmo contrato e' usado pelo PDV
visual e pela API REST.
"""
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError
from drf_spectacular.utils import extend_schema
from rest_framework.authentication import SessionAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.services.exceptions import DadosInvalidosError, EstoqueInsuficienteError
from apps.core.services.request_scope import empresa_operacional
from apps.pdv.models import SessaoPDV, VendaPDV
from apps.pdv.services.venda_pdv_service import VendaPDVService
from apps.produtos.api.exceptions import erro_response as _erro
from apps.produtos.api.exceptions import formatar_erros_api
from apps.produtos.models import Produto, ProdutoApresentacao
from apps.produtos.services.apresentacao_service import ApresentacaoService

from .permissions import TemPermissaoPDV
from .serializers import VendaCreateInputSerializer, VendaPDVSerializer
from .throttling import ThrottleVendaPDV


def _sessao_aberta(request, filial):
    return SessaoPDV.objects.for_filial(filial).filter(usuario=request.user, status='aberto').first()


def _converter_apresentacao_para_base(item, empresa):
    """Se o item veio com apresentacao_id+quantidade_comercial, resolve a
    apresentacao e devolve o item com `quantidade` em unidade base (mas
    mantendo `apresentacao_id`: `VendaPDVService` agora resolve o PRECO da
    apresentacao sozinho, via `PrecoService`/`ProdutoVendavelService` --
    ver apps/pdv/services/venda_pdv_service.py e
    apps/produtos/services/preco_service.py. Antes desta conexao, esta
    funcao calculava e injetava `preco_manual` aqui; nao precisa mais.
    Levanta DadosInvalidosError se a apresentacao nao existir/nao
    pertencer ao produto informado (o service tambem valida isso de novo,
    mas falhar cedo aqui, antes de chamar o service, da' um erro mais
    claro pra quem so' errou a conversao de quantidade)."""
    apresentacao_id = item.get('apresentacao_id')
    if not apresentacao_id:
        return item

    quantidade_comercial = item.get('quantidade_comercial')
    if quantidade_comercial in (None, ''):
        raise DadosInvalidosError('Informe quantidade_comercial junto com apresentacao_id.')

    try:
        apresentacao = ProdutoApresentacao.objects.select_related('unidade', 'produto').get(
            pk=apresentacao_id, produto_id=item['produto_id'], produto__in=Produto.objects.for_empresa(empresa),
            ativo=True,
        )
    except ProdutoApresentacao.DoesNotExist:
        raise DadosInvalidosError(
            f'Apresentacao {apresentacao_id} nao encontrada para o produto {item["produto_id"]}.',
        )
    if not apresentacao.permite_venda:
        raise DadosInvalidosError(f'A apresentacao "{apresentacao.descricao}" nao pode ser vendida.')

    try:
        quantidade_base = ApresentacaoService.converter(apresentacao, quantidade_comercial, para='base')
    except (InvalidOperation, ValueError):
        raise DadosInvalidosError(f'quantidade_comercial invalida: {quantidade_comercial!r}.')

    novo_item = {k: v for k, v in item.items() if k != 'quantidade_comercial'}
    novo_item['quantidade'] = str(quantidade_base)
    return novo_item


class VendaCreateView(APIView):
    """POST /api/pdv/vendas/"""

    authentication_classes = [SessionAuthentication]
    permission_classes = [TemPermissaoPDV]
    permissao_acao = 'criar'
    throttle_classes = [ThrottleVendaPDV]

    def get_exception_handler(self):
        return formatar_erros_api

    @extend_schema(request=VendaCreateInputSerializer, responses={201: VendaPDVSerializer, 200: VendaPDVSerializer})
    def post(self, request):
        filial = getattr(request, 'filial_ativa', None)
        if filial is None:
            return _erro('Nenhuma filial ativa selecionada nesta sessao.')

        chave_idempotencia = request.headers.get('Idempotency-Key', '').strip()
        if chave_idempotencia:
            venda_existente = VendaPDV.objects.for_filial(filial).filter(idempotency_key=chave_idempotencia).first()
            if venda_existente:
                return Response(VendaPDVSerializer(venda_existente).data, status=200)

        sessao = _sessao_aberta(request, filial)
        if sessao is None:
            return _erro('Nenhuma sessao de caixa aberta para este usuario nesta filial.')

        entrada = VendaCreateInputSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        empresa = empresa_operacional(request)

        try:
            itens = [_converter_apresentacao_para_base(item, empresa) for item in dados['itens']]
        except DadosInvalidosError as exc:
            return _erro(str(exc), codigo='apresentacao_invalida', campo='itens')

        try:
            venda = VendaPDVService.finalizar_venda(
                sessao=sessao,
                filial=filial,
                usuario=request.user,
                itens=itens,
                pagamentos=list(dados.get('pagamentos') or []),
                cliente_id=dados.get('cliente_id'),
                desconto=Decimal(str(dados.get('desconto') or '0')),
                acrescimo=Decimal(str(dados.get('acrescimo') or '0')),
                observacao=dados.get('observacao') or '',
                bonificacao=dados.get('bonificacao') or False,
                forcar_estoque_negativo=dados.get('forcar_estoque_negativo', True),
                idempotency_key=chave_idempotencia or None,
                request=request,
            )
        except EstoqueInsuficienteError as exc:
            return _erro(str(exc), codigo='estoque_insuficiente', campo=None)
        except DadosInvalidosError as exc:
            return _erro(str(exc))
        except IntegrityError:
            venda_existente = VendaPDV.objects.for_filial(filial).filter(idempotency_key=chave_idempotencia).first()
            if venda_existente:
                return Response(VendaPDVSerializer(venda_existente).data, status=200)
            raise

        return Response(VendaPDVSerializer(venda).data, status=201)
