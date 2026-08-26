from pathlib import Path

from django.test import SimpleTestCase


class AjusteRapidoEstoqueTemplateTests(SimpleTestCase):
    def setUp(self):
        self.template = Path(
            'apps/estoque/templates/estoque/estoque/ajuste_rapido.html'
        ).read_text(encoding='utf-8')

    def test_ajax_save_reads_current_csrf_cookie(self):
        self.assertIn("function csrfToken()", self.template)
        self.assertIn("getCookie('csrftoken')", self.template)
        self.assertIn("'X-CSRFToken': csrfToken()", self.template)
        self.assertNotIn("const csrf = document.querySelector('[data-csrf-token]')", self.template)

    def test_camera_has_ios_safe_fallbacks(self):
        self.assertIn('capture="environment"', self.template)
        self.assertIn('data-camera-file', self.template)
        self.assertIn('https://cdn.jsdelivr.net/npm/html5-qrcode@2.3.8/html5-qrcode.min.js', self.template)
        self.assertIn('function preferredCameraConfig()', self.template)
        self.assertIn('scanner.scanFile(file, true)', self.template)
        self.assertNotIn('aspectRatio: 1.7778', self.template)
        self.assertNotIn('Nao foi possivel abrir a camera. Autorize o acesso', self.template)
