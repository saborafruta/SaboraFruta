"""
O ciclo de uma viagem: montar, fechar, vender na rua, voltar e prestar contas.

FECHAR A CARGA É O PONTO SEM VOLTA
==================================

Enquanto a viagem está em planejamento nada aconteceu: nem estoque, nem
documento. Fechar é o momento em que a mercadoria sai do estabelecimento — o
estoque da filial baixa, o saldo em poder da viagem nasce e os documentos
ficam prontos para transmitir.

Depois disso, mexer na carga é reescrever o que o documento já disse. Por isso
o serviço recusa alteração fora do planejamento, e a correção é por baixa
declarada ou cancelamento — nunca por edição silenciosa.

O QUE SAIU TEM QUE FECHAR
=========================

Para a mercadoria que sai sem comprador, remetido = vendido + bonificado +
retornado + baixado. Enquanto essa conta não fecha, há mercadoria da empresa
na rua sem destino registrado. O encerramento cobra isso: viagem não encerra
com saldo em aberto, porque encerrar com sobra é perder o rastro justamente do
que a fiscalização pede para ver.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.fiscal.models import NaturezaOperacao
from apps.fiscal.services.natureza_operacao_service import NaturezaOperacaoService
from apps.logistica.models import ItemCarga, SaldoCarga, Viagem

ZERO = Decimal('0')

# Os destinos possiveis do que saiu na remessa. A conta que precisa fechar e'
# remetido = soma destes -- listar aqui evita que um destino novo entre sem
# aparecer na conciliacao.
DESTINOS_DO_SALDO = (
    'quantidade_vendida',
    'quantidade_bonificada',
    'quantidade_retornada',
    'quantidade_baixada',
)

# Como cada espécie mexe no estoque da filial quando a carga fecha. A natureza
# pode sobrescrever pelo campo `tipo_operacao_estoque`; isto é o padrão.
SAIDA_POR_ESPECIE = {
    NaturezaOperacao.Especie.VENDA: MovimentacaoEstoque.TipoOperacao.SAIDA,
    NaturezaOperacao.Especie.REMESSA_VENDA_FORA: MovimentacaoEstoque.TipoOperacao.SAIDA,
    NaturezaOperacao.Especie.BONIFICACAO: MovimentacaoEstoque.TipoOperacao.BONIFICACAO,
    NaturezaOperacao.Especie.REMESSA_SIMPLES: MovimentacaoEstoque.TipoOperacao.SAIDA,
}


class ViagemService:

    # ── Montar ───────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def criar(cls, filial, dados: dict, usuario=None) -> Viagem:
        """
        Abre uma viagem em planejamento.

        O NÚMERO É GERADO, e não pedido: número repetido bate na unique depois
        da pessoa já ter preenchido tudo, e inventar numeração à mão não é
        trabalho de quem monta carga.
        """
        ultimo = (
            Viagem.objects.filter(filial=filial)
            .order_by('-numero').values_list('numero', flat=True).first()
        )
        return Viagem.objects.create(
            filial=filial,
            numero=(ultimo or 0) + 1,
            data_saida=dados.get('data_saida') or timezone.localdate(),
            responsavel=usuario,
            vendedor=dados.get('vendedor'),
            motorista_nome=(dados.get('motorista_nome') or '').strip(),
            motorista_documento=(dados.get('motorista_documento') or '').strip(),
            veiculo_placa=(dados.get('veiculo_placa') or '').strip().upper(),
            veiculo_descricao=(dados.get('veiculo_descricao') or '').strip(),
            transportadora=dados.get('transportadora'),
            rota=(dados.get('rota') or '').strip(),
            uf_origem=(dados.get('uf_origem') or getattr(filial, 'uf', '') or '').upper(),
            uf_destino=(dados.get('uf_destino') or '').upper(),
            percurso_ufs=(dados.get('percurso_ufs') or '').upper(),
            observacao=(dados.get('observacao') or '').strip(),
        )

    @classmethod
    @transaction.atomic
    def adicionar_item(cls, viagem: Viagem, dados: dict) -> ItemCarga:
        """Põe um produto na carga, com a natureza fiscal dele."""
        cls._exigir_editavel(viagem)
        item = ItemCarga(
            viagem=viagem,
            natureza=dados['natureza'],
            produto=dados['produto'],
            lote=dados.get('lote'),
            cliente=dados.get('cliente'),
            pedido_venda=dados.get('pedido_venda'),
            quantidade=Decimal(str(dados.get('quantidade') or 0)),
            valor_unitario=Decimal(str(dados.get('valor_unitario') or 0)),
            peso_kg=Decimal(str(dados.get('peso_kg') or 0)),
            observacao=(dados.get('observacao') or '').strip(),
        )
        # `full_clean` para a validação do modelo valer aqui também: destinatário
        # obrigatório e quantidade positiva não podem depender de a tela lembrar.
        item.full_clean(exclude=['valor_total'])
        item.save()
        return item

    @classmethod
    @transaction.atomic
    def remover_item(cls, viagem: Viagem, item: ItemCarga) -> None:
        cls._exigir_editavel(viagem)
        item.delete()

    # ── Fechar ───────────────────────────────────────────────────────────

    @classmethod
    def conferir_antes_de_fechar(cls, viagem: Viagem) -> list[str]:
        """
        Tudo que impede a carga de sair, junto.

        DEVOLVE A LISTA INTEIRA, e não o primeiro problema: quem está com o
        caminhão encostado não pode descobrir as pendências uma por vez, a cada
        tentativa.
        """
        problemas = []
        itens = list(viagem.itens.select_related('natureza', 'produto', 'cliente'))
        if not itens:
            problemas.append('A carga está vazia.')
        if not (viagem.motorista_nome or viagem.veiculo_placa):
            problemas.append('Informe o motorista ou a placa — sem um dos dois a carga sai sem dizer quem levou.')

        tem_venda_fora = any(
            i.natureza.especie == NaturezaOperacao.Especie.REMESSA_VENDA_FORA for i in itens
        )
        if tem_venda_fora and not viagem.vendedor_id:
            problemas.append(
                'A carga leva mercadoria sem comprador: informe quem responde '
                'por ela na rua.'
            )

        for item in itens:
            if item.natureza.exige_destinatario and not item.cliente_id:
                problemas.append(f'{item.produto}: {item.natureza.descricao} precisa de destinatário.')
            # A regra fiscal é conferida AGORA, e não na transmissão: descobrir
            # que falta CFOP com a nota meio emitida é o pior momento possível.
            try:
                NaturezaOperacaoService.para_item(
                    natureza=item.natureza, filial=viagem.filial,
                    produto=item.produto, cliente=item.cliente,
                    data=viagem.data_saida,
                )
            except DadosInvalidosError as erro:
                problemas.append(str(erro))
        return problemas

    @classmethod
    @transaction.atomic
    def fechar_carga(cls, viagem: Viagem, usuario=None) -> Viagem:
        """
        Tira a mercadoria do estabelecimento.

        Baixa o estoque da filial e, para o que sai sem comprador, abre o saldo
        em poder da viagem. Os documentos ficam prontos para conferência antes
        de transmitir -- ver `ViagemDocumentoService`.
        """
        cls._exigir_editavel(viagem)
        problemas = cls.conferir_antes_de_fechar(viagem)
        if problemas:
            raise DadosInvalidosError(' '.join(problemas))

        for item in viagem.itens.select_related('natureza', 'produto'):
            if not item.natureza.movimenta_estoque:
                continue
            tipo = (
                item.natureza.tipo_operacao_estoque
                or SAIDA_POR_ESPECIE.get(item.natureza.especie)
            )
            if not tipo:
                continue
            MovimentacaoService.registrar_movimentacao(
                produto_id=item.produto_id,
                filial_id=viagem.filial_id,
                tipo_operacao=tipo,
                quantidade=item.quantidade,
                usuario_id=getattr(usuario, 'pk', None) or viagem.responsavel_id,
                lote_id=item.lote_id,
                valor_unitario=item.valor_unitario or None,
                documento_tipo='viagem',
                documento_id=viagem.pk,
                documento_numero=str(viagem.numero),
                observacao=f'Carga da viagem #{viagem.numero:06d} — {item.natureza.descricao}',
                permitir_sem_lote=True,
            )
            # O que sai sem comprador vira saldo em poder de quem viaja: e' o
            # livro que a prestacao de contas do retorno vai fechar.
            if item.natureza.especie == NaturezaOperacao.Especie.REMESSA_VENDA_FORA:
                saldo, _ = SaldoCarga.objects.get_or_create(
                    viagem=viagem, produto=item.produto, lote=item.lote,
                    defaults={'custo_unitario': item.valor_unitario or ZERO},
                )
                saldo.quantidade_remetida = (saldo.quantidade_remetida or ZERO) + item.quantidade
                saldo.save(update_fields=['quantidade_remetida', 'updated_at'])

        viagem.status = Viagem.Status.EM_ROTA
        viagem.save(update_fields=['status', 'updated_at'])
        return viagem

    # ── Na rua ───────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def registrar_saida_do_saldo(
        cls, viagem: Viagem, produto, quantidade: Decimal, campo: str, lote=None,
    ) -> SaldoCarga:
        """
        Dá baixa no saldo que viaja — venda, bonificação ou perda na estrada.

        NÃO MEXE NO ESTOQUE DA FILIAL: essa mercadoria já saiu de lá quando a
        carga fechou. Baixar de novo contaria a mesma saída duas vezes.
        """
        if campo not in DESTINOS_DO_SALDO:
            raise DadosInvalidosError('Tipo de baixa desconhecido para o saldo da viagem.')
        quantidade = Decimal(str(quantidade or 0))
        if quantidade <= ZERO:
            raise DadosInvalidosError('A quantidade precisa ser maior que zero.')

        saldo = (
            SaldoCarga.objects.select_for_update()
            .filter(viagem=viagem, produto=produto, lote=lote).first()
        )
        if saldo is None:
            raise DadosInvalidosError(
                f'{produto} não saiu nesta viagem como venda fora do estabelecimento.'
            )
        if quantidade > saldo.quantidade_em_poder:
            raise DadosInvalidosError(
                f'{produto}: só há {saldo.quantidade_em_poder} em poder da viagem, '
                f'e a baixa é de {quantidade}.'
            )
        setattr(saldo, campo, (getattr(saldo, campo) or ZERO) + quantidade)
        saldo.save(update_fields=[campo, 'updated_at'])
        return saldo

    @classmethod
    @transaction.atomic
    def registrar_retorno(cls, viagem: Viagem, produto, quantidade: Decimal,
                          lote=None, usuario=None) -> SaldoCarga:
        """O que não vendeu volta para o estoque da filial."""
        saldo = cls.registrar_saida_do_saldo(
            viagem, produto, quantidade, 'quantidade_retornada', lote=lote,
        )
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=viagem.filial_id,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(str(quantidade)),
            usuario_id=getattr(usuario, 'pk', None) or viagem.responsavel_id,
            lote_id=getattr(lote, 'pk', None),
            valor_unitario=saldo.custo_unitario or None,
            documento_tipo='viagem_retorno',
            documento_id=viagem.pk,
            documento_numero=str(viagem.numero),
            observacao=f'Retorno da viagem #{viagem.numero:06d}',
            permitir_sem_lote=True,
        )
        return saldo

    # ── Prestação de contas ──────────────────────────────────────────────

    @classmethod
    def conciliacao(cls, viagem: Viagem) -> list[dict]:
        """Linha a linha: o que saiu, o que teve destino e o que falta."""
        linhas = []
        for saldo in viagem.saldos.select_related('produto', 'lote'):
            linhas.append({
                'saldo': saldo,
                'produto': saldo.produto,
                'remetida': saldo.quantidade_remetida or ZERO,
                'vendida': saldo.quantidade_vendida or ZERO,
                'bonificada': saldo.quantidade_bonificada or ZERO,
                'retornada': saldo.quantidade_retornada or ZERO,
                'baixada': saldo.quantidade_baixada or ZERO,
                'em_poder': saldo.quantidade_em_poder,
                'fechado': saldo.fechado,
            })
        return linhas

    @classmethod
    def pendencias_de_encerramento(cls, viagem: Viagem) -> list[str]:
        return [
            f'{linha["produto"]}: {linha["em_poder"]} ainda sem destino registrado.'
            for linha in cls.conciliacao(viagem) if not linha['fechado']
        ]

    @classmethod
    @transaction.atomic
    def encerrar(cls, viagem: Viagem) -> Viagem:
        """
        Fecha a viagem.

        RECUSA COM SALDO EM ABERTO. Encerrar com sobra é perder o rastro de
        mercadoria da empresa que está na rua — exatamente o que a
        fiscalização pede para ver. Quebra e perda saem por baixa declarada.
        """
        if viagem.status == Viagem.Status.ENCERRADA:
            raise DadosInvalidosError('Esta viagem já foi encerrada.')
        if viagem.status == Viagem.Status.CANCELADA:
            raise DadosInvalidosError('Viagem cancelada não encerra.')

        pendencias = cls.pendencias_de_encerramento(viagem)
        if pendencias:
            raise DadosInvalidosError(
                'A carga não fecha: ' + ' '.join(pendencias)
                + ' Registre venda, bonificação, retorno ou baixa.'
            )
        viagem.status = Viagem.Status.ENCERRADA
        viagem.data_retorno = viagem.data_retorno or timezone.localdate()
        viagem.save(update_fields=['status', 'data_retorno', 'updated_at'])
        return viagem

    # ── Resumo para a tela ───────────────────────────────────────────────

    @staticmethod
    def resumo(viagem: Viagem) -> dict:
        agregado = viagem.itens.aggregate(
            valor=Sum('valor_total'), peso=Sum('peso_kg'),
        )
        por_natureza = {}
        for item in viagem.itens.select_related('natureza'):
            chave = item.natureza.especie
            atual = por_natureza.setdefault(
                chave, {'rotulo': item.natureza.get_especie_display(), 'itens': 0, 'valor': ZERO},
            )
            atual['itens'] += 1
            atual['valor'] += item.valor_total or ZERO
        return {
            'itens': viagem.itens.count(),
            'valor': agregado['valor'] or ZERO,
            'peso': agregado['peso'] or ZERO,
            'por_natureza': por_natureza,
            'em_poder': sum(
                (s.quantidade_em_poder for s in viagem.saldos.all()), ZERO,
            ),
        }

    # ── Guardas ──────────────────────────────────────────────────────────

    @staticmethod
    def _exigir_editavel(viagem: Viagem) -> None:
        if not viagem.editavel:
            raise DadosInvalidosError(
                'A carga já saiu. Alterar agora reescreveria o que o documento '
                'fiscal já declarou — use baixa, retorno ou cancelamento.'
            )
