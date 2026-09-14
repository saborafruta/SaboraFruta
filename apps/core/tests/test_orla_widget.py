import base64
import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace

from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.core.context_processors import orla_widget_context


def _decode_base64url(value):
    padding = '=' * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


@override_settings(
    ORLA_WIDGET_ENABLED=True,
    ORLA_WIDGET_URL='https://orla.example.com/',
    ORLA_WIDGET_PUBLIC_KEY='pk_ited',
    ORLA_WIDGET_SIGNING_SECRET='segredo-de-teste',
    ORLA_WIDGET_ALLOWED_HOSTS=['testserver'],
)
class OrlaWidgetContextTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get('/compras/pedidos/?pagina=2')
        empresa = SimpleNamespace(
            pk=10,
            nome_fantasia='Empresa Teste',
            razao_social='Empresa Teste Ltda.',
        )
        self.request.filial_ativa = SimpleNamespace(
            pk=20,
            nome_fantasia='Filial Centro',
            razao_social='Filial Centro Ltda.',
            empresa=empresa,
        )
        self.request.user = SimpleNamespace(
            is_authenticated=True,
            pk=7,
            nome='José da Silva',
            email='JOSE@example.com',
            empresa=empresa,
        )

    def test_assina_identidade_e_contexto_sem_expor_segredo(self):
        widget = orla_widget_context(self.request)['orla_widget']

        self.assertTrue(widget['enabled'])
        self.assertEqual(
            widget['script_url'],
            'https://orla.example.com/public/widget/v1/orla-widget.js',
        )
        encoded, signature = widget['user_token'].split('.')
        expected_signature = base64.urlsafe_b64encode(
            hmac.new(
                b'segredo-de-teste', encoded.encode('ascii'), hashlib.sha256,
            ).digest()
        ).rstrip(b'=').decode('ascii')
        self.assertTrue(hmac.compare_digest(signature, expected_signature))

        payload = json.loads(_decode_base64url(encoded).decode('utf-8'))
        self.assertEqual(payload['sub'], 'ited:user:jose@example.com')
        self.assertEqual(payload['name'], 'José da Silva')
        self.assertEqual(payload['aud'], 'pk_ited')
        self.assertEqual(payload['metadata']['empresa'], 'Empresa Teste')
        self.assertEqual(payload['metadata']['filial'], 'Filial Centro')
        self.assertNotIn('segredo-de-teste', json.dumps(widget))

        context = json.loads(widget['context_json'])
        self.assertEqual(context['system'], 'iTED')
        self.assertEqual(context['page'], '/compras/pedidos/')

    def test_nao_renderiza_para_visitante(self):
        self.request.user = SimpleNamespace(is_authenticated=False)

        widget = orla_widget_context(self.request)['orla_widget']

        self.assertFalse(widget['enabled'])

    @override_settings(ORLA_WIDGET_SIGNING_SECRET='')
    def test_sem_segredo_usa_identidade_anonima_do_widget(self):
        widget = orla_widget_context(self.request)['orla_widget']

        self.assertTrue(widget['enabled'])
        self.assertEqual(widget['user_token'], '')

    @override_settings(ORLA_WIDGET_ALLOWED_HOSTS=['ited.app.br'])
    def test_nao_renderiza_em_outra_instalacao(self):
        widget = orla_widget_context(self.request)['orla_widget']

        self.assertFalse(widget['enabled'])

    def test_template_global_carrega_widget_somente_quando_habilitado(self):
        source = Path('templates/_base.html').read_text(encoding='utf-8')

        self.assertIn('{% if orla_widget.enabled %}', source)
        self.assertIn('{% if orla_widget.user_token %}data-orla-user-token=', source)
        self.assertIn('data-orla-context="{{ orla_widget.context_json }}"', source)
