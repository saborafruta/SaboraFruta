"""
A NF-e de remessa para venda fora do estabelecimento.

A EMPRESA É REMETENTE E DESTINATÁRIA
====================================

Esta é a particularidade da operação, e a razão de ela não caber no fluxo
normal de venda: não há comprador. A mercadoria sai do estabelecimento para
ser vendida na rua, e a nota que a acompanha é emitida pela empresa contra ela
mesma — o próprio CNPJ nos dois lados. É isso que ampara a mercadoria em
trânsito sem que exista uma venda.

NADA DE FISCAL É DECIDIDO AQUI
==============================

CFOP, CST, CSOSN, alíquotas e o texto da natureza vêm todos de
`NaturezaOperacaoService`, que lê a tabela que a contabilidade cadastrou. Este
módulo monta o documento; ele não sabe que 5904 existe.

Se faltar regra, a emissão para — e para antes de reservar número, porque
número reservado e não usado vira buraco na numeração que a SEFAZ cobra depois.
"""
from decimal import Decimal
from typing import Any, Dict, List

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.fiscal.models import NaturezaOperacao
from apps.fiscal.services.natureza_operacao_service import NaturezaOperacaoService
from apps.financeiro.constants.enums import (
    StatusDocumentoFiscal, TipoDocumentoFiscal,
)
from apps.financeiro.models.fiscal import DocumentoFiscal

ZERO = Decimal('0')
CENTAVOS = Decimal('0.01')


def _digitos(valor: str) -> str:
    return ''.join(c for c in str(valor or '') if c.isdigit())


def _dinheiro(valor) -> Decimal:
    return (Decimal(str(valor or 0))).quantize(CENTAVOS)


def _float(valor) -> float:
    return float(_dinheiro(valor))


