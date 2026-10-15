// Catalogo delle traduzioni dell'interfaccia e traduttore (static/i18n).
// node --test tests/i18n.test.cjs
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const radice = path.join(__dirname, '..');
const sorgente = fs.readFileSync(path.join(radice, 'static/i18n/catalog.js'), 'utf8');
const catalogo = JSON.parse(sorgente.replace(/^\s*window\.APP_TRANSLATIONS\s*=\s*/, '').replace(/;\s*$/, ''));
const api = require(path.join(radice, 'static/i18n/i18n.js'))(catalogo, undefined);

test('ogni voce ha inglese, francese e tedesco, con gli stessi segnaposto', () => {
  const segnaposto = s => (s.match(/\{\d+\}/g) || []).sort().join();
  for (const [chiave, voce] of Object.entries(catalogo)) {
    for (const lingua of ['en', 'fr', 'de']) {
      assert.ok(voce[lingua], `manca ${lingua}: ${chiave}`);
      assert.strictEqual(segnaposto(voce[lingua]), segnaposto(chiave), `segnaposto ${lingua}: ${chiave}`);
    }
  }
});

test('i file per lingua coincidono col catalogo', () => {
  for (const lingua of ['en', 'fr', 'de', 'it']) {
    const file = JSON.parse(fs.readFileSync(path.join(radice, `static/i18n/${lingua}.json`), 'utf8'));
    assert.strictEqual(Object.keys(file).length, Object.keys(catalogo).length, lingua);
  }
});

test('niente riferimenti al vecchio connettore a catalogo', () => {
  assert.ok(!/icecat/i.test(sorgente));
});

test('modelli con segnaposto, numeri e frasi incollate', () => {
  assert.strictEqual(api.t('Sto cercando le schede di 12 prodotti…', 'en'), 'Looking for the details of 12 products…');
  assert.strictEqual(api.t('Elimina selezionati (3)', 'de'), 'Ausgewählte löschen (3)');
  assert.strictEqual(api.t('3 problemi', 'fr'), '3 problèmes');
  assert.strictEqual(api.t('fonte: Ricerca sul web', 'en'), 'source: Web search');
  assert.strictEqual(
    api.t('Prezzo scontato non inferiore a quello pieno: non applicato.; Marca non assegnata: il sito non ha la funzione Marche attiva.', 'en'),
    'Sale price not lower than the regular price: not applied.; Brand not assigned: the Brands feature is not active on the site.');
  assert.strictEqual(api.t('Prodotti', 'it'), 'Prodotti');
});

test('niente frasi tradotte a metà: una parola nota in testa a una frase ignota non basta', () => {
  const frase = 'Cerca qualcosa di nuovo che il catalogo non conosce ancora';
  assert.strictEqual(api.t(frase, 'en'), frase);
  assert.strictEqual(api.t('Attiva SearXNG', 'en'), 'Enable SearXNG');
  assert.strictEqual(api.t('attiva', 'en'), 'active');
  assert.strictEqual(
    api.t('Container tuo, nessuna chiave e nessun costo. Interroga Google, Yandex, DuckDuckGo e gli altri; i motori da usare si scelgono qui sotto.', 'de'),
    'Ihr eigener Container, ohne Schlüssel und ohne Kosten. Er fragt Google, Yandex, DuckDuckGo und die anderen ab; die zu verwendenden Suchmaschinen wählen Sie unten aus.');
});

