"""
A NF-e de retorno do que não foi vendido.

O CICLO QUE FICAVA ABERTO
=========================

A remessa tirou a mercadoria do estabelecimento amparada por uma nota da
empresa contra ela mesma. O que vendeu na rua ganhou sua nota. O que NÃO
vendeu voltava para a prateleira por movimentação interna — sem documento
nenhum.

Do lado fiscal isso deixa a remessa eternamente em aberto: existe uma nota
dizendo que 300 caixas saíram e nada dizendo que 120 voltaram. Numa
fiscalização, a diferença entre o que a remessa mandou sair e o que as vendas
justificam é exatamente o que se pede para explicar — e a explicação é esta
nota.

UMA NOTA POR VIAGEM, E NÃO POR PRODUTO
======================================

O caminhão volta uma vez. Emitir uma nota por produto que sobrou encheria a
numeração de documentos que descrevem o mesmo evento físico, e a conferência
teria de somar dez notas para saber o que voltou. A nota é da viagem e traz
todos os produtos que retornaram.

A EMPRESA NOS DOIS LADOS, DE NOVO
=================================

Como na remessa, não há terceiro: a mercadoria sai da empresa e volta para a
empresa. A diferença é o sentido — esta é nota de ENTRADA, e o CFOP de
retorno vem da natureza cadastrada, nunca daqui.

ELA NÃO MEXE NO ESTOQUE
=======================

O estoque já voltou quando a conferência do retorno foi registrada, produto a
produto, com a contagem física na mão. Esta nota é o documento daquele fato,
não o fato — dar entrada aqui somaria uma segunda vez o que já está na
prateleira.
"""
from decimal import Decimal
from typing import Any, Dict, List

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.financeiro.constants.enums import (
    StatusDocumentoFiscal, TipoDocumentoFiscal,
)
from apps.financeiro.models.fiscal import DocumentoFiscal
from apps.fiscal.models import NaturezaOperacao
from apps.fiscal.services.natureza_operacao_service import NaturezaOperacaoService
from apps.logistica.services.log_viagem import LogViagemService

ZERO = Decimal('0')
CENTAVOS = Decimal('0.01')

ORIGEM = 'viagem_retorno'
ORIGEM_REMESSA = 'viagem_remessa'

STATUS_MORTOS = (
    StatusDocumentoFiscal.CANCELADA,
    StatusDocumentoFiscal.REJEITADA,
    StatusDocumentoFiscal.DENEGADA,
)


def _digitos(valor) -> str:
    return ''.join(c for c in str(valor or '') if c.isdigit())


def _dinheiro(valor) -> Decimal:
    return Decimal(str(valor or 0)).quantize(CENTAVOS)


def _float(valor) -> float:
    return float(_dinheiro(valor))