class RemessaVendaForaService:

    # ── O que vai na nota ────────────────────────────────────────────────

    @classmethod
    def itens_da_viagem(cls, viagem) -> list:
        """As linhas da carga que saem por remessa para venda fora."""
        return list(
            viagem.itens
            .filter(natureza__especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA)
            .select_related('natureza', 'produto', 'produto__unidade_medida', 'lote')
        )

    @classmethod
    def conferir(cls, viagem) -> list[str]:
        """
        Tudo que impede a remessa de ser emitida, junto.

        A LISTA INTEIRA, e não o primeiro problema: descobrir as pendências uma
        por vez, a cada tentativa, com o caminhão esperando, é o pior jeito de
        trabalhar.
        """
        problemas = []
        itens = cls.itens_da_viagem(viagem)
        if not itens:
            problemas.append('A carga não tem mercadoria para venda fora do estabelecimento.')

        cnpj = _digitos(getattr(viagem.filial, 'cnpj', ''))
        if len(cnpj) != 14:
            problemas.append('A filial precisa de um CNPJ válido para emitir a remessa.')

        for item in itens:
            if not (item.produto.ncm or '').strip():
                problemas.append(f'{item.produto}: sem NCM — a SEFAZ rejeita a nota.')
            if (item.valor_unitario or ZERO) <= ZERO:
                problemas.append(
                    f'{item.produto}: sem valor para a remessa. A nota precisa '
                    'declarar quanto sai.'
                )
            try:
                NaturezaOperacaoService.para_item(
                    natureza=item.natureza, filial=viagem.filial,
                    produto=item.produto, cliente=None, data=viagem.data_saida,
                )
            except DadosInvalidosError as erro:
                problemas.append(str(erro))
        return problemas

    # ── O payload ────────────────────────────────────────────────────────

    @classmethod
    def construir_payload(cls, viagem, numero: int, serie: int) -> Dict[str, Any]:
        itens = cls.itens_da_viagem(viagem)
        problemas = cls.conferir(viagem)
        if problemas:
            raise DadosInvalidosError(' '.join(problemas))

        filial = viagem.filial
        cnpj = _digitos(filial.cnpj)
        emissao = (viagem.data_saida or timezone.localdate()).isoformat()
        # O horario nao muda o fiscal, mas a nota precisa de um instante; usar a
        # hora de saida quando ela existe deixa o documento coerente com a viagem.
        momento = f'{emissao}T{(viagem.hora_saida or timezone.localtime().time()).strftime("%H:%M:%S")}-03:00'

        linhas: List[Dict[str, Any]] = []
        natureza_texto = ''
        informacoes = []
        for numero_item, item in enumerate(itens, start=1):
            fiscal = NaturezaOperacaoService.para_item(
                natureza=item.natureza, filial=filial,
                produto=item.produto, cliente=None, data=viagem.data_saida,
            )
            natureza_texto = natureza_texto or fiscal.natureza_operacao
            if fiscal.informacoes_complementares:
                informacoes.append(fiscal.informacoes_complementares)

            produto = item.produto
            bruto = _dinheiro((item.quantidade or ZERO) * (item.valor_unitario or ZERO))
            linha = {
                'numero_item': numero_item,
                'codigo_produto': produto.codigo or str(produto.pk),
                'descricao': produto.descricao,
                'codigo_ncm': (produto.ncm or '').strip(),
                'cfop': fiscal.cfop,
                'unidade_comercial': (
                    produto.unidade_medida.sigla if produto.unidade_medida_id else 'UN'
                ),
                'quantidade_comercial': float(item.quantidade or ZERO),
                'valor_unitario_comercial': float(item.valor_unitario or ZERO),
                'valor_bruto': _float(bruto),
                'unidade_tributavel': (
                    produto.unidade_medida.sigla if produto.unidade_medida_id else 'UN'
                ),
                'quantidade_tributavel': float(item.quantidade or ZERO),
                'valor_unitario_tributavel': float(item.valor_unitario or ZERO),
                'inclui_no_total': '1',
            }
            # CST OU CSOSN, nunca os dois: quem manda e' o regime da empresa, e
            # a regra cadastrada ja' traz o que vale para ela.
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
            if item.lote_id:
                linha['numero_lote'] = item.lote.numero_lote
            linhas.append(linha)

        total = sum((Decimal(str(l['valor_bruto'])) for l in linhas), ZERO)

        payload: Dict[str, Any] = {
            'cnpj_emitente': cnpj,
            'natureza_operacao': natureza_texto,
            'numero': numero,
            'serie': str(serie),
            'data_emissao': momento,
            'data_entrada_saida': momento,
            'tipo_documento': '1',       # saída
            'finalidade_emissao': '1',   # normal
            'consumidor_final': 0,
            'presenca_comprador': '5',   # operação presencial fora do estabelecimento
            'local_destino': '1',        # operação interna
            'modalidade_frete': '0',     # por conta do emitente
            'items': linhas,
            # 90 = sem pagamento: a remessa nao cobra de ninguem.
            'formas_pagamento': [{'forma_pagamento': '90', 'valor_pagamento': 0.0}],
            'valor_produtos': _float(total),
            'valor_desconto': 0.0,
            'valor_total': _float(total),
        }
        cls._aplicar_empresa_como_destinataria(payload, filial)

        # A INFORMACAO COMPLEMENTAR EXPLICA A NOTA a quem a ler na estrada --
        # fiscal de barreira inclusive. Sem ela, uma nota da empresa para ela
        # mesma parece erro.
        texto = ' '.join([
            'Remessa para venda fora do estabelecimento.',
            f'Viagem {viagem.numero:06d}.',
            f'Veiculo {viagem.veiculo_placa}.' if viagem.veiculo_placa else '',
            f'Responsavel {viagem.motorista_nome}.' if viagem.motorista_nome else '',
            *informacoes,
        ])
        payload['informacoes_adicionais_contribuinte'] = ' '.join(texto.split())[:5000]
        return payload

    @staticmethod
    def _aplicar_empresa_como_destinataria(payload: Dict[str, Any], filial) -> None:
        """
        A empresa nos dois lados da nota.

        É o que caracteriza a remessa para venda fora do estabelecimento: sem
        comprador, o destinatário é o próprio emitente. Apontar para um cliente
        qualquer aqui produziria uma venda que não aconteceu.
        """
        payload.update({
            'nome_destinatario': filial.razao_social,
            'cnpj_destinatario': _digitos(filial.cnpj),
            'inscricao_estadual_destinatario': _digitos(
                getattr(filial, 'inscricao_estadual', '') or '',
            ) or None,
            'indicador_inscricao_estadual_destinatario': '1',
            'logradouro_destinatario': getattr(filial, 'endereco', '') or '',
            'numero_destinatario': getattr(filial, 'numero', '') or 'S/N',
            'bairro_destinatario': getattr(filial, 'bairro', '') or '',
            'municipio_destinatario': getattr(filial, 'cidade', '') or '',
            'uf_destinatario': (getattr(filial, 'uf', '') or '').upper(),
            'cep_destinatario': _digitos(getattr(filial, 'cep', '') or ''),
            'pais_destinatario': 'Brasil',
        })

    # ── Emitir ───────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def emitir(cls, viagem, usuario=None) -> DocumentoFiscal:
        """
        Reserva número, grava o documento e o deixa pronto para transmissão.

        CONFERE ANTES DE RESERVAR O NÚMERO. Número reservado e não usado vira
        buraco na numeração, que a SEFAZ cobra depois com inutilização — muito
        mais trabalho do que recusar a emissão agora.
        """
        problemas = cls.conferir(viagem)
        if problemas:
            raise DadosInvalidosError(' '.join(problemas))

        ja_emitida = DocumentoFiscal.objects.filter(
            origem_tipo='viagem_remessa', origem_id=viagem.pk,
        ).exclude(status__in=(
            StatusDocumentoFiscal.CANCELADA,
            StatusDocumentoFiscal.REJEITADA,
            StatusDocumentoFiscal.DENEGADA,
        )).first()
        if ja_emitida is not None:
            raise DadosInvalidosError(
                f'Esta viagem já tem a remessa {ja_emitida.numero}/{ja_emitida.serie}. '
                'Cancele a nota antes de emitir outra.'
            )

        numero, serie = cls._reservar_numero(viagem.filial)
        payload = cls.construir_payload(viagem, numero, serie)

        documento = DocumentoFiscal.objects.create(
            filial=viagem.filial,
            tipo_documento=TipoDocumentoFiscal.NFE,
            origem_tipo='viagem_remessa',
            origem_id=viagem.pk,
            numero=numero,
            serie=serie,
            natureza_operacao_descricao=payload['natureza_operacao'],
            tipo_operacao='1',
            finalidade_nfe=1,
            modalidade_frete=0,
            emitente_cnpj=viagem.filial.cnpj,
            # DESTINATARIO E' A PROPRIA EMPRESA -- e o snapshot registra isso,
            # para a nota continuar explicavel depois que o cadastro mudar.
            destinatario_tipo='filial',
            destinatario_id=viagem.filial_id,
            destinatario_snapshot={
                'nome': viagem.filial.razao_social,
                'cpf_cnpj': _digitos(viagem.filial.cnpj),
                'cidade': getattr(viagem.filial, 'cidade', ''),
                'uf': getattr(viagem.filial, 'uf', ''),
                'observacao': 'Remessa para venda fora do estabelecimento',
            },
            valor_produtos=_dinheiro(payload['valor_produtos']),
            valor_total=_dinheiro(payload['valor_total']),
            status=StatusDocumentoFiscal.PENDENTE,
            data_emissao=timezone.now(),
            usuario=usuario,
        )

        # O ELO ATE' A CARGA: cada linha da remessa passa a apontar para a nota
        # que a ampara -- viagem → carga → documento fiscal.
        viagem.itens.filter(
            natureza__especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
        ).update(documento_fiscal=documento)
        return documento

    @staticmethod
    def _reservar_numero(filial) -> tuple[int, int]:
        """Reserva número e série de NF-e, como o resto do sistema faz."""
        from apps.core.models.parametros import (
            ParametroDocumentoFiscal, ParametrosSistema,
        )

        parametros, _ = ParametrosSistema.objects.get_or_create(filial=filial)
        config = (
            ParametroDocumentoFiscal.objects.select_for_update()
            .filter(parametros=parametros, tipo_documento='nfe').first()
        )
        if config is not None:
            numero, serie = config.proximo_numero, config.serie or 1
            config.proximo_numero = numero + 1
            config.save(update_fields=['proximo_numero'])
            return numero, serie

        from apps.core.models.empresa import Filial

        travada = Filial.objects.select_for_update().get(pk=filial.pk)
        numero = travada.proximo_numero_nfe
        serie = travada.serie_nfe or 1
        travada.proximo_numero_nfe = numero + 1
        travada.save(update_fields=['proximo_numero_nfe'])
        return numero, serie
