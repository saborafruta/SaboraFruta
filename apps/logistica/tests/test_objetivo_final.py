"""
O objetivo final, percorrido pelas telas.

UMA VIAGEM, UMA CARGA, TRÊS OPERAÇÕES

    produtos já vendidos + produtos para vender na rota + bonificação
        ↓ carga → documentos fiscais → MDF-e → estoque em trânsito
        ↓ novas vendas → bonificações entregues → retorno das sobras
        ↓ NF-e de retorno → conciliação → encerramento

POR QUE PELA INTERFACE, E NÃO PELOS SERVIÇOS

Os serviços já têm os seus testes, e todos passam. Só que a promessa da
especificação não é "os serviços funcionam" — é que UMA PESSOA consegue
fazer isso, do começo ao fim, pelas telas que existem. Um passo que só
funciona chamando o serviço de dentro do shell é um passo que, na prática,
não existe.

Este teste faz o caminho inteiro por requisições HTTP, como quem clica: se
alguma etapa exigir contorno, ele quebra aqui e não no dia da carga.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import LoteProduto, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import (
    StatusContaReceber, StatusDocumentoFiscal,
)
from apps.financeiro.models.formas_pagamento import (
    CondicaoPagamento, FormaPagamento,
)
from apps.financeiro.models.receber_pagar import ContaReceber
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import (
    EntregaBonificacao, ItemCarga, ItemVendaViagem, SaldoCarga, VendaViagem,
    Viagem,
)
from apps.logistica.services.bonificacao_nfe import BonificacaoNFeService
from apps.logistica.services.entrega_bonificacao import (
    EntregaBonificacaoService,
)
from apps.logistica.services.estoque_viagem import EstoqueViagemService
from apps.logistica.services.fluxo_viagem import FluxoViagemService
from apps.logistica.services.rastreabilidade import RastreabilidadeService
from apps.logistica.services.mdfe_viagem import MDFeViagemService
from apps.logistica.services.remessa_nfe import RemessaVendaForaService
from apps.logistica.services.retorno_nfe import RetornoVendaForaService
from apps.logistica.services.venda_fora_nfe import VendaForaNFeService
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.logistica.services.vinculo_remessa import VinculoRemessaService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

ZERO = Decimal('0')
T = VendaViagem.Tipo


class JornadaBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Jornada LTDA', nome_fantasia='Jornada',
            cnpj='91345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='91345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
            endereco='Av. Principal', numero='100', bairro='Centro',
            cep='59000000', inscricao_estadual='123456789',
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='jornada@rota.local', nome='Jornada', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado da Esquina',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)

        for codigo, descricao, especie, cfop in (
            ('venda', 'Venda', NaturezaOperacao.Especie.VENDA, '5102'),
            ('remessa', 'Remessa', NaturezaOperacao.Especie.REMESSA_VENDA_FORA, '5904'),
            ('vendafora', 'Venda fora', NaturezaOperacao.Especie.VENDA_FORA, '5103'),
            ('retorno', 'Retorno', NaturezaOperacao.Especie.RETORNO_VENDA_FORA, '1904'),
            ('bonif', 'Bonificação', NaturezaOperacao.Especie.BONIFICACAO, '5910'),
        ):
            natureza = NaturezaOperacao.objects.create(
                filial=cls.filial, codigo=codigo, descricao=descricao,
                especie=especie,
                exige_destinatario=especie in (
                    NaturezaOperacao.Especie.VENDA,
                    NaturezaOperacao.Especie.VENDA_FORA,
                    NaturezaOperacao.Especie.BONIFICACAO,
                ),
            )
            RegraNaturezaOperacao.objects.create(natureza=natureza, cfop=cfop)
            setattr(cls, f'natureza_{codigo}', natureza)

        # A prazo gera título; dinheiro na entrega, não.
        cls.a_prazo = FormaPagamento.objects.create(
            empresa=cls.empresa, descricao='Boleto 30 dias', tipo='boleto',
            gera_parcelas=True, prazo_liquidacao_dias=30,
        )
        cls.a_vista = FormaPagamento.objects.create(
            empresa=cls.empresa, descricao='Dinheiro', tipo='dinheiro',
            gera_parcelas=False,
        )
        cls.condicao = CondicaoPagamento.objects.create(
            empresa=cls.empresa, descricao='3x 30/60/90',
            numero_parcelas=3, intervalo_dias=30, dias_primeira_parcela=30,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.produto = self._produto('P1', '5000')
        self.lote = LoteProduto.objects.create(
            filial=self.filial, produto=self.produto, numero_lote='L-1',
            quantidade_inicial=Decimal('1000'), quantidade_atual=Decimal('1000'),
        )

    # ── Fixtures ─────────────────────────────────────────────────────────

    def _produto(self, codigo, saldo):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade,
            descricao=f'Produto {codigo}', codigo=codigo, ncm='20079900',
            controla_lote=False, preco_venda=Decimal('10'),
            preco_custo=Decimal('4'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(saldo), usuario_id=self.usuario.pk,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
        )
        return produto

    def _viagem(self, itens):
        viagem = Viagem.objects.create(
            filial=self.filial, numero=Viagem.objects.count() + 1,
            motorista_nome='Seu Zé', veiculo_placa='ABC1D23',
            vendedor=self.usuario, responsavel=self.usuario,
        )
        for dados in itens:
            ViagemService.adicionar_item(viagem, {
                'produto': self.produto, 'valor_unitario': '10', **dados,
            })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        viagem.status = Viagem.Status.EM_VENDAS
        viagem.save(update_fields=['status'])
        return viagem




class ObjetivoFinalTests(JornadaBase):
    """A jornada inteira, pela interface."""

    def _post(self, nome, dados=None, **kwargs):
        resposta = self.client.post(
            reverse(nome, kwargs=kwargs), dados or {}, follow=True,
        )
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def _erros(self, resposta) -> list:
        return [
            m.message for m in resposta.context['messages']
            if m.level_tag == 'error'
        ]

    def test_uma_viagem_do_carregamento_ao_encerramento(self):
        # ── 1. A viagem ──────────────────────────────────────────────────
        resposta = self._post('logistica:viagem-create', {
            'data_saida': timezone.localdate().isoformat(),
            'motorista_nome': 'Seu Zé',
            'veiculo_placa': 'ABC1D23',
            'responsavel': self.usuario.pk,
            'vendedor': self.usuario.pk,
            'uf_origem': 'RN',
            'uf_destino': 'RN',
            'status': Viagem.Status.RASCUNHO,
        })
        self.assertEqual(self._erros(resposta), [])
        viagem = Viagem.objects.get(filial=self.filial)

        # ── 2. A carga: as três operações no mesmo caminhão ──────────────
        for especie, natureza, quantidade, cliente in (
            ('venda', self.natureza_venda, '150', self.cliente.pk),
            ('remessa_venda_fora', self.natureza_remessa, '200', ''),
            ('bonificacao', self.natureza_bonif, '10', self.cliente.pk),
        ):
            resposta = self._post(
                'logistica:viagem-item-create',
                {
                    'natureza': natureza.pk,
                    'produto': self.produto.pk,
                    'cliente': cliente,
                    'quantidade': quantidade,
                    'valor_unitario': '10',
                },
                pk=viagem.pk, especie=especie,
            )
            self.assertEqual(self._erros(resposta), [], especie)

        self.assertEqual(ItemCarga.objects.filter(viagem=viagem).count(), 3)

        # ── 3. Fechar a carga: o estoque sai ─────────────────────────────
        resposta = self._post('logistica:viagem-fechar-carga', pk=viagem.pk)
        self.assertEqual(self._erros(resposta), [])
        self.assertEqual(
            MovimentacaoEstoque.objects.filter(
                documento_tipo='viagem', documento_id=viagem.pk,
            ).count(),
            3,
        )

        # ── 4. Os documentos fiscais ─────────────────────────────────────
        resposta = self._post('logistica:viagem-emitir-remessa', pk=viagem.pk)
        self.assertEqual(self._erros(resposta), [])
        remessa = RemessaVendaForaService.nota_da_viagem(viagem)
        self.assertIsNotNone(remessa)

        # ── 5. O MDF-e consolida o transporte ────────────────────────────
        # O FORMULARIO JA' VEM PREENCHIDO PELA VIAGEM: pedir motorista e
        # placa de novo e' a chance de o manifesto sair com placa diferente
        # da que esta' no patio -- divergencia que nao da' erro em lugar
        # nenhum e aparece na fiscalizacao de estrada.
        formulario = self.client.get(
            reverse('logistica:mdfe-create'), {'viagem': viagem.pk},
        )
        inicial = formulario.context['form'].initial
        self.assertEqual(inicial['veiculo_placa'], 'ABC1D23')
        self.assertEqual(inicial['motorista_nome'], 'Seu Zé')

        resposta = self._post('logistica:mdfe-create', {
            'viagem': viagem.pk,
            'numero': inicial['numero'],
            'serie': inicial['serie'],
            'data_emissao': timezone.localdate().isoformat(),
            'modal': 'rodoviario',
            'motorista_nome': inicial['motorista_nome'],
            'veiculo_placa': inicial['veiculo_placa'],
            'uf_carregamento': 'RN',
            'municipio_carregamento': 'Natal',
            'uf_descarregamento': 'RN',
            'municipio_descarregamento': 'Mossoró',
            'peso_carga_kg': '100',
            'inicio_viagem': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
            'previsao_chegada': (
                timezone.localtime() + timedelta(hours=4)
            ).strftime('%Y-%m-%dT%H:%M'),
        })
        self.assertEqual(self._erros(resposta), [])

        # A SEFAZ E' O QUE FALTA AQUI, e nao a tela: o manifesto so' carrega
        # nota autorizada, e a transmissao para a SEFAZ e' o unico passo
        # desta jornada que o sistema ainda nao faz sozinho. O teste
        # autoriza a nota no lugar dela, e segue.
        remessa.chave = '3' * 44
        remessa.status = StatusDocumentoFiscal.AUTORIZADA
        remessa.save(update_fields=['chave', 'status'])

        resposta = self._post(
            'logistica:viagem-mdfe', {'documentos': [remessa.pk]}, pk=viagem.pk,
        )
        self.assertEqual(self._erros(resposta), [])
        painel = MDFeViagemService.painel(viagem)
        self.assertTrue(painel['emitido'])
        self.assertEqual(painel['documentos'], 1)
        # O MANIFESTO CONSOLIDA O TRANSPORTE e nao a operacao: a remessa
        # entra nele sem deixar de ser remessa.
        remessa.refresh_from_db()
        self.assertEqual(remessa.origem_tipo, 'viagem_remessa')

        # ── 6. O caminhão sai: estoque em trânsito ───────────────────────
        for destino in (
            Viagem.Status.AGUARDANDO_DOCUMENTOS,
            Viagem.Status.DOCUMENTOS_EMITIDOS,
            Viagem.Status.MDFE_AUTORIZADO,
            Viagem.Status.EM_TRANSITO,
            Viagem.Status.EM_VENDAS,
        ):
            resposta = self._post(
                'logistica:viagem-status', {'status': destino}, pk=viagem.pk,
            )
            self.assertEqual(self._erros(resposta), [], destino)

        viagem.refresh_from_db()
        self.assertEqual(viagem.status, Viagem.Status.EM_VENDAS)
        self.assertEqual(
            EstoqueViagemService.quadro(viagem)['em_poder'], Decimal('200'),
        )

        # ── 7. Novas vendas na rota ──────────────────────────────────────
        resposta = self._post('logistica:viagem-venda-create', {
            'tipo': VendaViagem.Tipo.VENDA,
            'cliente': self.cliente.pk,
            'produto': self.produto.pk,
            'quantidade': '180',
            'valor_unitario': '10',
        }, pk=viagem.pk)
        self.assertEqual(self._erros(resposta), [])

        # ── 8. Bonificação entregue na rua ───────────────────────────────
        resposta = self._post('logistica:viagem-venda-create', {
            'tipo': VendaViagem.Tipo.BONIFICACAO,
            'motivo': VendaViagem.Motivo.BRINDE,
            'cliente': self.cliente.pk,
            'produto': self.produto.pk,
            'quantidade': '10',
            'valor_unitario': '10',
        }, pk=viagem.pk)
        self.assertEqual(self._erros(resposta), [])

        quadro = EstoqueViagemService.quadro(viagem)
        self.assertEqual(quadro['venda_na_rua'], Decimal('180'))
        self.assertEqual(quadro['bonificacao_na_rua'], Decimal('10'))
        self.assertEqual(quadro['em_poder'], Decimal('10'))

        # ── 9. O retorno das sobras ──────────────────────────────────────
        resposta = self._post(
            'logistica:viagem-retorno', {'acao': 'tudo'}, pk=viagem.pk,
        )
        self.assertEqual(self._erros(resposta), [])
        self.assertEqual(
            EstoqueViagemService.quadro(viagem)['em_poder'], Decimal('0'),
        )

        # ── 10. A NF-e de retorno ────────────────────────────────────────
        resposta = self._post('logistica:viagem-emitir-retorno', pk=viagem.pk)
        self.assertEqual(self._erros(resposta), [])
        self.assertIsNotNone(RetornoVendaForaService.nota_da_viagem(viagem))

        # ── 11. A conciliação ────────────────────────────────────────────
        quadro = EstoqueViagemService.quadro(viagem)
        self.assertTrue(quadro['fecha'])
        self.assertEqual(quadro['carga_inicial'], Decimal('360'))
        self.assertEqual(quadro['destinos'], Decimal('360'))
        self.assertEqual(
            EstoqueViagemService.conciliacao(quadro)['cor'], 'verde',
        )

        # ── 12. O encerramento ───────────────────────────────────────────
        resposta = self._post('logistica:viagem-acerto', pk=viagem.pk)
        self.assertEqual(self._erros(resposta), [])
        viagem.refresh_from_db()
        self.assertEqual(viagem.status, Viagem.Status.FINALIZADA)

        # ── E a rastreabilidade, que é o que sobra depois ────────────────
        cadeia = RastreabilidadeService.cadeia(viagem, self.produto)
        self.assertEqual(cadeia['carga'], Decimal('360'))
        self.assertEqual(cadeia['vendas_realizadas'], Decimal('150'))
        self.assertEqual(cadeia['vendido'], Decimal('180'))
        self.assertEqual(cadeia['retornado'], Decimal('10'))
        # O FLUXO FECHA, MENOS PELO QUE DEPENDE DA SEFAZ. O manifesto foi
        # criado e consolidou a nota, mas so' conta como cumprido quando
        # autorizado -- e a transmissao e' o unico passo desta jornada que
        # o sistema ainda nao faz sozinho. Marcar a etapa de verde antes
        # disso seria dizer que a carga tem manifesto valido quando ela
        # ainda nao tem.
        etapas = {e['chave']: e for e in FluxoViagemService.etapas(viagem)}
        pendentes = [
            chave for chave, etapa in etapas.items()
            if etapa['estado'] not in ('concluida', 'dispensada')
        ]
        self.assertEqual(pendentes, ['mdfe'])

    def test_a_carga_das_tres_operacoes_nao_vira_um_documento_so(self):
        """
        O caminhão é um só; as operações, não. É a promessa da seção 29
        vista pelo resultado final: a viagem inteira produz documentos
        distintos, cada um com a sua natureza.
        """
        viagem = Viagem.objects.create(
            filial=self.filial, numero=99, motorista_nome='Seu Zé',
            veiculo_placa='ABC1D23', vendedor=self.usuario,
            responsavel=self.usuario,
        )
        for natureza, quantidade, cliente in (
            (self.natureza_venda, '150', self.cliente),
            (self.natureza_remessa, '200', None),
            (self.natureza_bonif, '10', self.cliente),
        ):
            ViagemService.adicionar_item(viagem, {
                'natureza': natureza, 'produto': self.produto,
                'cliente': cliente, 'quantidade': quantidade,
                'valor_unitario': '10',
            })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)

        remessa = RemessaVendaForaService.emitir(viagem, self.usuario)

        self.assertEqual(
            ItemCarga.objects.filter(
                viagem=viagem, documento_fiscal=remessa,
            ).count(),
            1,
        )
        self.assertEqual(
            ItemCarga.objects.filter(
                viagem=viagem, documento_fiscal__isnull=True,
            ).count(),
            2,
        )
