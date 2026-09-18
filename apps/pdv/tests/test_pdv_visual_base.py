from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

from django.template.loader import get_template
from django.test import SimpleTestCase


class PDVVisualBaseTests(SimpleTestCase):
    def test_modal_pos_venda_permanece_aberto_ate_pular_com_f10(self):
        template = (Path(__file__).resolve().parents[1] / 'templates/pdv/home.html').read_text(encoding='utf-8')

        self.assertIn('<kbd>F10</kbd>Pular &rarr; Nova Venda', template)
        self.assertIn("if (e.key==='F10') { e.preventDefault(); this.fecharModalImpressao(); }", template)
        self.assertIn("if (this.showModalImpressao) {", template)
        self.assertNotIn("imprimirCupom('termica'); fecharModalImpressao()", template)
        self.assertNotIn("imprimirCupom('a4'); fecharModalImpressao()", template)
        self.assertNotIn("if (!origem) this.fecharModalImpressao();", template)

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
        self.assertNotIn('Carregar mais resultados', template)
        self.assertIn('@scroll.passive="aoRolarProdutos($event)"', template)
        self.assertIn('async buscarProdutos(q, carregarMais=false)', template)
        self.assertIn("new URLSearchParams({q:termo,pagina:String(pagina)})", template)
        self.assertIn('aoRolarProdutos(evento)', template)
        self.assertIn('if (distanciaDoFim <= 140) this.carregarMaisProdutos();', template)
        self.assertIn("x-text=\"'Estoque: ' + fmtQtd(p.estoque_disponivel, p)\"", template)
        self.assertNotIn('stock-zero', template)
        self.assertNotIn('stock-low', template)
        self.assertNotIn('stock-ok', template)
        self.assertIn('resumoEstoqueCarrinho(item)', template)
        self.assertIn('aria-label="Valor final da venda com desconto"', template)
        self.assertIn('class="pdv-price-before"', template)
        toast = (Path(__file__).resolve().parents[1] / 'templates/pdv/_toast_styles.html').read_text(encoding='utf-8')
        self.assertIn('right: 18px;', toast)
        self.assertIn('bottom: 18px;', toast)
        self.assertIn('html:not(.tema-claro) .pdv-toast', toast)

    def test_total_editavel_aceita_desconto_ou_acrescimo(self):
        template = (Path(__file__).resolve().parents[1] / 'templates/pdv/home.html').read_text(encoding='utf-8')
        self.assertIn('Edite o total para aplicar desconto ou acréscimo.', template)
        self.assertIn('if(total>subtotal) {', template)
        self.assertIn('this.venda.acrescimo=this.arredondarDesconto(total-subtotal);', template)
        self.assertIn('this.venda.desconto=this.limitarDesconto(subtotal-total,subtotal);', template)
        self.assertIn("'+ R$ '+fmt(venda.acrescimo)+' de acréscimo'", template)
        self.assertNotIn('Para dar desconto, informe um total entre', template)

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

    def test_tema_claro_replica_cabecalho_azul_e_pagamento_sem_cores_fixas(self):
        template = (Path(__file__).resolve().parents[1] / 'templates/pdv/home.html').read_text(encoding='utf-8')
        claro = template.split('html.tema-claro body {', 1)[1].split('}', 1)[0]
        self.assertIn('linear-gradient(90deg,#0f172a 0%,#172554 42%,#1e3a8a 58%,#0f172a 100%)', claro)
        self.assertNotIn('linear-gradient(90deg,#f15a24 0%,#e8824a 55%,#c2410c 100%)', claro)
        self.assertIn('html.tema-claro .cat-tab.active { background:#ff8a4c', template)
        entrada = template.split('<!-- Input valor quando forma selecionada -->', 1)[1].split('<!-- Crédito do cliente', 1)[0]
        self.assertIn('class="payment-entry"', entrada)
        self.assertIn('class="payment-quick-value"', entrada)
        self.assertNotIn('background:#', entrada)
        self.assertNotIn('onmouseover=', entrada)
        self.assertIn('<select x-model="bandeiraPgto">', entrada)
        self.assertIn('Selecione a bandeira', entrada)
        self.assertIn('parcelasDisponiveis(formaPgtoSelecionada)', entrada)
        self.assertIn("if (cartao && !this.bandeiraPgto)", template)

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
        self.assertIn('x-show="temPromocao(ofertaCatalogo(p))"', template)
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
        self.assertGreaterEqual(template.count('linear-gradient(90deg,#0f172a 0%,#172554 42%,#1e3a8a 58%,#0f172a 100%)'), 2)
        self.assertNotIn('background:#c2410c !important;border-radius:0;', template)
        self.assertNotIn('linear-gradient(90deg,#f15a24 0%,#e8824a 55%,#c2410c 100%)', template)
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
        self.assertIn("navigator.serviceWorker.register('/sw.js'", base)
        self.assertIn("static 'pdv-manifest.json'", base)

    def test_pdv_persiste_rascunho_local_e_envia_idempotencia(self):
        template = (Path(__file__).resolve().parents[1] / 'templates/pdv/home.html').read_text(encoding='utf-8')
        local_store = (Path(__file__).resolve().parents[3] / 'static/js/pdv_local_store.js').read_text(encoding='utf-8')
        self.assertIn("static 'js/pdv_local_store.js'", template)
        self.assertIn("await this.restaurarRascunhoLocal()", template)
        self.assertIn("this.agendarPersistenciaLocal();", template)
        self.assertIn("'Idempotency-Key':this.venda.local_id", template)
        self.assertIn("persistirRascunhoLocal('resultado_incerto')", template)
        self.assertIn("durability: 'strict'", local_store)
        self.assertIn("navigator.storage.persist()", local_store)
        self.assertIn("indexedDB.open(DB_NAME, DB_VERSION)", local_store)

    def test_pdv_mantem_catalogo_e_fila_offline_com_restricoes(self):
        template = (Path(__file__).resolve().parents[1] / 'templates/pdv/home.html').read_text(encoding='utf-8')
        local_store = (Path(__file__).resolve().parents[3] / 'static/js/pdv_local_store.js').read_text(encoding='utf-8')
        service_worker = (Path(__file__).resolve().parents[3] / 'static/sw.js').read_text(encoding='utf-8')

        self.assertIn("const DB_VERSION = 3", local_store)
        self.assertIn("const SNAPSHOTS_STORE = 'snapshots'", local_store)
        self.assertIn("const QUEUE_STORE = 'vendas_pendentes'", local_store)
        self.assertIn('async enqueueSale(data)', local_store)
        self.assertIn('async listQueuedSales()', local_store)
        self.assertIn('async sincronizarCatalogoLocal(estado)', template)
        self.assertIn('buscarProdutosNoSnapshot(termo, pagina=1)', template)
        self.assertIn('async sincronizarFilaOffline()', template)
        self.assertIn("forma.requer_tef", template)
        self.assertIn("12 * 60 * 60 * 1000", template)
        self.assertIn("pdv_local_store.js?v=20260918-3", service_worker)

    def test_pdv_tem_abertura_fria_offline_com_pin_local(self):
        template = (Path(__file__).resolve().parents[1] / 'templates/pdv/home.html').read_text(encoding='utf-8')
        raiz = Path(__file__).resolve().parents[3]
        local_store = (raiz / 'static/js/pdv_local_store.js').read_text(encoding='utf-8')
        service_worker = (raiz / 'static/sw.js').read_text(encoding='utf-8')
        offline_shell = (raiz / 'static/pdv-offline.html').read_text(encoding='utf-8')
        offline_app = (raiz / 'static/js/pdv_offline_app.js').read_text(encoding='utf-8')

        self.assertIn("const OFFLINE_PROFILES_STORE = 'perfis_offline'", local_store)
        self.assertIn('async configureOfflineAccess(pin, profile)', local_store)
        self.assertIn('static async unlockOfflineProfile(scope, pin)', local_store)
        self.assertIn("iterations: 210000", local_store)
        self.assertIn("name: 'AES-GCM'", local_store)
        self.assertIn('failed_attempts >= 5', local_store)
        self.assertIn('showModalProtecaoOffline', template)
        self.assertIn('ativarProtecaoOffline()', template)
        self.assertIn('renovarProtecaoOffline()', template)
        self.assertIn("const PDV_OFFLINE_SHELL = '/static/pdv-offline.html?v=20260918-3'", service_worker)
        self.assertIn("url.pathname.startsWith('/pdv/')", service_worker)
        self.assertIn('Abrir PDV sem internet', offline_shell)
        self.assertIn('/static/js/pdv_offline_app.js?v=20260918-3', offline_shell)
        self.assertIn("await state.store.enqueueSale", offline_app)
        self.assertIn('delete safe.custo_atual', offline_app)
        self.assertIn("window.addEventListener('online'", offline_app)

    def test_service_worker_entrega_casco_offline_somente_ao_pdv(self):
        raiz = Path(__file__).resolve().parents[3]
        resultado = subprocess.run(
            [
                shutil.which('node'),
                str(Path(__file__).with_name('pdv_service_worker_behavior.cjs')),
            ],
            input=(raiz / 'static/sw.js').read_text(encoding='utf-8'),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)

    def test_indicador_distingue_online_offline_e_sincronizacao(self):
        template = (Path(__file__).resolve().parents[1] / 'templates/pdv/home.html').read_text(encoding='utf-8')

        self.assertIn("return this.sincronizandoFilaOffline || this._catalogoSyncEmAndamento", template)
        self.assertIn("return 'SINCRONIZANDO'", template)
        self.assertIn("conectividadeOnline ? 'ONLINE' : 'SEM INTERNET'", template)
        self.assertIn('pdv-connection-dot--online', template)
        self.assertIn('pdv-connection-dot--offline', template)
        self.assertIn('animation:pdv-offline-pulse', template)
        self.assertIn('pdv-connection-label--offline', template)
        self.assertIn('animation:pdv-offline-text', template)
        self.assertIn('pdv-connection-sync', template)
        self.assertIn('pdv-connection-label--sync', template)
        self.assertIn('animation:pdv-sync-spin', template)
        self.assertIn('prefers-reduced-motion: reduce', template)

    def test_cabecalho_global_preenche_o_canto_sob_a_curva_laranja(self):
        get_template('_base.html')
        base = (Path(__file__).resolve().parents[3] / 'templates' / '_base.html').read_text(
            encoding='utf-8'
        )

        self.assertIn(
            'linear-gradient(90deg, #f54e12 0%, #f17733 55%, #e55d25 100%)',
            base,
        )
        self.assertIn('background: #e55d25 !important;', base)
        self.assertIn('border-radius: 0 0 18px 0;', base)
        self.assertIn('body.tema-claro .app-topbar > * { position: relative; z-index: 1; }', base)
        self.assertNotIn('#542412', base)
        self.assertNotIn('border-radius:0 0 16px 0', base)
