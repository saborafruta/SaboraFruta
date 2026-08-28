"""
A NF-e da entrega feita na rua: venda ou bonificação.

O ELO QUE FALTAVA
=================

A remessa ampara a mercadoria enquanto ela roda no caminhão, e a venda na rua
já era registrada contra o saldo da viagem. Entre as duas havia um buraco: o
cliente que comprava no meio da rota ficava SEM DOCUMENTO. A mercadoria saía
do caminhão, o saldo baixava, o financeiro registrava — e nada acompanhava a
caixa até a mão de quem pagou.

É a nota desta venda que fecha a operação: ela transforma a remessa (que era
da empresa para ela mesma) na venda que de fato aconteceu, com CFOP próprio
de venda fora do estabelecimento.

NADA DE FISCAL É DECIDIDO AQUI
==============================

CFOP, CST, CSOSN, alíquotas e o texto da natureza vêm de
`NaturezaOperacaoService`, que lê a tabela cadastrada pela contabilidade —
por UF, regime e produto. Este módulo monta o documento; ele não sabe que
5103 ou 5104 existem, e nem deve saber.

A NATUREZA VEM DA ESPÉCIE DO QUE FOI ENTREGUE — `venda_fora` para venda,
`bonificacao` para bonificação — procurada no cadastro da filial. Sem ela a
emissão para e diz o que cadastrar. Escolher uma natureza de venda comum
"porque parece igual" produziria uma nota com o CFOP errado, e o erro só
apareceria na apuração, meses depois.

BONIFICAÇÃO É A MESMA ENTREGA COM OUTRA NATUREZA: mesmo cliente, mesmos
itens, mesmo saldo. Muda o CFOP e o fato de que ninguém paga — e por isso
ela sai sem meio de pagamento declarado, que é o que diria à SEFAZ que houve
recebimento.

QUEM COMPRA PRECISA TER NOME E DOCUMENTO
========================================

Uma NF-e sem destinatário identificado não existe. Na rua isso é um problema
real: o vendedor anota "padaria da esquina" e segue. Por isso a conferência
cobra CPF ou CNPJ ANTES de reservar número — número reservado e não usado
vira buraco na numeração, que a SEFAZ cobra depois com inutilização.

A NOTA NÃO MEXE NO ESTOQUE
==========================

A mercadoria já saiu do estabelecimento na remessa, e o saldo da viagem já
foi baixado quando a venda foi registrada. Emitir a nota é um ato fiscal,
não um movimento físico — dar baixa aqui tiraria pela terceira vez a mesma
caixa.
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
from apps.logistica.models import VendaViagem

ZERO = Decimal('0')
CENTAVOS = Decimal('0.01')

# A ORIGEM DIZ QUE OPERACAO O DOCUMENTO E'. Bonificacao gravada como
# "venda fora" ficaria REGISTRADA COMO VENDA nos proprios registros do
# sistema: quem consultasse documentos por origem veria a cortesia dentro
# das vendas, e o relatorio de vendas do mes contaria mercadoria que ninguem
# pagou.
ORIGEM_VENDA = 'viagem_venda_fora'
ORIGEM_BONIFICACAO = 'viagem_bonificacao'

ORIGEM_POR_TIPO = {
    VendaViagem.Tipo.VENDA: ORIGEM_VENDA,
    VendaViagem.Tipo.BONIFICACAO: ORIGEM_BONIFICACAO,
}

# Mantido para quem importa o nome antigo.
ORIGEM = ORIGEM_VENDA

# Nota já emitida que continua valendo — as outras não amparam nada.
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


class VendaForaNFeService:

    # ── A natureza ───────────────────────────────────────────────────────

    # A ESPÉCIE FISCAL DE CADA TIPO DE ENTREGA. Bonificação e venda são a
    # mesma entrega no caminhão e documentos diferentes na SEFAZ -- e é a
    # natureza cadastrada que carrega essa diferença, não o código.
    ESPECIE_POR_TIPO = {
        VendaViagem.Tipo.VENDA: NaturezaOperacao.Especie.VENDA_FORA,
        VendaViagem.Tipo.BONIFICACAO: NaturezaOperacao.Especie.BONIFICACAO,
    }

    NOME_DA_ESPECIE = {
        NaturezaOperacao.Especie.VENDA_FORA: 'venda fora do estabelecimento',
        NaturezaOperacao.Especie.BONIFICACAO: 'bonificação',
    }

    @classmethod
    def natureza(cls, filial, tipo=VendaViagem.Tipo.VENDA):
        """
        A natureza cadastrada para este tipo de entrega.

        UMA SÓ, E ATIVA. Duas naturezas da mesma espécie deixariam a nota
        depender de qual delas o código pegasse primeiro — e o CFOP mudaria
        sem ninguém ter escolhido nada.
        """
        especie = cls.ESPECIE_POR_TIPO.get(tipo, NaturezaOperacao.Especie.VENDA_FORA)
        nome = cls.NOME_DA_ESPECIE[especie]
        naturezas = list(
            NaturezaOperacao.objects.for_filial(filial).filter(
                especie=especie, ativo=True,
            )
        )
        if not naturezas:
            raise DadosInvalidosError(
                f'Nenhuma natureza de operação cadastrada para {nome}. '
                'Cadastre-a em Fiscal › Naturezas de operação antes de emitir '
                'a nota.'
            )
        if len(naturezas) > 1:
            raise DadosInvalidosError(
                f'Há mais de uma natureza ativa para {nome}: deixe apenas uma '
                'ativa, senão a nota sai com o CFOP de qualquer uma delas.'
            )
        return naturezas[0]

    # ── Conferência ──────────────────────────────────────────────────────

    @classmethod
    def conferir(cls, venda: VendaViagem) -> list[str]:
        """
        Tudo que impede esta nota de ser emitida, junto.

        A LISTA INTEIRA, e não o primeiro problema: quem está na rua com o
        cliente esperando não pode descobrir as pendências uma por vez.
        """
        problemas = []

        if venda.status == VendaViagem.Status.CANCELADA:
            problemas.append('Esta venda foi cancelada.')

        itens = list(venda.itens.select_related('produto', 'lote'))
        if not itens:
            problemas.append('A venda não tem itens.')

        if not venda.cliente_nome:
            problemas.append('A venda não diz para quem foi.')
        if not _digitos(venda.cliente_documento):
            problemas.append(
                'Sem CPF ou CNPJ do comprador não há como emitir NF-e — '
                'complete o cadastro do cliente na venda.'
            )

        try:
            natureza = cls.natureza(venda.viagem.filial, venda.tipo)
        except DadosInvalidosError as erro:
            problemas.append(str(erro))
            natureza = None

        if natureza is not None:
            for item in itens:
                # A REGRA FISCAL É CONFERIDA AGORA, e não na transmissão:
                # descobrir que falta CFOP com a nota meio emitida é o pior
                # momento possível.
                try:
                    NaturezaOperacaoService.para_item(
                        natureza=natureza, filial=venda.viagem.filial,
                        produto=item.produto, cliente=venda.cliente,
                        data=venda.data.date() if venda.data else None,
                    )
                except DadosInvalidosError as erro:
                    problemas.append(str(erro))
        return problemas

    @staticmethod
    def origem(venda: VendaViagem) -> str:
        """Sob que operação este documento é arquivado."""
        return ORIGEM_POR_TIPO.get(venda.tipo, ORIGEM_VENDA)

    @classmethod
    def nota_da_venda(cls, venda: VendaViagem):
        """A NF-e que ampara esta entrega, se já houver uma viva."""
        return (
            DocumentoFiscal.objects
            .filter(origem_tipo=cls.origem(venda), origem_id=venda.pk)
            .exclude(status__in=STATUS_MORTOS)
            .order_by('-id')
            .first()
        )

    # ── O payload ────────────────────────────────────────────────────────

    @classmethod
    def construir_payload(cls, venda: VendaViagem, numero: int, serie: int) -> Dict[str, Any]:
        problemas = cls.conferir(venda)
        if problemas:
            raise DadosInvalidosError(' '.join(problemas))

        viagem = venda.viagem
        filial = viagem.filial
        natureza = cls.natureza(filial, venda.tipo)
        momento = timezone.localtime(venda.data or timezone.now()).strftime(
            '%Y-%m-%dT%H:%M:%S-03:00'
        )

        linhas: List[Dict[str, Any]] = []
        natureza_texto = ''
        informacoes = []
        for numero_item, item in enumerate(
            venda.itens.select_related('produto', 'lote'), start=1,
        ):
            fiscal = NaturezaOperacaoService.para_item(
                natureza=natureza, filial=filial, produto=item.produto,
                cliente=venda.cliente,
                data=venda.data.date() if venda.data else None,
            )
            natureza_texto = natureza_texto or fiscal.natureza_operacao
            if fiscal.informacoes_complementares:
                informacoes.append(fiscal.informacoes_complementares)

            produto = item.produto
            unidade = produto.unidade_medida.sigla if produto.unidade_medida_id else 'UN'
            bruto = _dinheiro((item.quantidade or ZERO) * (item.valor_unitario or ZERO))
            linha = {
                'numero_item': numero_item,
                'codigo_produto': produto.codigo or str(produto.pk),
                'descricao': produto.descricao,
                'codigo_ncm': (produto.ncm or '').strip(),
                'cfop': fiscal.cfop,
                'unidade_comercial': unidade,
                'quantidade_comercial': float(item.quantidade or ZERO),
                'valor_unitario_comercial': float(item.valor_unitario or ZERO),
                'valor_bruto': _float(bruto),
                'unidade_tributavel': unidade,
                'quantidade_tributavel': float(item.quantidade or ZERO),
                'valor_unitario_tributavel': float(item.valor_unitario or ZERO),
                'inclui_no_total': '1',
            }
            # CST OU CSOSN, nunca os dois: quem manda é o regime da empresa, e
            # a regra cadastrada já traz o que vale para ela.
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
            # O LOTE VAI NA NOTA porque é ele que a rastreabilidade persegue
            # num recall: sem ele, a nota diz o que foi vendido e não de qual
            # produção veio.
            if item.lote_id:
                linha['numero_lote'] = item.lote.numero_lote
            linhas.append(linha)

        total = sum((Decimal(str(l['valor_bruto'])) for l in linhas), ZERO)

        payload: Dict[str, Any] = {
            'cnpj_emitente': _digitos(filial.cnpj),
            'natureza_operacao': natureza_texto,
            'numero': numero,
            'serie': str(serie),
            'data_emissao': momento,
            'data_entrada_saida': momento,
            'tipo_documento': '1',       # saída
            'finalidade_emissao': '1',   # normal
            'consumidor_final': 1,
            # 5 = operação presencial FORA do estabelecimento. É o código que
            # descreve exatamente esta venda, e ele não é escolha de estilo:
            # a SEFAZ cruza presença com CFOP.
            'presenca_comprador': '5',
            'local_destino': '1',
            'modalidade_frete': '0',
            'items': linhas,
            'formas_pagamento': cls._formas_pagamento(venda, total),
            'valor_produtos': _float(total),
            'valor_desconto': 0.0,
            'valor_total': _float(total),
        }
        cls._aplicar_destinatario(payload, venda)

        # A INFORMAÇÃO COMPLEMENTAR AMARRA A NOTA À REMESSA. Quem lê esta nota
        # na barreira precisa entender que a mercadoria saiu antes, com outra
        # nota, na mesma viagem.
        texto = ' '.join([
            (
                'Bonificacao entregue fora do estabelecimento.'
                if venda.bonificacao else 'Venda fora do estabelecimento.'
            ),
            f'Viagem {viagem.numero:06d}.',
            f'Veiculo {viagem.veiculo_placa}.' if viagem.veiculo_placa else '',
            *informacoes,
        ])
        payload['informacoes_adicionais_contribuinte'] = ' '.join(texto.split())[:5000]
        return payload

    @staticmethod
    def _formas_pagamento(venda: VendaViagem, total: Decimal) -> list[dict]:
        """
        Como o cliente pagou, no código que a SEFAZ entende.

        O CÓDIGO VEM DO CADASTRO (`FormaPagamento.codigo_sefaz`), não de uma
        tabela escrita aqui: é a mesma parametrização que o resto do sistema
        usa. Sem forma informada, vai `99` (outros) — que é honesto, e não um
        palpite de dinheiro ou cartão.
        """
        # 90 = SEM PAGAMENTO. Bonificação entrega sem cobrar, e declarar um
        # meio de pagamento nela diria à SEFAZ que houve recebimento.
        if venda.bonificacao:
            return [{'forma_pagamento': '90', 'valor_pagamento': 0.0}]
        codigo = getattr(venda.forma_pagamento, 'codigo_sefaz', '') or '99'
        return [{'forma_pagamento': codigo, 'valor_pagamento': _float(total)}]

    @staticmethod
    def _aplicar_destinatario(payload: Dict[str, Any], venda: VendaViagem) -> None:
        """
        Quem comprou na rua.

        O ENDEREÇO VEM DO QUE A VENDA GUARDOU, e não do cadastro de agora: a
        nota tem de continuar explicável depois que o cliente mudar de
        endereço.
        """
        documento = _digitos(venda.cliente_documento)
        endereco = venda.endereco or {}
        chave = 'cpf_destinatario' if len(documento) == 11 else 'cnpj_destinatario'

        payload[chave] = documento
        payload['nome_destinatario'] = venda.cliente_nome
        for campo, origem in (
            ('logradouro_destinatario', 'logradouro'),
            ('numero_destinatario', 'numero'),
            ('bairro_destinatario', 'bairro'),
            ('municipio_destinatario', 'cidade'),
            ('uf_destinatario', 'uf'),
            ('cep_destinatario', 'cep'),
        ):
            valor = endereco.get(origem) or ''
            if valor:
                payload[campo] = str(valor)

    # ── Emitir ───────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def emitir(cls, venda: VendaViagem, usuario=None) -> DocumentoFiscal:
        """
        Reserva número, grava o documento e o amarra à venda.

        CONFERE ANTES DE RESERVAR O NÚMERO, pela mesma razão da remessa:
        número reservado e não usado vira buraco na numeração.
        """
        problemas = cls.conferir(venda)
        if problemas:
            raise DadosInvalidosError(' '.join(problemas))

        ja_emitida = cls.nota_da_venda(venda)
        if ja_emitida is not None:
            raise DadosInvalidosError(
                f'Esta venda já tem a nota {ja_emitida.numero}/{ja_emitida.serie}. '
                'Cancele-a antes de emitir outra.'
            )

        from apps.logistica.services.remessa_nfe import RemessaVendaForaService

        # O NÚMERO É RESERVADO PELO MESMO CAMINHO da remessa: duas contagens
        # de numeração na mesma empresa dariam duas notas com o mesmo número.
        numero, serie = RemessaVendaForaService._reservar_numero(venda.viagem.filial)
        payload = cls.construir_payload(venda, numero, serie)

        filial = venda.viagem.filial
        documento = DocumentoFiscal.objects.create(
            filial=filial,
            tipo_documento=TipoDocumentoFiscal.NFE,
            origem_tipo=cls.origem(venda),
            origem_id=venda.pk,
            numero=numero,
            serie=serie,
            natureza_operacao_descricao=payload['natureza_operacao'],
            tipo_operacao='1',
            finalidade_nfe=1,
            modalidade_frete=0,
            emitente_cnpj=filial.cnpj,
            destinatario_tipo='cliente',
            destinatario_id=venda.cliente_id,
            # O SNAPSHOT CONGELA O CLIENTE como ele era na venda: a nota
            # precisa continuar explicável depois que o cadastro mudar.
            destinatario_snapshot={
                'nome': venda.cliente_nome,
                'cpf_cnpj': _digitos(venda.cliente_documento),
                'cidade': (venda.endereco or {}).get('cidade', ''),
                'uf': (venda.endereco or {}).get('uf', ''),
                'observacao': (
                    f'{venda.get_tipo_display()} fora do estabelecimento — '
                    f'viagem {venda.viagem.numero:06d}'
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

        venda.documento_fiscal = documento
        venda.save(update_fields=['documento_fiscal', 'updated_at'])
        LogViagemService.registrar(
            venda.viagem, LogViagemService.DOCUMENTO_EMITIDO, usuario=usuario,
            documento=documento,
            motivo=f'NF-e de {venda.get_tipo_display().lower()} — {venda.cliente_nome}',
        )
        return documento
