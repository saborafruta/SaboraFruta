"""
Mudança de status do delivery — Kanban e o cadastro rápido da tela de
rastreio (`apps.mapas`) compartilham a mesma conta (`VendaPDV.
mudar_status_delivery`), pra "o motorista marcou entregue no celular" e
"alguém arrastou o card" nunca divergirem em como o campo é atualizado.
"""
import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.mapas.services.roteirizacao import Rota
from apps.mapas.services.geocoder import Resultado
from apps.pdv.models import RotaDeliveryPublica, VendaPDV


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

    def test_venda_salva_como_pendente_nao_aparece_como_paga(self):
        venda = self._venda(numero=10)
        venda.status = 'aberta'
        venda.save(update_fields=['status'])

        resp = self.client.get(reverse('pdv:delivery'))

        self.assertEqual(resp.status_code, 200)
        pedido = next(
            pedido
            for coluna in resp.context['colunas']
            for pedido in coluna['pedidos']
            if pedido.pk == venda.pk
        )
        self.assertFalse(pedido.pago)
        self.assertTrue(pedido.pagamento_pendente)
        self.assertContains(resp, 'Pagamento pendente')

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
        em_entrega = self._venda(numero=103, status_delivery='em_entrega')
        self._venda(numero=102, status_delivery='entregue')

        tela = self.client.get(reverse('pdv:delivery_rotas'))
        kanban = self.client.get(reverse('pdv:delivery'))

        self.assertEqual(tela.status_code, 200)
        self.assertEqual(tela.headers['X-Frame-Options'], 'SAMEORIGIN')
        self.assertContains(tela, 'Rota do Delivery')
        self.assertContains(tela, 'Melhor rota')
        self.assertContains(tela, 'Reotimizar livres')
        self.assertContains(tela, 'Google Maps')
        self.assertContains(tela, 'Expandir mapa')
        self.assertContains(tela, 'requestFullscreen')
        self.assertContains(tela, 'Hora prevista de saída')
        self.assertContains(tela, 'Combustível necessário')
        self.assertContains(tela, 'Custo estimado da rota')
        self.assertContains(tela, 'fuelAutonomy')
        self.assertNotContains(tela, 'Waze')
        pedidos = json.loads(tela.context['pedidos_json'])
        self.assertIn(ativo.pk, [p['id'] for p in pedidos])
        self.assertNotIn(em_entrega.pk, [p['id'] for p in pedidos])
        self.assertNotIn(102, [p['numero'] for p in pedidos])
        self.assertContains(kanban, 'Rota do Delivery')
        self.assertContains(kanban, "abrirListaDelivery('preparando', 'Em Preparo')")
        self.assertContains(kanban, 'Ver Em Preparo em lista')
        self.assertContains(kanban, 'deliveryListModal')
        self.assertContains(kanban, 'Pesquisar cliente, pedido, endereço ou status')
        self.assertContains(kanban, 'kanbanSearch')
        self.assertContains(kanban, 'Pesquisar cards por cliente, pedido, endereço, status ou entregador')
        self.assertContains(kanban, '<th>Pedido</th>', html=True)
        self.assertContains(kanban, '<th>Cliente</th>', html=True)
        self.assertContains(kanban, '<th>Endereço</th>', html=True)
        self.assertContains(kanban, '<th>Status</th>', html=True)
        self.assertContains(kanban, '<th>Pagamento</th>', html=True)
        self.assertContains(kanban, 'deliveryListBody')
        self.assertContains(kanban, 'data-columns="off"', html=False)
        self.assertContains(kanban, '?embed=1')
        dados_kanban = json.loads(kanban.context['pedidos_json'])
        self.assertEqual(dados_kanban[str(ativo.pk)]['status_delivery'], 'preparando')
        self.assertEqual(dados_kanban[str(ativo.pk)]['status_label'], 'Em Preparo')

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

    @patch('apps.mapas.services.roteirizacao.OSRMRoteirizador.rota')
    def test_calculo_usa_hora_prevista_informada(self, mock_rota):
        venda = self._venda(numero=204)
        mock_rota.return_value = Rota(
            distancia_m=10000, duracao_s=3600,
            geometria=[[-5.79, -35.21], [-5.80, -35.22], [-5.79, -35.21]],
        )

        resp = self.client.post(
            reverse('pdv:delivery_rota_calcular'),
            data=json.dumps({
                'pedidos': [venda.pk],
                'minutos_parada': 5,
                'saida_prevista': '08:30',
            }),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['saida'], '08:30')
        self.assertEqual(resp.json()['retorno'], '09:35')

    @patch('apps.mapas.services.roteirizacao.OSRMRoteirizador.matriz_distancias')
    @patch('apps.mapas.services.roteirizacao.OSRMRoteirizador.rota')
    def test_gerar_rota_otimiza_distancia_pelas_ruas(self, mock_rota, mock_matriz):
        proximo = self._venda(numero=211)
        cliente_distante = Cliente.objects.create(
            filial=self.filial, razao_social='Cliente Distante', cpf_cnpj='98765432101',
            latitude=-5.9000, longitude=-35.3100,
        )
        distante = VendaPDV.objects.create(
            filial=self.filial, numero_venda=212, cliente=cliente_distante,
            usuario=self.usuario, status='finalizada', delivery=True,
            status_delivery='novo', data_venda=timezone.now(),
        )
        cliente_intermediario = Cliente.objects.create(
            filial=self.filial, razao_social='Cliente Intermediario', cpf_cnpj='98765432102',
            latitude=-5.8100, longitude=-35.2300,
        )
        intermediario = VendaPDV.objects.create(
            filial=self.filial, numero_venda=213, cliente=cliente_intermediario,
            usuario=self.usuario, status='finalizada', delivery=True,
            status_delivery='novo', data_venda=timezone.now(),
        )
        mock_rota.return_value = Rota(
            distancia_m=30000, duracao_s=3600,
            geometria=[[-5.79, -35.21], [-5.90, -35.31], [-5.79, -35.21]],
        )
        # Índices: 0 filial, 1 próximo, 2 distante, 3 intermediário.
        # A sequência 1 -> 3 -> 2 -> 0 é menor pela malha viária.
        mock_matriz.return_value = [
            [0, 1, 9, 2],
            [1, 0, 10, 1],
            [9, 10, 0, 1],
            [2, 1, 1, 0],
        ]

        resp = self.client.post(
            reverse('pdv:delivery_rota_calcular'),
            data=json.dumps({
                'pedidos': [proximo.pk, distante.pk, intermediario.pk],
                'otimizar': True,
            }),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['ordem'], [proximo.pk, intermediario.pk, distante.pk])

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

    def test_endereco_pode_ser_confirmado_mesmo_com_coordenada(self):
        self.cliente.endereco = 'Rua Confirmada'
        self.cliente.numero = '25'
        self.cliente.bairro = 'Centro'
        self.cliente.cidade = 'Natal'
        self.cliente.uf = 'RN'
        self.cliente.save(update_fields=[
            'endereco', 'numero', 'bairro', 'cidade', 'uf',
        ])
        venda = self._venda(numero=302)

        tela = self.client.get(reverse('pdv:delivery_rotas'))
        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59000-000',
                'rua': 'Rua Confirmada',
                'numero': '25',
                'complemento': 'Sala 2',
                'bairro': 'Centro',
                'cidade': 'Natal',
                'uf': 'rn',
            }),
            content_type='application/json',
        )

        self.cliente.refresh_from_db()
        venda.refresh_from_db()
        self.assertContains(tela, 'Editar ou confirmar endereço')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['tem_coordenada'])
        self.assertEqual(self.cliente.endereco, 'Rua Confirmada')
        self.assertEqual(self.cliente.uf, 'RN')
        self.assertEqual(venda.endereco_entrega['complemento'], 'Sala 2')

    def test_endereco_da_rota_exige_dados_para_localizacao(self):
        venda = self._venda(numero=303)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({'rua': 'Rua sem cidade'}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn('cidade', resp.json()['erro'])

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_parada_manual_exige_observacao_e_geocodifica_sem_criar_cliente(self, resolver):
        resolver.return_value = Resultado(-5.82, -35.24, 'exata')
        quantidade_clientes = Cliente.objects.count()

        resp = self.client.post(
            reverse('pdv:delivery_rota_localizar_parada_manual'),
            data=json.dumps({
                'observacao': 'Buscar caixas térmicas', 'rua': 'Rua Manual',
                'numero': '50', 'bairro': 'Centro', 'cidade': 'Natal', 'uf': 'rn',
            }), content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['observacao'], 'Buscar caixas térmicas')
        self.assertEqual(resp.json()['endereco']['uf'], 'RN')
        self.assertEqual(Cliente.objects.count(), quantidade_clientes)

    @patch('apps.mapas.services.roteirizacao.OSRMRoteirizador.rota')
    def test_calculo_aceita_parada_manual_misturada_com_pedido(self, mock_rota):
        venda = self._venda(numero=304)
        mock_rota.return_value = Rota(
            distancia_m=15000, duracao_s=2400,
            geometria=[[-5.79, -35.21], [-5.80, -35.22], [-5.81, -35.23], [-5.79, -35.21]],
        )
        manual = {
            'tipo': 'manual', 'id': 'buscar-caixas',
            'observacao': 'Buscar caixas térmicas',
            'endereco': {'rua': 'Rua das Caixas', 'numero': '10', 'bairro': 'Centro', 'cidade': 'Natal', 'uf': 'RN'},
            'endereco_texto': 'Rua das Caixas, 10, Centro, Natal, RN',
            'lat': -5.81, 'lng': -35.23,
        }

        resp = self.client.post(
            reverse('pdv:delivery_rota_calcular'),
            data=json.dumps({'paradas': [
                {'tipo': 'pedido', 'id': venda.pk}, manual,
            ]}), content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['ordem_paradas'], [f'pedido:{venda.pk}', 'manual:buscar-caixas'])
        self.assertEqual(resp.json()['paradas'][1]['observacao'], 'Buscar caixas térmicas')
        self.assertEqual(resp.json()['tempo_total_s'], 3000)


class DeliveryMotoristaPublicoTests(DeliveryKanbanBase):

    def setUp(self):
        super().setUp()
        self.filial.latitude = -5.7900
        self.filial.longitude = -35.2100
        self.filial.endereco = 'Rua da Matriz'
        self.filial.numero = '100'
        self.filial.bairro = 'Centro'
        self.filial.cidade = 'Natal'
        self.filial.geo_fixado = True
        self.filial.save(update_fields=[
            'latitude', 'longitude', 'endereco', 'numero', 'bairro', 'cidade', 'geo_fixado',
        ])
        self.cliente.latitude = -5.8000
        self.cliente.longitude = -35.2200
        self.cliente.celular = '(84) 99999-1234'
        self.cliente.endereco = 'Rua do Cliente'
        self.cliente.numero = '200'
        self.cliente.bairro = 'Ponta Negra'
        self.cliente.cidade = 'Natal'
        self.cliente.uf = 'RN'
        self.cliente.geo_fixado = True
        self.cliente.save(update_fields=[
            'latitude', 'longitude', 'celular', 'endereco', 'numero', 'bairro', 'cidade', 'uf',
            'geo_fixado',
        ])

    def _publicar(self, pedidos, entregador='João', etas=None):
        payload = {'pedidos': [p.pk for p in pedidos], 'entregador': entregador}
        if etas is not None:
            payload['etas'] = etas
        return self.client.post(
            reverse('pdv:delivery_rota_publicar'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_link_e_curto_fixo_e_atualiza_o_conteudo(self):
        primeiro = self._venda(numero=401)
        segundo = self._venda(numero=402)

        primeira_publicacao = self._publicar([primeiro]).json()
        segunda_publicacao = self._publicar([segundo]).json()

        self.assertEqual(primeira_publicacao['url'], segunda_publicacao['url'])
        rota = RotaDeliveryPublica.objects.get(filial=self.filial)
        self.assertEqual(len(rota.token), 22)
        self.assertEqual(rota.pedido_ids, [segundo.pk])
        self.assertEqual(rota.entregador, 'João')

    def test_painel_publico_exibe_contato_observacao_pagamento_e_pedido(self):
        venda = self._venda(numero=410)
        venda.observacao_delivery = 'Entregar na recepção lateral.'
        venda.valor_total = 89.90
        venda.save(update_fields=['observacao_delivery', 'valor_total'])
        url = self._publicar([venda]).json()['url']
        self.client.logout()

        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '#410')
        self.assertContains(resp, 'Cliente Delivery')
        self.assertContains(resp, 'Entregar na recepção lateral.')
        self.assertContains(resp, 'class="card-note"', html=False)
        self.assertContains(resp, '(84) 99999-1234')
        self.assertContains(resp, 'WhatsApp do cliente')
        self.assertContains(resp, 'PAGO')
        self.assertContains(resp, 'Marcar parada 1 como entregue')
        self.assertContains(resp, 'Ver mais')
        self.assertEqual(resp.headers['Cache-Control'], 'private, no-store')

    def test_painel_publico_exibe_eta_publicada_em_cada_entrega(self):
        venda = self._venda(numero=412)
        url = self._publicar(
            [venda], etas={str(venda.pk): '14:35'},
        ).json()['url']
        rota = RotaDeliveryPublica.objects.get(filial=self.filial)
        self.client.logout()

        resp = self.client.get(url)

        self.assertEqual(rota.pedido_etas, {str(venda.pk): '14:35'})
        self.assertContains(resp, 'Chegada 14:35')

    def test_painel_publico_nao_marca_venda_pendente_como_paga(self):
        venda = self._venda(numero=411)
        venda.status = 'aberta'
        venda.save(update_fields=['status'])
        url = self._publicar([venda]).json()['url']
        self.client.logout()

        resp = self.client.get(url)

        self.assertContains(resp, 'Venda com pagamento pendente.')
        self.assertContains(resp, 'RECEBER NA ENTREGA')
        self.assertNotContains(resp, '<span class="tag paid">PAGO</span>', html=True)

    def test_motoboy_conclui_somente_pedido_da_rota(self):
        permitido = self._venda(numero=420)
        fora_da_rota = self._venda(numero=421)
        self._publicar([permitido])
        rota = RotaDeliveryPublica.objects.get(filial=self.filial)
        self.client.logout()

        concluido = self.client.post(
            reverse('delivery_publico:concluir', args=[rota.token, permitido.pk]),
        )
        negado = self.client.post(
            reverse('delivery_publico:concluir', args=[rota.token, fora_da_rota.pk]),
        )

        permitido.refresh_from_db()
        fora_da_rota.refresh_from_db()
        self.assertEqual(concluido.status_code, 302)
        self.assertEqual(permitido.status_delivery, VendaPDV.StatusDelivery.ENTREGUE)
        self.assertEqual(negado.status_code, 404)
        self.assertEqual(fora_da_rota.status_delivery, VendaPDV.StatusDelivery.NOVO)

    def test_motoboy_conclui_pedido_sem_recarregar_a_pagina(self):
        venda = self._venda(numero=422)
        self._publicar([venda])
        rota = RotaDeliveryPublica.objects.get(filial=self.filial)
        self.client.logout()

        resp = self.client.post(
            reverse('delivery_publico:concluir', args=[rota.token, venda.pk]),
            HTTP_ACCEPT='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], VendaPDV.StatusDelivery.ENTREGUE)
        self.assertEqual(venda.status_delivery, VendaPDV.StatusDelivery.ENTREGUE)

    def test_motoboy_pode_desmarcar_e_pedido_volta_para_status_anterior(self):
        venda = self._venda(numero=423, status_delivery='preparando')
        self._publicar([venda])
        rota = RotaDeliveryPublica.objects.get(filial=self.filial)
        self.client.logout()
        url = reverse('delivery_publico:concluir', args=[rota.token, venda.pk])

        self.client.post(
            url, data=json.dumps({'entregue': True}),
            content_type='application/json', HTTP_ACCEPT='application/json',
        )
        rota.refresh_from_db()
        self.assertEqual(
            rota.pedido_status_anteriores,
            {str(venda.pk): VendaPDV.StatusDelivery.PREPARANDO},
        )
        resp = self.client.post(
            url, data=json.dumps({'entregue': False}),
            content_type='application/json', HTTP_ACCEPT='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['concluido'])
        self.assertEqual(resp.json()['status_label'], 'Em Preparo')
        self.assertEqual(venda.status_delivery, VendaPDV.StatusDelivery.PREPARANDO)
        rota.refresh_from_db()
        self.assertEqual(rota.pedido_status_anteriores, {})

    def test_painel_joga_concluidos_para_o_final_sem_trocar_numero_da_rota(self):
        concluida = self._venda(numero=424)
        pendente = self._venda(numero=425)
        concluida.mudar_status_delivery(VendaPDV.StatusDelivery.ENTREGUE)
        url = self._publicar([concluida, pendente]).json()['url']
        self.client.logout()

        resp = self.client.get(url)

        self.assertEqual(
            [item['venda'].pk for item in resp.context['pedidos']],
            [pendente.pk, concluida.pk],
        )
        self.assertEqual(
            [item['ordem'] for item in resp.context['pedidos']],
            [2, 1],
        )

    def test_painel_oferece_somente_google_em_rota_unica(self):
        pedidos = [self._venda(numero=440 + indice) for indice in range(7)]
        url = self._publicar(pedidos).json()['url']
        self.client.logout()

        resp = self.client.get(url)

        self.assertContains(resp, 'Abrir no Google Maps')
        self.assertNotContains(resp, 'OsmAnd')
        self.assertNotContains(resp, 'GPX')
        self.assertContains(resp, 'waypoints=', html=False)
        parametros = parse_qs(urlparse(resp.context['google_maps_completa']).query)
        self.assertNotIn('dir_action', parametros)
        self.assertIn('Rua do Cliente', parametros['waypoints'][0])

    def test_painel_publico_exibe_e_conclui_parada_manual(self):
        venda = self._venda(numero=460)
        publicacao = self.client.post(
            reverse('pdv:delivery_rota_publicar'),
            data=json.dumps({
                'pedidos': [venda.pk],
                'paradas_extras': [{
                    'tipo': 'manual', 'id': 'buscar-documentos',
                    'observacao': 'Buscar documentos assinados',
                    'endereco': {'rua': 'Rua Manual', 'numero': '50', 'bairro': 'Centro', 'cidade': 'Natal', 'uf': 'RN'},
                    'endereco_texto': 'Rua Manual, 50, Centro, Natal, RN',
                    'lat': -5.82, 'lng': -35.24,
                }],
                'ordem_paradas': ['manual:buscar-documentos', f'pedido:{venda.pk}'],
                'etas': {'manual:buscar-documentos': '14:10', str(venda.pk): '14:30'},
            }), content_type='application/json',
        )
        rota = RotaDeliveryPublica.objects.get(filial=self.filial)
        self.client.logout()

        tela = self.client.get(publicacao.json()['url'])
        concluida = self.client.post(
            reverse('delivery_publico:concluir_parada_extra', args=[rota.token, 'buscar-documentos']),
            data=json.dumps({'entregue': True}), content_type='application/json',
            HTTP_ACCEPT='application/json',
        )

        self.assertContains(tela, 'Buscar documentos assinados')
        self.assertContains(tela, 'PARADA MANUAL')
        self.assertContains(tela, 'Chegada 14:10')
        self.assertEqual(concluida.status_code, 200)
        rota.refresh_from_db()
        self.assertEqual(rota.paradas_extras_concluidas, ['buscar-documentos'])
