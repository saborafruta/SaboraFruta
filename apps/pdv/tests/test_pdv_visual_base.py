from pathlib import Path

from django.template.loader import get_template
from django.test import SimpleTestCase


class PDVVisualBaseTests(SimpleTestCase):
    def test_tema_claro_tem_paleta_de_pagamento_propria(self):
        template = (Path(__file__).resolve().parents[1] / 'templates/pdv/home.html').read_text(encoding='utf-8')
        claro = template.split('html.tema-claro body {', 1)[1].split('}', 1)[0]
        self.assertIn('--pdv-bg:#ffffff;', claro)
        self.assertIn('--pdv-pgto-pix-bg:#ccfbf1;', claro)
        self.assertIn('--pdv-pgto-pix-fg:#0f766e;', claro)
        self.assertIn('--pdv-pgto-credito-fg:#1d4ed8;', claro)
        self.assertIn('--pdv-pgto-debito-fg:#047857;', claro)
        self.assertIn('window.__erpApplyPdvTheme?.();', template)
        self.assertIn('--pdv-warning:#b45309;', claro)
        self.assertIn('-webkit-text-fill-color:var(--pdv-t1)', template)
        self.assertIn('html.tema-claro .cart-empty-icon { stroke:#cbd5e1; }', template)

    def test_pdv_carrega_sem_flash_e_oculta_tags_normais(self):
        template = (Path(__file__).resolve().parents[1] / 'templates/pdv/home.html').read_text(encoding='utf-8')
        self.assertNotIn('x-init="init()"', template)
        self.assertIn('x-show="sessaoCarregada && !sessao"', template)
        self.assertIn('x-show="temPromocao(item)"', template)
        self.assertIn('x-show="temPromocao(p)"', template)
        self.assertIn('width:340px;flex-shrink:0;', template)
        self.assertIn('width:410px;flex-shrink:0;', template)
        header = template.split('<header class="pdv-topbar"', 1)[1].split('</header>', 1)[0]
        for title in ['Atalhos de teclado', 'Sangria / Caixa', 'Configurações']:
            self.assertNotIn(f'title="{title}"', header)
        for title in ['Tela cheia', 'Imprimir']:
            self.assertIn(f'title="{title}"', header)

    def test_template_compila_com_navegacao_lateral(self):
        get_template('pdv/home.html')
        template = (
            Path(__file__).resolve().parents[1] / 'templates' / 'pdv' / 'home.html'
        ).read_text(encoding='utf-8')

        self.assertIn('showSystemMenu', template)
        self.assertIn('system-nav-drawer sidebar-favorites-nav', template)
        self.assertIn('brand-wordmark-neutral', template)
        self.assertIn("{% url 'core:dashboard' as dashboard_url %}", template)

    def test_pdv_integra_favoritos_filial_perfil_e_pagamentos(self):
        template = (
            Path(__file__).resolve().parents[1] / 'templates' / 'pdv' / 'home.html'
        ).read_text(encoding='utf-8')

        self.assertIn('sidebar-favorites-data', template)
        self.assertIn('data-full-favorites="true"', template)
        self.assertIn('outline:2px solid var(--pdv-accent)', template)
        self.assertNotIn('#f15a24', template)
        self.assertNotIn('#f97316', template)
        self.assertIn("static 'core/js/sidebar_favorites.js'", template)
        self.assertIn("core:trocar-filial", template)
        self.assertIn('fotoPerfilAberta', template)
        self.assertIn('payment-method-grid', template)
        self.assertIn('sidebar-favorites-records', template)
        self.assertIn('payment-panel-body', template)
        self.assertIn('payment-panel-footer', template)
        header = template.split('<header class="pdv-topbar"', 1)[1].split('</header>', 1)[0]
        self.assertLess(header.index('topbar-branch'), header.index('topbar-user'))
        self.assertIn("!sessao ? 'payment-locked'", template)
        self.assertIn('Sua sessão expirou ou você não tem permissão', template)

    def test_base_hidrata_tema_antes_da_tela_aparecer(self):
        get_template('pdv_base.html')
        base = (Path(__file__).resolve().parents[3] / 'templates' / 'pdv_base.html').read_text(
            encoding='utf-8'
        )

        self.assertIn('erp-prehydrate tema-escuro', base)
        self.assertIn('window.__erpApplyPdvTheme', base)
        self.assertIn('Space+Grotesk', base)
