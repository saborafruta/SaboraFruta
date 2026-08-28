"""
Vendas feitas durante a viagem.

O SALDO DA CARGA É O LIMITE
===========================

Nunca se vende mais do que está no caminhão. Não é uma validação de formulário
— é a regra que impede a mesma mercadoria de ser vendida duas vezes e o saldo
de fechar negativo no retorno, quando já não há como saber o que aconteceu.

A checagem acontece no serviço, e não na tela, porque a venda também pode
chegar por outro caminho (aplicativo do vendedor, importação) e a regra tem
que valer em todos.

A VENDA NÃO MEXE NO ESTOQUE DA FILIAL
=====================================

Aquela mercadoria já saiu de lá quando a carga fechou, amparada pela remessa.
Baixar de novo aqui contaria a mesma saída duas vezes. O que a venda faz é
consumir o saldo em poder da viagem.
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.logistica.models import (
    ItemVendaViagem, SaldoCarga, VendaViagem, Viagem,
)
from apps.logistica.services.viagem import ViagemService

ZERO = Decimal('0')

# A venda na rua só faz sentido enquanto o caminhão está fora. Antes de sair
# não há saldo; depois de finalizar, a viagem já prestou contas.
VIAGENS_QUE_VENDEM = (
    Viagem.Status.EM_TRANSITO,
    Viagem.Status.EM_VENDAS,
    Viagem.Status.RETORNANDO,
    Viagem.Status.AGUARDANDO_CONFERENCIA,
)


class VendaViagemService:

    # ── O que dá para vender ─────────────────────────────────────────────

    @classmethod
    def saldo_disponivel(cls, viagem, produto, lote=None) -> Decimal:
        """Quanto deste produto ainda está no caminhão."""
        saldo = SaldoCarga.objects.filter(
            viagem=viagem, produto=produto, lote=lote,
        ).first()
        return saldo.quantidade_em_poder if saldo else ZERO

    @classmethod
    def disponivel_para_venda(cls, viagem) -> list[dict]:
        """
        O que a viagem tem para vender, produto a produto.

        É o que a tela oferece: vender de um produto que não viajou não é
        engano de digitação, é impossibilidade — e por isso a lista já sai
        limitada ao que está no caminhão.
        """
        linhas = []
        for saldo in viagem.saldos.select_related('produto', 'lote'):
            if saldo.quantidade_em_poder <= ZERO:
                continue
            linhas.append({
                'saldo': saldo,
                'produto': saldo.produto,
                'lote': saldo.lote,
                'disponivel': saldo.quantidade_em_poder,
                'remetida': saldo.quantidade_remetida or ZERO,
            })
        linhas.sort(key=lambda linha: str(linha['produto']))
        return linhas

    # ── Registrar ────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def registrar(cls, viagem: Viagem, dados: dict, usuario=None) -> VendaViagem:
        """
        Registra uma venda e baixa o saldo da carga.

        A QUANTIDADE É CONFERIDA CONTRA O SALDO antes de qualquer coisa ser
        gravada. Deixar a venda entrar e corrigir depois produziria saldo
        negativo, que no retorno é indistinguível de furto.
        """
        cls._exigir_viagem_na_rua(viagem)

        produto = dados.get('produto')
        if produto is None:
            raise DadosInvalidosError('Escolha o produto vendido.')
        quantidade = Decimal(str(dados.get('quantidade') or 0))
        if quantidade <= ZERO:
            raise DadosInvalidosError('A quantidade precisa ser maior que zero.')

        lote = dados.get('lote')
        disponivel = cls.saldo_disponivel(viagem, produto, lote)
        if disponivel <= ZERO:
            raise DadosInvalidosError(
                f'{produto} não está nesta viagem — só dá para vender o que '
                'saiu no caminhão.'
            )
        if quantidade > disponivel:
            raise DadosInvalidosError(
                f'{produto}: a carga tem {disponivel} e a venda é de {quantidade}. '
                'Nunca se vende mais do que está no caminhão.'
            )

        cliente = dados.get('cliente')
        nome = (dados.get('cliente_nome') or '').strip()
        if cliente and not nome:
            nome = str(cliente)
        if not nome:
            raise DadosInvalidosError('Informe para quem foi a entrega.')

        # BONIFICAÇÃO É A MESMA ENTREGA COM OUTRA NATUREZA: mesmo cliente,
        # mesmos itens, mesmo saldo — muda que ninguém paga e que ela baixa
        # em outra coluna da conciliação.
        tipo = dados.get('tipo') or VendaViagem.Tipo.VENDA
        if tipo not in VendaViagem.Tipo.values:
            raise DadosInvalidosError('Tipo de entrega desconhecido.')

        venda = VendaViagem.objects.create(
            viagem=viagem,
            tipo=tipo,
            numero=cls.proximo_numero(viagem),
            data=dados.get('data') or timezone.now(),
            cliente=cliente,
            cliente_nome=nome,
            cliente_documento=cls._documento(dados, cliente),
            endereco=cls._endereco(dados, cliente),
            condicao_pagamento=dados.get('condicao_pagamento'),
            forma_pagamento=dados.get('forma_pagamento'),
            observacao=(dados.get('observacao') or '').strip(),
            vendedor=usuario or viagem.vendedor,
        )
        ItemVendaViagem.objects.create(
            venda=venda, produto=produto, lote=lote,
            quantidade=quantidade,
            valor_unitario=Decimal(str(dados.get('valor_unitario') or 0)),
            # O VINCULO COM A REMESSA E' GRAVADO AGORA, e nao descoberto
            # depois: se a remessa for cancelada e reemitida, a busca
            # passaria a apontar a nota nova para mercadoria que saiu sob a
            # antiga.
            remessa=cls._remessa(viagem),
        )
        venda.recalcular_total()

        # A BAIXA E' NO SALDO DA VIAGEM, nao no estoque da filial: aquela
        # mercadoria ja' saiu de la' quando a carga fechou. A COLUNA DEPENDE
        # DO TIPO -- somar bonificacao em "vendido" faria a viagem parecer ter
        # faturado o que foi dado.
        ViagemService.registrar_saida_do_saldo(
            viagem, produto, quantidade, venda.campo_do_saldo, lote=lote,
        )
        return venda

    @classmethod
    @transaction.atomic
    def adicionar_item(cls, venda: VendaViagem, dados: dict) -> ItemVendaViagem:
        """Mais um produto na mesma venda — um cliente costuma levar vários."""
        cls._exigir_viagem_na_rua(venda.viagem)
        if venda.status != VendaViagem.Status.REGISTRADA:
            raise DadosInvalidosError('Esta venda foi cancelada.')

        produto = dados.get('produto')
        quantidade = Decimal(str(dados.get('quantidade') or 0))
        lote = dados.get('lote')
        if produto is None or quantidade <= ZERO:
            raise DadosInvalidosError('Informe o produto e a quantidade.')

        disponivel = cls.saldo_disponivel(venda.viagem, produto, lote)
        if quantidade > disponivel:
            raise DadosInvalidosError(
                f'{produto}: a carga tem {disponivel} e a venda é de {quantidade}.'
            )

        item = ItemVendaViagem.objects.create(
            venda=venda, produto=produto, lote=lote, quantidade=quantidade,
            valor_unitario=Decimal(str(dados.get('valor_unitario') or 0)),
            remessa=cls._remessa(venda.viagem),
        )
        venda.recalcular_total()
        ViagemService.registrar_saida_do_saldo(
            venda.viagem, produto, quantidade, venda.campo_do_saldo, lote=lote,
        )
        return item

    @classmethod
    @transaction.atomic
    def cancelar(cls, venda: VendaViagem, motivo: str = '') -> VendaViagem:
        """
        Cancela a venda e devolve a mercadoria ao saldo da viagem.

        O SALDO PRECISA VOLTAR. Cancelar sem devolver deixaria a viagem com
        menos mercadoria no papel do que no caminhão, e o retorno acusaria uma
        sobra que ninguém explica.
        """
        if venda.status == VendaViagem.Status.CANCELADA:
            raise DadosInvalidosError('Esta venda já foi cancelada.')
        if venda.documento_fiscal_id:
            raise DadosInvalidosError(
                'Esta venda já tem nota emitida. Cancele o documento fiscal antes.'
            )

        for item in venda.itens.select_related('produto', 'lote'):
            saldo = SaldoCarga.objects.select_for_update().filter(
                viagem=venda.viagem, produto=item.produto, lote=item.lote,
            ).first()
            if saldo is None:
                continue
            # DEVOLVE NA COLUNA DE ONDE SAIU: cancelar uma bonificacao
            # descontando de "vendido" criaria venda negativa e bonificacao
            # fantasma na mesma linha.
            campo = venda.campo_do_saldo
            setattr(saldo, campo, max(
                ZERO, (getattr(saldo, campo) or ZERO) - (item.quantidade or ZERO),
            ))
            saldo.save(update_fields=[campo, 'updated_at'])

        venda.status = VendaViagem.Status.CANCELADA
        if motivo:
            quebra = chr(10)
            venda.observacao = (
                f'{venda.observacao}{quebra}[Cancelamento] {motivo}'.strip()
            )
        venda.save(update_fields=['status', 'observacao', 'updated_at'])
        return venda

    # ── Apoio ────────────────────────────────────────────────────────────

    @staticmethod
    def _remessa(viagem):
        """
        A remessa viva da viagem — ou `None` quando ainda não há uma.

        NÃO TRAVA A ENTREGA. Uma venda pode acontecer antes de a nota de
        remessa ser emitida (o caminhão sai de madrugada, a nota sai às 8h),
        e recusar a venda por isso pararia a rua por causa de um documento
        que chega depois. O vínculo fica vazio e a tela diz isso.
        """
        from apps.logistica.services.remessa_nfe import RemessaVendaForaService

        return RemessaVendaForaService.nota_da_viagem(viagem)

    @staticmethod
    def proximo_numero(viagem) -> int:
        ultimo = (
            VendaViagem.objects.filter(viagem=viagem)
            .order_by('-numero').values_list('numero', flat=True).first()
        )
        return (ultimo or 0) + 1

    @staticmethod
    def _documento(dados, cliente) -> str:
        documento = ''.join(
            c for c in str(dados.get('cliente_documento') or '') if c.isdigit()
        )
        if documento:
            return documento
        return (getattr(cliente, 'cpf_cnpj', '') or '') if cliente else ''

    @staticmethod
    def _endereco(dados, cliente) -> dict:
        """
        O endereço copiado no momento da venda.

        É para onde a mercadoria foi naquele dia. Cliente que muda de endereço
        depois não pode reescrever o histórico de uma entrega já feita.
        """
        informado = (dados.get('endereco') or '').strip()
        if informado:
            return {'endereco': informado}
        if cliente is None:
            return {}
        return {
            campo: getattr(cliente, campo, '') or ''
            for campo in ('endereco', 'numero', 'bairro', 'cidade', 'uf', 'cep')
        }

    @staticmethod
    def _exigir_viagem_na_rua(viagem) -> None:
        if viagem.status not in VIAGENS_QUE_VENDEM:
            raise DadosInvalidosError(
                f'A viagem está em "{viagem.get_status_display()}". A venda na '
                'rua só acontece com o caminhão fora.'
            )

    @classmethod
    def resumo(cls, viagem) -> dict:
        vendas = viagem.vendas.filter(status=VendaViagem.Status.REGISTRADA)
        return {
            'quantidade': vendas.count(),
            'valor': sum((v.valor_total or ZERO for v in vendas), ZERO),
            'disponivel': sum(
                (linha['disponivel'] for linha in cls.disponivel_para_venda(viagem)),
                ZERO,
            ),
        }
