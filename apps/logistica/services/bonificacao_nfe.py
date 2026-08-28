"""
A NF-e de bonificação — operação própria, e não uma venda de valor zero.

POR QUE ISTO É UMA ROTINA E NÃO UM PARÂMETRO DA VENDA

Bonificação e venda entregam a mesma caixa ao mesmo cliente e, no papel,
parecem a mesma coisa com outro preço. Fiscalmente não são: a venda tem
receita e a bonificação não; a venda tem meio de pagamento e a bonificação
não; a venda vive num CFOP de venda e a bonificação no dela. Tratar a
segunda como "venda com desconto de 100%" é o erro clássico — ele passa na
emissão e reaparece na apuração, com receita declarada que ninguém recebeu.

Por isso a bonificação tem entrada própria, arquivo próprio
(`origem_tipo='viagem_bonificacao'`) e conferência própria. **Ela não é
registrada como venda em lugar nenhum do sistema** — nem no documento, nem
no relatório que lê documentos por origem.

O QUE ELA COMPARTILHA, E POR QUÊ

O DESENHO da nota é o mesmo: item, NCM, CST/CSOSN, alíquotas, destinatário,
lote. Duplicar essa montagem daria dois lugares para corrigir quando a SEFAZ
mudar um campo — e um deles ficaria para trás. O que muda (a natureza, o
meio de pagamento, o texto e o arquivo) já é decidido pelo tipo da entrega.

NADA DE FISCAL É DECIDIDO AQUI

CFOP, CST, CSOSN e alíquotas vêm da natureza cadastrada para a espécie
`bonificacao`, com as regras que a contabilidade escreveu por UF de origem e
destino, operação interna ou interestadual, regime tributário, NCM e
produto. O código não sabe que 5910 ou 6910 existem — e não deve saber.

O MOTIVO É EXIGIDO ANTES DA NOTA

Uma bonificação sem motivo registrado não deveria virar documento: a nota
sai, a mercadoria vai embora, e a pergunta "por que demos isso?" fica sem
resposta para sempre. O motivo já é obrigatório no registro; aqui ele é
conferido de novo, porque entregas antigas nasceram antes dessa exigência.
"""
from apps.core.services.exceptions import DadosInvalidosError
from apps.logistica.models import VendaViagem
from apps.logistica.services.venda_fora_nfe import (
    ORIGEM_BONIFICACAO, VendaForaNFeService,
)

ORIGEM = ORIGEM_BONIFICACAO


class BonificacaoNFeService:

    @staticmethod
    def _exigir_bonificacao(entrega: VendaViagem) -> None:
        if entrega.tipo != VendaViagem.Tipo.BONIFICACAO:
            raise DadosInvalidosError(
                'Esta entrega é uma venda — a nota dela sai pela rotina de '
                'venda fora do estabelecimento.'
            )

    @staticmethod
    def natureza(filial):
        """A natureza cadastrada para bonificação — única e ativa."""
        return VendaForaNFeService.natureza(
            filial, VendaViagem.Tipo.BONIFICACAO,
        )

    @classmethod
    def conferir(cls, entrega: VendaViagem) -> list[str]:
        """
        Tudo que impede esta nota, junto — incluindo o motivo.

        A LISTA INTEIRA, e não o primeiro problema: quem está na rua não pode
        descobrir as pendências uma por vez.
        """
        cls._exigir_bonificacao(entrega)
        problemas = VendaForaNFeService.conferir(entrega)
        if not entrega.motivo:
            problemas.append(
                'Esta bonificação não tem motivo registrado — sem ele, "por '
                'que demos isso?" fica sem resposta depois que a nota sair.'
            )
        return problemas

    @classmethod
    def construir_payload(cls, entrega: VendaViagem, numero: int, serie: int) -> dict:
        cls._exigir_bonificacao(entrega)
        problemas = cls.conferir(entrega)
        if problemas:
            raise DadosInvalidosError(' '.join(problemas))
        return VendaForaNFeService.construir_payload(entrega, numero, serie)

    @classmethod
    def nota_da_bonificacao(cls, entrega: VendaViagem):
        """A nota desta bonificação, se já houver uma viva."""
        cls._exigir_bonificacao(entrega)
        return VendaForaNFeService.nota_da_venda(entrega)

    @classmethod
    def emitir(cls, entrega: VendaViagem, usuario=None):
        """
        Emite a nota da bonificação e a arquiva como operação própria.

        A CONFERÊNCIA ACONTECE AQUI TAMBÉM, antes de a emissão compartilhada
        reservar número: número reservado e não usado vira buraco na
        numeração, que a SEFAZ cobra depois com inutilização.
        """
        cls._exigir_bonificacao(entrega)
        problemas = cls.conferir(entrega)
        if problemas:
            raise DadosInvalidosError(' '.join(problemas))
        return VendaForaNFeService.emitir(entrega, usuario=usuario)
