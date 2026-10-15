# Changelog

## 2.1.0 — 2026-10-15

### Added
- **Categorie del sito da CataSync** (scheda Categorie): si creano, anche dentro un'altra, si
  eliminano e si mettono in ordine trascinandole o con le frecce. L'ordine è quello di Prodotti →
  Categorie in WooCommerce; le nuove vanno in fondo al loro livello. Eliminandone una, i prodotti
  restano sul sito, le sottocategorie salgono di un livello e nel portale si tolgono corrispondenze
  e categorie rimaste appese (le righe non ancora pubblicate tornano da completare).
- **Mu-plugin «CataSync – Menu delle categorie»** (`wordpress/catasync-category-menu.php`, si
  scarica anche dalla scheda Categorie): ogni categoria, creata da CataSync o dal sito, va sotto la
  voce di menu scelta, con le sottocategorie sotto la loro categoria (si può spegnere), nello stesso
  ordine delle categorie. Il nome segue la categoria, le categorie eliminate spariscono, le voci
  messe a mano restano dopo le categorie. Si imposta dal sito (Aspetto → Menu categorie) o da
  CataSync (Categorie → Menu del sito), con le stesse chiavi REST di WooCommerce.
- Controlli delle traduzioni su tutti i testi fissi delle pagine e del JavaScript: un testo nuovo
  senza traduzione fa fallire la verifica su GitHub.

### Changed
- Scheda Categorie riorganizzata: albero delle categorie, menu del sito, corrispondenze, attributi
  e marche.
- Creare, eliminare e ordinare le categorie e cambiare il menu del sito vogliono l'account attivo,
  come pubblicare; la quota dei prodotti qui non conta.

### Fixed
- Messaggi dell'abbonamento (sospeso, scaduto, quota esaurita) che restavano in italiano con
  l'interfaccia in un'altra lingua.

## 2.0.1 — 2026-10-11

### Added
- Guida: come nascono numeri e note delle release automatiche (`docs/GUIDA.md`, capitolo 14).
- Release ritirate: i numeri elencati in `docs/releases/ritirate.txt` perdono release e tag al
  giro successivo del workflow.

### Fixed
- Release automatiche: `[minor]` e `[major]` contano solo nel titolo del commit. Letti anche nel
  resto del messaggio, avevano fatto uscire per errore una 3.0.0 al posto della 2.0.1 (ritirata).

## 2.0.0 — Ricerca sul web come unica fonte, schede in più lingue

Prima versione pubblica. Le schede si cercano sul web partendo dall'EAN o dal codice
produttore, e ogni «non trovato» dice perché.

### Added
- **Ricerca riscritta.** Da una a tre domande per codice, distanziate (`SEARCH_PAUSE`, di serie
  4 secondi anche fra codici diversi), solo ai motori che sui codici funzionano: con SearXNG
  `google cse, yandex, google, startpage, duckduckgo, brave` (impostabili da Impostazioni,
  valgono anche se spenti nel `settings.yml`). La prima domanda va nella lingua e nel paese delle
  schede (it-IT, en-GB, fr-FR, de-DE, es-ES), la seconda ovunque, la terza fra virgolette; ci si
  ferma appena due risultati riportano il codice.
- **Rinvio quando i motori bloccano.** Se tutti i motori utili rispondono CAPTCHA, «accesso
  negato» o «troppe richieste», il codice non diventa «non trovato»: si riprova da solo dopo
  10, 30, 90 e 240 minuti (`SEARCH_MAX_DEFERRALS`).
- **Diario della ricerca e pulsante «Perché?».** Per ogni codice: domande fatte, motori che
  hanno risposto e quelli che ci hanno bloccati (col motivo), siti aperti e cosa è successo su
  ciascuno. Cause: motori bloccati, motore irraggiungibile, nessun sito col codice, siti che
  rifiutano la lettura, nessuna pagina che conferma, solo somiglianza del nome, codice interno
  del grossista.
- **Impostazioni → Com'è andata la ricerca**: stato dei motori, cause dei mancati, siti che
  rifiutano la lettura. **Prova a lotti**: un elenco di codici cercati uno per uno, con l'esito.
- **Lettura amichevole delle pagine** (`app/rete.py`): `robots.txt` secondo l'RFC 9309
  (token `tdsynccatalog` e `*`), una richiesta ogni 1,5 secondi per sito, protezioni anti-bot
  riconosciute (Cloudflare, Akamai, DataDome, Imperva, Sucuri, PerimeterX, CAPTCHA, 429) e
  saltate, senza aggirarle. Il browser serve solo alle pagine fatte di JavaScript e ai guasti
  del server. HTTP/2 quando c'è `h2`.
- **Pagine di categoria e ricerche interne riconosciute** (tema del sito, elenchi di prodotti,
  decine di EAN nella stessa pagina): non diventano più la scheda; si segue il link della
  tessera che riporta il codice.
