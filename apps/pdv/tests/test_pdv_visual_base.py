from pathlib import Path
from types import SimpleNamespace

from django.template.loader import get_template
from django.test import SimpleTestCase


class PDVVisualBaseTests(SimpleTestCase):
    def test_descontos_sincronizados_e_botao_remover_com_temas(self):
        template = (Path(__file__).resolve().parents[1] / 'templates/pdv/home.html').read_text(encoding='utf-8')
        for field in ['Desconto do item em percentual', 'Desconto do item em reais', 'Desconto geral em percentual', 'Desconto geral em reais']:
            self.assertIn('aria-label="' + field + '"', template)
        self.assertIn('@click="abrirDescontoGeral()"', template)
        self.assertIn('this.abrirDescontoGeral();', template)
        self.assertIn('class="cart-remove-btn"', template)
        self.assertIn('html.tema-claro .cart-remove-btn', template)
        self.assertIn('.cart-remove-btn:focus-visible', template)
        self.assertIn('@click.stop="visualizarComprovante(v.id)"', template)
        self.assertIn('{% include "pdv/_comprovante_methods.html" %}', template)
        self.assertIn('{% include "pdv/_toast_styles.html" %}', template)
        self.assertIn('#pdv-app .dic-field:focus-within', template)
        self.assertIn('class="pending-modal"', template)
        self.assertIn('class="item-discount-badge"', template)
        self.assertIn('maxlength="500" placeholder="Observação do item..."', template)
        self.assertIn('Observação: ${this.escapeHtml(obs)}', template)
        self.assertIn('const detalhes = qtd === 1', template)
        self.assertIn('<div class="item-list">${itensHtml}</div>', template)
        self.assertIn('.item-note { display:block; width:100%;', template)
        self.assertIn('Carregar mais resultados', template)
        self.assertIn('async buscarProdutos(q, carregarMais=false)', template)
        self.assertIn("new URLSearchParams({q:termo,pagina:String(pagina)})", template)
        toast = (Path(__file__).resolve().parents[1] / 'templates/pdv/_toast_styles.html').read_text(encoding='utf-8')
        self.assertIn('right: 18px;', toast)
        self.assertIn('bottom: 18px;', toast)
        self.assertIn('html:not(.tema-claro) .pdv-toast', toast)

    def test_catalogo_mostra_nome_completo_no_hover(self):
        template = (Path(__file__).resolve().parents[1] / 'templates/pdv/home.html').read_text(encoding='utf-8')
        self.assertIn('class="prod-card-main" :title="p.descricao"', template)

    def test_logo_opcional_preserva_menu_normal(self):
        navigation = get_template('core/_sidebar_navigation.html')
        context = {
            'filial_ativa': SimpleNamespace(imagem=None, nome_fantasia='Empresa teste', razao_social='Empresa teste LTDA'),
            'empresa_logo_url': '/media/logo-empresa-teste.png',
            'request': SimpleNamespace(path='/pdv/'),
        }
        self.assertIn('/media/logo-empresa-teste.png', navigation.render(context))
        context['hide_sidebar_logo'] = True
        pdv_navigation = navigation.render(context)
        self.assertNotIn('/media/logo-empresa-teste.png', pdv_navigation)
        self.assertNotIn('sidebar-branch-logo-frame', pdv_navigation)
        self.assertIn('Dashboard', pdv_navigation)

    def test_favoritos_aparecem_selecionados_hover_ou_foco(self):
        template = (Path(__file__).resolve().parents[1] / 'templates/pdv/home.html').read_text(encoding='utf-8')
        selector = '.system-nav-drawer .sidebar-favoritable-link'
        self.assertIn(selector + ' > .sidebar-favorite-toggle:not(.is-favorite) { visibility:hidden;opacity:0;pointer-events:none; }', template)
        self.assertIn(selector + ':focus-within > .sidebar-favorite-toggle:not(.is-favorite) { visibility:visible;opacity:1;pointer-events:auto; }', template)
        self.assertIn('@media (hover:hover) and (pointer:fine)', template)
        self.assertIn(selector + ':hover > .sidebar-favorite-toggle:not(.is-favorite) { visibility:visible;opacity:1;pointer-events:auto; }', template)
        self.assertIn('.sidebar-favorite-toggle.is-favorite { color:#facc15; }', template)
        navigation = (Path(__file__).resolve().parents[2] / 'core/templates/core/_sidebar_navigation.html').read_text(encoding='utf-8')
        self.assertNotIn('onmouseover=', navigation)
        self.assertIn('@mouseenter="$el.style.background=temaClaro?', navigation)

    def test_mobile_tem_barra_arrastavel_e_menu_fora_da_rolagem(self):
        template = (Path(__file__).resolve().parents[1] / 'templates/pdv/home.html').read_text(encoding='utf-8')
        self.assertNotIn('#pdv-app > header .topbar-chip { display:none', template)
        self.assertNotIn('header.pdv-topbar::after', template)
        self.assertNotIn('grid-template-rows:44px 44px 44px;', template)
        self.assertIn('overflow-x:auto;overflow-y:hidden;scrollbar-width:none;', template)
        self.assertIn('padding:calc(6px + env(safe-area-inset-top))', template)
        self.assertIn('height:calc(68px + env(safe-area-inset-bottom))', template)
        header = template.split('<header class="pdv-topbar"', 1)[1].split('</header>', 1)[0]
        for hook in ['topbar-brand', 'topbar-sales-actions', 'topbar-print', 'topbar-branch', 'topbar-user']:
            self.assertIn(hook, header)
        self.assertIn('aria-label="Menu do usuário"', header)
        self.assertNotIn('id="sidebar-root"', header)
        self.assertNotIn('@click.outside="showSystemMenu=false"', header)
        self.assertIn('{% include "core/_sidebar_navigation.html" with hide_sidebar_logo=True %}', template)

    def test_pagamento_neutro_troco_e_contraste_claro(self):
        template = (Path(__file__).resolve().parents[1] / 'templates/pdv/home.html').read_text(encoding='utf-8')
        self.assertIn('background:var(--pdv-selection-bg)', template)
        self.assertIn('class="payment-record"', template)
        self.assertIn('.payment-change { color:var(--pdv-warning);', template)
        self.assertIn(':aria-pressed="formaPgtoSelecionada?.id===forma.id"', template)
        self.assertIn('html.tema-claro .btn-fim:disabled { opacity:1 !important;', template)
        claro = template.split('html.tema-claro body {', 1)[1].split('}', 1)[0]
        self.assertIn('--pdv-paid:#15803d;', claro)
        self.assertIn('--pdv-warning:#b45309;', claro)
        def luminance(hex_color):
            channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
            return sum(c * w for c, w in zip(linear, (0.2126, 0.7152, 0.0722)))
        for fg, bg in [('#15803d', '#ffffff'), ('#b45309', '#ffffff'), ('#475569', '#e2e8f0')]:
            self.assertGreaterEqual((luminance(bg) + .05) / (luminance(fg) + .05), 4.5)

    def test_tema_claro_replica_cabecalho_laranja_e_pagamento_sem_cores_fixas(self):
        template = (Path(__file__).resolve().parents[1] / 'templates/pdv/home.html').read_text(encoding='utf-8')
        claro = template.split('html.tema-claro body {', 1)[1].split('}', 1)[0]
        self.assertIn('#f15a24', claro)
        self.assertIn('html.tema-claro .cat-tab.active { background:#ff8a4c', template)
        entrada = template.split('<!-- Input valor quando forma selecionada -->', 1)[1].split('<!-- Crédito do cliente', 1)[0]
        self.assertIn('class="payment-entry"', entrada)
        self.assertIn('class="payment-quick-value"', entrada)
        self.assertNotIn('background:#', entrada)
        self.assertNotIn('onmouseover=', entrada)
        self.assertIn('<select x-model="bandeiraPgto">', entrada)

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
        self.assertIn('linear-gradient(90deg,#f15a24 0%,#e8824a 50%,#c75a22 76%,#542412 100%)', template)
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