class RetornoVendaForaService:

    # ── O que voltou ─────────────────────────────────────────────────────

    @staticmethod
    def itens_do_retorno(viagem) -> list:
        """
        Os saldos da viagem que registraram retorno.

        SÓ O QUE VOLTOU. Produto que saiu e vendeu inteiro não entra: a nota
        de retorno descreve mercadoria que está fisicamente de volta na
        prateleira, e listar o que não voltou seria declarar entrada de
        mercadoria que não existe.
        """
        return [
            saldo for saldo in viagem.saldos.select_related('produto', 'lote')
            if (saldo.quantidade_retornada or ZERO) > ZERO
        ]

    @staticmethod
    def natureza(filial):
        """A natureza cadastrada para retorno de venda fora — única e ativa."""
        naturezas = list(
            NaturezaOperacao.objects.for_filial(filial).filter(
                especie=NaturezaOperacao.Especie.RETORNO_VENDA_FORA, ativo=True,
            )
        )
        if not naturezas:
            raise DadosInvalidosError(
                'Nenhuma natureza de operação cadastrada para retorno de venda '
                'fora do estabelecimento. Cadastre-a em Fiscal › Naturezas de '
                'operação antes de emitir a nota.'
            )
        if len(naturezas) > 1:
            raise DadosInvalidosError(
                'Há mais de uma natureza ativa para retorno de venda fora: '
                'deixe apenas uma ativa, senão a nota sai com o CFOP de '
                'qualquer uma delas.'
            )
        return naturezas[0]

    @classmethod
    def remessa_da_viagem(cls, viagem):
        """
        A nota que tirou a mercadoria — é a ela que o retorno responde.

        Pergunta feita a quem emite a remessa, e não repetida aqui: duas
        consultas discordariam no dia em que "viva" mudasse de significado.
        """
        from apps.logistica.services.remessa_nfe import RemessaVendaForaService

        return RemessaVendaForaService.nota_da_viagem(viagem)

    @classmethod
    def nota_da_viagem(cls, viagem):
        """A nota de retorno desta viagem, se já houver uma viva."""
        return (
            DocumentoFiscal.objects
            .filter(origem_tipo=ORIGEM, origem_id=viagem.pk)
            .exclude(status__in=STATUS_MORTOS)
            .order_by('-id')
            .first()
        )

    @classmethod
    def vinculo(cls, viagem) -> dict:
        """
        A remessa que este retorno responde, e o que vai voltar nela.

        O VÍNCULO PRECISA SER LEGÍVEL ANTES DA EMISSÃO, e não só depois: é
        conferindo a nota de origem — chave, número, série, data — contra o
        que se está devolvendo que alguém percebe que está emitindo o
        retorno da viagem errada. Depois de transmitida, corrigir custa
        cancelamento.

        `None` na remessa é caso real e não erro: a nota pode não ter sido
        emitida, e a tela diz isso em vez de esconder o retorno.
        """
        remessa = cls.remessa_da_viagem(viagem)
        linhas = [
            {
                'produto': saldo.produto,
                'lote': saldo.lote,
                'quantidade': saldo.quantidade_retornada or ZERO,
                'valor_unitario': saldo.custo_unitario or ZERO,
                'valor': (
                    (saldo.quantidade_retornada or ZERO)
                    * (saldo.custo_unitario or ZERO)
                ).quantize(CENTAVOS),
            }
            for saldo in cls.itens_do_retorno(viagem)
        ]
        return {
            'remessa': remessa,
            'chave': getattr(remessa, 'chave', '') or '',
            'numero': getattr(remessa, 'numero', None),
            'serie': getattr(remessa, 'serie', None),
            'data': getattr(remessa, 'data_emissao', None),
            'sem_remessa': remessa is None,
            'sem_chave': remessa is not None and not remessa.chave,
            'linhas': linhas,
            'quantidade': sum((l['quantidade'] for l in linhas), ZERO),
            'valor': sum((l['valor'] for l in linhas), ZERO),
            'nota': cls.nota_da_viagem(viagem),
        }

    # ── Conferência ──────────────────────────────────────────────────────

    @classmethod
    def conferir(cls, viagem) -> list[str]:
        """Tudo que impede a nota de retorno, junto."""
        problemas = []

        saldos = cls.itens_do_retorno(viagem)
        if not saldos:
            problemas.append(
                'Nenhum produto foi registrado como retornado nesta viagem.'
            )

        # O QUE AINDA ESTA' NA RUA NAO VOLTOU. Emitir a nota com saldo em
        # aberto declararia entrada de mercadoria que ninguem contou -- e
        # depois nao haveria como corrigir sem cancelar a nota.
        em_aberto = [
            f'{s.produto}: {s.quantidade_em_poder} ainda sem destino'
            for s in viagem.saldos.select_related('produto')
            if s.quantidade_em_poder > ZERO
        ]
        if em_aberto:
            problemas.append(
                'A conferência do retorno não terminou — ' + '; '.join(em_aberto)
                + '. Registre venda, bonificação, retorno ou baixa antes da nota.'
            )

        try:
            natureza = cls.natureza(viagem.filial)
        except DadosInvalidosError as erro:
            problemas.append(str(erro))
            natureza = None

        if natureza is not None:
            for saldo in saldos:
                try:
                    NaturezaOperacaoService.para_item(
                        natureza=natureza, filial=viagem.filial,
                        produto=saldo.produto, cliente=None,
                        data=viagem.data_retorno or viagem.data_saida,
                    )
                except DadosInvalidosError as erro:
                    problemas.append(str(erro))
        return problemas

    # ── O payload ────────────────────────────────────────────────────────

    @classmethod
    def construir_payload(cls, viagem, numero: int, serie: int) -> Dict[str, Any]:
        problemas = cls.conferir(viagem)
        if problemas:
            raise DadosInvalidosError(' '.join(problemas))

        filial = viagem.filial
        natureza = cls.natureza(filial)
        data = viagem.data_retorno or timezone.localdate()
        momento = f'{data.isoformat()}T{timezone.localtime().strftime("%H:%M:%S")}-03:00'

        linhas: List[Dict[str, Any]] = []
        natureza_texto = ''
        informacoes = []
        for numero_item, saldo in enumerate(cls.itens_do_retorno(viagem), start=1):
            fiscal = NaturezaOperacaoService.para_item(
                natureza=natureza, filial=filial, produto=saldo.produto,
                cliente=None, data=data,
            )
            natureza_texto = natureza_texto or fiscal.natureza_operacao
            if fiscal.informacoes_complementares:
                informacoes.append(fiscal.informacoes_complementares)

            produto = saldo.produto
            unidade = produto.unidade_medida.sigla if produto.unidade_medida_id else 'UN'
            quantidade = saldo.quantidade_retornada or ZERO
            # O VALOR E' O MESMO DA SAIDA. A mercadoria voltou como saiu: dar
            # a ela outro valor no retorno faria a remessa e o retorno nao
            # baterem, e a diferenca apareceria como resultado inventado.
            unitario = saldo.custo_unitario or ZERO
            bruto = _dinheiro(quantidade * unitario)
            linha = {
                'numero_item': numero_item,
                'codigo_produto': produto.codigo or str(produto.pk),
                'descricao': produto.descricao,
                'codigo_ncm': (produto.ncm or '').strip(),
                'cfop': fiscal.cfop,
                'unidade_comercial': unidade,
                'quantidade_comercial': float(quantidade),
                'valor_unitario_comercial': float(unitario),
                'valor_bruto': _float(bruto),
                'unidade_tributavel': unidade,
                'quantidade_tributavel': float(quantidade),
                'valor_unitario_tributavel': float(unitario),
                'inclui_no_total': '1',
            }
            if fiscal.csosn:
                linha['icms_situacao_tributaria'] = fiscal.csosn
            elif fiscal.cst_icms:
                linha['icms_situacao_tributaria'] = fiscal.cst_icms
            if fiscal.aliquota_icms is not None:
                linha['icms_aliquota'] = float(fiscal.aliquota_icms)
            if fiscal.cst_pis:
                linha['pis_situacao_tributaria'] = fiscal.cst_pis
            if fiscal.aliquota_pis is not None:
                linha['pis_aliquota_porcentual'] = float(fiscal.aliquota_pis)
            if fiscal.cst_cofins:
                linha['cofins_situacao_tributaria'] = fiscal.cst_cofins
            if fiscal.aliquota_cofins is not None:
                linha['cofins_aliquota_porcentual'] = float(fiscal.aliquota_cofins)
            if fiscal.cst_ipi:
                linha['ipi_situacao_tributaria'] = fiscal.cst_ipi
            if fiscal.aliquota_ipi is not None:
                linha['ipi_aliquota'] = float(fiscal.aliquota_ipi)
            if produto.cest:
                linha['cest'] = produto.cest
            if saldo.lote_id:
                linha['numero_lote'] = saldo.lote.numero_lote
            linhas.append(linha)

        total = sum((Decimal(str(l['valor_bruto'])) for l in linhas), ZERO)
        cnpj = _digitos(filial.cnpj)

        payload: Dict[str, Any] = {
            'cnpj_emitente': cnpj,
            'natureza_operacao': natureza_texto,
            'numero': numero,
            'serie': str(serie),
            'data_emissao': momento,
            'data_entrada_saida': momento,
            # 0 = ENTRADA. É o que distingue esta nota da remessa: mesma
            # empresa nos dois lados, sentido oposto.
            'tipo_documento': '0',
            'finalidade_emissao': '1',
            'consumidor_final': 0,
            'presenca_comprador': '5',
            'local_destino': '1',
            'modalidade_frete': '0',
            'items': linhas,
            # 90 = sem pagamento: retorno não cobra de ninguém.
            'formas_pagamento': [{'forma_pagamento': '90', 'valor_pagamento': 0.0}],
            'valor_produtos': _float(total),
            'valor_desconto': 0.0,
            'valor_total': _float(total),
        }
        cls._aplicar_empresa_nos_dois_lados(payload, filial)

        # A NOTA DE RETORNO SO' SE EXPLICA COM A REMESSA AO LADO. Quem confere
        # precisa ligar as duas sem procurar: e' a diferenca entre elas que a
        # fiscalizacao pede para justificar.
        remessa = cls.remessa_da_viagem(viagem)
        if remessa is not None and remessa.chave:
            payload['notas_referenciadas'] = [{'chave_nfe': remessa.chave}]

        texto = ' '.join([
            'Retorno de mercadoria nao vendida em venda fora do estabelecimento.',
            f'Viagem {viagem.numero:06d}.',
            (
                (
                    f'Remessa {remessa.numero}/{remessa.serie} de '
                    f'{timezone.localtime(remessa.data_emissao):%d/%m/%Y}.'
                )
                if remessa is not None else ''
            ),
            f'Veiculo {viagem.veiculo_placa}.' if viagem.veiculo_placa else '',
            *informacoes,
        ])
        payload['informacoes_adicionais_contribuinte'] = ' '.join(texto.split())[:5000]
        return payload

    @staticmethod
    def _aplicar_empresa_nos_dois_lados(payload: Dict[str, Any], filial) -> None:
        """A empresa remete para si mesma, agora no sentido da entrada."""
        cnpj = _digitos(filial.cnpj)
        payload['cnpj_destinatario'] = cnpj
        payload['nome_destinatario'] = filial.razao_social
        for campo, valor in (
            ('logradouro_destinatario', getattr(filial, 'endereco', '')),
            ('numero_destinatario', getattr(filial, 'numero', '')),
            ('bairro_destinatario', getattr(filial, 'bairro', '')),
            ('municipio_destinatario', getattr(filial, 'cidade', '')),
            ('uf_destinatario', getattr(filial, 'uf', '')),
            ('cep_destinatario', _digitos(getattr(filial, 'cep', ''))),
        ):
            if valor:
                payload[campo] = str(valor)
        inscricao = getattr(filial, 'inscricao_estadual', '')
        if inscricao:
            payload['inscricao_estadual_destinatario'] = _digitos(inscricao)

    # ── Emitir ───────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def emitir(cls, viagem, usuario=None) -> DocumentoFiscal:
        """
        Reserva número, grava o documento e o amarra à viagem.

        CONFERE ANTES DE RESERVAR O NÚMERO: número reservado e não usado vira
        buraco na numeração.
        """
        problemas = cls.conferir(viagem)
        if problemas:
            raise DadosInvalidosError(' '.join(problemas))

        ja_emitida = cls.nota_da_viagem(viagem)
        if ja_emitida is not None:
            raise DadosInvalidosError(
                f'Esta viagem já tem a nota de retorno {ja_emitida.numero}/'
                f'{ja_emitida.serie}. Cancele-a antes de emitir outra.'
            )

        from apps.logistica.services.remessa_nfe import RemessaVendaForaService

        numero, serie = RemessaVendaForaService._reservar_numero(viagem.filial)
        payload = cls.construir_payload(viagem, numero, serie)
        filial = viagem.filial

        documento = DocumentoFiscal.objects.create(
            filial=filial,
            tipo_documento=TipoDocumentoFiscal.NFE,
            origem_tipo=ORIGEM,
            origem_id=viagem.pk,
            numero=numero,
            serie=serie,
            natureza_operacao_descricao=payload['natureza_operacao'],
            # 0 = entrada, como no payload: o documento gravado precisa dizer
            # o mesmo que a nota transmitida.
            tipo_operacao='0',
            finalidade_nfe=1,
            modalidade_frete=0,
            emitente_cnpj=filial.cnpj,
            destinatario_tipo='filial',
            destinatario_id=filial.pk,
            destinatario_snapshot={
                'nome': filial.razao_social,
                'cpf_cnpj': _digitos(filial.cnpj),
                'cidade': getattr(filial, 'cidade', ''),
                'uf': getattr(filial, 'uf', ''),
                'observacao': (
                    f'Retorno de venda fora do estabelecimento — '
                    f'viagem {viagem.numero:06d}'
                ),
            },
            valor_produtos=_dinheiro(payload['valor_produtos']),
            valor_total=_dinheiro(payload['valor_total']),
            status=StatusDocumentoFiscal.PENDENTE,
            # O QUE VAI PARA A SEFAZ E' O QUE FOI CONFERIDO AQUI. Entre a
            # emissao e a transmissao a operacao continua andando, e
            # remontar depois mandaria numeros diferentes dos que esta
            # nota registrou.
            payload_envio=payload,
            data_emissao=timezone.now(),
            usuario=usuario,
        )
        LogViagemService.registrar(
            viagem, LogViagemService.DOCUMENTO_EMITIDO, usuario=usuario,
            documento=documento, motivo='NF-e de retorno de venda fora',
        )
        return documento