- **Lingua delle schede**: IT, EN, FR, DE, ES. Decide il negozio (se impostata), poi il selettore
  «Schede in» (che parte dalla lingua dell'interfaccia), poi il cliente. Pagine lette chiedendo
  quella lingua, preferite nella fusione, il resto tradotto con LibreTranslate (`verso` la lingua
  delle schede). Cache separata per codice e lingua (`CACHE_DAYS`).
- **Interfaccia in italiano, inglese, francese e tedesco** con catalogo completo, compresi i
  messaggi del server; il traduttore capisce i modelli con segnaposto e le frasi incollate.
- **Favicon caricabile** da *Impostazioni → Aspetto del portale*: PNG, SVG, ICO, WebP o JPG fino a
  1 MB, formato riconosciuto dai byte, avviso se non è quadrata o è piccola, subito visibile nella
  scheda, usata anche da `/favicon.ico` e, se è un'immagine a pixel, come icona sul telefono.
  Favicon di serie inclusa.
- **Codice sorgente** in fondo alle pagine, con la versione (`SOURCE_URL`, AGPL-3.0).
- `BIND_ADDRESS` e `HOST_PORT` per la porta del portale; `SEARXNG_SECRET` nel `.env` (il
  `settings.yml` in repository non contiene segreti).
- Test automatici (`tests/`, `python -m unittest discover -s tests`, `node --test
  tests/i18n.test.cjs`) e verifica su GitHub Actions, compresa la build dell'immagine.
- **Release automatiche**: a ogni modifica di `main` che passa i test esce una release. Il
  numero è quello di `VERSION.txt` se è nuovo, altrimenti l'ultima release + 1 sull'ultima
  cifra; `[minor]` o `[major]` nel titolo del commit fanno salire la seconda o la prima.
  `VERSION.txt` e `CHANGELOG.md` li aggiorna il workflow, e il portale legge la versione da
  `VERSION.txt`.

### Changed
- La base della scheda è la pagina più affidabile (EAN dichiarato nei dati strutturati, poi codice
  in pagina), non la prima letta: prima una pagina debole letta per prima faceva scartare come
  «incoerenti» le schede vere degli altri siti.
- La lingua di una pagina si giudica sul testo finale (descrizione e specifiche comprese) e con
  parole esclusive di ciascuna lingua: «con», «del», «su», «al» non fanno più passare per spagnola
  una pagina italiana.
- Nome «povero» nei dati strutturati (solo la marca): vince il titolo della pagina, e quel nome
  diventa la marca se manca.
- Foto: si aggiunge sempre quella di Open Graph, si riparano gli indirizzi rotti del tipo
  `https://sito//sito/cdn/…`, si scartano le foto di varianti sorelle (stessa famiglia, misure
  diverse); riserva con le foto della pagina se quelle dichiarate non passano la verifica.
- Pagine in UTF-8 lette come UTF-8 anche quando il meta dichiara un'altra codifica.
- Parametri di tracciamento (`srsltid`, `utm_*`…) tolti dai risultati: la stessa pagina non si
  chiede due volte.
- `searxng/settings.yml`: sospensioni dei motori come nelle versioni recenti di SearXNG (un'ora
  per i CAPTCHA invece di un giorno), timeout da 6 secondi, Yandex acceso, Bing escluso dalle
  ricerche sui codici.
- Container rinominati `catasync-db` e `catasync-hub` (il volume del database resta quello).
- Testi dell'interfaccia senza il nome dell'agenzia: «l'amministratore del portale».
- I negozi esistenti, all'aggiornamento, restano nella lingua del loro cliente (fissata sul
  negozio una volta sola).

### Removed
- Il vecchio connettore a un catalogo esterno a pagamento, con le sue impostazioni e variabili
  d'ambiente: la ricerca sul web fa tutto. Al primo avvio il database si ripulisce da solo
  (fonti, cache, impostazioni e riferimenti nelle righe), una volta sola.

### Fixed
- **Sicurezza — HTML attivo dalle pagine lette**: descrizioni ripulite (solo paragrafi, elenchi,
  grassetti, tabelle, senza attributi), nomi e specifiche in testo puro, al salvataggio, alla
  correzione a mano, prima di scrivere su WooCommerce e prima di mostrarle nell'interfaccia.
- **Sicurezza — richieste verso la rete interna**: pagine, `robots.txt`, foto e browser aprono solo
  indirizzi pubblici, anche dopo un rimando.
- **Sicurezza — SVG**: loghi e favicon con script, gestori di eventi o contenuti incorporati
  rifiutati; serviti comunque con una CSP `sandbox`.
- «Pubblica» sulla singola riga non faceva niente nei carichi senza pubblicazione automatica.
- Il numero di specifiche impostato sul sito non si salvava in modifica.
- Specifiche vere scartate come «commerciali» per pezzi di parola («presostato», «cartuccia»,
  «resistenza»).
- Un `productID` numerico interno del negozio faceva credere che la pagina dichiarasse un altro
  codice.
- Pagine senza dati strutturati scartate anche quando riportavano l'EAN esatto.
- DuckDuckGo che chiede la verifica anti-bot (risposta 202) passava per «zero risultati».
- Selettori della lingua che mostravano «Italiano» anche con un'altra lingua scelta.
- Tendine riempite dal sito (categoria nella finestra «Correggi la scheda», categoria in
  tabella, cliente, sito, ora del giro notturno) che mostravano la prima voce dell'elenco
  invece di quella salvata.
- Con l'interfaccia in un'altra lingua restavano in italiano alcuni titoli delle finestre, le
  note dei motori di ricerca e le descrizioni delle fonti; le frasi sconosciute non escono più
  tradotte a metà («Search da sola la pagina…»).
- Tema chiaro: barra in alto e caselle di spunta poco leggibili.

## 1.x

Versioni interne, non pubblicate: portale multi-cliente con un connettore a catalogo esterno e
la ricerca sul web come ripiego.
