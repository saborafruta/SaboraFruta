/*
 * Service worker do ERP iNoovaTed.
 *
 * Regra de ouro deste arquivo: NUNCA guardar HTML de página autenticada.
 * O sistema é multiusuário e multifilial -- servir uma página do cache
 * poderia mostrar dados de outro usuário (ou de outra filial) para quem
 * abrisse depois no mesmo aparelho. Então:
 *
 *   - estático versionado (CSS, ícones, libs de CDN): cache primeiro,
 *     porque é igual para todo mundo e é o que faz a tela abrir rápido
 *     e continuar abrindo com sinal ruim;
 *   - qualquer outra coisa (páginas, APIs, POST): rede, sempre. Sem
 *     fallback de conteúdo -- só um aviso de offline na navegação.
 *
 * O "offline-first" de verdade da tela do motorista não está aqui: as
 * posições captadas sem sinal ficam na fila em localStorage (ver
 * rastreio.html) e sobem quando a conexão volta. Cache de HTML não
 * resolveria isso e traria o risco acima.
 */

const VERSAO = 'erp-v1';
const CACHE_ESTATICO = `estatico-${VERSAO}`;

// Só o casco: o que é igual para qualquer usuário.
const PRE_CACHE = [
  '/static/css/tailwind-built.css',
  '/static/favicon.svg',
  '/static/pwa-icon-192.png',
  '/static/pwa-icon-512.png',
];

self.addEventListener('install', (evento) => {
  evento.waitUntil(
    caches.open(CACHE_ESTATICO)
      // addAll falha inteiro se um arquivo faltar; individual tolera.
      .then((cache) => Promise.allSettled(PRE_CACHE.map((url) => cache.add(url))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (evento) => {
  evento.waitUntil(
    caches.keys()
      .then((nomes) => Promise.all(
        nomes.filter((n) => n !== CACHE_ESTATICO).map((n) => caches.delete(n)),
      ))
      .then(() => self.clients.claim()),
  );
});

function ehEstatico(url) {
  return url.pathname.startsWith('/static/')
    || url.hostname === 'unpkg.com'
    || url.hostname === 'fonts.googleapis.com'
    || url.hostname === 'fonts.gstatic.com';
}

self.addEventListener('fetch', (evento) => {
  const req = evento.request;

  // POST/PUT/DELETE nunca passam por cache: são as escritas do sistema.
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  if (ehEstatico(url)) {
    // Cache primeiro, e revalida em segundo plano para a próxima visita.
    evento.respondWith(
      caches.match(req).then((cacheado) => {
        const daRede = fetch(req).then((resp) => {
          if (resp && resp.ok) {
            const copia = resp.clone();
            caches.open(CACHE_ESTATICO).then((c) => c.put(req, copia));
          }
          return resp;
        }).catch(() => cacheado);
        return cacheado || daRede;
      }),
    );
    return;
  }

  // Navegação sem rede: avisa em vez de dar a tela de erro do navegador.
  // Não servimos HTML do cache -- ver comentário do topo.
  if (req.mode === 'navigate') {
    evento.respondWith(
      fetch(req).catch(() => new Response(
        `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
         <meta name="viewport" content="width=device-width,initial-scale=1">
         <title>Sem conexão</title><style>
         body{font-family:Inter,Arial,sans-serif;background:#1c1c1e;color:#f8fafc;
              display:flex;align-items:center;justify-content:center;
              min-height:100vh;margin:0;padding:24px;text-align:center;}
         .cx{max-width:380px}h1{font-size:19px;margin:0 0 10px}
         p{font-size:14px;line-height:1.6;color:#94a3b8;margin:0 0 18px}
         button{background:#2563eb;color:#fff;border:none;border-radius:10px;
                padding:12px 22px;font-size:15px;font-weight:700}
         </style></head><body><div class="cx">
         <h1>Sem conexão</h1>
         <p>Esta tela precisa de internet para carregar. Se você estava
         rastreando uma entrega, as posições captadas continuam salvas no
         aparelho e sobem sozinhas quando a conexão voltar.</p>
         <button onclick="location.reload()">Tentar de novo</button>
         </div></body></html>`,
        { headers: { 'Content-Type': 'text/html; charset=utf-8' }, status: 503 },
      )),
    );
  }
});
