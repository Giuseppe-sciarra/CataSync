# Guida di CataSync

Installazione, aggiornamenti e backup sono nel [README](../README.it.md). Qui c'è tutto il
resto: come lavora il cliente, come funziona la ricerca, cosa arriva su WooCommerce.

## 1. Come lavora il cliente

Pagina **Prodotti**:

1. **Incolla i codici** nel riquadro, uno per riga (o carica un CSV): EAN oppure codice
   produttore (es. `97924465`). Non il codice di magazzino del negozio: i codici che
   iniziano con 20-29 sono interni del grossista e vengono segnalati.
2. **Cerca le schede**: le righe compaiono man mano nella tabella *I tuoi prodotti*.
3. **Categoria e prezzo** si mettono direttamente in tabella (quelli li sa solo il cliente).
   Selezionando più righe si assegna la stessa categoria a tutte.
4. **Pubblica sul sito**: i prodotti pronti vanno online. I codici già presenti sul sito si
   aggiornano, non si duplicano.

Sotto ogni riga non trovata c'è il motivo in breve e il pulsante **Perché?**, che apre il
diario della ricerca: cosa si è chiesto ai motori e cosa hanno risposto (anche quelli che ci
hanno bloccati), quali siti sono usciti e cosa è successo aprendo ciascuno.

Cercando col **codice produttore**, se la pagina trovata riporta l'EAN lo si recupera e
finisce sul prodotto WooCommerce.

**Storico** elenca i carichi; **Categorie** gestisce le categorie del sito (creazione,
eliminazione, ordine, menu del sito: vedi il capitolo 15) e mostra attributi globali e marche;
**Negozio** contiene i dati anagrafici del cliente.

## 2. La ricerca sul web

### Il giro di un codice

1. **Domande ai motori**, distanziate di qualche secondo (`SEARCH_PAUSE`, di serie 4) anche
   fra codici diversi: prima il codice nella lingua delle schede, poi — solo se servono altri
   risultati — il codice ovunque, poi il codice fra virgolette. Ci si ferma appena due
   risultati riportano il codice. Se il codice da solo non basta, una domanda sul nome del
   prodotto ricavato dai titoli.
2. **Ordine dei candidati**: prima le pagine che riportano il codice, poi quelle nella lingua
   giusta, poi produttori e grossisti; marketplace in fondo e solo se dichiarano l'EAN.
   Restano fuori comparatori, siti di codici a barre, cataloghi con licenza.
3. **Lettura delle pagine**, una alla volta per sito (1,5 secondi fra due richieste allo
   stesso sito), rispettando il `robots.txt`. Si leggono i **dati strutturati** che i siti
   pubblicano per Google Shopping — JSON-LD `schema.org/Product`, microdata, Open Graph — e,
   quando sono avari, tabelle di specifiche e paragrafi della pagina.
4. **Conferma del codice**: la pagina deve dichiarare quell'EAN nei dati strutturati, o
   riportarlo nel testo o nell'indirizzo, o combaciare col codice produttore. Una pagina che
   dichiara un EAN diverso si scarta senza discutere.
5. **Pagine di categoria e ricerche interne** si riconoscono (tema del sito, elenchi di
   prodotti, decine di EAN diversi nella stessa pagina): non sono la scheda, ma se riportano
   il codice si **segue il link della tessera** e si legge la scheda vera.
6. **Incrocio fra siti**: si raccolgono fino a tre o quattro pagine che confermano, si
   controlla che parlino dello stesso prodotto (modello, marca, codice produttore,
   specifiche) e si fondono: comanda la pagina più affidabile (EAN dichiarato, poi codice in
   pagina), le altre riempiono i buchi. Una sola pagina che somiglia per nome senza riportare
   il codice **non basta**: meglio una scheda da compilare che quella di un altro prodotto.
7. **Foto**: si scaricano e si misurano; via segnaposto, loghi, icone e immagini troppo
   piccole.
8. **Traduzione** nella lingua delle schede di quello che arriva in un'altra lingua.

Le schede trovate restano in cache per `CACHE_DAYS` giorni (di serie 30), per codice e
lingua. I prezzi **non arrivano mai** dalle pagine.

### Lettura «amichevole», niente aggiramenti

