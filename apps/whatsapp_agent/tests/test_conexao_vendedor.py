import base64
import json
import shutil
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.cadastros.services.cliente_service import ClienteService
from apps.core.models import Empresa, Filial, PerfilAcesso, Permissao, Usuario
from apps.crm.services import CarteiraService
from apps.whatsapp_agent.forms import ConexaoWhatsAppVendedorForm
from apps.whatsapp_agent.models import (
    ConexaoWhatsAppVendedor,
    ConversaWhatsApp,
    MensagemWhatsApp,
)
from apps.whatsapp_agent.templatetags.whatsapp_ui import formatar_texto_whatsapp
from apps.whatsapp_agent.views import _vendedor_operacional_da_conexao


@override_settings(
    WHATSAPP_EVOLUTION_URL='https://evolution.example.com',
    WHATSAPP_EVOLUTION_API_KEY='chave-teste',
)
class ConexaoWhatsAppVendedorTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='ited-whatsapp-media-')
        self.media_override = override_settings(MEDIA_ROOT=self.media_root)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(shutil.rmtree, self.media_root, True)
        self.empresa = Empresa.objects.create(
            razao_social='Empresa WhatsApp CRM', cnpj='44555666000113',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Matriz WhatsApp CRM',
            nome_fantasia='Matriz WhatsApp CRM', cnpj='44555666000113', uf='RN',
            is_matriz=True,
        )
        self.perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Vendedor WhatsApp', ativo=True,
        )
        Permissao.objects.create(
            perfil=self.perfil, modulo='crm', pode_ver=True,
            pode_criar=True, pode_editar=True,
        )
        self.vendedor = Usuario.objects.create_user(
            email='whatsapp@example.com', nome='Vendedor WhatsApp', password='teste1234',
            empresa=self.empresa, filial=self.filial, perfil=self.perfil,
        )
        perfil_gestor = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Gestor WhatsApp', is_admin=True, ativo=True,
        )
        self.gestor = Usuario.objects.create_user(
            email='gestor-whatsapp@example.com', nome='Gestor WhatsApp',
            password='teste1234', empresa=self.empresa, filial=self.filial,
            perfil=perfil_gestor,
        )

    def test_credenciais_meta_sao_armazenadas_criptografadas(self):
        conexao = ConexaoWhatsAppVendedor(
            empresa=self.empresa, filial_padrao=self.filial, vendedor=self.vendedor,
            meta_phone_number_id='123456789',
        )
        conexao.definir_meta_access_token('token-secreto')
        conexao.definir_meta_verify_token('verify-secreto')
        conexao.definir_meta_app_secret('app-secreto')
        conexao.full_clean()
        conexao.save()

        self.assertNotIn('token-secreto', conexao.meta_access_token_criptografado)
        self.assertEqual(conexao.obter_meta_access_token(), 'token-secreto')
        self.assertEqual(conexao.obter_meta_verify_token(), 'verify-secreto')
        self.assertEqual(conexao.obter_meta_app_secret(), 'app-secreto')
        self.assertTrue(conexao.somente_atendimento_humano)

    def test_resolve_vendedor_sem_carregar_relacoes_no_banco_operacional(self):
        conexao = self._conexao_qr()

        vendedor = _vendedor_operacional_da_conexao(
            SimpleNamespace(pk=conexao.pk),
        )

        self.assertEqual(vendedor, self.vendedor)

    def test_gestor_cadastra_numero_e_segue_para_qr_code(self):
        self.client.force_login(self.gestor)

        resposta = self.client.post(
            reverse('whatsapp_agent:crm-conexao-nova'),
            {
                'vendedor': self.vendedor.pk,
                'filial_padrao': self.filial.pk,
                'numero_conectado': '5584999994444',
                'nome_conectado': 'Vendas WhatsApp',
            },
        )

        conexao = ConexaoWhatsAppVendedor.objects.get(
            numero_conectado='5584999994444',
        )
        self.assertRedirects(
            resposta,
            reverse(
                'whatsapp_agent:crm-conexao-onboarding',
                kwargs={'pk': conexao.pk},
            ),
        )
        self.assertEqual(conexao.status, ConexaoWhatsAppVendedor.Status.PENDENTE)
        self.assertTrue(conexao.somente_atendimento_humano)
        self.assertTrue(conexao.ativo)
        self.assertTrue(conexao.instancia.startswith('ited-crm-'))

    def test_cadastro_nao_expoe_campos_tecnicos_da_meta(self):
        self.client.force_login(self.gestor)

        resposta = self.client.get(reverse('whatsapp_agent:crm-conexao-nova'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Próxima etapa: ler o QR Code')
        self.assertNotContains(resposta, 'Conta oficial da Meta')
        self.assertNotContains(resposta, 'ID do número na Meta')
        self.assertNotContains(resposta, 'Credenciais técnicas')

    @patch('django.db.router.db_for_read', return_value='tenant_operacional')
    def test_formulario_valida_fks_no_banco_gerencial(self, db_for_read):
        form = ConexaoWhatsAppVendedorForm(
            data={
                'vendedor': self.vendedor.pk,
                'filial_padrao': self.filial.pk,
                'numero_conectado': '5584999995555',
                'nome_conectado': 'Vendas Gerencial',
            },
            empresa=self.empresa,
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        conexao = form.save()
        self.assertEqual(conexao._state.db, 'default')
        self.assertEqual(conexao.vendedor.email, self.vendedor.email)

    @patch('apps.whatsapp_agent.views.EvolutionClient.configurar_webhook')
    @patch('apps.whatsapp_agent.views.EvolutionClient.estado', return_value='close')
    def test_vendedor_visualiza_seu_numero_mas_nao_abre_cadastro(
        self, estado, configurar_webhook,
    ):
        conexao = self._conexao_qr()
        self.client.force_login(self.vendedor)

        lista = self.client.get(reverse('whatsapp_agent:crm-conexoes'))
        cadastro = self.client.get(reverse('whatsapp_agent:crm-conexao-nova'))
        onboarding = self.client.get(
            reverse(
                'whatsapp_agent:crm-conexao-onboarding',
                kwargs={'pk': conexao.pk},
            ),
        )

        self.assertEqual(lista.status_code, 200)
        self.assertContains(lista, '5584999990000')
        self.assertRedirects(cadastro, reverse('whatsapp_agent:crm-conexoes'))
        self.assertEqual(onboarding.status_code, 200)
        self.assertContains(onboarding, 'Leia como no WhatsApp Web')
        self.assertNotContains(onboarding, 'Conta oficial da Meta')

    @patch('apps.whatsapp_agent.views.EvolutionClient.conectar')
    @patch('apps.whatsapp_agent.views.EvolutionClient.estado', return_value='close')
    def test_status_gera_qr_code_para_o_vendedor(self, estado, conectar):
        conectar.return_value = {'code': 'conteudo-qr-vendedor'}
        conexao = self._conexao_qr()
        self.client.force_login(self.gestor)

        resposta = self.client.get(
            reverse(
                'whatsapp_agent:crm-conexao-status',
                kwargs={'pk': conexao.pk},
            ),
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(resposta.json()['conectado'])
        self.assertTrue(resposta.json()['qr_code'].startswith('data:image/png;base64,'))

    def test_conexao_recusa_vendedor_de_outra_empresa(self):
        outra = Empresa.objects.create(
            razao_social='Empresa Externa', cnpj='55666777000124',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        outra_filial = Filial.objects.create(
            empresa=outra, razao_social='Matriz Externa', nome_fantasia='Matriz Externa',
            cnpj='55666777000124', uf='RN', is_matriz=True,
        )
        outro_perfil = PerfilAcesso.objects.create(empresa=outra, nome='Vendedor', ativo=True)
        outro = Usuario.objects.create_user(
            email='externo@example.com', nome='Externo', password='teste1234',
            empresa=outra, filial=outra_filial, perfil=outro_perfil,
        )
        conexao = ConexaoWhatsAppVendedor(
            empresa=self.empresa, filial_padrao=self.filial, vendedor=outro,
        )
        with self.assertRaises(ValidationError):
            conexao.full_clean()

    def _conexao_qr(self):
        conexao = ConexaoWhatsAppVendedor(
            empresa=self.empresa,
            filial_padrao=self.filial,
            vendedor=self.vendedor,
            numero_conectado='5584999990000',
            status=ConexaoWhatsAppVendedor.Status.CONECTADO,
        )
        conexao.save()
        return conexao

    def _post_evolution(self, conexao, evento, dados):
        corpo = json.dumps({
            'event': evento,
            'instance': conexao.instancia,
            'data': dados,
        }).encode('utf-8')
        return self.client.post(
            reverse('whatsapp_agent:webhook', kwargs={'secret': conexao.webhook_secret}),
            data=corpo,
            content_type='application/json',
        )

    def test_webhook_qr_do_vendedor_registra_entrada(self):
        conexao = self._conexao_qr()
        url = reverse(
            'whatsapp_agent:webhook', kwargs={'secret': conexao.webhook_secret},
        )

        verificacao = self.client.get(url)
        evento = self._post_evolution(conexao, 'messages.upsert', {
            'key': {
                'id': 'evolution-entrada-1', 'fromMe': False,
                'remoteJid': '5584999991111@s.whatsapp.net',
            },
            'pushName': 'Cliente da carteira',
            'message': {'conversation': 'Quero um orçamento'},
        })

        self.assertEqual(verificacao.status_code, 405)
        self.assertEqual(evento.status_code, 200)
        self.assertEqual(evento.json()['resultado'], 'recebido')
        conversa = ConversaWhatsApp.objects.get()
        self.assertEqual(conversa.conexao_vendedor, conexao)
        self.assertIsNone(conversa.configuracao)
        self.assertTrue(conversa.fora_carteira)
        self.assertTrue(conversa.janela_atendimento_ativa)
        mensagem = conversa.mensagens.get()
        self.assertEqual(mensagem.origem, MensagemWhatsApp.Origem.CLIENTE)
        self.assertEqual(mensagem.texto_original, 'Quero um orçamento')

    @patch('apps.whatsapp_agent.webhook.EvolutionClient.obter_midia_base64')
    def test_foto_e_audio_recebidos_ficam_disponiveis_no_crm(self, obter_midia):
        obter_midia.side_effect = [
            {
                'base64': base64.b64encode(b'conteudo-imagem').decode('ascii'),
                'mimetype': 'image/jpeg',
            },
            {
                'base64': base64.b64encode(b'conteudo-audio').decode('ascii'),
                'mimetype': 'audio/ogg',
            },
        ]
        conexao = self._conexao_qr()
        eventos = [
            ('midia-foto-1', {
                'imageMessage': {
                    'mimetype': 'image/jpeg', 'caption': 'Foto do produto',
                    'fileName': 'produto.jpg',
                },
            }),
            ('midia-audio-1', {
                'audioMessage': {'mimetype': 'audio/ogg', 'ptt': True},
            }),
        ]
        for identificador, message in eventos:
            resposta = self._post_evolution(conexao, 'messages.upsert', {
                'key': {
                    'id': identificador, 'fromMe': False,
                    'remoteJid': '5584999991111@s.whatsapp.net',
                },
                'pushName': 'Cliente com mídia',
                'message': message,
            })
            self.assertEqual(resposta.status_code, 200)
            self.assertEqual(resposta.json()['resultado'], 'recebido')

        foto = MensagemWhatsApp.objects.get(identificador_externo='midia-foto-1')
        audio = MensagemWhatsApp.objects.get(identificador_externo='midia-audio-1')
        self.assertEqual(foto.tipo, 'imagem')
        self.assertEqual(foto.arquivo_nome_original, 'produto.jpg')
        self.assertEqual(foto.arquivo_mimetype, 'image/jpeg')
        self.assertEqual(audio.tipo, 'audio')
        self.assertTrue(audio.arquivo)

        foto.conversa.fora_carteira = False
        foto.conversa.save(update_fields=['fora_carteira', 'updated_at'])
        self.client.force_login(self.vendedor)
        arquivo_foto = self.client.get(reverse(
            'whatsapp_agent:crm-midia', args=[foto.conversa_id, foto.pk],
        ))
        arquivo_audio = self.client.get(reverse(
            'whatsapp_agent:crm-midia', args=[audio.conversa_id, audio.pk],
        ))
        self.assertEqual(arquivo_foto.status_code, 200)
        self.assertEqual(b''.join(arquivo_foto.streaming_content), b'conteudo-imagem')
        self.assertEqual(arquivo_audio.status_code, 200)
        self.assertEqual(b''.join(arquivo_audio.streaming_content), b'conteudo-audio')

        outro_vendedor = Usuario.objects.create_user(
            email='midia-alheia@example.com', nome='Outro vendedor',
            password='teste1234', empresa=self.empresa, filial=self.filial,
            perfil=self.perfil,
        )
        self.client.force_login(outro_vendedor)
        bloqueado = self.client.get(reverse(
            'whatsapp_agent:crm-midia', args=[foto.conversa_id, foto.pk],
        ))
        self.assertEqual(bloqueado.status_code, 404)

        exclusao = self._post_evolution(conexao, 'messages.delete', {
            'key': {'id': 'midia-foto-1'},
        })
        foto.refresh_from_db()
        self.assertEqual(exclusao.json()['resultado'], 'mensagem_apagada')
        self.assertEqual(foto.status, 'apagada')
        self.assertTrue(foto.arquivo)

    def test_eco_do_celular_e_exclusao_preservam_historico(self):
        conexao = self._conexao_qr()
        eco = self._post_evolution(conexao, 'messages.upsert', {
            'key': {
                'id': 'evolution-saida-1', 'fromMe': True,
                'remoteJid': '5584999991111@s.whatsapp.net',
            },
            'message': {'conversation': 'Segue a proposta'},
        })

        self.assertEqual(eco.status_code, 200)
        mensagem = MensagemWhatsApp.objects.get(identificador_externo='evolution-saida-1')
        self.assertEqual(mensagem.origem, MensagemWhatsApp.Origem.CELULAR)
        self.assertEqual(mensagem.autor_email, self.vendedor.email)
        self.assertEqual(mensagem.texto_original, 'Segue a proposta')

        exclusao = self._post_evolution(conexao, 'messages.delete', {
            'key': {'id': 'evolution-saida-1'},
        })
        self.assertEqual(exclusao.status_code, 200)
        mensagem.refresh_from_db()
        self.assertEqual(mensagem.status, 'apagada')
        self.assertIsNotNone(mensagem.apagada_em)
        self.assertEqual(mensagem.texto, 'Segue a proposta')
        self.assertEqual(mensagem.texto_original, 'Segue a proposta')

    def test_lid_nome_e_celular_sem_nono_digito_reutilizam_conversa(self):
        conexao = self._conexao_qr()
        conversa = ConversaWhatsApp.objects.create(
            conexao_vendedor=conexao, filial=self.filial,
            remote_jid='5584999991111@s.whatsapp.net', telefone='5584999991111',
            nome_contato='Nome cadastrado no iTED', fora_carteira=False,
        )

        resposta = self._post_evolution(conexao, 'messages.upsert', {
            'key': {
                'id': 'evolution-lid-1', 'fromMe': False,
                'remoteJid': '123456789012345@lid',
                'remoteJidAlt': '558499991111@s.whatsapp.net',
            },
            'pushName': 'Nome diferente no WhatsApp',
            'message': {'conversation': 'Resposta pelo celular'},
        })

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(ConversaWhatsApp.objects.filter(ativa=True).count(), 1)
        conversa.refresh_from_db()
        self.assertEqual(conversa.nome_contato, 'Nome cadastrado no iTED')
        self.assertTrue(conversa.mensagens.filter(texto='Resposta pelo celular').exists())

    def test_evento_de_status_marca_mensagem_como_entregue_e_lida(self):
        conexao = self._conexao_qr()
        conversa = ConversaWhatsApp.objects.create(
            conexao_vendedor=conexao, filial=self.filial,
            remote_jid='5584999991111@s.whatsapp.net', telefone='5584999991111',
            nome_contato='Cliente CRM', fora_carteira=False,
        )
        mensagem = MensagemWhatsApp.objects.create(
            conversa=conversa, direcao='saida', origem='crm', texto='Olá',
            texto_original='Olá', identificador_externo='status-1', status='enviada',
        )

        entregue = self._post_evolution(conexao, 'messages.update', {
            'key': {'id': 'status-1'}, 'update': {'status': 'DELIVERY_ACK'},
        })
        mensagem.refresh_from_db()
        self.assertEqual(entregue.json()['resultado'], 'status_atualizado')
        self.assertEqual(mensagem.status, 'entregue')

        lida = self._post_evolution(conexao, 'messages.update', {
            'key': {'id': 'status-1'}, 'update': {'status': 4},
        })
        mensagem.refresh_from_db()
        self.assertEqual(lida.json()['resultado'], 'status_atualizado')
        self.assertEqual(mensagem.status, 'lida')

    def test_formatacao_whatsapp_renderiza_sem_permitir_html(self):
        html = str(formatar_texto_whatsapp(
            '*Orçamento* <script>alert(1)</script> https://ited.app.br/orcamento/abc',
        ))

        self.assertIn('<strong>Orçamento</strong>', html)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('href="https://ited.app.br/orcamento/abc"', html)

    @patch('apps.whatsapp_agent.views.EvolutionClient.enviar_texto')
    def test_vendedor_envia_pelo_crm_e_mensagem_identifica_a_origem(
        self, enviar_texto,
    ):
        enviar_texto.return_value = {'key': {'id': 'evolution.crm-1'}}
        conexao = self._conexao_qr()
        conversa = ConversaWhatsApp.objects.create(
            conexao_vendedor=conexao,
            filial=self.filial,
            remote_jid='5584999991111@s.whatsapp.net',
            telefone='5584999991111',
            nome_contato='Cliente CRM',
            fora_carteira=False,
            ultima_mensagem_cliente_em=timezone.now(),
        )
        self.client.force_login(self.vendedor)

        inbox = self.client.get(
            reverse('whatsapp_agent:crm-inbox-detail', kwargs={'pk': conversa.pk}),
        )

        resposta = self.client.post(
            reverse('whatsapp_agent:crm-enviar', kwargs={'pk': conversa.pk}),
            {'mensagem': 'Orçamento enviado pelo iTED'},
        )

        self.assertEqual(inbox.status_code, 200)
        self.assertContains(inbox, 'Cliente CRM')
        self.assertRedirects(
            resposta,
            reverse('whatsapp_agent:crm-inbox-detail', kwargs={'pk': conversa.pk}),
        )
        enviar_texto.assert_called_once_with(
            '5584999991111', 'Orçamento enviado pelo iTED',
        )
        mensagem = MensagemWhatsApp.objects.get()
        self.assertEqual(mensagem.origem, MensagemWhatsApp.Origem.CRM)
        self.assertEqual(mensagem.autor_email, self.vendedor.email)
        self.assertEqual(mensagem.identificador_externo, 'evolution.crm-1')

    @patch('apps.whatsapp_agent.views.EvolutionClient.enviar_texto')
    def test_envio_ajax_retorna_mensagem_sem_redirecionar(self, enviar_texto):
        enviar_texto.return_value = {'key': {'id': 'evolution.ajax-1'}}
        conexao = self._conexao_qr()
        conversa = ConversaWhatsApp.objects.create(
            conexao_vendedor=conexao, filial=self.filial,
            remote_jid='5584999991212@s.whatsapp.net', telefone='5584999991212',
            nome_contato='Cliente em tempo real', fora_carteira=False,
        )
        self.client.force_login(self.vendedor)

        resposta = self.client.post(
            reverse('whatsapp_agent:crm-enviar', args=[conversa.pk]),
            {'mensagem': 'Mensagem sem atualizar a página'},
            HTTP_ACCEPT='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertTrue(resposta.json()['ok'])
        self.assertEqual(
            resposta.json()['mensagem']['texto'],
            'Mensagem sem atualizar a página',
        )
        self.assertEqual(resposta.json()['mensagem']['status'], 'enviada')

    @patch('apps.whatsapp_agent.views.EvolutionClient.enviar_midia')
    def test_vendedor_envia_imagem_com_legenda_sem_recarregar(self, enviar_midia):
        enviar_midia.return_value = {'key': {'id': 'evolution.imagem-1'}}
        conexao = self._conexao_qr()
        conversa = ConversaWhatsApp.objects.create(
            conexao_vendedor=conexao, filial=self.filial,
            remote_jid='5584999991414@s.whatsapp.net', telefone='5584999991414',
            nome_contato='Cliente da imagem', fora_carteira=False,
        )
        self.client.force_login(self.vendedor)
        imagem = SimpleUploadedFile(
            'produto.png', b'png-de-teste', content_type='image/png',
        )

        resposta = self.client.post(
            reverse('whatsapp_agent:crm-enviar', args=[conversa.pk]),
            {'mensagem': 'Veja este produto', 'arquivo': imagem},
            HTTP_ACCEPT='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(resposta.status_code, 200, resposta.content)
        mensagem = MensagemWhatsApp.objects.get()
        self.assertEqual(mensagem.tipo, 'imagem')
        self.assertEqual(mensagem.texto, 'Veja este produto')
        self.assertEqual(mensagem.arquivo_nome_original, 'produto.png')
        self.assertTrue(mensagem.arquivo)
        self.assertEqual(resposta.json()['mensagem']['arquivo']['tipo'], 'imagem')
        enviar_midia.assert_called_once_with(
            '5584999991414', b'png-de-teste', 'produto.png', 'image/png',
            tipo='imagem', legenda='Veja este produto',
        )

    @patch('apps.whatsapp_agent.views.EvolutionClient.enviar_midia')
    def test_vendedor_envia_documento_sem_exigir_texto(self, enviar_midia):
        enviar_midia.return_value = {'key': {'id': 'evolution.documento-1'}}
        conexao = self._conexao_qr()
        conversa = ConversaWhatsApp.objects.create(
            conexao_vendedor=conexao, filial=self.filial,
            remote_jid='5584999991616@s.whatsapp.net', telefone='5584999991616',
            nome_contato='Cliente do documento', fora_carteira=False,
        )
        self.client.force_login(self.vendedor)

        resposta = self.client.post(
            reverse('whatsapp_agent:crm-enviar', args=[conversa.pk]),
            {'mensagem': '', 'arquivo': SimpleUploadedFile(
                'catalogo.pdf', b'%PDF-teste', content_type='application/pdf',
            )},
            HTTP_ACCEPT='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(resposta.status_code, 200, resposta.content)
        mensagem = MensagemWhatsApp.objects.get()
        self.assertEqual(mensagem.tipo, 'documento')
        self.assertEqual(mensagem.texto, '')
        self.assertEqual(mensagem.arquivo_nome_original, 'catalogo.pdf')
        enviar_midia.assert_called_once_with(
            '5584999991616', b'%PDF-teste', 'catalogo.pdf', 'application/pdf',
            tipo='documento', legenda='',
        )

    def test_compositor_expoe_emoji_imagem_e_anexo(self):
        conexao = self._conexao_qr()
        conversa = ConversaWhatsApp.objects.create(
            conexao_vendedor=conexao, filial=self.filial,
            remote_jid='5584999991515@s.whatsapp.net', telefone='5584999991515',
            nome_contato='Cliente compositor', fora_carteira=False,
        )
        self.client.force_login(self.vendedor)

        pagina = self.client.get(reverse(
            'whatsapp_agent:crm-inbox-detail', args=[conversa.pk],
        ))

        self.assertContains(pagina, 'id="crm-emoji-button"')
        self.assertContains(pagina, 'id="crm-image-button"')
        self.assertContains(pagina, 'id="crm-attach-button"')
        self.assertContains(pagina, 'id="crm-file-input"')
        self.assertContains(pagina, 'crm-wa-media-slot')
        self.assertContains(pagina, 'schedule(delay=document.hidden?5000:700)')
        self.assertContains(pagina, 'class="crm-wa-compose"')
        self.assertContains(pagina, 'class="wa-btn primary crm-wa-page-action"')
        self.assertContains(
            pagina,
            '.crm-wa-sidebar,.crm-wa-chat{min-height:0;overflow:hidden}',
        )
        self.assertContains(
            pagina,
            '.crm-wa-app[data-selected-conversation] .crm-wa-sidebar{display:none}',
        )

    def test_sincronizacao_incremental_entrega_novas_mensagens(self):
        conexao = self._conexao_qr()
        conversa = ConversaWhatsApp.objects.create(
            conexao_vendedor=conexao, filial=self.filial,
            remote_jid='5584999991313@s.whatsapp.net', telefone='5584999991313',
            nome_contato='Cliente sincronizado', fora_carteira=False,
        )
        primeira = MensagemWhatsApp.objects.create(
            conversa=conversa, direcao='entrada', origem='cliente',
            texto='Primeira mensagem', texto_original='Primeira mensagem',
        )
        conversa.ultima_mensagem_em = primeira.created_at
        conversa.save(update_fields=['ultima_mensagem_em', 'updated_at'])
        self.client.force_login(self.vendedor)

        inicial = self.client.get(
            reverse('whatsapp_agent:crm-atualizacoes', args=[conversa.pk]),
        )
        desde = inicial.json()['sincronizado_em']
        MensagemWhatsApp.objects.create(
            conversa=conversa, direcao='entrada', origem='cliente',
            texto='Chegou agora', texto_original='Chegou agora',
        )
        conversa.ultima_mensagem_em = timezone.now()
        conversa.save(update_fields=['ultima_mensagem_em', 'updated_at'])
        incremental = self.client.get(
            reverse('whatsapp_agent:crm-atualizacoes', args=[conversa.pk]),
            {'desde': desde},
        )

        self.assertEqual(inicial.status_code, 200)
        self.assertEqual(inicial.json()['mensagens'][0]['texto'], 'Primeira mensagem')
        self.assertEqual(incremental.status_code, 200)
        self.assertEqual(
            [item['texto'] for item in incremental.json()['mensagens']],
            ['Chegou agora'],
        )
        self.assertEqual(incremental.json()['total_conversas'], 1)

    def test_contador_de_nao_lidas_e_individual_por_usuario(self):
        conexao = self._conexao_qr()
        conversa_aberta = ConversaWhatsApp.objects.create(
            conexao_vendedor=conexao, filial=self.filial,
            remote_jid='5584999991717@s.whatsapp.net', telefone='5584999991717',
            nome_contato='Cliente aberto', fora_carteira=False,
        )
        conversa_pendente = ConversaWhatsApp.objects.create(
            conexao_vendedor=conexao, filial=self.filial,
            remote_jid='5584999991818@s.whatsapp.net', telefone='5584999991818',
            nome_contato='Cliente pendente', fora_carteira=False,
        )
        for texto in ('Mensagem um', 'Mensagem dois'):
            mensagem = MensagemWhatsApp.objects.create(
                conversa=conversa_pendente, direcao='entrada', origem='cliente',
                texto=texto, texto_original=texto,
            )
            conversa_pendente.ultima_mensagem_em = mensagem.created_at
        conversa_pendente.save(update_fields=['ultima_mensagem_em', 'updated_at'])

        self.client.force_login(self.vendedor)
        vendedor = self.client.get(
            reverse('whatsapp_agent:crm-atualizacoes', args=[conversa_aberta.pk]),
        ).json()
        resumo_vendedor = {
            item['id']: item['nao_lidas'] for item in vendedor['conversas']
        }

        self.client.force_login(self.gestor)
        gestor = self.client.get(
            reverse('whatsapp_agent:crm-atualizacoes', args=[conversa_aberta.pk]),
        ).json()
        resumo_gestor = {
            item['id']: item['nao_lidas'] for item in gestor['conversas']
        }

        self.assertEqual(resumo_vendedor[conversa_aberta.pk], 0)
        self.assertEqual(resumo_vendedor[conversa_pendente.pk], 2)
        self.assertEqual(resumo_gestor[conversa_pendente.pk], 2)

        self.client.force_login(self.vendedor)
        self.client.get(
            reverse('whatsapp_agent:crm-atualizacoes', args=[conversa_pendente.pk]),
        )
        vendedor_depois = self.client.get(
            reverse('whatsapp_agent:crm-atualizacoes', args=[conversa_aberta.pk]),
        ).json()
        self.assertEqual(
            next(
                item['nao_lidas'] for item in vendedor_depois['conversas']
                if item['id'] == conversa_pendente.pk
            ),
            0,
        )

        self.client.force_login(self.gestor)
        gestor_depois = self.client.get(
            reverse('whatsapp_agent:crm-atualizacoes', args=[conversa_aberta.pk]),
        ).json()
        self.assertEqual(
            next(
                item['nao_lidas'] for item in gestor_depois['conversas']
                if item['id'] == conversa_pendente.pk
            ),
            2,
        )

    def test_nova_mensagem_da_conversa_aberta_nao_fica_nao_lida(self):
        conexao = self._conexao_qr()
        conversa = ConversaWhatsApp.objects.create(
            conexao_vendedor=conexao, filial=self.filial,
            remote_jid='5584999991919@s.whatsapp.net', telefone='5584999991919',
            nome_contato='Cliente em leitura', fora_carteira=False,
        )
        mensagem = MensagemWhatsApp.objects.create(
            conversa=conversa, direcao='entrada', origem='cliente',
            texto='Mensagem instantânea', texto_original='Mensagem instantânea',
        )
        conversa.ultima_mensagem_em = mensagem.created_at
        conversa.save(update_fields=['ultima_mensagem_em', 'updated_at'])
        self.client.force_login(self.vendedor)

        resposta = self.client.get(
            reverse('whatsapp_agent:crm-atualizacoes', args=[conversa.pk]),
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()['conversas'][0]['nao_lidas'], 0)

    @patch('apps.whatsapp_agent.gateway.MetaCloudClient.enviar_template')
    @patch('apps.whatsapp_agent.views.EvolutionClient.enviar_texto')
    def test_crm_permite_envio_humano_apos_24_horas_sem_usar_modelo(
        self, enviar_texto, enviar_template,
    ):
        enviar_texto.return_value = {'key': {'id': 'manual-apos-24h'}}
        conexao = self._conexao_qr()
        conversa = ConversaWhatsApp.objects.create(
            conexao_vendedor=conexao,
            filial=self.filial,
            remote_jid='5584999993333@s.whatsapp.net',
            telefone='5584999993333',
            nome_contato='Cliente com janela encerrada',
            fora_carteira=False,
            ultima_mensagem_cliente_em=timezone.now() - timedelta(hours=25),
        )
        self.client.force_login(self.vendedor)

        pagina = self.client.get(
            reverse('whatsapp_agent:crm-inbox-detail', kwargs={'pk': conversa.pk}),
        )
        resposta = self.client.post(
            reverse('whatsapp_agent:crm-enviar', kwargs={'pk': conversa.pk}),
            {'mensagem': 'Contato manual do vendedor'},
        )

        self.assertEqual(pagina.status_code, 200)
        self.assertContains(pagina, 'Envio somente manual')
        self.assertRedirects(
            resposta,
            reverse('whatsapp_agent:crm-inbox-detail', kwargs={'pk': conversa.pk}),
        )
        enviar_texto.assert_called_once_with(
            '5584999993333', 'Contato manual do vendedor',
        )
        enviar_template.assert_not_called()
        self.assertTrue(MensagemWhatsApp.objects.filter(origem='crm').exists())

    def test_nova_conversa_busca_apenas_na_carteira_do_vendedor(self):
        conexao = self._conexao_qr()
        cliente_carteira = ClienteService.criar(
            {
                'razao_social': 'Cliente da minha carteira',
                'cpf_cnpj': '12345678901', 'celular': '84999991111',
                'tipo_pessoa': 'F',
            },
            usuario=self.vendedor,
            filial=self.filial,
        )
        cliente_alheio = Cliente.objects.create(
            filial=self.filial, razao_social='Cliente de outro vendedor',
            cpf_cnpj='10987654321', celular='84999992222', tipo_pessoa='F', ativo=True,
        )
        outro_vendedor = Usuario.objects.create_user(
            email='outro-vendedor@example.com', nome='Outro vendedor', password='teste1234',
            empresa=self.empresa, filial=self.filial, perfil=self.perfil,
        )
        CarteiraService.atribuir(
            cliente=cliente_alheio, vendedor=outro_vendedor, atribuido_por=self.gestor,
        )
        self.client.force_login(self.vendedor)

        busca = self.client.get(
            reverse('whatsapp_agent:crm-nova-conversa-dados'),
            {'conexao': conexao.pk, 'q': 'Cliente'},
        )
        tentativa_alheia = self.client.post(
            reverse('whatsapp_agent:crm-nova-conversa-iniciar'),
            data=json.dumps({'conexao_id': conexao.pk, 'cliente_id': cliente_alheio.pk}),
            content_type='application/json',
        )
        inicio = self.client.post(
            reverse('whatsapp_agent:crm-nova-conversa-iniciar'),
            data=json.dumps({'conexao_id': conexao.pk, 'cliente_id': cliente_carteira.pk}),
            content_type='application/json',
        )

        self.assertEqual(busca.status_code, 200)
        self.assertEqual(
            [item['nome'] for item in busca.json()['clientes']],
            ['Cliente da minha carteira'],
        )
        self.assertEqual(tentativa_alheia.status_code, 403)
        self.assertEqual(inicio.status_code, 200, inicio.content)
        conversa = ConversaWhatsApp.objects.get(nome_contato='Cliente da minha carteira')
        self.assertEqual(conversa.conexao_vendedor, conexao)
        self.assertFalse(conversa.fora_carteira)

    def test_vendedor_nao_acessa_conversa_fora_da_carteira(self):
        conexao = self._conexao_qr()
        conversa = ConversaWhatsApp.objects.create(
            conexao_vendedor=conexao,
            filial=self.filial,
            remote_jid='5584999992222@s.whatsapp.net',
            telefone='5584999992222',
            fora_carteira=True,
        )
        self.client.force_login(self.vendedor)

        resposta = self.client.get(
            reverse('whatsapp_agent:crm-inbox-detail', kwargs={'pk': conversa.pk}),
        )
        atualizacoes = self.client.get(
            reverse('whatsapp_agent:crm-atualizacoes', kwargs={'pk': conversa.pk}),
        )

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(atualizacoes.status_code, 404)

        self.client.force_login(self.gestor)
        resposta_gestor = self.client.get(
            reverse('whatsapp_agent:crm-inbox-detail', kwargs={'pk': conversa.pk}),
        )
        self.assertEqual(resposta_gestor.status_code, 200)
