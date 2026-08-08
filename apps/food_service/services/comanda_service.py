from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.food_service.models import Comanda, ComplementoItemComanda, ItemComanda, Mesa
from apps.food_service.services.ficha_tecnica_service import FichaTecnicaService
from apps.food_service.services.notificacao_service import notificar_pedido_recebido
from apps.pdv.services.venda_pdv_service import VendaPDVService


class ComandaService:
    """
    Ciclo de vida de uma comanda: abrir, lançar/remover/transferir itens,
    unir/dividir mesas ou comandas, e fechar convergindo para uma VendaPDV.
    """

    @classmethod
    @transaction.atomic
    def abrir(
        cls,
        *,
        filial,
        usuario,
        mesas: list[Mesa] | None = None,
        cliente=None,
        nome_ocupante: str = '',
        garcom=None,
        quantidade_pessoas: int = 1,
        tipo: str | None = None,
    ) -> Comanda:
        if not tipo:
            tipo = Comanda.Tipo.MESA if mesas else (
                Comanda.Tipo.CLIENTE if cliente else Comanda.Tipo.INDIVIDUAL
            )
        comanda = Comanda.objects.create(
            filial=filial,
            cliente=cliente,
            nome_ocupante=nome_ocupante,
            garcom=garcom,
            quantidade_pessoas=quantidade_pessoas or 1,
            tipo=tipo,
        )
        if mesas:
            comanda.mesas.set(mesas)
            Mesa.objects.filter(pk__in=[m.pk for m in mesas]).update(status=Mesa.Status.OCUPADA)
        return comanda

    @classmethod
    def adicionar_item(cls, *, comanda: Comanda, produto, quantidade, observacoes: str = '') -> ItemComanda:
        if comanda.status != Comanda.Status.ABERTA:
            raise DadosInvalidosError('Comanda não está aberta.')
        quantidade = Decimal(str(quantidade))
        if quantidade <= 0:
            raise DadosInvalidosError('Quantidade deve ser positiva.')
        preco_info = VendaPDVService.resolver_preco_produto(
            produto, comanda.filial, quantidade, cliente=comanda.cliente,
        )
        item = ItemComanda.objects.create(
            comanda=comanda,
            produto=produto,
            quantidade=quantidade,
            valor_unitario=preco_info['preco'],
            observacoes=observacoes,
            recebido_em=timezone.now(),
        )
        notificar_pedido_recebido(item)
        return item

    @classmethod
    def remover_item(cls, *, item: ItemComanda):
        if item.comanda.status != Comanda.Status.ABERTA:
            raise DadosInvalidosError('Comanda não está aberta.')
        item.delete()

    @classmethod
    def adicionar_complemento(
        cls, *, item: ItemComanda, produto, quantidade='1',
    ) -> ComplementoItemComanda:
        if item.comanda.status != Comanda.Status.ABERTA:
            raise DadosInvalidosError('Comanda não está aberta.')
        quantidade = Decimal(str(quantidade))
        if quantidade <= 0:
            raise DadosInvalidosError('Quantidade deve ser positiva.')
        preco_info = VendaPDVService.resolver_preco_produto(
            produto, item.comanda.filial, quantidade, cliente=item.comanda.cliente,
        )
        return ComplementoItemComanda.objects.create(
            item=item,
            produto=produto,
            quantidade=quantidade,
            valor_unitario=preco_info['preco'],
        )

    @classmethod
    def remover_complemento(cls, *, complemento: ComplementoItemComanda):
        if complemento.item.comanda.status != Comanda.Status.ABERTA:
            raise DadosInvalidosError('Comanda não está aberta.')
        complemento.delete()

    @classmethod
    def transferir_item(cls, *, item: ItemComanda, destino: Comanda) -> ItemComanda:
        if destino.status != Comanda.Status.ABERTA:
            raise DadosInvalidosError('Comanda de destino não está aberta.')
        item.comanda = destino
        item.save(update_fields=['comanda'])
        return item

    @classmethod
    @transaction.atomic
    def unir_comandas(cls, *, origem: Comanda, destino: Comanda):
        if origem.pk == destino.pk:
            raise DadosInvalidosError('Selecione comandas diferentes para unir.')
        for item in list(origem.itens.all()):
            cls.transferir_item(item=item, destino=destino)
        origem.status = Comanda.Status.CANCELADA
        origem.save(update_fields=['status'])

    @classmethod
    @transaction.atomic
    def separar(cls, *, comanda: Comanda) -> Comanda:
        """
        Abre uma comanda-irmã vazia nas mesmas mesas, pronta para receber
        itens transferidos desta (`transferir_item`) — o primitivo por trás
        de "separar comandas" quando a divisão é por item/por pessoa.
        """
        if comanda.status != Comanda.Status.ABERTA:
            raise DadosInvalidosError('Comanda não está aberta.')
        nova = Comanda.objects.create(
            filial=comanda.filial,
            garcom=comanda.garcom,
            tipo=Comanda.Tipo.INDIVIDUAL,
        )
        if comanda.mesas.exists():
            nova.mesas.set(comanda.mesas.all())
        return nova

    @classmethod
    @transaction.atomic
    def transferir_mesa(cls, *, comanda: Comanda, mesa_origem: Mesa, mesa_destino: Mesa):
        comanda.mesas.remove(mesa_origem)
        comanda.mesas.add(mesa_destino)
        mesa_destino.status = Mesa.Status.OCUPADA
        mesa_destino.save(update_fields=['status'])
        if not mesa_origem.comandas.filter(status=Comanda.Status.ABERTA).exists():
            mesa_origem.status = Mesa.Status.LIVRE
            mesa_origem.save(update_fields=['status'])

    @classmethod
    @transaction.atomic
    def unir_mesas(cls, *, comanda: Comanda, mesas_adicionais: list[Mesa]):
        comanda.mesas.add(*mesas_adicionais)
        Mesa.objects.filter(pk__in=[m.pk for m in mesas_adicionais]).update(status=Mesa.Status.OCUPADA)

    @classmethod
    @transaction.atomic
    def liberar_mesa(cls, *, comanda: Comanda, mesa: Mesa):
        """
        "Dividir mesas": separa uma mesa de uma comanda que cobre várias
        (união desfeita), liberando-a para outro atendimento sem fechar a
        comanda nem afetar as demais mesas vinculadas.
        """
        if comanda.mesas.count() <= 1:
            raise DadosInvalidosError('A comanda só tem uma mesa vinculada — não há o que dividir.')
        comanda.mesas.remove(mesa)
        if not mesa.comandas.filter(status=Comanda.Status.ABERTA).exists():
            mesa.status = Mesa.Status.LIVRE
            mesa.save(update_fields=['status'])

    @classmethod
    @transaction.atomic
    def fechar(
        cls,
        *,
        comanda: Comanda,
        request,
        pagamentos: list[dict],
        desconto=Decimal('0'),
        acrescimo=Decimal('0'),
        nota_divisao: str = '',
    ):
        from apps.pdv.views.pdv import _sessao_aberta

        if comanda.status != Comanda.Status.ABERTA:
            raise DadosInvalidosError('Comanda não está aberta.')

        itens_comanda = list(comanda.itens.prefetch_related('complementos').all())
        if not itens_comanda:
            raise DadosInvalidosError('Comanda sem itens lançados.')

        sessao = _sessao_aberta(request)
        if not sessao:
            raise DadosInvalidosError('Nenhuma sessão de caixa aberta.')

        # Cada complemento vira sua própria linha na venda (mesmo tratamento
        # fiscal/estoque de qualquer produto vendido) -- não existe um
        # conceito de "sub-item" em VendaPDV/ItemVendaPDV, então a relação
        # com o item principal fica só no lado da comanda (que continua
        # consultável no histórico após o fechamento).
        itens = []
        produtos_vendidos = []  # (produto, quantidade) -- pra explodir ficha técnica depois
        for item in itens_comanda:
            itens.append({'produto_id': item.produto_id, 'quantidade': item.quantidade})
            produtos_vendidos.append((item.produto, item.quantidade))
            for complemento in item.complementos.all():
                itens.append({'produto_id': complemento.produto_id, 'quantidade': complemento.quantidade})
                produtos_vendidos.append((complemento.produto, complemento.quantidade))

        observacao = comanda.observacoes
        if nota_divisao:
            observacao = f'{observacao}\n{nota_divisao}'.strip()

        venda = VendaPDVService.finalizar_venda(
            sessao=sessao,
            filial=comanda.filial,
            usuario=request.user,
            itens=itens,
            pagamentos=pagamentos,
            cliente_id=comanda.cliente_id,
            desconto=desconto,
            acrescimo=acrescimo,
            observacao=observacao,
            request=request,
        )

        # Pratos com ficha técnica ativa consomem os ingredientes do
        # estoque (não o próprio prato -- ver FichaTecnicaService). Prato
        # sem ficha cadastrada passa direto, sem efeito nenhum aqui.
        for produto, quantidade in produtos_vendidos:
            FichaTecnicaService.consumir_ingredientes(
                produto=produto,
                quantidade_vendida=quantidade,
                filial=comanda.filial,
                usuario=request.user,
                documento_id=venda.pk,
                documento_numero=str(venda.numero_venda),
            )

        comanda.venda_pdv = venda
        comanda.status = Comanda.Status.FECHADA
        comanda.fechada_em = timezone.now()
        comanda.save(update_fields=['venda_pdv', 'status', 'fechada_em'])

        for mesa in comanda.mesas.all():
            if not mesa.comandas.filter(status=Comanda.Status.ABERTA).exists():
                mesa.status = Mesa.Status.AGUARDANDO_LIMPEZA
                mesa.save(update_fields=['status'])

        return venda

    @staticmethod
    def _linha_pdv(produto):
        return {
            'produto_id': produto.pk,
            'descricao': produto.descricao_pdv or produto.descricao,
            'codigo_barras': produto.codigo_barras or '',
            'tipo_produto': produto.tipo_produto,
            'icone': produto.linha_producao.icone if produto.linha_producao else '📦',
            'cor': produto.linha_producao.cor_identificacao if produto.linha_producao else None,
            'linha': produto.linha_producao.nome if produto.linha_producao else None,
        }

    @classmethod
    def itens_para_pdv(cls, comanda: Comanda) -> list[dict]:
        """
        Monta os itens da comanda no mesmo formato que o carrinho do PDV usa
        pra renderizar (espelha `api_pendente_detalhe`) -- so leitura, nao
        grava nada. Usado quando o fechamento passa a escolher a forma de
        pagamento la no PDV em vez de aqui.
        """
        if comanda.status != Comanda.Status.ABERTA:
            raise DadosInvalidosError('Comanda não está aberta.')

        itens_comanda = list(
            comanda.itens
            .select_related('produto__linha_producao')
            .prefetch_related('complementos__produto__linha_producao')
            .all()
        )
        if not itens_comanda:
            raise DadosInvalidosError('Comanda sem itens lançados.')

        itens = []
        for item in itens_comanda:
            itens.append({
                **cls._linha_pdv(item.produto),
                'quantidade': float(item.quantidade),
                'valor_unitario': float(item.valor_unitario),
                'valor_total': float(item.valor_total),
            })
            for complemento in item.complementos.all():
                itens.append({
                    **cls._linha_pdv(complemento.produto),
                    'quantidade': float(complemento.quantidade),
                    'valor_unitario': float(complemento.valor_unitario),
                    'valor_total': float(complemento.valor_total),
                })
        return itens

    @classmethod
    @transaction.atomic
    def fechar_apos_pdv(cls, *, comanda: Comanda, venda, usuario):
        """
        Fecha a comanda depois que o pagamento foi feito direto no PDV (fluxo
        novo, ver `itens_para_pdv`) -- espelha o final de `fechar()`, mas sem
        repetir a baixa de estoque do produto vendido (a `finalizar_venda` do
        PDV ja fez isso) e sem recriar a VendaPDV (ja existe). So consome a
        ficha tecnica e fecha a comanda/libera as mesas.
        """
        if comanda.status != Comanda.Status.ABERTA:
            # Ja foi fechada por outro caminho (ex.: duplo clique/reload) --
            # a venda no PDV ja aconteceu de qualquer forma, entao nao ha
            # erro a levantar aqui, so nada mais a fazer.
            return

        produtos_vendidos = []
        for item in comanda.itens.prefetch_related('complementos').all():
            produtos_vendidos.append((item.produto, item.quantidade))
            for complemento in item.complementos.all():
                produtos_vendidos.append((complemento.produto, complemento.quantidade))

        for produto, quantidade in produtos_vendidos:
            FichaTecnicaService.consumir_ingredientes(
                produto=produto,
                quantidade_vendida=quantidade,
                filial=comanda.filial,
                usuario=usuario,
                documento_id=venda.pk,
                documento_numero=str(venda.numero_venda),
            )

        comanda.venda_pdv = venda
        comanda.status = Comanda.Status.FECHADA
        comanda.fechada_em = timezone.now()
        comanda.save(update_fields=['venda_pdv', 'status', 'fechada_em'])

        for mesa in comanda.mesas.all():
            if not mesa.comandas.filter(status=Comanda.Status.ABERTA).exists():
                mesa.status = Mesa.Status.AGUARDANDO_LIMPEZA
                mesa.save(update_fields=['status'])