Il portale si presenta come un browser normale, ma non aggira niente: rispetta il
`robots.txt` (il suo nome è `tdsynccatalog`, valgono anche le regole per `*`), non forza
verifiche anti-bot, non risolve CAPTCHA, non cambia indirizzo. Un sito protetto da
Cloudflare, Akamai, DataDome & c. si salta e si segnala nel diario («protezione Cloudflare
(verifica anti-bot)»). Le pagine fatte col JavaScript si aprono col browser del compose, se
c'è. Pagine, `robots.txt`, foto e browser aprono solo indirizzi pubblici: niente richieste
verso la rete interna, nemmeno dopo un rimando.

### Quando sono i motori a bloccare

Google & c. rispondono con CAPTCHA o «troppe richieste» se li si interroga a raffica: per
questo le domande sono poche e distanziate. Se tutti i motori utili rispondono con un blocco,
il codice **non** viene dato per inesistente: si rimette in coda e si riprova da solo dopo 10,
30, 90 e 240 minuti (`SEARCH_MAX_DEFERRALS`, di serie 4 rinvii). La riga mostra «Motori di
ricerca bloccati: riprovo da solo alle …».

### Impostazioni → Ricerca automatica sul web

- **Motore**: SearXNG (consigliato), DuckDuckGo senza chiave (solo per provare), Brave Search,
  Google Programmable Search o SerpAPI con le loro API a pagamento.
- **Motori da interrogare** (solo SearXNG): vuoto = quelli che sui codici funzionano —
  `google cse, yandex, google, startpage, duckduckgo, brave`. Valgono anche se nel
  `settings.yml` sono spenti. Bing, Qwant e Mojeek sui numeri lunghi rispondono a caso
  (partite di football, canzoni, giochi): restano fuori.
- **LibreTranslate**: indirizzo del container (`http://libretranslate:5000`) ed eventuale chiave.
- **Browser**: indirizzo di browserless (`http://browser:3000`), per le pagine JavaScript.
- **Prova un codice**: la scheda trovata e il diario completo.
- **Com'è andata la ricerca**: stato dei motori (chi risponde, chi ci blocca e perché), le
  cause dei mancati degli ultimi carichi, i siti che rifiutano la lettura.
- **Prova a lotti**: incolli un elenco di codici e vedi, codice per codice, trovato o no e
  perché. Serve a capire se un problema è di un codice o di tutta la ricerca.

### SearXNG

Il `settings.yml` nella cartella `searxng/` è già pronto: uscita JSON attiva (senza, il
portale riceve un 403), sospensioni dei motori di un'ora invece che di un giorno, timeout da
6 secondi. La chiave segreta arriva da `SEARXNG_SECRET` nel `.env`: il file in repository non
contiene segreti. Nel portale come indirizzo va `http://searxng:8080` se sta nello stesso
compose, altrimenti l'indirizzo della macchina con la porta.

Tienilo aggiornato (`docker compose pull searxng`): le versioni vecchie non hanno alcuni
motori (`google cse`) e sospendono un motore per un giorno intero al primo CAPTCHA.

### Una nota sui contenuti

Foto e descrizioni sono opere protette di chi le ha fatte. I produttori le pubblicano perché
i rivenditori le usino per vendere i loro prodotti, i grossisti per lo stesso motivo; altri
negozi no. Per questo la ricerca preferisce produttori e grossisti, salta comparatori e
cataloghi con licenza, e non aggira chi chiede di non essere letto. L'uso dei contenuti sul
negozio resta responsabilità di chi li pubblica.

## 3. Lingua delle schede (e dell'interfaccia)

Sono due cose diverse.

**La lingua dell'interfaccia** la sceglie ognuno col selettore 🌐 in alto (italiano,
inglese, francese, tedesco, o quella del browser). Cambia solo i testi del portale: le schede
dei prodotti restano come sono.

**La lingua delle schede** è quella in cui finiscono nome, descrizione e specifiche sul sito
WooCommerce: italiano, inglese, francese, tedesco o spagnolo. Chi decide, in ordine:

1. il **sito** (*Siti → Modifica → Lingua delle schede di questo negozio*), se impostata: un
   negozio inglese riceve schede in inglese chiunque lanci il carico;
2. il selettore **Schede in** sopra il riquadro dei codici, che di partenza segue la lingua
   dell'interfaccia (interfaccia in inglese → schede in inglese);
3. la lingua del **cliente**.

Con la lingua delle schede cambia tutta la ricerca:

