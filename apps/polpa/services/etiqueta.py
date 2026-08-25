"""
A etiqueta do lote: o que ela diz e para onde o QR leva.

A ETIQUETA É O ÚNICO PEDAÇO DO SISTEMA QUE SAI DA FÁBRICA. Ela vai colada
no saco que chega ao supermercado, e é ela que a fiscalização lê. Por isso
os dados dela não são "campos escolhidos numa tela": são o que a legislação
e o cliente cobram, e cada um tem uma origem única no cadastro.

O QUE ELA CARREGA, e de onde vem cada coisa:

  · nome e código do produto — do catálogo (seção 1);
  · lote, fabricação e validade — do `LoteProduto`, criado no encerramento
    da ordem (seção 3), com a validade calculada pelo prazo do produto;
  · peso — quantidade × peso do produto, calculado (seção 15);
  · código de barras — o EAN do produto quando existe; senão, o NÚMERO DO
    LOTE em Code128, que é o que o almoxarifado precisa ler;
  · QR — abre a rastreabilidade daquele lote.

O QR LEVA PARA DENTRO DO SISTEMA, e pede login. É a mesma decisão da
etiqueta da moda: o código poupa a digitação do endereço, não substitui o
acesso. Uma página pública de rastreabilidade é outra conversa — mostraria
fornecedor, custo e processo para quem pegasse a embalagem no mercado.

NADA É GRAVADO. A etiqueta é derivada do lote a cada impressão: guardar uma
cópia dos dados daria uma etiqueta que continua dizendo "válido até 10/03"
depois de alguém corrigir a validade do lote.
"""
from __future__ import annotations

from decimal import Decimal

from django.urls import reverse

from apps.core.services.barras import suportado

ZERO = Decimal('0')


class EtiquetaService:

    @staticmethod
    def dados(lote) -> dict:
        """Tudo que a etiqueta mostra, com a origem de cada campo."""
        produto = lote.produto
        ficha = getattr(produto, 'ficha_polpa', None)
        armazenado = getattr(lote, 'armazenamento_polpa', None)

        peso_unitario = produto.peso_liquido or ZERO
        peso = (
            ((lote.quantidade_atual or ZERO) * peso_unitario).quantize(Decimal('0.001'))
            if peso_unitario else None
        )

        return {
            'lote': lote,
            'produto': produto,
            'nome': ficha.nome_completo if ficha else produto.descricao,
            'codigo': produto.codigo or '',
            'numero_lote': lote.numero_lote,
            'fabricacao': lote.data_fabricacao,
            'validade': lote.data_validade,
            'peso_unitario': peso_unitario or None,
            'peso': peso,
            'quantidade': lote.quantidade_atual,
            'unidade': getattr(produto.unidade_medida, 'sigla', ''),
            'sabor': ficha.sabor if ficha else '',
            'registro_mapa': ficha.registro_mapa if ficha else '',
            'conservacao': produto.get_condicao_armazenamento_display(),
            'temperatura': produto.temperatura_maxima,
            'onde': armazenado.onde if armazenado else '',
            'codigo_barras': EtiquetaService.codigo_de_barras(lote),
        }

    @staticmethod
    def codigo_de_barras(lote) -> str:
        """
        O que vai desenhado em barras.

        O EAN DO PRODUTO QUANDO EXISTE — é ele que o caixa do supermercado
        lê, e uma etiqueta com outro código faria a venda travar. Sem EAN,
        vai o NÚMERO DO LOTE: para o almoxarifado da fábrica, que precisa
        identificar aquele lote específico, ele é até mais útil.

        Vazio quando nem um nem outro serve para Code128 — melhor não
        desenhar do que desenhar um código que o leitor traduz para outra
        coisa, que é erro silencioso.
        """
        ean = (lote.produto.codigo_barras or '').strip()
        if ean and suportado(ean):
            return ean
        numero = (lote.numero_lote or '').strip()
        return numero if suportado(numero) else ''

    @staticmethod
    def url_rastreabilidade(request, lote) -> str:
        """
        Para onde o QR aponta: a rastreabilidade daquele lote.

        A tela já aceita `?lote=`, e por isso não precisou de rota nova — uma
        rota paralela para a mesma consulta daria duas telas de
        rastreabilidade, e a segunda é a que ninguém mantém.
        """
        caminho = f"{reverse('lotes:rastreabilidade')}?lote={lote.pk}"
        return request.build_absolute_uri(caminho)

    @staticmethod
    def pendencias(lote) -> list[str]:
        """
        O que falta para esta etiqueta poder ir para a rua.

        NÃO IMPEDE A IMPRESSÃO: a etiqueta de uso interno vale sem EAN, e
        travar faria a fábrica imprimir num Word à parte -- que é como a
        etiqueta deixa de ter qualquer relação com o sistema. Mas o que
        falta aparece, porque etiqueta sem validade não pode sair.
        """
        faltando = []
        if not lote.data_validade:
            faltando.append(
                'Sem validade — a etiqueta não pode ir para a rua assim.'
            )
        if not lote.data_fabricacao:
            faltando.append('Sem data de fabricação.')
        if not (lote.produto.codigo_barras or '').strip():
            faltando.append(
                'Produto sem código de barras — vai o número do lote em '
                'Code128, que o caixa do supermercado não lê.'
            )
        if not lote.produto.peso_liquido:
            faltando.append('Produto sem peso — a etiqueta sai sem o peso.')
        return faltando
