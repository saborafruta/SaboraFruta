module.exports = {
  // O TEMA ESCURO DESTE APP NAO E' O DO SISTEMA OPERACIONAL. Sem esta linha o
  // Tailwind usa a estrategia `media`, e compila todo `dark:` dentro de
  // `@media (prefers-color-scheme: dark)` -- ou seja, os 222 utilitarios
  // `dark:` espalhados por 31 templates obedeciam o Windows, e nao o botao de
  // tema do proprio ERP. Quem usava o sistema no escuro com o SO no claro via
  // todos eles errados, e o contrario tambem.
  //
  // `tema-escuro` e' a classe que o `_base.html` alterna, no <html> e no
  // <body>. `.dark` nunca existiu aqui -- era so' a convencao copiada da
  // documentacao do Tailwind.
  darkMode: ['class', '.tema-escuro'],
  content: [
    './templates/**/*.html',
    './apps/**/templates/**/*.html',
    './apps/templates/**/*.html',
    './apps/**/*.py',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
      },
      colors: {
        primary: {
          50: '#eff6ff',
          100: '#dbeafe',
          200: '#bfdbfe',
          300: '#93c5fd',
          400: '#60a5fa',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
          800: '#1e40af',
          900: '#1e3a8a',
        },
        surface: {
          DEFAULT: '#1c1c1e',
          card: '#242426',
          hover: '#2e2e30',
          border: '#2a2a2e',
          muted: '#323236',
          deep: '#161618',
        },
        accent: {
          DEFAULT: '#e8824a',
          hover: '#d56a2e',
          muted: 'rgba(232,130,74,0.10)',
        },
      },
    },
  },
};
