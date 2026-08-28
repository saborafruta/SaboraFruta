"""
A venda que originou a expedição — e o cliente que vem dela.

O QUE ESTA TELA REDIGITAVA

O pedido de expedição pedia o cliente de novo, com a venda ao lado sabendo
quem era. Cada redigitação é uma chance de expedir a mercadoria de alguém
para outra pessoa — e o pedido continua parecendo certo em todo relatório,
porque nada no sistema liga uma coisa à outra.

O QUE ESTES TESTES CERCAM:

  · O CLIENTE DA EXPEDIÇÃO É O CLIENTE DA VENDA. Não é preenchimento de
    tela: é regra do formulário, e vale para quem chega por outro caminho;

  · SEM VENDA, O CLIENTE VOLTA A SER OBRIGATÓRIO. Carga avulsa — amostra,
    reposição, brinde — existe e também precisa de destinatário;

  · PEDIDO CANCELADO NÃO É OFERECIDO: expedir o que foi cancelado é entregar
    mercadoria que ninguém mais comprou;

  · A TELA MOSTRA O CAMPO e traz as vendas da filial, sem vazar as de outra.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.logistica.forms import PedidoExpedicaoForm
from apps.logistica.models import PedidoExpedicao
from apps.vendas.models import PedidoVenda


class ExpedicaoDaVendaBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Expedição LTDA', nome_fantasia='Expedição',
            cnpj='31145678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='31145678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='expedicao@rota.local', nome='Expedição', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado da Esquina',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.outro_cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Padaria do Bairro',
            cpf_cnpj='98765432100', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.outro_cliente, filial=cls.filial)

    def setUp(self):
        self.client.force_login(self.usuario)

    # ── Fixtures ─────────────────────────────────────────────────────────

    def _venda(self, cliente=None, numero='1001', status=None):
        return PedidoVenda.objects.create(
            filial=self.filial,
            numero_pedido=numero,
            cliente=cliente or self.cliente,
            data_emissao=timezone.now(),
            status=status or PedidoVenda.Status.CONFIRMADO,
            usuario=self.usuario,
            valor_total=Decimal('500.00'),
        )

    def _dados(self, **extra):
        dados = {
            'numero': '1',
            'data_pedido': timezone.localdate().isoformat(),
            'status': PedidoExpedicao.Status.ABERTO,
            'prioridade': PedidoExpedicao.Prioridade.NORMAL,
        }
        dados.update(extra)
        return dados


class ClienteDaVendaTests(ExpedicaoDaVendaBase):
    """A regra: o cliente da expedição é o cliente da venda."""

    def test_escolher_a_venda_dispensa_escolher_o_cliente(self):
        venda = self._venda()

        form = PedidoExpedicaoForm(
            self._dados(pedido_venda=venda.pk), filial=self.filial,
        )

        self.assertTrue(form.is_valid(), form.errors)
        pedido = form.save(commit=False)
        pedido.filial = self.filial
        pedido.save()
        self.assertEqual(pedido.cliente, self.cliente)
        self.assertEqual(pedido.pedido_venda, venda)

    def test_a_venda_manda_no_cliente_mesmo_se_vier_outro_no_post(self):
        """
        Expedir para um cliente diferente do que comprou seria entregar a
        mercadoria de alguém a outra pessoa — e o pedido continuaria
        parecendo certo em todo relatório.
        """
        venda = self._venda()

        form = PedidoExpedicaoForm(
            self._dados(
                pedido_venda=venda.pk, cliente=self.outro_cliente.pk,
            ),
            filial=self.filial,
        )

        self.assertTrue(form.is_valid(), form.errors)
        pedido = form.save(commit=False)
        pedido.filial = self.filial
        pedido.save()
        self.assertEqual(pedido.cliente, self.cliente)

    def test_sem_venda_o_cliente_continua_obrigatorio(self):
        """Carga avulsa também precisa de destinatário."""
        form = PedidoExpedicaoForm(self._dados(), filial=self.filial)

        self.assertFalse(form.is_valid())
        self.assertIn('cliente', form.errors)
        self.assertIn('pedido de venda', form.errors['cliente'][0])

    def test_carga_avulsa_sai_sem_venda(self):
        """Amostra, reposição e brinde não nascem de pedido nenhum."""
        form = PedidoExpedicaoForm(
            self._dados(cliente=self.cliente.pk), filial=self.filial,
        )

        self.assertTrue(form.is_valid(), form.errors)
        pedido = form.save(commit=False)
        pedido.filial = self.filial
        pedido.save()
        self.assertIsNone(pedido.pedido_venda)
        self.assertEqual(pedido.cliente, self.cliente)

    def test_pedido_cancelado_nao_e_oferecido(self):
        cancelado = self._venda(numero='2002', status=PedidoVenda.Status.CANCELADO)

        form = PedidoExpedicaoForm(filial=self.filial)

        self.assertNotIn(
            cancelado, form.fields['pedido_venda'].queryset,
        )

    def test_venda_de_outra_filial_nao_entra(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Filial 2',
            cnpj='31145678000353', uf='RN', cidade='Mossoró',
        )
        alheia = PedidoVenda.objects.create(
            filial=outra, numero_pedido='3003', cliente=self.cliente,
            data_emissao=timezone.now(), status=PedidoVenda.Status.CONFIRMADO,
            usuario=self.usuario,
        )

        form = PedidoExpedicaoForm(filial=self.filial)

        self.assertNotIn(alheia, form.fields['pedido_venda'].queryset)


class TelaTests(ExpedicaoDaVendaBase):
    """O campo aparece e traz as vendas da filial."""

    def test_a_tela_de_novo_pedido_mostra_o_campo_da_venda(self):
        venda = self._venda()

        html = self.client.get(
            reverse('logistica:pedido-expedicao-create'),
        ).content.decode()

        self.assertIn('Pedido de venda', html)
        self.assertIn('Buscar pedido ou cliente', html)
        self.assertIn(venda.numero_pedido, html)

    def test_a_tela_diz_de_onde_vem_o_cliente(self):
        html = self.client.get(
            reverse('logistica:pedido-expedicao-create'),
        ).content.decode()

        self.assertIn('Venda e cliente', html)
        self.assertIn('o cliente vem dele', html)

    def test_criar_pela_tela_com_a_venda_escolhida(self):
        venda = self._venda()

        self.client.post(
            reverse('logistica:pedido-expedicao-create'),
            self._dados(pedido_venda=venda.pk),
            follow=True,
        )

        pedido = PedidoExpedicao.objects.get(filial=self.filial)
        self.assertEqual(pedido.pedido_venda, venda)
        self.assertEqual(pedido.cliente, self.cliente)