- la prima domanda ai motori si fa **nella lingua e nel paese delle schede** (it-IT, en-GB,
  fr-FR, de-DE, es-ES): escono per primi i negozi di quel paese, con testi già nella lingua
  giusta; solo se non bastano si cerca ovunque;
- i siti si aprono chiedendo quella lingua (`Accept-Language`), e fra più pagine dello stesso
  prodotto vince quella scritta nella lingua giusta;
- quello che arriva comunque in un'altra lingua si **traduce** con LibreTranslate (se è
  configurato) e la scheda lo segnala: «tradotta in automatico, rileggila». Senza
  LibreTranslate la scheda resta nella lingua trovata, con un avviso. Il compose carica solo
  italiano, inglese, francese, tedesco e spagnolo (`LT_LOAD_ONLY`): per tradurre anche dalle
  pagine olandesi, svedesi, polacche, russe… aggiungi `nl,sv,pl,ru` e compagnia (più RAM e
  disco);
- la cache è separata per lingua: lo stesso EAN cercato in italiano e in inglese sono due
  ricerche diverse.

## 4. Prezzi e schede da completare

**I prezzi non arrivano mai dalle fonti.** Il prezzo lo scrive il cliente nel portale, e solo
quello arriva su WooCommerce. Ogni riga ha **prezzo** e **prezzo scontato**: se lo scontato
non è inferiore al pieno viene segnalato, perché WooCommerce lo ignorerebbe in silenzio.

**Quando la ricerca non trova niente**, la riga resta *Non trovato* con una scheda vuota
pronta da compilare (*Compila a mano*): nome, marca, descrizione, immagini, specifiche,
prezzo. Appena è completa si pubblica come tutte le altre.

**Quante specifiche.** Certe pagine ne hanno anche novanta. Il portale ne scrive sei
(impostabile per sito, 0 = tutte): le prime sono quelle che contano.

**Campi obbligatori.** Si scelgono per sito, e valgono solo per i campi che quel giro scrive
davvero: il giro notturno non tocca i prezzi, quindi non pretende nemmeno che ci siano. Se ne
manca uno la riga resta ferma e non parte verso il sito, con scritto cosa manca.

## 5. Aspetto del portale e dati del negozio

**Nome e logo** del portale da *Impostazioni → Aspetto del portale*: compaiono in barra e
nella pagina di accesso. PNG, JPG, WebP o SVG fino a 2 MB; senza logo si mostra il nome
scritto. Logo e favicon stanno su rotte pubbliche (`/media/portale/logo`,
`/media/portale/favicon`) perché servono alla pagina di accesso.

**Tema chiaro e scuro.** Senza scelta esplicita segue il sistema operativo; il pulsante in
alto cicla fra *automatico*, *chiaro* e *scuro*. Si applica prima del disegno: niente lampo
bianco all'apertura.

**Favicon.** Di serie c'è quella del portale. Da *Impostazioni → Aspetto del portale →
Carica favicon* metti la tua: PNG quadrato da almeno 180×180 pixel (meglio 512×512), oppure
SVG o ICO, massimo 1 MB. Il formato si riconosce dal file, non dal nome. Vale per tutte le
pagine, compresa quella di accesso, e anche per `/favicon.ico`; se è un PNG (o WebP/JPG) fa
anche da icona sul telefono (`apple-touch-icon`). Si vede subito nella scheda, senza
ricaricare. Un'immagine non quadrata o più piccola di 180 pixel passa con un avviso; un SVG
con script o gestori di eventi viene rifiutato. *Torna a quella di serie* la toglie.

**Negozio.** Ogni cliente ha la sua scheda con ragione sociale, partita IVA, codice fiscale,
SDI, PEC, sede, contatti e logo. Sono dati interni al portale: non finiscono sul sito.

## 6. Stato dei prodotti

I prodotti **nascono pubblicati**. Si cambia per sito (*Stato dei prodotti creati*) e per
singolo carico (*Opzioni* sopra i codici).

## 7. Eliminare un carico

Dallo **Storico**, **Elimina**. Prima di fare qualsiasi cosa il portale mostra il conto:
quanti prodotti ha **creato** quel carico (gli unici eliminabili), quanti ne ha solo
**aggiornati** (erano già sul sito: non si toccano mai) e cosa succede alle immagini. Poi due
strade: *Elimina anche i prodotti dal sito*, oppure *Elimina solo dal portale*.

