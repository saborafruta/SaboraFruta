"""
Resolve o CFOP e a tributação de uma operação.

É a única porta por onde o sistema descobre com que CFOP e com que CST uma
mercadoria sai. Ninguém mais decide isso: quem emite pergunta aqui, e aqui a
resposta vem da tabela que a contabilidade cadastrou.

A REGRA MAIS ESPECÍFICA GANHA
=============================

Uma linha sem UF, sem regime e sem produto é o padrão da natureza. Acrescentar
UF de destino, regime, NCM ou produto estreita o alvo. Quando duas regras
servem, vence a mais estreita — assim a contabilidade cadastra o geral uma vez
e trata só as exceções, em vez de repetir tudo para cada combinação.

QUANDO NÃO HÁ REGRA, PARA
=========================

Sem regra cadastrada não existe CFOP padrão de emergência. Chutar um CFOP
produz nota autorizada errada, que só aparece na apuração e custa carta de
correção ou cancelamento fora do prazo. Falta de regra é problema de cadastro,
e o lugar de descobrir isso é antes de transmitir.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao


@dataclass(frozen=True)
class ContextoFiscal:
    """O que descreve a operação para efeito de regra."""

    uf_origem: str = ''
    uf_destino: str = ''
    regime_tributario: str = ''
    ncm: str = ''
    produto_id: int | None = None
    data: date | None = None

    @property
    def interestadual(self) -> bool:
        return bool(self.uf_origem and self.uf_destino and self.uf_origem != self.uf_destino)


@dataclass
class ResultadoFiscal:
    """O que a emissão precisa saber para montar o item da nota."""

    cfop: str
    natureza_operacao: str
    cst_icms: str = ''
    csosn: str = ''
    cst_pis: str = ''
    cst_cofins: str = ''
    cst_ipi: str = ''
    aliquota_icms: Decimal | None = None
    reducao_base_icms: Decimal | None = None
    aliquota_ipi: Decimal | None = None
    aliquota_pis: Decimal | None = None
    aliquota_cofins: Decimal | None = None
    finalidade_nfe: int = 1
    informacoes_complementares: str = ''
    regra_id: int | None = None
    # A trilha de por que esta regra foi escolhida. Sem isso, "por que saiu
    # 6910 nesta nota?" vira arqueologia de banco seis meses depois.
    justificativa: dict = field(default_factory=dict)


class NaturezaOperacaoService:

    @staticmethod
    def contexto_da_operacao(filial, cliente=None, produto=None, data=None) -> ContextoFiscal:
        """Monta o contexto a partir de quem emite, para quem e do quê."""
        empresa = getattr(filial, 'empresa', None)
        return ContextoFiscal(
            uf_origem=(getattr(filial, 'uf', '') or '').upper(),
            # Sem destinatário — remessa para venda fora — a operação é interna:
            # a nota é contra a própria empresa, e ela não atravessa fronteira.
            uf_destino=((getattr(cliente, 'uf', '') or getattr(filial, 'uf', '') or '')).upper(),
            regime_tributario=getattr(empresa, 'regime_tributario', '') or '',
            ncm=(getattr(produto, 'ncm', '') or '')[:8],
            produto_id=getattr(produto, 'pk', None),
            data=data or timezone.localdate(),
        )

    @classmethod
    def resolver(cls, natureza: NaturezaOperacao, contexto: ContextoFiscal) -> ResultadoFiscal:
        """
        Devolve a regra que vale para esta operação.

        Levanta `DadosInvalidosError` quando não há regra — ver o cabeçalho do
        módulo sobre por que não existe padrão de emergência.
        """
        regra = cls._melhor_regra(natureza, contexto)
        if regra is None:
            raise DadosInvalidosError(
                f'Sem regra fiscal para "{natureza.descricao}" '
                f'({contexto.uf_origem or "?"} → {contexto.uf_destino or "?"}). '
                'Cadastre a regra em Fiscal › Naturezas de operação antes de emitir.'
            )
        return ResultadoFiscal(
            cfop=regra.cfop,
            natureza_operacao=regra.natureza_operacao_texto or natureza.descricao,
            cst_icms=regra.cst_icms,
            csosn=regra.csosn,
            cst_pis=regra.cst_pis,
            cst_cofins=regra.cst_cofins,
            cst_ipi=regra.cst_ipi,
            aliquota_icms=regra.aliquota_icms,
            reducao_base_icms=regra.reducao_base_icms,
            aliquota_ipi=regra.aliquota_ipi,
            aliquota_pis=regra.aliquota_pis,
            aliquota_cofins=regra.aliquota_cofins,
            finalidade_nfe=regra.finalidade_nfe,
            informacoes_complementares=regra.informacoes_complementares,
            regra_id=regra.pk,
            justificativa={
                'natureza': natureza.codigo,
                'uf_origem': contexto.uf_origem,
                'uf_destino': contexto.uf_destino,
                'interestadual': contexto.interestadual,
                'regime': contexto.regime_tributario,
                'ncm': contexto.ncm,
                'especificidade': regra.especificidade,
            },
        )

    @classmethod
    def _melhor_regra(cls, natureza, contexto) -> RegraNaturezaOperacao | None:
        hoje = contexto.data or timezone.localdate()

        candidatas = (
            RegraNaturezaOperacao.objects
            .filter(natureza=natureza, ativo=True)
            # VIGÊNCIA: regra que ainda não começou ou que já terminou não vale.
            # Sem isto, mudar a regra reescreveria como a nota de ontem devia
            # ter saído.
            .filter(Q(vigencia_inicio__isnull=True) | Q(vigencia_inicio__lte=hoje))
            .filter(Q(vigencia_fim__isnull=True) | Q(vigencia_fim__gte=hoje))
            # Campo vazio na regra significa "serve para qualquer".
            .filter(Q(uf_origem='') | Q(uf_origem=contexto.uf_origem))
            .filter(Q(regime_tributario='') | Q(regime_tributario=contexto.regime_tributario))
            .filter(Q(ncm='') | Q(ncm=contexto.ncm))
            .filter(Q(produto__isnull=True) | Q(produto_id=contexto.produto_id))
        )
        if contexto.interestadual:
            candidatas = candidatas.filter(
                Q(uf_destino='') | Q(uf_destino=contexto.uf_destino)
            )
        else:
            # Operação interna nunca casa com uma regra marcada como
            # interestadual, nem com uma que aponte para outra UF.
            candidatas = candidatas.filter(
                Q(uf_destino='') | Q(uf_destino=contexto.uf_destino)
            ).filter(somente_interestadual=False)

        melhores = sorted(
            candidatas,
            key=lambda r: (r.especificidade, r.vigencia_inicio or date.min, r.pk),
            reverse=True,
        )
        return melhores[0] if melhores else None

    @classmethod
    def para_item(cls, natureza, filial, produto, cliente=None, data=None) -> ResultadoFiscal:
        """Atalho: resolve direto a partir dos objetos da operação."""
        contexto = cls.contexto_da_operacao(
            filial=filial, cliente=cliente, produto=produto, data=data,
        )
        return cls.resolver(natureza, contexto)

    @classmethod
    def por_especie(cls, filial, especie: str) -> NaturezaOperacao:
        """
        A natureza que a filial usa para uma espécie de operação.

        O sistema conhece ESPÉCIES (remessa para venda fora, bonificação); o
        nome e os números de cada uma são cadastro. Quando a filial tem mais de
        uma natureza da mesma espécie, a escolha é de quem monta a carga -- aqui
        só se resolve o caso de haver uma só.
        """
        naturezas = list(
            NaturezaOperacao.objects.for_filial(filial).filter(especie=especie, ativo=True)
        )
        if not naturezas:
            rotulo = dict(NaturezaOperacao.Especie.choices).get(especie, especie)
            raise DadosInvalidosError(
                f'Nenhuma natureza de operação cadastrada para "{rotulo}". '
                'Cadastre em Fiscal › Naturezas de operação.'
            )
        if len(naturezas) > 1:
            raise DadosInvalidosError(
                f'Há {len(naturezas)} naturezas para esta operação — escolha '
                'qual usar ao montar a carga.'
            )
        return naturezas[0]
