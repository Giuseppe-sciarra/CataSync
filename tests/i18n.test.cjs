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
