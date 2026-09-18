from pathlib import Path

from django.test import SimpleTestCase


class DashboardHeroTemplateTests(SimpleTestCase):
    def test_dashboard_exibe_banner_sem_emoji_de_mao(self):
        raiz = Path(__file__).resolve().parents[1]
        template = (raiz / 'templates' / 'core' / 'dashboard.html').read_text(
            encoding='utf-8'
        )

        self.assertIn('class="dashboard-hero"', template)
        self.assertIn('ÁREA DE TRABALHO', template)
        self.assertIn('{{ saudacao }}, {{ primeiro_nome }}', template)
        self.assertIn('{% now "d \\d\\e F" %}', template)
        self.assertNotIn('👋', template)
        self.assertNotIn('Olá, {{ user.nome|default:user.email }}', template)

    def test_banner_tem_variantes_clara_e_responsiva(self):
        raiz = Path(__file__).resolve().parents[1]
        template = (raiz / 'templates' / 'core' / 'dashboard.html').read_text(
            encoding='utf-8'
        )
        inicio_css = (raiz / 'static' / 'core' / 'css' / 'inicio.css').read_text(
            encoding='utf-8'
        )

        self.assertIn('body.tema-claro .dashboard-shell .dashboard-hero {', template)
        self.assertIn('linear-gradient(135deg, #f16028 0%, #f17843 46%, #f97316 100%)', template)
        self.assertIn('body.tema-claro .dashboard-shell .dashboard-hero h2 { color: #fff;', template)
        self.assertIn('body.tema-claro .dashboard-shell .dashboard-hero-eyebrow { color: #fed7aa; }', template)
        self.assertIn('linear-gradient(135deg,#f16028 0%,#f17843 46%,#f97316 100%)', inicio_css)
        self.assertIn('body.tema-claro .inicio-hero h2 { color:#fff;', inicio_css)
        self.assertIn('body.tema-claro .inicio-eyebrow { color:#fed7aa; }', inicio_css)
        self.assertIn('@media (max-width: 600px)', template)
        self.assertIn('.dashboard-shell .dashboard-hero-date { display: none; }', template)