**Le immagini.** `DELETE /products/{id}` elimina il prodotto ma **non** le immagini: restano
nella libreria media. Cancellarle richiede `/wp/v2/media`, dove le chiavi WooCommerce non
valgono. Per questo nella scheda del sito ci sono due campi **facoltativi**: utente WordPress
e **Password per applicazioni** (Utenti → Profilo → Password per applicazioni). Se ci sono, le
immagini spariscono insieme ai prodotti.

## 8. Abbonamento

Ogni cliente ha stato (*in prova*, *attivo*, *sospeso*, *scaduto*), piano, scadenza e quota
mensile di prodotti pubblicabili (zero = nessun limite). Senza abbonamento valido si può
ancora cercare e guardare le schede: si blocca la pubblicazione sul sito, controllata anche
dentro la coda prima di ogni singola scheda. La quota si consuma solo sui prodotti creati
sul sito e si azzera il primo del mese.

## 9. Aggiornamento notturno

Si imposta **per singolo sito**, dalla scheda del sito.

- **Orario e giorni**, nel fuso di `TZ` (di serie `Europe/Rome`). Nessun giorno = tutte le
  notti.
- **Quali prodotti**: *solo quelli importati da qui* (il portale sa già cosa ha pubblicato:
  zero chiamate al sito per sceglierli) oppure *tutti quelli con un EAN sul sito*, sfogliando il
  catalogo via REST col tetto di `WC_MAX_SCAN_PAGES` (30 pagine = 3000 prodotti).
- **Quanti per volta** e **salta quelli freschi**: tetto per giro e giorni minimi fra due
  aggiornamenti dello stesso prodotto. Le schede trovate da meno di `CACHE_DAYS` giorni si
  riprendono dalla cache, senza nuove ricerche.

**Le politiche notturne sono separate da quelle manuali.** Di serie: descrizioni e
specifiche *sempre*, immagini e marca *solo se mancano*, **nome, categorie ed EAN mai**. Gli
aggiornamenti non consumano quota. Senza abbonamento attivo il giro non parte.

Il pianificatore non tiene niente in memoria: guarda `last_resync_at` e l'orologio. Se il
container si riavvia, al giro dopo riprende senza saltare né raddoppiare; c'è una finestra
di recupero di due ore. **Aggiorna ora** lancia lo stesso giro subito.

## 10. Cose da sapere

**Politiche di scrittura.** Ogni sito ha, campo per campo, tre scelte: *mai*, *solo se
vuoto*, *sempre*. Senza, al secondo giro si cancellerebbero le descrizioni sistemate a mano.

**Specifiche.** Non si creano attributi globali a raffica: se sul sito esiste già un
attributo globale con quel nome si usa quello (filtrabile), altrimenti si scrive un
attributo locale del prodotto. A scelta, per sito, le specifiche finiscono anche nel meta
`_td_product_specs` (shortcode `[td_specifiche]` e campi YOOtheme del plugin TD Product
Specs); senza il plugin il meta resta lì senza fare niente.

**Immagini.** Si passano gli indirizzi e le scarica WooCommerce nella libreria del cliente.
Ripubblicando lo stesso prodotto le immagini già presenti non si duplicano.

**Chiavi REST.** Servono con permesso *Lettura/Scrittura*. Stanno cifrate a riposo, le vede
solo l'amministratore (mascherate) e non compaiono in nessuna risposta dell'area cliente.

**Descrizioni.** L'HTML che arriva dalle pagine si ripulisce (solo paragrafi, elenchi,
grassetti e tabelle, senza attributi) al salvataggio, alla correzione a mano, prima di
scrivere su WooCommerce e prima di mostrarlo nel portale.

## 11. Struttura del codice

