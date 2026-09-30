"""
Mudança de status do delivery — Kanban e o cadastro rápido da tela de
rastreio (`apps.mapas`) compartilham a mesma conta (`VendaPDV.
mudar_status_delivery`), pra "o motorista marcou entregue no celular" e
"alguém arrastou o card" nunca divergirem em como o campo é atualizado.
"""
import datetime
import json
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.crm.models import RecompraCliente
from apps.mapas.services.roteirizacao import Rota
from apps.mapas.services.geocoder import Resultado
from apps.pdv.models import ItemVendaPDV, RotaDelivery, RotaDeliveryPublica, VendaPDV
from apps.pdv.views.pdv import _delivery_agrupar_paradas, _delivery_rota_endereco_hash
from apps.pdv.views.delivery_publico import _urls_google_maps_em_trechos
from apps.produtos.models import Produto, UnidadeMedida


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
        Cliente.objects.filter(pk=self.cliente.pk).update(
            cep='59158155', endereco='Avenida Antártida', numero='25',
            bairro='Parque das Nações', cidade='Parnamirim', uf='RN',
            latitude=-5.8000, longitude=-35.2200, geo_precisao='exata',
            geo_fixado=True,
        )
        self.cliente.refresh_from_db()
        Cliente.objects.filter(pk=self.cliente.pk).update(
            geo_endereco_hash=self.cliente.hash_endereco_atual(),
        )
        self.cliente.refresh_from_db()

    def test_tela_exibe_pedidos_ativos_e_botao_existe_no_kanban(self):
        ativo = self._venda(numero=101, status_delivery='preparando')
        em_entrega = self._venda(numero=103, status_delivery='em_entrega')
        self._venda(numero=102, status_delivery='entregue')

        tela = self.client.get(reverse('pdv:delivery_rotas'))
        kanban = self.client.get(reverse('pdv:delivery'))

        self.assertEqual(tela.status_code, 200)
        self.assertEqual(tela.headers['X-Frame-Options'], 'SAMEORIGIN')
        self.assertContains(tela, 'Rota do Delivery')
        self.assertContains(tela, 'Otimizar rota inteira')
        self.assertContains(tela, 'Otimizar não travados')
        self.assertContains(tela, 'Paradas movidas manualmente são travadas automaticamente')
        self.assertContains(tela, 'route-accent')
        self.assertContains(tela, '--active-route-color')
        self.assertContains(tela, 'Você está editando')
        self.assertContains(tela, 'EM EDIÇÃO')
        self.assertContains(tela, 'Pedidos, sequência, mapa e link abaixo pertencem a esta rota')
        self.assertContains(tela, 'Google Maps')
        self.assertContains(tela, 'Google Maps · moto')
        self.assertContains(tela, 'googleRouteSections')
        self.assertContains(tela, 'O limite de pontos do Google Maps foi atingido')
        self.assertContains(tela, 'Trecho ${section.number} de ${section.total}')
        self.assertContains(tela, 'Expandir mapa')
        self.assertContains(tela, 'requestFullscreen')
        self.assertContains(tela, 'Todos os pinos podem ser arrastados')
        self.assertContains(tela, 'manualCoordinateUrl')
        self.assertContains(tela, 'draggable:true')
        self.assertContains(tela, 'saveDraggedBranch')
        self.assertContains(tela, 'saveDraggedStop')
        self.assertContains(tela, 'function addBranchMarker')
        self.assertContains(tela, "'⌂','base',`Saída · ${branch.nome}`")
        self.assertContains(tela, "baseMarker.setZIndexOffset(800)")
        self.assertContains(tela, "sobrepostos[`${Number(branch.lat).toFixed(4)}:${Number(branch.lng).toFixed(4)}`]=1")
        self.assertContains(tela, "content:'SAÍDA'")
        self.assertContains(tela, 'width:26px; height:26px')
        self.assertContains(tela, 'function locationPopupHtml')
        self.assertContains(tela, 'dr-location-popup-list')
        self.assertContains(tela, '${count} pedidos')
        self.assertContains(tela, 'id="selectAllOrders"')
        self.assertContains(tela, 'id="deselectAllOrders"')
        self.assertContains(tela, 'Marcar todos e otimizar')
        self.assertContains(tela, 'refreshOrderLocation(order,true,true)')
        self.assertContains(tela, 'Marcar e tentar localizar este endereço')
        self.assertContains(tela, 'Localizar ao marcar')
        self.assertContains(tela, 'refreshOrderLocation(order,true)')
        self.assertContains(tela, 'openAddressModal(id)')
        self.assertNotContains(tela, "${o.tem_coordenada?'':'disabled'}")
        self.assertContains(tela, 'state.locked.clear()')
        self.assertContains(tela, 'scheduleAutomaticCalculation(true)')
        self.assertContains(tela, 'Todos foram marcados. Otimizando os demais')
        self.assertContains(tela, 'order.localizacao_revalidacao_pendente=true')
        self.assertContains(tela, 'state.selected.some(routeKeyCanCalculate)')
        self.assertContains(tela, 'calculableKeys.map(stopPayload)')
        self.assertContains(tela, 'Rota otimizada com os pedidos localizáveis')
        self.assertContains(tela, 'continua marcado e entrará após corrigir o endereço')
        self.assertContains(tela, 'Fora do cálculo · corrija o endereço')
        self.assertContains(tela, 'Pendente · fora da rota')
        self.assertContains(tela, "pendingGroup?'false':'true'")
        self.assertContains(tela, 'routedGroups.indexOf(group)')
        self.assertContains(tela, "tone==='warning'?' warning'")
        self.assertContains(tela, 'antes de publicar a rota')
        self.assertNotContains(tela, 'id="mapLocationDetail"')
        self.assertContains(tela, 'new ResizeObserver')
        self.assertContains(tela, 'calculationSequence+=1')
        self.assertContains(tela, 'else {resetRouteMap();drawPendingMarkers();resetSummary();}')
        self.assertContains(tela, 'function resetRouteMap()')
        self.assertContains(tela, 'hiddenAtBranch')
        self.assertContains(tela, 'bucket===branchBucket')
        self.assertContains(tela, 'não foi exibido sobre a saída')
        self.assertContains(tela, 'dr-map-legend-line return')
        self.assertContains(tela, 'returnAccentLine')
        self.assertContains(tela, "dashArray:'8 10'")
        self.assertContains(tela, 'Hora prevista de saída')
        self.assertContains(tela, 'Combustível necessário')
        self.assertContains(tela, 'Custo estimado da rota')
        self.assertContains(tela, 'fuelAutonomy')
        self.assertContains(tela, 'opportunitySegment')
        self.assertContains(tela, 'Entenda os filtros RFM')
        self.assertContains(tela, 'Configurar RFM')
        self.assertContains(tela, 'rfmConfigModal')
        self.assertContains(tela, 'rfmFAutomatic')
        self.assertContains(tela, 'rfmMAutomatic')
        self.assertContains(tela, 'Ajuda para configurar RFM')
        self.assertContains(tela, 'Como preencher estas faixas?')
        self.assertContains(tela, 'Automático:')
        self.assertContains(tela, 'Manual:')
        self.assertContains(tela, 'manualCepLookup')
        self.assertContains(tela, reverse('cadastros:consultar-cep'))
        self.assertContains(tela, 'dr-opportunity-toolbar-row')
        self.assertContains(tela, 'R5 F5 M5:')
        self.assertContains(tela, 'Para recuperar clientes:')
        self.assertContains(tela, 'Por que foi sugerido?')
        self.assertContains(tela, 'Combustível adicional estimado')
        self.assertContains(tela, 'ponto mais próximo da rota')
        self.assertContains(tela, 'Combina potencial de recompra, RFM e proximidade da rota')
        self.assertContains(tela, 'opportunityDetailModal')
        self.assertContains(tela, 'Sugestão pelo histórico de compra')
        self.assertContains(tela, 'Adicionar como parada')
        self.assertContains(tela, 'Fazer venda')
        self.assertContains(tela, 'stopObservationModal')
        self.assertContains(tela, 'routePdvModal')
        self.assertContains(tela, 'data-edit-manual')
        self.assertContains(tela, 'data-reset-location')
        self.assertContains(tela, 'function resetOrderLocation')
        self.assertContains(tela, 'Local automático')
        self.assertContains(tela, 'Localizar e atualizar')
        self.assertContains(tela, 'delivery-route-sale-completed')
        self.assertContains(tela, 'Buscando a melhor posição na rota')
        self.assertContains(tela, 'routeTabs')
        self.assertContains(tela, '＋ Nova rota')
        self.assertContains(tela, 'Copiar link desta rota')
        self.assertContains(tela, 'data-route-link')
        self.assertContains(tela, 'Link individual de')
        self.assertContains(tela, 'Ver comprovante da venda')
        self.assertContains(tela, reverse('pdv:comprovante_venda', args=[0]))
        self.assertContains(tela, 'data-receipt-overlay')
        self.assertContains(tela, 'receiptOverlayFrame')
        self.assertContains(tela, 'frame.srcdoc = frameDocument(html, response.url)')
        self.assertContains(tela, 'Cobrar na entrega')
        self.assertContains(tela, 'Marcar concluída')
        self.assertContains(tela, 'data-complete-order')
        self.assertContains(tela, 'conferenceModal')
        self.assertContains(tela, 'Marcar todos')
        self.assertContains(tela, 'Conferência pendente')
        self.assertContains(tela, 'Finalizar rota')
        self.assertContains(tela, '📊 Relatório')
        self.assertContains(tela, 'Consumo das rotas')
        self.assertContains(tela, '☷ Colunas')
        self.assertContains(tela, "loadReport('diario')")
        self.assertContains(tela, 'Cliente Delivery')
        self.assertContains(tela, 'Compra Delivery')
        self.assertContains(tela, 'dr-conference-progress')
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
        self.assertContains(kanban, '↻ Atualizar')
        self.assertContains(kanban, 'atualizarRotaDelivery()')
        self.assertContains(kanban, 'delivery-route-modal-open')
        self.assertContains(kanban, 'requestAnimationFrame(abrirRotaDelivery)')
        dados_kanban = json.loads(kanban.context['pedidos_json'])
        self.assertEqual(dados_kanban[str(ativo.pk)]['status_delivery'], 'preparando')
        self.assertEqual(dados_kanban[str(ativo.pk)]['status_label'], 'Em Preparo')

    def test_finalizado_diretamente_no_kanban_sai_do_rascunho_da_rota(self):
        venda = self._venda(numero=104)
        rota = RotaDelivery.objects.create(
            filial=self.filial,
            nome='Rota ainda não utilizada',
            pedido_ids=[venda.pk],
            estado={'selected': [f'pedido:{venda.pk}']},
        )
        venda.mudar_status_delivery(VendaPDV.StatusDelivery.FINALIZADO)

        tela = self.client.get(reverse('pdv:delivery_rotas'))
        pedidos = json.loads(tela.context['pedidos_json'])

        self.assertNotIn(venda.pk, {pedido['id'] for pedido in pedidos})
        self.assertIn(venda.pk, rota.pedido_ids)

        publicacao = self.client.post(
            reverse('pdv:delivery_rota_publicar'),
            data=json.dumps({'rota_id': rota.pk, 'pedidos': [venda.pk]}),
            content_type='application/json',
        )
        self.assertEqual(publicacao.status_code, 400)
        self.assertEqual(
            publicacao.json()['erro'],
            'A rota contém pedidos que não estão mais disponíveis para entrega.',
        )

    def test_finalizado_no_kanban_sai_da_rota_publicada_sem_conclusao_na_rota(self):
        venda = self._venda(numero=105)
        publicacao = self.client.post(
            reverse('pdv:delivery_rota_publicar'),
            data=json.dumps({'pedidos': [venda.pk]}),
            content_type='application/json',
        )
        venda.mudar_status_delivery(VendaPDV.StatusDelivery.FINALIZADO)

        tela_interna = self.client.get(reverse('pdv:delivery_rotas'))
        pedidos = json.loads(tela_interna.context['pedidos_json'])
        painel_motoboy = self.client.get(publicacao.json()['url'])

        self.assertNotIn(venda.pk, {pedido['id'] for pedido in pedidos})
        self.assertNotContains(painel_motoboy, '#105')

    def test_pedido_concluido_pela_rota_permanece_como_historico(self):
        venda = self._venda(numero=106)
        publicacao = self.client.post(
            reverse('pdv:delivery_rota_publicar'),
            data=json.dumps({'pedidos': [venda.pk]}),
            content_type='application/json',
        )
        rota = RotaDelivery.objects.get(token=publicacao.json()['url'].rstrip('/').split('/')[-1])
        conclusao = self.client.post(
            reverse('pdv:delivery_rota_concluir_pedido', args=[rota.pk, venda.pk]),
            data=json.dumps({'concluido': True}),
            content_type='application/json',
        )

        tela_interna = self.client.get(reverse('pdv:delivery_rotas'))
        pedidos = json.loads(tela_interna.context['pedidos_json'])
        painel_motoboy = self.client.get(publicacao.json()['url'])

        self.assertEqual(conclusao.status_code, 200)
        self.assertIn(venda.pk, {pedido['id'] for pedido in pedidos})
        self.assertContains(painel_motoboy, '#106')

    def test_rota_identifica_venda_que_deve_ser_cobrada_na_entrega(self):
        venda = self._venda(numero=104)
        venda.status = 'aberta'
        venda.save(update_fields=['status'])

        tela = self.client.get(reverse('pdv:delivery_rotas'))
        pedidos = json.loads(tela.context['pedidos_json'])
        pedido = next(item for item in pedidos if item['id'] == venda.pk)

        self.assertFalse(pedido['pago'])
        self.assertTrue(pedido['pagamento_pendente'])
        self.assertFalse(pedido['comprovante_disponivel'])

    def test_historico_cliente_traz_ranking_com_frequencia_valor_e_ultima_compra(self):
        unidade = UnidadeMedida.objects.create(
            empresa=self.empresa, sigla='UN', descricao='Unidade',
        )
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=unidade, descricao='Polpa de acerola',
            descricao_pdv='Acerola 1 kg', codigo='ACE-1', ncm='08119000',
        )
        venda = self._venda(numero=109)
        venda.valor_total = Decimal('36.00')
        venda.save(update_fields=['valor_total'])
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=produto, numero_item=1, quantidade=Decimal('3'),
            unidade_medida='UN', valor_unitario=Decimal('12'), valor_total=Decimal('36'),
        )
        self._venda(numero=110)

        resp = self.client.get(reverse('pdv:api_historico_cliente', args=[self.cliente.pk]))

        self.assertEqual(resp.status_code, 200)
        ranking = resp.json()['produtos_frequentes'][0]
        self.assertEqual(ranking['descricao'], 'Acerola 1 kg')
        self.assertEqual(ranking['qtd_pedidos'], 1)
        self.assertEqual(ranking['qtd_total'], 3.0)
        self.assertEqual(ranking['qtd_media'], 3.0)
        self.assertEqual(ranking['valor_total'], 36.0)
        self.assertTrue(ranking['ultima_compra'])
        self.assertTrue(resp.json()['cliente_delivery'])
        self.assertEqual(resp.json()['compras_delivery'], 2)
        self.assertTrue(resp.json()['compras'][0]['delivery'])

    @patch('apps.mapas.services.roteirizacao.OSRMRoteirizador.rota')
    def test_calculo_preserva_ordem_e_inclui_retorno(self, mock_rota):
        primeiro = self._venda(numero=201)
        segundo_cliente = Cliente.objects.create(
            filial=self.filial, razao_social='Segundo Cliente', cpf_cnpj='98765432100',
            latitude=-5.8100, longitude=-35.2300, geo_fixado=True,
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

    @patch('apps.mapas.services.roteirizacao.OSRMRoteirizador.rota')
    def test_pedidos_nao_consecutivos_no_mesmo_endereco_formam_um_local(self, mock_rota):
        primeiro = self._venda(numero=221)
        outro = self._venda(numero=222)
        ultimo = self._venda(numero=223)
        outro.endereco_entrega = {
            'cep': '59073150', 'rua': 'Rua Monte Rei', 'numero': '122',
            'bairro': 'Planalto', 'cidade': 'Natal', 'uf': 'RN',
            '_latitude': -5.859, '_longitude': -35.253,
        }
        outro.endereco_entrega['_geo_hash'] = _delivery_rota_endereco_hash(outro.endereco_entrega)
        outro.endereco_entrega['_geo_origem'] = 'manual'
        outro.save(update_fields=['endereco_entrega'])
        mock_rota.return_value = Rota(
            distancia_m=12000, duracao_s=1800,
            geometria=[[-5.79, -35.21], [-5.8, -35.22], [-5.859, -35.253], [-5.79, -35.21]],
        )

        resp = self.client.post(
            reverse('pdv:delivery_rota_calcular'),
            data=json.dumps({'pedidos': [primeiro.pk, outro.pk, ultimo.pk]}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        dados = resp.json()
        self.assertEqual(dados['ordem'], [primeiro.pk, ultimo.pk, outro.pk])
        self.assertEqual(dados['total_locais'], 2)
        self.assertEqual(len(dados['locais'][0]['pedidos']), 2)
        self.assertEqual(dados['locais'][0]['total_pedidos'], 2)
        self.assertEqual(dados['paradas'][0]['cep'], '59158155')
        self.assertEqual(dados['paradas'][0]['eta'], dados['paradas'][1]['eta'])
        self.assertEqual(len(mock_rota.call_args.args[0]), 4)

    @patch('apps.mapas.services.roteirizacao.OSRMRoteirizador.rota')
    def test_limite_de_50_considera_locais_e_nao_pedidos(self, mock_rota):
        pedidos = [self._venda(numero=700 + i) for i in range(51)]
        for i, pedido in enumerate(pedidos):
            endereco = {
                'cep': '59158155', 'rua': 'Avenida Antártida',
                'numero': str(100 + (i if i < 50 else 0)),
                'bairro': 'Parque das Nações', 'cidade': 'Parnamirim', 'uf': 'RN',
                '_latitude': -5.8, '_longitude': -35.22,
                '_geo_origem': 'manual',
            }
            endereco['_geo_hash'] = _delivery_rota_endereco_hash(endereco)
            pedido.endereco_entrega = endereco
            pedido.save(update_fields=['endereco_entrega'])
        mock_rota.return_value = Rota(
            distancia_m=1000, duracao_s=600,
            geometria=[[-5.79, -35.21], [-5.8, -35.22], [-5.79, -35.21]],
        )

        resp = self.client.post(
            reverse('pdv:delivery_rota_calcular'),
            data=json.dumps({'pedidos': [pedido.pk for pedido in pedidos]}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['total_locais'], 50)
        self.assertEqual(len(resp.json()['paradas']), 51)
        self.assertEqual(len(mock_rota.call_args.args[0]), 52)

        for i, pedido in enumerate(pedidos):
            endereco = dict(pedido.endereco_entrega)
            endereco['numero'] = str(100 + i)
            endereco['_geo_hash'] = _delivery_rota_endereco_hash(endereco)
            pedido.endereco_entrega = endereco
            pedido.save(update_fields=['endereco_entrega'])

        resp = self.client.post(
            reverse('pdv:delivery_rota_calcular'),
            data=json.dumps({'pedidos': [pedido.pk for pedido in pedidos]}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn('50 locais', resp.json()['erro'])
        mock_rota.assert_called_once()

    def test_sem_numero_entra_no_mesmo_condominio_mas_nao_so_pelo_cep(self):
        numerado = self._venda(numero=601)
        sem_numero = self._venda(numero=602)
        outro_condominio = self._venda(numero=603)
        comum = {
            'cep': '59158155', 'rua': 'Avenida Antartida',
            'bairro': 'Parque das Nacoes', 'cidade': 'Parnamirim', 'uf': 'RN',
        }
        numerado.endereco_entrega = {**comum, 'numero': '501',
                                     'complemento': 'Condominio Novo Leblon, Casa R2'}
        sem_numero.endereco_entrega = {**comum, 'numero': '',
                                      'complemento': 'Condominio Novo Leblon - Casa A N 25'}
        outro_condominio.endereco_entrega = {**comum, 'numero': '',
                                             'complemento': 'Condominio Outro - Casa 1'}
        for venda in (numerado, sem_numero, outro_condominio):
            venda.save(update_fields=['endereco_entrega'])
        paradas = [
            {'tipo': 'pedido', 'chave': f'pedido:{venda.pk}', 'venda': venda,
             'ponto': (-5.93, -35.2)}
            for venda in (numerado, sem_numero, outro_condominio)
        ]

        grupos, _ = _delivery_agrupar_paradas(paradas, set())

        self.assertEqual(len(grupos), 2)
        self.assertEqual([p['venda'].pk for p in grupos[0]['paradas']],
                         [numerado.pk, sem_numero.pk])
        self.assertEqual(grupos[1]['paradas'][0]['venda'].pk, outro_condominio.pk)

    @patch('apps.mapas.services.roteirizacao.OSRMRoteirizador.matriz_distancias')
    @patch('apps.mapas.services.roteirizacao.OSRMRoteirizador.rota')
    def test_gerar_rota_otimiza_distancia_pelas_ruas(self, mock_rota, mock_matriz):
        proximo = self._venda(numero=211)
        cliente_distante = Cliente.objects.create(
            filial=self.filial, razao_social='Cliente Distante', cpf_cnpj='98765432101',
            latitude=-5.9000, longitude=-35.3100, geo_fixado=True,
        )
        distante = VendaPDV.objects.create(
            filial=self.filial, numero_venda=212, cliente=cliente_distante,
            usuario=self.usuario, status='finalizada', delivery=True,
            status_delivery='novo', data_venda=timezone.now(),
        )
        cliente_intermediario = Cliente.objects.create(
            filial=self.filial, razao_social='Cliente Intermediario', cpf_cnpj='98765432102',
            latitude=-5.8100, longitude=-35.2300, geo_fixado=True,
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


class DeliveryRotasPersistentesTests(DeliveryKanbanBase):

    def setUp(self):
        super().setUp()
        self.filial.latitude = -5.7900
        self.filial.longitude = -35.2100
        self.filial.save(update_fields=['latitude', 'longitude'])
        Cliente.objects.filter(pk=self.cliente.pk).update(
            cep='59158155', endereco='Avenida Antártida', numero='25',
            bairro='Parque das Nações', cidade='Parnamirim', uf='RN',
            latitude=-5.8000, longitude=-35.2200, geo_precisao='exata',
            geo_fixado=True,
        )
        self.cliente.refresh_from_db()
        Cliente.objects.filter(pk=self.cliente.pk).update(
            geo_endereco_hash=self.cliente.hash_endereco_atual(),
        )
        self.cliente.refresh_from_db()

    def test_cria_duas_rotas_nomeadas_e_salva_conferencia_no_servidor(self):
        primeira = self.client.get(reverse('pdv:delivery_rotas'))
        rota_1 = RotaDelivery.objects.get(filial=self.filial)
        criada = self.client.post(
            reverse('pdv:delivery_rota_criar'),
            data=json.dumps({'nome': 'Rota Zona Sul'}), content_type='application/json',
        )
        rota_2 = RotaDelivery.objects.get(pk=criada.json()['rota']['id'])
        venda = self._venda(numero=390)

        salva = self.client.post(
            reverse('pdv:delivery_rota_salvar', args=[rota_2.pk]),
            data=json.dumps({
                'nome': 'Rota Zona Sul',
                'estado': {
                    'selected': [f'pedido:{venda.pk}'], 'manualStops': {},
                    'locked': [], 'driver': 'João', 'fuelPrice': '7',
                    'fuelAutonomy': '14',
                    'result': {'distancia_km': 28, 'tempo_total_s': 3600},
                },
                'conferencia_itens': {str(venda.pk): ['10', '11']},
            }), content_type='application/json',
        )

        self.assertEqual(primeira.status_code, 200)
        self.assertEqual(criada.status_code, 200)
        self.assertEqual(salva.status_code, 200)
        self.assertEqual(RotaDelivery.objects.filter(filial=self.filial).count(), 2)
        rota_2.refresh_from_db()
        self.assertEqual(rota_2.pedido_ids, [venda.pk])
        self.assertEqual(rota_2.conferencia_itens[str(venda.pk)], ['10', '11'])
        self.assertEqual(rota_2.combustivel_litros, Decimal('2.000'))
        self.assertEqual(rota_2.custo_combustivel, Decimal('14.00'))
        self.assertNotEqual(rota_1.token, rota_2.token)
        link_1 = json.loads(primeira.context['rotas_json'])[0]['link_motoboy']
        link_2 = criada.json()['rota']['link_motoboy']
        self.assertNotEqual(link_1, link_2)

    def test_cada_rota_publicada_retorna_link_individual(self):
        self.client.get(reverse('pdv:delivery_rotas'))
        rota_1 = RotaDelivery.objects.get(filial=self.filial)
        rota_2 = RotaDelivery.objects.create(filial=self.filial, nome='Rota Zona Norte')
        venda_1 = self._venda(numero=392)
        venda_2 = self._venda(numero=393)

        def publicar(rota, venda):
            return self.client.post(
                reverse('pdv:delivery_rota_publicar'),
                data=json.dumps({
                    'rota_id': rota.pk,
                    'pedidos': [venda.pk],
                    'ordem_paradas': [f'pedido:{venda.pk}'],
                    'entregador': rota.nome,
                }),
                content_type='application/json',
            )

        publicada_1 = publicar(rota_1, venda_1)
        publicada_2 = publicar(rota_2, venda_2)

        self.assertEqual(publicada_1.status_code, 200)
        self.assertEqual(publicada_2.status_code, 200)
        self.assertEqual(publicada_1.json()['rota_id'], rota_1.pk)
        self.assertEqual(publicada_2.json()['rota_id'], rota_2.pk)
        self.assertNotEqual(publicada_1.json()['url'], publicada_2.json()['url'])
        self.assertContains(self.client.get(publicada_1.json()['url']), '#392')
        self.assertContains(self.client.get(publicada_2.json()['url']), '#393')

    def test_finaliza_somente_depois_de_todas_as_entregas_concluidas(self):
        venda = self._venda(numero=391)
        rota = RotaDelivery.objects.create(
            filial=self.filial, nome='Rota Centro', pedido_ids=[venda.pk],
        )

        bloqueada = self.client.post(reverse('pdv:delivery_rota_finalizar', args=[rota.pk]))
        venda.status_delivery = VendaPDV.StatusDelivery.ENTREGUE
        venda.save(update_fields=['status_delivery'])
        finalizada = self.client.post(reverse('pdv:delivery_rota_finalizar', args=[rota.pk]))

        self.assertEqual(bloqueada.status_code, 400)
        self.assertEqual(finalizada.status_code, 200)
        rota.refresh_from_db()
        self.assertEqual(rota.status, RotaDelivery.Status.FINALIZADA)
        self.assertIsNotNone(rota.finalizada_em)

    def test_usuario_conclui_e_reabre_entrega_diretamente_na_rota(self):
        venda = self._venda(
            numero=394,
            status_delivery=VendaPDV.StatusDelivery.PREPARANDO,
        )
        rota = RotaDelivery.objects.create(
            filial=self.filial, nome='Rota Centro', pedido_ids=[venda.pk],
        )
        url = reverse(
            'pdv:delivery_rota_concluir_pedido', args=[rota.pk, venda.pk],
        )

        concluida = self.client.post(
            url, data=json.dumps({'concluido': True}), content_type='application/json',
        )
        venda.refresh_from_db()
        rota.refresh_from_db()

        self.assertEqual(concluida.status_code, 200)
        self.assertEqual(venda.status_delivery, VendaPDV.StatusDelivery.ENTREGUE)
        self.assertEqual(
            rota.pedido_status_anteriores[str(venda.pk)],
            VendaPDV.StatusDelivery.PREPARANDO,
        )
        self.assertEqual(rota.conclusoes_pedidos[str(venda.pk)]['origem'], 'sistema')
        self.assertEqual(rota.conclusoes_pedidos[str(venda.pk)]['nome'], 'Fulano')
        self.assertTrue(rota.conclusoes_pedidos[str(venda.pk)]['em'])
        self.assertEqual(concluida.json()['conclusao']['nome'], 'Fulano')

        reaberta = self.client.post(
            url, data=json.dumps({'concluido': False}), content_type='application/json',
        )
        venda.refresh_from_db()

        self.assertEqual(reaberta.status_code, 200)
        self.assertEqual(venda.status_delivery, VendaPDV.StatusDelivery.PREPARANDO)
        self.assertEqual(reaberta.json()['status_label'], 'Em Preparo')
        rota.refresh_from_db()
        self.assertEqual(rota.conclusoes_pedidos, {})

    def test_usuario_pode_desmarcar_pedido_finalizado_na_rota(self):
        venda = self._venda(
            numero=397,
            status_delivery=VendaPDV.StatusDelivery.FINALIZADO,
        )
        rota = RotaDelivery.objects.create(
            filial=self.filial, nome='Rota Centro', pedido_ids=[venda.pk],
        )

        resposta = self.client.post(
            reverse('pdv:delivery_rota_concluir_pedido', args=[rota.pk, venda.pk]),
            data=json.dumps({'concluido': False}), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(venda.status_delivery, VendaPDV.StatusDelivery.EM_ENTREGA)

    def test_nao_conclui_pedido_que_nao_pertence_a_rota(self):
        permitido = self._venda(numero=395)
        outro = self._venda(numero=396)
        rota = RotaDelivery.objects.create(
            filial=self.filial, nome='Rota Centro', pedido_ids=[permitido.pk],
        )

        resposta = self.client.post(
            reverse('pdv:delivery_rota_concluir_pedido', args=[rota.pk, outro.pk]),
            data=json.dumps({'concluido': True}), content_type='application/json',
        )

        self.assertEqual(resposta.status_code, 404)
        outro.refresh_from_db()
        self.assertEqual(outro.status_delivery, VendaPDV.StatusDelivery.NOVO)

    def test_nao_finaliza_rota_sem_paradas(self):
        rota = RotaDelivery.objects.create(filial=self.filial, nome='Rota vazia')

        resposta = self.client.post(reverse('pdv:delivery_rota_finalizar', args=[rota.pk]))

        self.assertEqual(resposta.status_code, 400)
        self.assertIn('ao menos uma parada', resposta.json()['erro'])

    def test_relatorio_soma_consumo_das_rotas_finalizadas(self):
        agora = timezone.now()
        RotaDelivery.objects.create(
            filial=self.filial, nome='Rota Norte', status='finalizada', ativa=False,
            finalizada_em=agora, pedido_ids=[1, 2], distancia_km=Decimal('30'),
            combustivel_litros=Decimal('3'), custo_combustivel=Decimal('21'),
        )
        RotaDelivery.objects.create(
            filial=self.filial, nome='Rota Sul', status='finalizada', ativa=False,
            finalizada_em=agora, pedido_ids=[3], distancia_km=Decimal('10'),
            combustivel_litros=Decimal('1'), custo_combustivel=Decimal('7'),
        )

        resp = self.client.get(reverse('pdv:delivery_rotas_relatorio_consumo'), {'periodo': 'mensal'})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['resumo']['rotas'], 2)
        self.assertEqual(resp.json()['resumo']['paradas'], 3)
        self.assertEqual(resp.json()['resumo']['distancia_km'], 40.0)
        self.assertEqual(resp.json()['resumo']['custo'], 28.0)

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_endereco_pode_ser_confirmado_mesmo_com_coordenada(self, resolver):
        resolver.return_value = Resultado(-5.79, -35.21, 'exata')
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
        self.assertEqual(venda.endereco_entrega['_latitude'], -5.79)

    def test_pedido_com_cep_mas_sem_numero_entra_com_alerta(self):
        Cliente.objects.filter(pk=self.cliente.pk).update(
            numero='', geo_precisao='aproximada', latitude=-5.65, longitude=-35.30,
            geo_fixado=False,
        )
        self.cliente.refresh_from_db()
        Cliente.objects.filter(pk=self.cliente.pk).update(
            geo_endereco_hash=self.cliente.hash_endereco_atual(),
        )
        venda = self._venda(numero=304)

        tela = self.client.get(reverse('pdv:delivery_rotas'))
        pedidos = json.loads(tela.context['pedidos_json'])
        pedido = next(item for item in pedidos if item['id'] == venda.pk)

        self.assertFalse(pedido['tem_coordenada'])
        self.assertTrue(pedido['localizacao_revalidacao_pendente'])
        self.assertContains(tela, 'dr-address-warning-icon')
        self.assertContains(tela, 'dr-address-warning-tooltip')

    def test_pedido_numerado_salvo_com_ponto_do_cep_e_revalidado(self):
        venda = self._venda(numero=307)
        venda.endereco_entrega = {
            'cep': '59072710', 'rua': 'Rua Bela Vista', 'numero': '17',
            'bairro': 'Cidade Nova', 'cidade': 'Natal', 'uf': 'RN',
            '_latitude': -5.795, '_longitude': -35.20944,
            '_geo_precisao': 'aproximada', '_geo_origem': 'cep',
            '_geo_cep_validado': True,
        }
        venda.endereco_entrega['_geo_hash'] = _delivery_rota_endereco_hash(
            venda.endereco_entrega
        )
        venda.save(update_fields=['endereco_entrega'])

        tela = self.client.get(reverse('pdv:delivery_rotas'))
        pedidos = json.loads(tela.context['pedidos_json'])
        pedido = next(item for item in pedidos if item['id'] == venda.pk)

        self.assertFalse(pedido['tem_coordenada'])
        self.assertTrue(pedido['localizacao_revalidacao_pendente'])
        self.assertAlmostEqual(pedido['lat'], -5.795)
        self.assertIn('validado com o CEP novamente', pedido['coordenada_aviso'])

    def test_pedido_sem_numero_com_complemento_salvo_no_cep_e_revalidado(self):
        venda = self._venda(numero=313)
        venda.endereco_entrega = {
            'cep': '59158155', 'rua': 'Avenida Antártida', 'numero': '',
            'complemento': 'Condomínio Novo Leblon - Casa A N 25',
            'bairro': 'Parque das Nações', 'cidade': 'Parnamirim', 'uf': 'RN',
            '_latitude': -5.91556, '_longitude': -35.26278,
            '_geo_precisao': 'aproximada', '_geo_origem': 'cep',
            '_geo_cep_validado': True,
        }
        venda.endereco_entrega['_geo_hash'] = _delivery_rota_endereco_hash(
            venda.endereco_entrega
        )
        venda.save(update_fields=['endereco_entrega'])

        tela = self.client.get(reverse('pdv:delivery_rotas'))
        pedidos = json.loads(tela.context['pedidos_json'])
        pedido = next(item for item in pedidos if item['id'] == venda.pk)

        self.assertFalse(pedido['tem_coordenada'])
        self.assertTrue(pedido['localizacao_revalidacao_pendente'])

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_endereco_sem_numero_troca_coordenada_distante_pela_do_cep(self, resolver):
        resolver.side_effect = [
            Resultado(-5.65, -35.30, 'aproximada'),
            Resultado(-5.9293706, -35.2108215, 'aproximada'),
        ]
        venda = self._venda(numero=308)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59158-155', 'rua': 'Avenida Antártida', 'numero': '',
                'bairro': 'Parque das Nações', 'cidade': 'Parnamirim', 'uf': 'RN',
                'atualizar_cliente': False,
            }), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertAlmostEqual(venda.endereco_entrega['_latitude'], -5.9293706)
        self.assertEqual(venda.endereco_entrega['_geo_origem'], 'cep')
        self.assertEqual(venda.endereco_entrega['_geo_provider'], 'awesomeapi_cep')
        self.assertTrue(venda.endereco_entrega['_geo_cep_validado'])
        self.assertIn('número', resp.json()['coordenada_aviso'])
        self.assertTrue(resp.json()['localizacao_requer_revisao'])
        self.assertEqual(resolver.call_count, 2)

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_endereco_com_numero_preserva_ponto_exato_do_arcgis(self, resolver):
        resolver.return_value = Resultado(-5.837797, -35.243565, 'exata')
        venda = self._venda(numero=309)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59158-155', 'rua': 'Avenida Antártida', 'numero': '501',
                'bairro': 'Parque das Nações', 'cidade': 'Parnamirim', 'uf': 'RN',
                'atualizar_cliente': False,
            }), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertAlmostEqual(venda.endereco_entrega['_longitude'], -35.243565)
        self.assertEqual(venda.endereco_entrega['_geo_origem'], 'arcgis')
        self.assertEqual(resp.json()['coordenada_aviso'], '')
        self.assertEqual(resolver.call_count, 2)

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_ponto_exato_distante_do_cep_e_substituido(self, resolver):
        resolver.side_effect = [
            Resultado(-5.65, -35.30, 'exata'),
            Resultado(-5.9293706, -35.2108215, 'aproximada'),
        ]
        venda = self._venda(numero=3091)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59158-155', 'rua': 'Avenida Antártida', 'numero': '501',
                'bairro': 'Parque das Nações', 'cidade': 'Parnamirim', 'uf': 'RN',
            }), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(venda.endereco_entrega['_geo_origem'], 'cep')
        self.assertAlmostEqual(venda.endereco_entrega['_latitude'], -5.9293706)
        self.assertAlmostEqual(venda.endereco_entrega['_geo_cep_lat'], -5.9293706)
        self.assertTrue(resp.json()['localizacao_requer_revisao'])

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_cep_consultado_no_navegador_evitar_erro_429_do_servidor(self, resolver):
        resolver.return_value = Resultado(-5.65, -35.30, 'exata')
        venda = self._venda(numero=3094)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59158-155', 'rua': 'Avenida Antártida', 'numero': '501',
                'bairro': 'Parque das Nações', 'cidade': 'Parnamirim', 'uf': 'RN',
                'coordenada_cep': {
                    'provider': 'awesomeapi_cep', 'cep': '59158155',
                    'rua': 'Avenida Antártida', 'cidade': 'Parnamirim', 'uf': 'RN',
                    'lat': '-5.9293706', 'lng': '-35.2108215',
                },
            }), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(venda.endereco_entrega['_geo_origem'], 'cep')
        self.assertAlmostEqual(venda.endereco_entrega['_latitude'], -5.9293706)

    def test_ponto_automatico_salvo_fora_do_raio_do_cep_e_bloqueado(self):
        venda = self._venda(numero=3092)
        endereco = {
            'cep': '59158155', 'rua': 'Avenida Antártida', 'numero': '501',
            'bairro': 'Parque das Nações', 'cidade': 'Parnamirim', 'uf': 'RN',
            '_latitude': -5.65, '_longitude': -35.30,
            '_geo_cep_lat': -5.9293706, '_geo_cep_lng': -35.2108215,
            '_geo_origem': 'arcgis', '_geo_lookup_version': 5,
        }
        endereco['_geo_hash'] = _delivery_rota_endereco_hash(endereco)
        venda.endereco_entrega = endereco
        venda.save(update_fields=['endereco_entrega'])

        tela = self.client.get(reverse('pdv:delivery_rotas'))
        pedido = next(item for item in json.loads(tela.context['pedidos_json']) if item['id'] == venda.pk)

        self.assertFalse(pedido['tem_coordenada'])
        self.assertTrue(pedido['localizacao_revalidacao_pendente'])

    def test_ponto_manual_do_cliente_nao_migra_para_outro_numero(self):
        venda = self._venda(numero=3093)
        venda.endereco_entrega = {
            'cep': '59158155', 'rua': 'Avenida Antártida', 'numero': '501',
            'bairro': 'Parque das Nações', 'cidade': 'Parnamirim', 'uf': 'RN',
        }
        venda.save(update_fields=['endereco_entrega'])

        tela = self.client.get(reverse('pdv:delivery_rotas'))
        pedido = next(item for item in json.loads(tela.context['pedidos_json']) if item['id'] == venda.pk)

        self.assertFalse(pedido['tem_coordenada'])

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_endereco_com_numero_usa_ponto_generico_do_cep_com_aviso(self, resolver):
        resolver.side_effect = [
            Resultado(erro='resultado incompatível com o CEP informado'),
            Resultado(-5.795, -35.20944, 'aproximada'),
            Resultado(erro='número não encontrado sem CEP'),
            Resultado(erro='estabelecimento não encontrado'),
        ]
        venda = self._venda(numero=311)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59072-710', 'rua': 'Rua Bela Vista', 'numero': '17',
                'bairro': 'Cidade Nova', 'cidade': 'Natal', 'uf': 'RN',
                'atualizar_cliente': False,
            }), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(venda.endereco_entrega['_geo_origem'], 'cep')
        self.assertTrue(venda.endereco_entrega['_geo_requer_revisao'])
        self.assertIn('Local exato não encontrado no mapa', resp.json()['coordenada_aviso'])
        self.assertTrue(resp.json()['localizacao_requer_revisao'])
        self.assertEqual(resolver.call_count, 4)

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_numero_na_mesma_avenida_com_cep_do_outro_lado_nao_cai_no_centro_do_cep(self, resolver):
        resolver.side_effect = [
            Resultado(erro='resultado incompatível com o CEP informado'),
            Resultado(-5.8039054, -35.2093272, 'aproximada'),
            Resultado(-5.8071367, -35.2042242, 'aproximada', detalhes={
                'tipo': 'streetaddress', 'numero': '1250', 'pontuacao': 98.72,
                'endereco': 'Avenida Almirante Alexandrino de Alencar 1250, Tirol, Natal',
                'cep': '59015350',
            }),
        ]
        venda = self._venda(numero=2405)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59022-350', 'rua': 'Avenida Almirante Alexandrino de Alencar',
                'numero': '1250', 'bairro': 'Lagoa Seca', 'cidade': 'Natal', 'uf': 'RN',
            }), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(venda.endereco_entrega['_geo_origem'], 'interpolado')
        self.assertAlmostEqual(venda.endereco_entrega['_latitude'], -5.8071367)
        self.assertEqual(venda.endereco_entrega['_geo_cep_divergente'], '59015350')
        self.assertEqual(venda.endereco_entrega['_geo_lookup_version'], 5)
        self.assertIn('outro CEP', resp.json()['coordenada_aviso'])
        self.assertTrue(resp.json()['localizacao_requer_revisao'])
        self.assertEqual(resolver.call_count, 3)

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_numero_de_outra_rua_nao_substitui_ponto_do_cep(self, resolver):
        resolver.side_effect = [
            Resultado(erro='resultado incompatível com o CEP informado'),
            Resultado(-5.8039, -35.2093, 'aproximada'),
            Resultado(-5.804, -35.209, 'aproximada', detalhes={
                'tipo': 'streetaddress', 'numero': '1250', 'pontuacao': 99,
                'endereco': 'Avenida Outra 1250, Natal', 'cep': '59015350',
            }),
            Resultado(erro='estabelecimento não encontrado'),
        ]
        venda = self._venda(numero=2406)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59022-350', 'rua': 'Avenida Almirante Alexandrino de Alencar',
                'numero': '1250', 'bairro': 'Lagoa Seca', 'cidade': 'Natal', 'uf': 'RN',
            }), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(venda.endereco_entrega['_geo_origem'], 'cep')
        self.assertAlmostEqual(venda.endereco_entrega['_latitude'], -5.8039)

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_endereco_nao_encontrado_busca_cliente_pelo_nome(self, resolver):
        resolver.side_effect = [
            Resultado(erro='número não encontrado'),
            Resultado(-5.8516429, -35.2039826, 'aproximada'),
            Resultado(
                -5.8488, -35.2068, 'aproximada',
                detalhes={'numero': '2835', 'tipo': 'poi'},
            ),
        ]
        venda = self._venda(numero=315)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59078-570', 'rua': 'Rua Leôncio Etelvino de Medeiros',
                'numero': '2835', 'bairro': 'Capim Macio', 'cidade': 'Natal',
                'uf': 'RN', 'atualizar_cliente': False,
            }), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(venda.endereco_entrega['_geo_origem'], 'estabelecimento')
        self.assertEqual(venda.endereco_entrega['_geo_referencia'], 'Cliente Delivery')
        self.assertTrue(venda.endereco_entrega['_geo_numero_confirmado'])
        self.assertIn('encontrada pelo nome', resp.json()['coordenada_aviso'])
        self.assertFalse(resp.json()['localizacao_requer_revisao'])
        self.assertIn('Cliente Delivery', resolver.call_args_list[2].args[0])

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_nome_do_cliente_nao_pode_virar_rua_homonima_distante(self, resolver):
        resolver.side_effect = [
            Resultado(-5.8567, -35.2491, 'aproximada', detalhes={
                'tipo': 'streetaddress', 'numero': '122', 'pontuacao': 100,
            }),
            Resultado(-5.8590, -35.2534, 'aproximada'),
            Resultado(-5.8050, -35.2197, 'aproximada', detalhes={
                'tipo': 'StreetName', 'pontuacao': 82.07,
            }),
        ]
        venda = self._venda(numero=2374)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59073-150', 'rua': 'Rua Monte Rei', 'numero': '122',
                'bairro': 'Planalto', 'cidade': 'Natal', 'uf': 'RN',
                'atualizar_cliente': False,
            }), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(venda.endereco_entrega['_geo_origem'], 'interpolado')
        self.assertAlmostEqual(venda.endereco_entrega['_latitude'], -5.8567)
        self.assertTrue(resp.json()['localizacao_requer_revisao'])
        self.assertEqual(resp.json()['localizacao_origem'], 'interpolado')
        self.assertIn('estimado ao longo da rua', resp.json()['coordenada_aviso'])

    def test_ponto_antigo_pelo_nome_sem_cep_validado_exige_revisao(self):
        venda = self._venda(numero=2374)
        venda.endereco_entrega = {
            'cep': '59073150', 'rua': 'Rua Monte Rei', 'numero': '122',
            'bairro': 'Planalto', 'cidade': 'Natal', 'uf': 'RN',
            '_latitude': -5.8050, '_longitude': -35.2197,
            '_geo_origem': 'estabelecimento', '_geo_cep_validado': False,
        }
        venda.endereco_entrega['_geo_hash'] = _delivery_rota_endereco_hash(venda.endereco_entrega)
        venda.save(update_fields=['endereco_entrega'])

        tela = self.client.get(reverse('pdv:delivery_rotas'))
        pedido = next(item for item in json.loads(tela.context['pedidos_json']) if item['id'] == venda.pk)

        self.assertFalse(pedido['tem_coordenada'])
        self.assertTrue(pedido['localizacao_revalidacao_pendente'])

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_endereco_do_novo_leblon_usa_complemento_em_vez_do_ponto_do_cep(self, resolver):
        resolver.side_effect = [
            Resultado(erro='resultado incompatível com o CEP informado'),
            Resultado(-5.9293706, -35.2108215, 'aproximada'),
            Resultado(erro='número não encontrado sem CEP'),
            Resultado(-5.930077912533, -35.205803891679, 'aproximada'),
        ]
        venda = self._venda(numero=312)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59158-155', 'rua': 'Avenida Antártida', 'numero': '10',
                'complemento': 'Condomínio Novo Leblon - Casa A N 25',
                'bairro': 'Parque das Nações', 'cidade': 'Parnamirim', 'uf': 'RN',
                'atualizar_cliente': False,
            }), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(venda.endereco_entrega['_geo_origem'], 'complemento')
        self.assertAlmostEqual(venda.endereco_entrega['_latitude'], -5.930077912533)
        self.assertAlmostEqual(venda.endereco_entrega['_longitude'], -35.205803891679)
        self.assertIn('Condomínio Novo Leblon', resp.json()['coordenada_aviso'])
        self.assertFalse(resp.json()['localizacao_requer_revisao'])
        self.assertEqual(resolver.call_count, 4)

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_numero_valido_prevalece_sobre_instrucao_no_complemento(self, resolver):
        resolver.side_effect = [
            Resultado(-5.8899051, -35.2021521, 'aproximada', detalhes={
                'tipo': 'streetaddress', 'numero': '274', 'pontuacao': 98.04,
            }),
            Resultado(-5.8902925, -35.2025551, 'aproximada'),
        ]
        venda = self._venda(numero=2415)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59152-360', 'rua': 'Rua Paraú', 'numero': '274',
                'complemento': 'ou chamar na escola', 'bairro': 'Nova Parnamirim',
                'cidade': 'Parnamirim', 'uf': 'RN',
            }), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(venda.endereco_entrega['_geo_origem'], 'interpolado')
        self.assertAlmostEqual(venda.endereco_entrega['_latitude'], -5.8899051)
        self.assertEqual(venda.endereco_entrega['_geo_lookup_version'], 5)
        self.assertEqual(resolver.call_count, 2)

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_complemento_distante_nao_substitui_cep(self, resolver):
        resolver.side_effect = [
            Resultado(erro='número não encontrado'),
            Resultado(-5.8902925, -35.2025551, 'aproximada'),
            Resultado(-5.9194102, -35.2675198, 'aproximada'),
            Resultado(erro='estabelecimento não encontrado'),
        ]
        venda = self._venda(numero=2416)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59152-360', 'rua': 'Rua Paraú', 'numero': '274',
                'complemento': 'Condomínio com nome semelhante',
                'bairro': 'Nova Parnamirim', 'cidade': 'Parnamirim', 'uf': 'RN',
            }), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(venda.endereco_entrega['_geo_origem'], 'cep')
        self.assertAlmostEqual(venda.endereco_entrega['_latitude'], -5.8902925)

    def test_ponto_antigo_por_complemento_nao_e_aceito_sem_revalidacao(self):
        venda = self._venda(numero=2417)
        venda.endereco_entrega = {
            'cep': '59152360', 'rua': 'Rua Paraú', 'numero': '274',
            'complemento': 'ou chamar na escola', 'bairro': 'Nova Parnamirim',
            'cidade': 'Parnamirim', 'uf': 'RN',
            '_latitude': -5.9194102, '_longitude': -35.2675198,
            '_geo_origem': 'complemento', '_geo_cep_validado': False,
        }
        venda.endereco_entrega['_geo_hash'] = _delivery_rota_endereco_hash(venda.endereco_entrega)
        venda.save(update_fields=['endereco_entrega'])

        tela = self.client.get(reverse('pdv:delivery_rotas'))
        pedido = next(item for item in json.loads(tela.context['pedidos_json']) if item['id'] == venda.pk)

        self.assertFalse(pedido['tem_coordenada'])
        self.assertTrue(pedido['localizacao_revalidacao_pendente'])
        self.assertContains(tela, 'stop.localizacao_revalidacao_pendente')
        self.assertContains(tela, 'await refreshSelectedLocations()')

        calculo = self.client.post(
            reverse('pdv:delivery_rota_calcular'),
            data=json.dumps({'pedidos': [venda.pk]}), content_type='application/json',
        )
        self.assertEqual(calculo.status_code, 400)
        self.assertIn('sem coordenada confirmada', calculo.json()['erro'])

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_endereco_sem_numero_e_complemento_prioriza_coordenada_do_cep(self, resolver):
        resolver.side_effect = [
            Resultado(erro='resultado incompatível com o CEP informado'),
            Resultado(-5.9293706, -35.2108215, 'aproximada'),
        ]
        venda = self._venda(numero=314)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59158-155', 'rua': 'Avenida Antártida', 'numero': '',
                'complemento': 'Condomínio Novo Leblon - Casa A N 25',
                'bairro': 'Parque das Nações', 'cidade': 'Parnamirim', 'uf': 'RN',
                'atualizar_cliente': False,
            }), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(venda.endereco_entrega['_geo_origem'], 'cep')
        self.assertEqual(venda.endereco_entrega['_geo_provider'], 'awesomeapi_cep')
        self.assertAlmostEqual(venda.endereco_entrega['_latitude'], -5.9293706)
        self.assertAlmostEqual(venda.endereco_entrega['_longitude'], -35.2108215)
        self.assertTrue(resp.json()['localizacao_requer_revisao'])
        self.assertEqual(resolver.call_count, 2)

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_endereco_aproximado_sem_validacao_do_cep_nao_usa_ponto_da_rua(self, resolver):
        resolver.side_effect = [
            Resultado(-5.65, -35.30, 'aproximada'),
            Resultado(erro='CEP sem coordenada disponível'),
            Resultado(erro='logradouro não encontrado'),
        ]
        venda = self._venda(numero=310)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59158-155', 'rua': 'Avenida Antártida', 'numero': '',
                'bairro': 'Parque das Nações', 'cidade': 'Parnamirim', 'uf': 'RN',
                'atualizar_cliente': False,
            }), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 422)
        self.assertNotIn('_latitude', venda.endereco_entrega)
        self.assertIn('nem localizar o logradouro', resp.json()['erro'])

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_endereco_desestruturado_usa_ponto_aproximado_do_logradouro(self, resolver):
        resolver.side_effect = [
            Resultado(erro='resultado incompatível com o CEP informado'),
            Resultado(erro='resultado incompatível com a rua informada'),
            Resultado(-5.9416, -35.1718, 'aproximada', detalhes={
                'tipo': 'streetname', 'pontuacao': 88.12,
                'endereco': 'Residencial Catuana, Parnamirim, RN',
            }),
        ]
        venda = self._venda(numero=2401)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59160-414',
                'rua': 'Residencial Catuana, Rua Camapuã casa 420',
                'numero': '', 'complemento': 'ALPHAVILLE NATAL RESIDENCIAL C',
                'bairro': 'Pium (Distrito Litoral)',
                'cidade': 'Parnamirim', 'uf': 'RN', 'atualizar_cliente': False,
            }), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(venda.endereco_entrega['_geo_origem'], 'rua')
        self.assertTrue(venda.endereco_entrega['_geo_rua_validada'])
        self.assertFalse(venda.endereco_entrega['_geo_cep_validado'])
        self.assertAlmostEqual(venda.endereco_entrega['_latitude'], -5.9416)
        self.assertIn('ponto genérico da rua', resp.json()['coordenada_aviso'])
        self.assertTrue(resp.json()['localizacao_requer_revisao'])

        tela = self.client.get(reverse('pdv:delivery_rotas'))
        pedido = next(
            item for item in json.loads(tela.context['pedidos_json'])
            if item['id'] == venda.pk
        )
        self.assertTrue(pedido['tem_coordenada'])
        self.assertFalse(pedido['localizacao_revalidacao_pendente'])
        self.assertEqual(resolver.call_count, 3)
        self.assertEqual(
            resolver.call_args_list[2].args[0],
            'Residencial Catuana, Parnamirim, RN, Brasil',
        )

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_endereco_pode_ser_salvo_apenas_na_entrega(self, resolver):
        resolver.return_value = Resultado(-5.91, -35.19, 'exata')
        venda = self._venda(numero=305)
        endereco_cliente_original = self.cliente.endereco

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59158-155', 'rua': 'Rua da Entrega', 'numero': '90',
                'bairro': 'Parque das Nações', 'cidade': 'Parnamirim', 'uf': 'RN',
                'atualizar_cliente': False,
            }), content_type='application/json',
        )

        self.cliente.refresh_from_db()
        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.cliente.endereco, endereco_cliente_original)
        self.assertEqual(venda.endereco_entrega['rua'], 'Rua da Entrega')
        self.assertEqual(venda.endereco_entrega['_longitude'], -35.19)

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_endereco_pode_atualizar_tambem_o_cadastro_do_cliente(self, resolver):
        resolver.return_value = Resultado(-5.92, -35.20, 'exata')
        venda = self._venda(numero=307)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59158-155', 'rua': 'Rua Nova', 'numero': '101',
                'bairro': 'Parque das Nações', 'cidade': 'Parnamirim', 'uf': 'RN',
                'atualizar_cliente': True,
            }), content_type='application/json',
        )

        self.cliente.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['atualizou_cliente'])
        self.assertEqual(self.cliente.endereco, 'Rua Nova')
        self.assertEqual(self.cliente.numero, '101')
        self.assertEqual(self.cliente.latitude, -5.92)

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_resultado_aproximado_com_numero_sem_referencia_usa_cep(self, resolver):
        resolver.side_effect = [
            Resultado(-5.65, -35.30, 'aproximada'),
            Resultado(-5.9293706, -35.2108215, 'aproximada'),
            Resultado(erro='estabelecimento não encontrado'),
        ]
        venda = self._venda(numero=306)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_endereco', args=[venda.pk]),
            data=json.dumps({
                'cep': '59158-155', 'rua': 'Avenida Antártida', 'numero': '25',
                'bairro': 'Parque das Nações', 'cidade': 'Parnamirim', 'uf': 'RN',
                'atualizar_cliente': False,
            }), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(venda.endereco_entrega['_geo_origem'], 'cep')
        self.assertTrue(resp.json()['localizacao_requer_revisao'])
        self.assertIn('Arraste o pino', resp.json()['coordenada_aviso'])

    def test_coordenada_manual_do_pedido_e_persistida_e_auditada(self):
        venda = self._venda(numero=316)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_coordenada_manual'),
            data=json.dumps({
                'tipo': 'pedido', 'id': venda.pk,
                'lat': -5.93011, 'lng': -35.20591,
            }), content_type='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(venda.endereco_entrega['_geo_origem'], 'manual')
        self.assertEqual(venda.endereco_entrega['_geo_precisao'], 'manual')
        self.assertEqual(venda.endereco_entrega['_geo_provider'], 'usuario')
        self.assertEqual(venda.endereco_entrega['_geo_ajustado_por'], 'Fulano')
        self.assertFalse(venda.endereco_entrega['_geo_requer_revisao'])
        self.assertAlmostEqual(venda.endereco_entrega['_latitude'], -5.93011)

    def test_coordenada_manual_atualiza_todos_os_pedidos_do_mesmo_local(self):
        primeiro = self._venda(numero=317)
        segundo = self._venda(numero=318)

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_coordenada_manual'),
            data=json.dumps({
                'tipo': 'pedido', 'ids': [primeiro.pk, segundo.pk],
                'lat': -5.93011, 'lng': -35.20591,
            }), content_type='application/json',
        )

        primeiro.refresh_from_db()
        segundo.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.json()['pedido_ids']), {primeiro.pk, segundo.pk})
        self.assertEqual(primeiro.endereco_entrega['_geo_origem'], 'manual')
        self.assertEqual(segundo.endereco_entrega['_geo_origem'], 'manual')
        self.assertEqual(primeiro.endereco_entrega['_latitude'], segundo.endereco_entrega['_latitude'])

    def test_coordenada_manual_aceita_mesmo_condominio_sem_numero(self):
        numerado = self._venda(numero=321)
        sem_numero = self._venda(numero=322)
        endereco = {'cep': '59158155', 'rua': 'Avenida Antartida',
                    'bairro': 'Parque das Nacoes', 'cidade': 'Parnamirim', 'uf': 'RN'}
        numerado.endereco_entrega = {**endereco, 'numero': '501',
                                     'complemento': 'Condominio Novo Leblon, Casa R2'}
        sem_numero.endereco_entrega = {**endereco, 'numero': '',
                                      'complemento': 'Condominio Novo Leblon - Casa A N 25'}
        numerado.save(update_fields=['endereco_entrega'])
        sem_numero.save(update_fields=['endereco_entrega'])

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_coordenada_manual'),
            data=json.dumps({'tipo': 'pedido', 'ids': [numerado.pk, sem_numero.pk],
                             'lat': -5.93011, 'lng': -35.20591}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        sem_numero.refresh_from_db()
        self.assertEqual(sem_numero.endereco_entrega['_geo_origem'], 'manual')

    def test_coordenada_manual_recusa_pedidos_de_enderecos_diferentes(self):
        primeiro = self._venda(numero=319)
        segundo = self._venda(numero=320)
        segundo.endereco_entrega = {
            'cep': '59073150', 'rua': 'Rua Monte Rei', 'numero': '122',
            'bairro': 'Planalto', 'cidade': 'Natal', 'uf': 'RN',
        }
        segundo.save(update_fields=['endereco_entrega'])

        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_coordenada_manual'),
            data=json.dumps({
                'tipo': 'pedido', 'ids': [primeiro.pk, segundo.pk],
                'lat': -5.93011, 'lng': -35.20591,
            }), content_type='application/json',
        )

        primeiro.refresh_from_db()
        segundo.refresh_from_db()
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(primeiro.endereco_entrega)
        self.assertNotIn('_latitude', segundo.endereco_entrega)

    def test_coordenada_manual_da_filial_fica_protegida(self):
        resp = self.client.post(
            reverse('pdv:delivery_rota_atualizar_coordenada_manual'),
            data=json.dumps({
                'tipo': 'filial', 'lat': -5.78991, 'lng': -35.20981,
            }), content_type='application/json',
        )

        self.filial.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.filial.geo_fixado)
        self.assertEqual(self.filial.geo_precisao, 'manual')
        self.assertAlmostEqual(self.filial.latitude, -5.78991)

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
                'observacao': 'Buscar caixas térmicas', 'cep': '59080000',
                'rua': 'Rua Manual',
                'numero': '50', 'bairro': 'Centro', 'cidade': 'Natal', 'uf': 'rn',
            }), content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['observacao'], 'Buscar caixas térmicas')
        self.assertEqual(resp.json()['endereco']['uf'], 'RN')
        self.assertEqual(Cliente.objects.count(), quantidade_clientes)

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_parada_manual_tenta_dois_provedores_sem_relaxar_endereco(self, resolver):
        resolver.side_effect = [
            Resultado(erro='endereco nao encontrado'),
            Resultado(-5.87, -35.20, 'exata'),
        ]

        resp = self.client.post(
            reverse('pdv:delivery_rota_localizar_parada_manual'),
            data=json.dumps({
                'observacao': 'Buscar material', 'cep': '59080460',
                'rua': 'Rua Arnaldo Neves da Silva', 'numero': '15',
                'complemento': 'Bloco teste', 'bairro': 'Neópolis',
                'cidade': 'Natal', 'uf': 'RN',
            }), content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resolver.call_count, 2)
        primeira_consulta = resolver.call_args_list[1].args[0]
        primeira_chave = resolver.call_args_list[1].args[1]
        self.assertNotIn('Bloco teste', primeira_consulta)
        self.assertIn('Rua Arnaldo Neves da Silva, 15', primeira_consulta)
        self.assertEqual(len(primeira_chave), 32)
        self.assertEqual(resp.json()['endereco']['complemento'], 'Bloco teste')
        self.assertIn('59080-460', resp.json()['endereco_texto'])

    @patch('apps.mapas.services.geocoder.GeocodificacaoService.resolver')
    def test_parada_manual_retorna_json_quando_geocodificador_falha(self, resolver):
        resolver.side_effect = RuntimeError('provider indisponível')

        resp = self.client.post(
            reverse('pdv:delivery_rota_localizar_parada_manual'),
            data=json.dumps({
                'observacao': 'Buscar material', 'cep': '59080000', 'rua': 'Rua Manual',
                'numero': '50', 'bairro': 'Centro', 'cidade': 'Natal', 'uf': 'RN',
            }), content_type='application/json',
        )

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.headers['Content-Type'], 'application/json')
        self.assertIn('serviço de localização', resp.json()['erro'])

    def test_configuracao_de_custo_fica_guardada_na_filial(self):
        resp = self.client.post(
            reverse('pdv:delivery_rota_salvar_configuracao'),
            data=json.dumps({
                'combustivel_preco': '6.29', 'autonomia_km_l': '32.5',
                'minutos_por_parada': 8,
            }), content_type='application/json',
        )
        tela = self.client.get(reverse('pdv:delivery_rotas'))
        rota = RotaDeliveryPublica.objects.get(filial=self.filial)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(float(rota.combustivel_preco), 6.29)
        self.assertEqual(float(rota.autonomia_km_l), 32.5)
        self.assertEqual(rota.minutos_por_parada, 8)
        configuracao = json.loads(tela.context['configuracao_rota_json'])
        self.assertEqual(configuracao['minutos_por_parada'], 8)
        self.assertContains(tela, 'Oportunidades de Venda')

    def test_configuracao_rfm_fica_guardada_na_filial_e_valida_ordem(self):
        resp = self.client.post(
            reverse('pdv:delivery_rota_salvar_configuracao_rfm'),
            data=json.dumps({
                'r5_dias': 15, 'r4_dias': 35, 'r3_dias': 70, 'r2_dias': 140,
                'f_automatico': False,
                'f5_compras': 20, 'f4_compras': 10, 'f3_compras': 5, 'f2_compras': 2,
                'm_automatico': False,
                'm5_valor': 20000, 'm4_valor': 10000,
                'm3_valor': 5000, 'm2_valor': 1000,
            }),
            content_type='application/json',
        )
        rota = RotaDeliveryPublica.objects.get(filial=self.filial)
        tela = self.client.get(reverse('pdv:delivery_rotas'))
        configuracao = json.loads(tela.context['configuracao_rota_json'])['rfm']

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(rota.rfm_r5_dias, 15)
        self.assertEqual(rota.rfm_r2_dias, 140)
        self.assertEqual(configuracao['r5_dias'], 15)
        self.assertFalse(configuracao['f_automatico'])
        self.assertEqual(configuracao['f5_compras'], 20)
        self.assertFalse(configuracao['m_automatico'])
        self.assertEqual(configuracao['m4_valor'], 10000.0)

        invalida = self.client.post(
            reverse('pdv:delivery_rota_salvar_configuracao_rfm'),
            data=json.dumps({
                'r5_dias': 60, 'r4_dias': 30, 'r3_dias': 90, 'r2_dias': 180,
                'f_automatico': True,
                'f5_compras': 10, 'f4_compras': 7, 'f3_compras': 4, 'f2_compras': 2,
                'm_automatico': True,
                'm5_valor': 10000, 'm4_valor': 5000,
                'm3_valor': 2000, 'm2_valor': 500,
            }),
            content_type='application/json',
        )
        self.assertEqual(invalida.status_code, 400)

    @patch('apps.mapas.services.proximidade.ProximidadeService.clientes_proximos')
    def test_oportunidades_combina_rfm_recompra_e_proximidade(self, proximos):
        candidato = Cliente.objects.create(
            filial=self.filial, razao_social='Cliente Campeão', cpf_cnpj='44555666000177',
            endereco='Rua Comercial', numero='90', bairro='Centro', cidade='Natal', uf='RN',
            celular='84999998888', latitude=-5.81, longitude=-35.23,
        )
        recompra = RecompraCliente.objects.create(
            filial=self.filial, cliente=candidato, qtd_compras=18,
            ultima_compra=timezone.localdate() - datetime.timedelta(days=20),
            valor_medio='850.00', valor_total_periodo='15300.00', score=92,
            status=RecompraCliente.Status.VERMELHO,
            dias_restantes=-5,
            proxima_compra_prevista=timezone.localdate() - datetime.timedelta(days=5),
            frequencia=RecompraCliente.Frequencia.SEMANAL,
        )
        candidato.recompra = recompra
        candidato.distancia_m = 400
        proximos.return_value = [candidato]
        for numero in (801, 802):
            VendaPDV.objects.create(
                filial=self.filial, numero_venda=numero, cliente=candidato,
                usuario=self.usuario, status='finalizada', delivery=True,
                status_delivery='entregue', data_venda=timezone.now(),
            )

        resp = self.client.post(
            reverse('pdv:delivery_rota_oportunidades'),
            data=json.dumps({'paradas': [{'lat': -5.80, 'lng': -35.22}], 'raio_m': 3000}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        oportunidade = resp.json()['oportunidades'][0]
        self.assertEqual(oportunidade['nome'], 'Cliente Campeão')
        self.assertEqual(oportunidade['rfm'], 'R5 F5 M5')
        self.assertEqual(oportunidade['rfm_r'], 5)
        self.assertEqual(oportunidade['rfm_f'], 5)
        self.assertEqual(oportunidade['rfm_m'], 5)
        self.assertEqual(oportunidade['segmento_rfm'], 'Campeão')
        self.assertGreaterEqual(oportunidade['prioridade'], 90)
        self.assertEqual(oportunidade['desvio_km_estimado'], 0.8)
        self.assertEqual(oportunidade['momento_recompra'], 100)
        self.assertIn('atrasada há 5', oportunidade['motivo_momento'])
        self.assertTrue(oportunidade['cliente_delivery'])
        self.assertEqual(oportunidade['compras_delivery'], 2)

        prioridade_atrasada = oportunidade['prioridade']
        recompra.dias_restantes = 20
        recompra.proxima_compra_prevista = timezone.localdate() + datetime.timedelta(days=20)
        recompra.status = RecompraCliente.Status.VERDE
        resp_recente = self.client.post(
            reverse('pdv:delivery_rota_oportunidades'),
            data=json.dumps({'paradas': [{'lat': -5.80, 'lng': -35.22}], 'raio_m': 3000}),
            content_type='application/json',
        )
        oportunidade_recente = resp_recente.json()['oportunidades'][0]
        self.assertLess(oportunidade_recente['prioridade'], prioridade_atrasada)
        self.assertIn('daqui a 20', oportunidade_recente['motivo_momento'])

        rota = RotaDeliveryPublica.objects.create(
            filial=self.filial, rfm_r5_dias=10, rfm_r4_dias=30,
            rfm_r3_dias=60, rfm_r2_dias=120,
            rfm_configuracao={
                'f_automatico': False,
                'f5_compras': 20, 'f4_compras': 10, 'f3_compras': 5, 'f2_compras': 2,
                'm_automatico': False,
                'm5_valor': 20000, 'm4_valor': 10000,
                'm3_valor': 5000, 'm2_valor': 1000,
            },
        )
        resp_configurada = self.client.post(
            reverse('pdv:delivery_rota_oportunidades'),
            data=json.dumps({'paradas': [{'lat': -5.80, 'lng': -35.22}], 'raio_m': 3000}),
            content_type='application/json',
        )
        configurada = resp_configurada.json()['oportunidades'][0]
        self.assertEqual(configurada['rfm_r'], 4)
        self.assertEqual(configurada['rfm_f'], 4)
        self.assertEqual(configurada['rfm_m'], 4)
        self.assertEqual(resp_configurada.json()['configuracao_rfm']['r5_dias'], 10)

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

    def test_nao_publica_pedido_com_ponto_automatico_antigo(self):
        venda = self._venda(numero=403)
        endereco = {
            'cep': '59158155', 'rua': 'Avenida Antártida', 'numero': '501',
            'bairro': 'Parque das Nações', 'cidade': 'Parnamirim', 'uf': 'RN',
            '_latitude': -5.65, '_longitude': -35.30,
            '_geo_origem': 'arcgis', '_geo_lookup_version': 4,
        }
        endereco['_geo_hash'] = _delivery_rota_endereco_hash(endereco)
        venda.endereco_entrega = endereco
        venda.save(update_fields=['endereco_entrega'])

        resp = self._publicar([venda])

        self.assertEqual(resp.status_code, 400)
        self.assertIn('#403', resp.json()['erro'])
        self.assertFalse(RotaDelivery.objects.filter(filial=self.filial, pedido_ids=[venda.pk]).exists())

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
        self.assertContains(resp, 'Ver comprovante')
        self.assertContains(resp, 'data-receipt-overlay data-receipt-title', count=2)
        self.assertContains(resp, 'receiptOverlayFrame')
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

    def test_painel_agrupa_pedidos_do_mesmo_condominio_sem_repetir_aviso(self):
        numerado = self._venda(numero=413)
        sem_numero = self._venda(numero=414)
        outro_local = self._venda(numero=415)
        endereco = {'cep': '59158155', 'rua': 'Avenida Antartida',
                    'bairro': 'Parque das Nacoes', 'cidade': 'Parnamirim', 'uf': 'RN'}
        numerado.endereco_entrega = {**endereco, 'numero': '501',
                                     'complemento': 'Condominio Novo Leblon - Casa R2'}
        sem_numero.endereco_entrega = {**endereco, 'numero': '',
                                      'complemento': 'Condominio Novo Leblon - Casa A N 25'}
        for pedido in (numerado, sem_numero):
            pedido.endereco_entrega.update({
                '_latitude': -5.92937, '_longitude': -35.21082,
                '_geo_origem': 'manual',
                '_geo_hash': _delivery_rota_endereco_hash(pedido.endereco_entrega),
            })
        numerado.save(update_fields=['endereco_entrega'])
        sem_numero.save(update_fields=['endereco_entrega'])
        publicacao = self._publicar([numerado, outro_local, sem_numero])
        self.assertEqual(publicacao.status_code, 200, publicacao.content)
        url = publicacao.json()['url']
        self.client.logout()

        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context['locais']), 2)
        self.assertEqual(resp.context['locais'][0]['total'], 2)
        self.assertEqual(
            [pedido['venda'].pk for pedido in resp.context['locais'][0]['pedidos']],
            [numerado.pk, sem_numero.pk],
        )
        self.assertContains(resp, '2 pedidos neste local', count=1)
        self.assertContains(resp, ' data-location data-route-order=', count=2)
        self.assertNotContains(resp, 'Mesmo local:')
        self.assertContains(resp, 'Marcar pedido #413 como entregue')
        self.assertContains(resp, 'Marcar pedido #414 como entregue')
        self.assertContains(resp, 'Casa A N 25')

        rota = RotaDeliveryPublica.objects.get(filial=self.filial)
        self.client.post(
            reverse('delivery_publico:concluir', args=[rota.token, numerado.pk]),
            HTTP_ACCEPT='application/json',
        )
        parcial = self.client.get(url)
        self.assertEqual(parcial.context['locais'][0]['concluidos'], 1)
        self.assertContains(parcial, '1/2 concluídos')
        self.assertContains(parcial, 'Marcar pedido #414 como entregue')

    def test_painel_publico_nao_marca_venda_pendente_como_paga(self):
        venda = self._venda(numero=411)
        venda.status = 'aberta'
        venda.save(update_fields=['status'])
        url = self._publicar([venda]).json()['url']
        self.client.logout()

        resp = self.client.get(url)

        self.assertContains(resp, 'Venda com pagamento pendente.')
        self.assertContains(resp, 'COBRAR NA ENTREGA')
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
        url_painel = self._publicar([venda]).json()['url']
        rota = RotaDeliveryPublica.objects.get(filial=self.filial)
        self.client.logout()

        resp = self.client.post(
            reverse('delivery_publico:concluir', args=[rota.token, venda.pk]),
            HTTP_ACCEPT='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], VendaPDV.StatusDelivery.ENTREGUE)
        self.assertIn('Finalizado pelo motoboy', resp.json()['conclusao_texto'])
        self.assertEqual(venda.status_delivery, VendaPDV.StatusDelivery.ENTREGUE)
        rota_operacional = RotaDelivery.objects.get(token=rota.token)
        self.assertEqual(
            rota_operacional.conclusoes_pedidos[str(venda.pk)]['origem'],
            'motoboy',
        )
        self.assertContains(self.client.get(url_painel), 'Finalizado pelo motoboy')

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
        rota_operacional = RotaDelivery.objects.get(token=rota.token)
        self.assertEqual(rota_operacional.conclusoes_pedidos, {})

    def test_motoboy_pode_desmarcar_pedido_concluido_na_rota(self):
        venda = self._venda(numero=426)
        url = self._publicar([venda]).json()['url']
        rota = RotaDeliveryPublica.objects.get(filial=self.filial)
        self.client.post(
            reverse('delivery_publico:concluir', args=[rota.token, venda.pk]),
            HTTP_ACCEPT='application/json',
        )
        self.client.logout()

        resposta = self.client.post(
            reverse('delivery_publico:concluir', args=[rota.token, venda.pk]),
            data=json.dumps({'entregue': False}),
            content_type='application/json', HTTP_ACCEPT='application/json',
        )

        venda.refresh_from_db()
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(venda.status_delivery, VendaPDV.StatusDelivery.NOVO)
        painel = self.client.get(url)
        pedido = next(item for item in painel.context['pedidos'] if item['venda'].pk == venda.pk)
        self.assertTrue(pedido['pode_alterar'])

    def test_motoboy_abre_comprovante_da_venda_da_rota(self):
        venda = self._venda(numero=427)
        self._publicar([venda])
        rota = RotaDeliveryPublica.objects.get(filial=self.filial)
        fora_da_rota = self._venda(numero=428)
        comprovante_interno = self.client.get(reverse(
            'pdv:comprovante_venda', args=[venda.pk],
        ))
        self.client.logout()

        comprovante = self.client.get(reverse(
            'delivery_publico:comprovante', args=[rota.token, venda.pk],
        ))
        pdf = self.client.get(reverse(
            'delivery_publico:comprovante_pdf', args=[rota.token, venda.pk],
        ))
        negado = self.client.get(reverse(
            'delivery_publico:comprovante', args=[rota.token, fora_da_rota.pk],
        ))

        self.assertEqual(comprovante.status_code, 200)
        self.assertContains(comprovante, 'Este comprovante não é um documento fiscal.')
        self.assertEqual(comprovante['X-Frame-Options'], 'SAMEORIGIN')
        self.assertEqual(comprovante_interno['X-Frame-Options'], 'SAMEORIGIN')
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertEqual(negado.status_code, 404)

    def test_painel_joga_concluidos_para_o_final_sem_trocar_numero_da_rota(self):
        concluida = self._venda(numero=424)
        pendente = self._venda(numero=425)
        url = self._publicar([concluida, pendente]).json()['url']
        rota = RotaDeliveryPublica.objects.get(filial=self.filial)
        self.client.post(
            reverse('delivery_publico:concluir', args=[rota.token, concluida.pk]),
            HTTP_ACCEPT='application/json',
        )
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
        self.assertEqual(parametros['travelmode'], ['two-wheeler'])
        self.assertEqual(parametros['origin'], ['-5.79,-35.21'])
        self.assertEqual(parametros['destination'], ['-5.79,-35.21'])
        self.assertIn('-5.8,-35.22', parametros['waypoints'][0])

    def test_google_maps_divide_rota_grande_em_trechos_visiveis(self):
        paradas = [
            {
                'ordem': indice, 'chave': f'pedido:{indice}',
                'maps_location': f'-5.{indice:02d},-35.{indice:02d}',
            }
            for indice in range(1, 21)
        ]

        trechos = _urls_google_maps_em_trechos(self.filial, paradas)

        self.assertEqual(len(trechos), 3)
        self.assertEqual([(item['local_inicio'], item['local_fim']) for item in trechos], [(1, 9), (10, 18), (19, 20)])
        primeiro = parse_qs(urlparse(trechos[0]['url']).query)
        segundo = parse_qs(urlparse(trechos[1]['url']).query)
        ultimo = parse_qs(urlparse(trechos[2]['url']).query)
        self.assertEqual(primeiro['origin'], ['-5.79,-35.21'])
        self.assertEqual(primeiro['destination'], ['-5.09,-35.09'])
        self.assertEqual(segundo['origin'], ['-5.09,-35.09'])
        self.assertEqual(ultimo['destination'], ['-5.79,-35.21'])
        self.assertEqual(len(primeiro['waypoints'][0].split('|')), 8)
        self.assertEqual(len(ultimo['waypoints'][0].split('|')), 2)

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
