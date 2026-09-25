"""
Mudança de status do delivery — Kanban e o cadastro rápido da tela de
rastreio (`apps.mapas`) compartilham a mesma conta (`VendaPDV.
mudar_status_delivery`), pra "o motorista marcou entregue no celular" e
"alguém arrastou o card" nunca divergirem em como o campo é atualizado.
"""
import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.mapas.services.roteirizacao import Rota
from apps.pdv.models import VendaPDV


class DeliveryKanbanBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Delivery Kanban LTDA', nome_fantasia='Delivery',
            cnpj='93345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='93345678000272',
            uf='RN', is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='delivery@teste.local', nome='Fulano', password='x' * 12,
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Cliente Delivery', cpf_cnpj='12345678901',
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def _venda(self, numero=1, status_delivery=VendaPDV.StatusDelivery.NOVO):
        return VendaPDV.objects.create(
            filial=self.filial, numero_venda=numero, cliente=self.cliente,
            usuario=self.usuario, status='finalizada', delivery=True,
            status_delivery=status_delivery, data_venda=timezone.now(),
        )


class MudarStatusDeliveryModelTests(DeliveryKanbanBase):

    def test_muda_o_status(self):
        venda = self._venda()
        venda.mudar_status_delivery(VendaPDV.StatusDelivery.EM_ENTREGA)
        venda.refresh_from_db()
        self.assertEqual(venda.status_delivery, VendaPDV.StatusDelivery.EM_ENTREGA)

    def test_finalizado_carimba_o_encerramento(self):
        venda = self._venda()
        venda.mudar_status_delivery(VendaPDV.StatusDelivery.FINALIZADO)
        venda.refresh_from_db()
        self.assertIsNotNone(venda.delivery_encerrado_em)

    def test_voltar_para_etapa_ativa_zera_o_encerramento(self):
        venda = self._venda()
        venda.mudar_status_delivery(VendaPDV.StatusDelivery.CANCELADO)
        venda.mudar_status_delivery(VendaPDV.StatusDelivery.EM_ENTREGA)
        venda.refresh_from_db()
        self.assertIsNone(venda.delivery_encerrado_em)

    def test_entregador_e_observacao_sao_opcionais(self):
        venda = self._venda()
        venda.mudar_status_delivery(VendaPDV.StatusDelivery.EM_ENTREGA, entregador='Lenard')
        venda.refresh_from_db()
        self.assertEqual(venda.entregador, 'Lenard')
        self.assertEqual(venda.observacao_delivery, '')


class DeliveryMoverViewTests(DeliveryKanbanBase):

    def test_move_o_pedido_no_kanban(self):
        venda = self._venda()
        resp = self.client.post(
            reverse('pdv:delivery_mover', args=[venda.pk]),
            data='{"status": "em_entrega"}', content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        venda.refresh_from_db()
        self.assertEqual(venda.status_delivery, 'em_entrega')

    def test_status_invalido_e_rejeitado(self):
        venda = self._venda()
        resp = self.client.post(
            reverse('pdv:delivery_mover', args=[venda.pk]),
            data='{"status": "nao_existe"}', content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_pedido_de_outra_filial_nao_e_encontrado(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Outra', cnpj='93345678000353', uf='RN',
        )
        venda = VendaPDV.objects.create(
            filial=outra, numero_venda=1, cliente=self.cliente,
            usuario=self.usuario, status='finalizada', delivery=True,
            data_venda=timezone.now(),
        )
        resp = self.client.post(
            reverse('pdv:delivery_mover', args=[venda.pk]),
            data='{"status": "em_entrega"}', content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)


class DeliveryRotasViewTests(DeliveryKanbanBase):

    def setUp(self):
        super().setUp()
        self.filial.latitude = -5.7900
        self.filial.longitude = -35.2100
        self.filial.save(update_fields=['latitude', 'longitude'])
        self.cliente.latitude = -5.8000
        self.cliente.longitude = -35.2200
        self.cliente.save(update_fields=['latitude', 'longitude'])

    def test_tela_exibe_pedidos_ativos_e_botao_existe_no_kanban(self):
        ativo = self._venda(numero=101, status_delivery='preparando')
        self._venda(numero=102, status_delivery='entregue')

        tela = self.client.get(reverse('pdv:delivery_rotas'))
        kanban = self.client.get(reverse('pdv:delivery'))

        self.assertEqual(tela.status_code, 200)
        self.assertEqual(tela.headers['X-Frame-Options'], 'SAMEORIGIN')
        self.assertContains(tela, 'Rota do Delivery')
        pedidos = json.loads(tela.context['pedidos_json'])
        self.assertIn(ativo.pk, [p['id'] for p in pedidos])
        self.assertNotIn(102, [p['numero'] for p in pedidos])
        self.assertContains(kanban, 'Rota do Delivery')
        self.assertContains(kanban, '?embed=1')

    @patch('apps.mapas.services.roteirizacao.OSRMRoteirizador.rota')
    def test_calculo_preserva_ordem_e_inclui_retorno(self, mock_rota):
        primeiro = self._venda(numero=201)
        segundo_cliente = Cliente.objects.create(
            filial=self.filial, razao_social='Segundo Cliente', cpf_cnpj='98765432100',
            latitude=-5.8100, longitude=-35.2300,
        )
        segundo = VendaPDV.objects.create(
            filial=self.filial, numero_venda=202, cliente=segundo_cliente,
            usuario=self.usuario, status='finalizada', delivery=True,
            status_delivery='novo', data_venda=timezone.now(),
        )
        mock_rota.return_value = Rota(
            distancia_m=12000, duracao_s=1800,
            geometria=[[-5.79, -35.21], [-5.81, -35.23], [-5.8, -35.22], [-5.79, -35.21]],
        )

        resp = self.client.post(
            reverse('pdv:delivery_rota_calcular'),
            data=json.dumps({'pedidos': [segundo.pk, primeiro.pk], 'minutos_parada': 5}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        dados = resp.json()
        self.assertEqual(dados['ordem'], [segundo.pk, primeiro.pk])
        self.assertEqual(dados['distancia_km'], 12.0)
        self.assertEqual(dados['tempo_total_s'], 2400)
        self.assertEqual(len(dados['paradas']), 2)
        pontos = mock_rota.call_args.args[0]
        self.assertEqual(pontos[0], pontos[-1])

    def test_pedido_sem_coordenada_e_rejeitado(self):
        self.cliente.latitude = None
        self.cliente.longitude = None
        self.cliente.save(update_fields=['latitude', 'longitude'])
        venda = self._venda(numero=301)

        resp = self.client.post(
            reverse('pdv:delivery_rota_calcular'),
            data=json.dumps({'pedidos': [venda.pk]}), content_type='application/json',
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn('sem coordenada', resp.json()['erro'].lower())