```
app/
  main.py        avvio, sessioni, intestazioni di sicurezza, pagine, favicon
  db.py          motore del database e aggiornamento automatico dello schema
  models.py      clienti, utenti, siti, carichi, righe, cache, corrispondenze
  config.py      variabili d'ambiente e versione (da VERSION.txt)
  store.py       impostazioni globali (ricerca, aspetto del portale)
  providers.py   catena delle fonti, cache per codice e lingua
  web_search.py  domande ai motori (SearXNG & c.), salute dei motori, ordine dei candidati
  web_source.py  lettura delle pagine, conferma del codice, pagine di elenco, fusione fra siti
  rete.py        lettura amichevole: robots.txt, una pagina alla volta per sito, blocchi
                 riconosciuti, solo indirizzi pubblici
  html_sicuro.py pulizia dell'HTML delle schede, controllo di SVG e immagini caricate
  diagnosi.py    diario della ricerca e cause del «non trovato»
  lingue.py      lingue delle schede: riconoscimento, intestazioni, paesi
  traduzione.py  LibreTranslate verso la lingua delle schede
  codici.py      EAN, codici produttore, codici interni 20-29
  quality.py     campi obbligatori e completezza delle schede
  wc_rest.py     dialogo con le REST di WooCommerce
  worker.py      coda: cerca, rimanda se i motori bloccano, pubblica
  scheduler.py   giro notturno
  billing.py     abbonamento e quota
  routers/       auth, admin, client
templates/       login e console
static/          CSS, Alpine, logica UI, favicon, traduzioni dell'interfaccia (i18n/)
searxng/         settings.yml del motore di ricerca
wordpress/       mu-plugin per il menu delle categorie (catasync-category-menu.php)
tests/           test automatici (python -m unittest discover -s tests; node --test tests/i18n.test.cjs)
.github/         controlli e release automatiche
```

## 12. Cosa usa delle REST WooCommerce

| Serve a | Chiamata WooCommerce |
|---|---|
| Verificare il collegamento | `GET /products?per_page=1` + `GET /system_status` |
| Categorie, attributi, marche | `GET /products/categories`, `/products/attributes`, `/products/brands` |
| Trovare un prodotto dall'EAN | `GET /products?global_unique_id=`, ripiego su `?sku=` |
| Creare | `POST /products` |
| Aggiornare | `PUT /products/{id}` |
| Marca nuova | `POST /products/brands` |
| Categorie: creare, eliminare | `POST` / `DELETE /products/categories` |
| Ordine delle categorie | `POST /products/categories/batch` (`menu_order`, a lotti da cento) |
| Menu del sito (col mu-plugin) | `GET` / `POST /wp-json/wc-catasync/v1/menu`, `POST …/menu/allinea` |

Autenticazione Basic su HTTPS. Se il server mangia l'intestazione `Authorization` — capita
con certe configurazioni Apache — il portale riprova da solo con le chiavi nell'indirizzo,
la modalità prevista da WooCommerce proprio per quel caso.

**Cosa scrive**: nome, descrizione, SKU dal codice produttore, EAN nel campo nativo
`global_unique_id`, prezzo e prezzo scontato, categorie, immagini, attributi, marca, più i
meta di servizio `_tdic_gtin`, `_tdic_synced`, `_tdic_mpn`, `_tdic_source` (servono a
riconoscere i prodotti già pubblicati).

## 13. Possibili sviluppi

- Export CSV e feed per i marketplace (Google Shopping, Meta, Trovaprezzi, Amazon): gli
  stessi dati già normalizzati, cambia solo l'imballaggio.
- Email a fine carico, con com'è andata.
- Chiave API per cliente, per spingere i codici da un gestionale.
- Marchio bianco completo: dominio del rivenditore (nome e logo ci sono già).
- Report dei consumi scaricabile: quanti prodotti al mese, per sito.
- Campi SEO: titolo e descrizione trovati mappati su Yoast o Rank Math, senza inventare niente.

## 14. Versioni e release