// Testi fissi delle pagine: nodi di testo e attributi (title, placeholder, aria-label, alt),
// spezzati come li vede il browser (un <code> o un <strong> separa due nodi). Quello che sta in
// <code>, <script>, <style> o sotto translate="no" non si traduce.
function testiDellePagine() {
  const testi = new Set();
  for (const file of ['templates/dashboard.html', 'templates/login.html']) {
    let html = fs.readFileSync(path.join(radice, file), 'utf8')
      .replace(/<!--[\s\S]*?-->/g, ' ').replace(/\{%[\s\S]*?%\}/g, ' ').replace(/\{\{[\s\S]*?\}\}/g, 'TD Sync Catalog');
    const pila = [];
    // Un tag con gli attributi fra virgolette (dentro possono esserci «>» e «=>»), oppure testo.
    const re = /<!doctype[^>]*>|<(\/?)([a-zA-Z][\w-]*)((?:\s+[^\s=>"'\/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>"']+))?)*)\s*\/?>|([^<]+)/gi;
    const entita = s => s.replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(+n)).replace(/&nbsp;/g, ' ')
      .replace(/&times;/g, '×').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"');
    let m;
    while ((m = re.exec(html))) {
      if (m[4] !== undefined) {
        const muto = pila.some(e => e.muto);
        const s = entita(m[4]).replace(/\s+/g, ' ').trim();
        if (!muto && /\p{L}{3,}/u.test(s)) testi.add(s);
        continue;
      }
      if (!m[2]) continue;   // <!doctype>
      const [, chiude, nome, attributi] = m;
      const tag = nome.toLowerCase();
      if (chiude) {
        const i = pila.map(e => e.tag).lastIndexOf(tag);
        if (i >= 0) pila.length = i;
        continue;
      }
      for (const a of attributi.matchAll(/\s(title|placeholder|aria-label|alt)="([^"]*)"/g)) {
        const s = entita(a[2]).replace(/\s+/g, ' ').trim();
        if (/\p{L}{3,}/u.test(s) && !/translate="no"/.test(attributi)) testi.add(s);
      }
      const vuoto = /\/\s*$/.test(attributi) || ['input', 'br', 'img', 'meta', 'link', 'hr', 'source'].includes(tag);
      if (!vuoto) pila.push({ tag, muto: ['code', 'script', 'style', 'pre', 'textarea'].includes(tag) || /translate="no"/.test(attributi) });
    }
  }
  return [...testi];
}

test('ogni testo fisso delle pagine ha la sua traduzione, intera', () => {
  // Nomi propri, codici e indirizzi: restano come sono in tutte le lingue.
  const uguali = /^(?:\(AGPL-3\.0\)|EAN|· EAN|Email|PEC|SerpAPI|TD Sync Catalog|Bearer token|Consumer key|Consumer secret|X-Api-Key|cod\.|dati\.articolo|English|Español|Italiano|Français|Deutsch|https?:\/\/\S*(?: https?:\/\/\S*)*|[\d\s A-Z]+)$/u;
  const modelli = Object.keys(catalogo).filter(k => /\{\d+\}/.test(k))
    .map(k => new RegExp('^' + k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\\\{\d+\\\}/g, '.+?') + '$'));
  const tradotto = s => catalogo[s] || uguali.test(s) || modelli.some(r => r.test(s));
  // «Accedi · TD Sync Catalog»: pezzi uniti da « · », ognuno col suo posto nel catalogo.
  const mancanti = testiDellePagine().filter(s => !tradotto(s) && !s.split(/\s+·\s+/).every(tradotto));
  assert.deepStrictEqual(mancanti, []);
});

test('i testi scritti nel JavaScript (t, tt, say, fail) sono nel catalogo', () => {
  const js = fs.readFileSync(path.join(radice, 'static/hub.js'), 'utf8');
  const testi = new Set();
  for (const m of js.matchAll(/this\.(?:t|tt|say|fail)\(\s*(['"])((?:\\.|(?!\1).)*)\1/g)) {
    const s = m[2].replace(/\\'/g, "'").replace(/\\"/g, '"').replace(/\s+/g, ' ').trim();
    if (/\p{L}{3,}/u.test(s)) testi.add(s);
  }
  const mancanti = [...testi].filter(s => !catalogo[s]);
  assert.deepStrictEqual(mancanti, []);
});