Ogni modifica che arriva su `main` diventa una release da sola, appena i controlli di GitHub
(test e build dell'immagine) sono passati. Lo fa `.github/workflows/release.yml`, le regole
stanno in `.github/scripts/rilascio.py`.

**Il numero.**

- Se in `VERSION.txt` c'è un numero che non è ancora uscito, esce quello: per una 2.1.0 basta
  scriverlo lì.
- Altrimenti sale di uno l'ultima cifra: 2.0.0 → 2.0.1 → 2.0.2.
- `[minor]` o `[major]` nel titolo del commit (la prima riga) fanno salire la cifra di mezzo
  (2.1.0) o la prima (3.0.0). Nel resto del messaggio non contano.
- `[skip ci]` nel messaggio di commit: niente controlli e niente release.

**Le note.** Si usa, nell'ordine:

1. `docs/releases/<versione>.md`, se c'è: note scritte a mano (quelle della 2.0.0 sono lì);
2. la voce `## <versione>` del `CHANGELOG.md`;
3. la voce `## Non rilasciato` del `CHANGELOG.md`: alla release prende numero e data;
4. altrimenti l'elenco dei messaggi di commit dall'ultima release, che finisce anche nel
   `CHANGELOG.md`.

**Il commit del workflow.** Quando il numero sale da solo, o il `CHANGELOG.md` va aggiornato,
il workflow aggiunge un commit «Versione X.Y.Z [skip ci]» su `main`: così il numero in fondo
alle pagine è sempre quello della release. Se lavori su una copia locale, prima della modifica
successiva fai `git pull`.

Se una release non parte (controlli falliti, per esempio), si rilancia da *Actions → Release →
Run workflow* dopo aver sistemato.

**Ritirare una release.** Se ne esce una sbagliata, scrivi il suo numero in
`docs/releases/ritirate.txt`, col motivo: al giro successivo il workflow toglie release e tag.
Il numero giusto lo rimetti in `VERSION.txt`.

## 15. Categorie e menu del sito

### Le categorie

Nella scheda **Categorie** c'è l'albero delle categorie del negozio, nell'ordine in cui le mostra
il sito: è l'ordine di *Prodotti → Categorie* in WooCommerce (il campo `menu_order`).

- **Crea**: nome e, se serve, la categoria madre («Dentro»). La nuova va in fondo al suo livello.
  Due categorie con lo stesso nome allo stesso livello non si possono avere.
- **Ordine**: si trascina una categoria sopra o sotto una sorella, oppure si usano le frecce, e si
  preme **Salva l'ordine**. Vale fra sorelle: una sottocategoria si sposta fra le sottocategorie
  della stessa madre. Finché l'ordine non è salvato, creare ed eliminare sono fermi.
- **Elimina**: prima una finestra dice quanti prodotti e quante sottocategorie ci sono dentro. I
  prodotti restano sul sito (WooCommerce mette in «Senza categoria» quelli che non hanno altre
  categorie), le sottocategorie salgono di un livello. Nel portale si tolgono le corrispondenze
  verso quella categoria, la categoria di riserva dei carichi che la usavano e la categoria delle
  righe che la usavano: quelle non ancora pubblicate tornano da completare.
- «Senza categoria» non compare e non si elimina: è la categoria di riserva di WooCommerce.

Le categorie create dal sito finiscono dove le mette WooCommerce: senza un ordine proprio stanno in
cima, finché non le sposti.

Creare, eliminare e ordinare scrive sul sito: serve l'account attivo, come per pubblicare (la
quota dei prodotti qui non conta).

### Il menu del sito

Il mu-plugin `wordpress/catasync-category-menu.php` («CataSync – Menu delle categorie») mette le
categorie sotto una voce del menu del sito e le tiene allineate. Si scarica anche dalla scheda
Categorie; va in `wp-content/mu-plugins/` e parte da solo.

- **Dove**: si sceglie il menu e la voce (per esempio «Prodotti») da *Categorie → Menu del sito* in
  CataSync, oppure sul sito da *Aspetto → Menu categorie*. Una voce che è già una categoria non si
  può scegliere.
- **Cosa fa**: ogni categoria ha la sua voce sotto quella scelta, le sottocategorie sotto la voce
  della loro categoria (con «Sottocategorie» spento vanno solo le principali). L'ordine è quello
  delle categorie; il nome della voce segue il nome della categoria, anche quando cambia.
  «Senza categoria» resta fuori.
- **Quando**: alla fine di ogni richiesta in cui una categoria nasce, cambia, viene eliminata o
  riordinata — da CataSync, da *Prodotti → Categorie* (anche trascinandole) o da un altro plugin.
  Riordinarne trenta in un colpo vuol dire un solo riallineamento.
- **Voci messe a mano**: sotto la voce scelta restano, dopo le categorie. Le voci delle categorie
  invece le gestisce il plugin: se le sposti a mano, al giro dopo tornano al loro posto. Se cambi la
  voce scelta, quelle create dal plugin sotto la voce di prima se ne vanno.
- **Eliminando una categoria** la sua voce sparisce e le voci figlie salgono di un livello, come
  quando si toglie una voce da *Aspetto → Menu*.
- **Allinea adesso** rimette tutto in ordine subito; funziona anche col plugin spento, per un
  allineamento una tantum.

CataSync parla col plugin sulle rotte `/wp-json/wc-catasync/v1/menu`: il prefisso `wc-` fa
accettare a WooCommerce le stesse chiavi REST del negozio. La chiave dev'essere di un utente che
può modificare i menu (amministratore o gestore del negozio) e avere il permesso di scrittura;
senza chiave le rotte non rispondono.
