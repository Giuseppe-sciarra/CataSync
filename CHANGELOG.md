# Changelog

## 2.10.0 — 2026-10-26

### Fixed
- **Prodotti trovati che non arrivavano sul negozio** («Trovato, ma non è arrivato sul negozio»):
  WooCommerce le foto le scarica da sé, come un robot (niente Referer, «WordPress/x.y»), e
  molti siti dei produttori a un robot le negano; per una sola foto negata WooCommerce rifiuta
  il prodotto intero («Error getting remote image …»). Il portale invece le aveva verificate
  come un browser, quindi la scheda risultava pronta e poi falliva tre volte di fila. Ora:
  - le foto prese dal web passano dal portale (`app/foto.py`): le scarica come un browser,
    dalla pagina dove le ha trovate, controlla che siano JPG, PNG, WebP o GIF e le dà a
    WooCommerce da `PUBLIC_URL/media/foto/…`. Senza `PUBLIC_URL` si va come prima;
  - se WooCommerce rifiuta comunque una foto, si toglie quella e si riprova: il prodotto va
    sul sito con le altre e la riga lo segnala. Se non ne resta nessuna e il sito pretende
    le foto, la riga dice di caricarle dal computer (o, se le rifiuta anche dal portale, di
    controllare che il portale sia raggiungibile dal server del sito);
  - le scritture dei prodotti con foto aspettano fino a 180 secondi invece di `SITE_TIMEOUT`;
  - gli errori del sito si salvano spiegati in italiano (foto, SKU o EAN già usati), col
    messaggio originale fra parentesi; dopo la pubblicazione la scheda punta alle foto nella
    libreria del sito.
- **Registro delle ricerche**: per le schede non arrivate sul negozio dice anche il perché
  (foto non scaricate, SKU o EAN già su un altro prodotto, chiavi REST rifiutate, sito che non
  risponde), senza mostrare l'indirizzo del sito. Vale anche per gli errori già registrati.

### Added
- **Foto dal computer nelle schede del catalogo** (Correggi la scheda e Rivedi una a una), non
  solo da indirizzo: `POST /api/items/{id}/foto`. Nella correzione le foto si vedono in
  miniatura, si mettono in ordine e la prima è la principale, come nel Magazzino. Le foto
  caricate restano sul portale finché una scheda le usa (cartella `foto-schede`); quelle che
  nessuno usa più si buttano dopo una settimana.

### Changed
- **Storico**: «Dettagli» (cliente) e «Apri» (amministratore) aprono la scheda del carico subito
  sotto la sua riga, non più in fondo a tutto l'elenco; la riga aperta resta evidenziata e si porta
  a schermo sotto la barra in alto. «Chiudi» la richiude lì.

## 2.9.0 — 2026-10-20

### Added
- **Banche dati libere** prima della ricerca sul web (`app/banche_dati.py`): UPCitemdb (piano trial,
  100 richieste al giorno per IP, nessuna chiave) e Open Food / Beauty / Pet Food / Products Facts.
  Danno nome, marca, codice produttore e foto; la descrizione solo se nella lingua della scheda. Al
  limite giornaliero si va avanti senza segnare il buco. Nel diario compaiono dopo Icecat.
- Con marca e codice produttore noti, se i motori col solo EAN non trovano pagine si cerca il codice
  produttore e le pagine si confermano su quello.
- **Cataloghi diretti** (`web_search.CATALOGHI`, `ARCHIVI_EAN`): coi motori a vuoto o bloccati si
  cercano i codici senza motore in mezzo. Archivi EAN (go-upc, barcodelookup, buycott, ean-search,
  eandata, upcitemdb…), un centinaio di comparatori, negozi e marketplace per paese (idealo,
  geizhals, billiger, eBay, ManoMano, trovaprezzi, MediaWorld, Unieuro, Cdiscount, Fnac,
  PcComponentes, Currys, Newegg, grossisti di termoidraulica…) e le vetrine Google Shopping e Bing
  Shopping, i cui link ai negozi diventano candidati. Si segue il link della scheda, che vale solo se
  dichiara l'EAN. Chi respinge va in pausa come un motore e si vede fra i motori in Impostazioni;
  budget a parte (14 pagine di catalogo per codice). Motori bloccati + cataloghi a vuoto = si rimanda.
- Interruttore **«Leggi i cataloghi anche se robots.txt lo vieta»** in *Impostazioni → Cataloghi
  diretti*, spento di serie: acceso apre anche eBay, ManoMano, trovaprezzi, kelkoo, ePrice,
  pricerunner, Walmart. Amazon resta fuori comunque. Il controllo sugli indirizzi interni resta.

### Changed
- **Archivio delle schede**: `CACHE_DAYS=0` (nuovo valore di serie) tiene per sempre le schede
  trovate, per codice e lingua, in `ean_cache` (solo testi e indirizzi delle foto). Un codice
  cercato una volta è pronto per tutti i clienti. «Riprova» ricerca davvero; togliere un prodotto
  dal catalogo butta solo le schede parziali o «non trovato», non quelle complete.

### Removed
- **Pagina pubblica** (presentazione, prezzi, «Richiedi informazioni», informativa privacy) e la sua
  scheda in amministrazione: la radice del portale porta all'accesso (o al pannello, se già dentro).
  Cosa fa il portale si spiega sul sito dell'agenzia. Via `CONTACT_TO`; la posta SMTP resta per il
  codice via email della verifica in due passaggi. Via il carattere Instrument Sans.

## 2.8.1 — 2026-10-19

### Fixed
- Icecat risponde HTTP 400 «The GTIN can not be found» quando non ha l'EAN: prima risultava un
  guasto («Icecat non risponde come dovrebbe»), ora è «Icecat non ha questo EAN nel suo catalogo»
  e si passa alla ricerca sul web come previsto.

### Added
- **Icecat per codice produttore**: se l'EAN non c'è ma la ricerca sul web trova marca e codice
  produttore, si richiede a Icecat per marca + codice; trovata, la scheda Icecat fa da base e il
  web riempie i buchi.
- La prova in *Impostazioni → Icecat* accetta anche marca e codice produttore.

## 2.8.0 — 2026-10-19

### Added
- **Icecat come prima fonte**, con un'utenza sola per tutto il portale (*Impostazioni → Icecat*:
  nome utente, API access token, Content access token, cifrati). Ogni EAN di ogni cliente si chiede
  prima a Icecat: scheda completa → la ricerca sul web non parte; parziale → la completa il web;
  assente (o solo con Full Icecat, o fuori settore) → cerca il web. I testi che Icecat ha solo in
  inglese si traducono con LibreTranslate.
- **Prova un EAN su Icecat** in Impostazioni: cosa arriva o perché non c'è.
- Icecat anche nella prova a lotti e nel confronto delle fonti del cliente; nel diario «Perché?»
  è la prima riga, col suo esito.

### Changed
- Il vecchio collegamento a Icecat per cliente resta tolto: l'utenza ora è una, del portale.

## 2.7.4 — 2026-10-19

### Added
- **Brave Search API di riserva a SearXNG** (*Impostazioni → Ricerca automatica sul web*): quando
  i motori di SearXNG bloccano o non trovano il codice, la stessa domanda va all'API di Brave,
  che non blocca mai. Facoltativa: senza chiave si usa solo SearXNG. Vale anche per la ricerca
  col codice produttore.
- **Seconda lettura col browser** delle pagine giuste ma povere (senza descrizione o con una foto
  sola): Chrome le riapre dopo il JavaScript e si tiene la lettura più ricca.

### Changed
- Con un motore a pagamento giri e pause non si applicano più: servono solo ai motori letti
  senza accordo (SearXNG, DuckDuckGo).
- La nota su Google Programmable Search dice che l'API è chiusa ai nuovi clienti e si spegne il
  1° gennaio 2027.

### Fixed
- Righe che non sono codici («docker inspect searxng --format», descrizioni incollate per
  sbaglio) non si cercano più: consumavano ricerche e facevano scattare i blocchi. Da una riga
  come «8801643654689 SAMSUNG» si cerca l'EAN.

## 2.7.3 — 2026-10-19

### Added
- **Completamento col codice produttore.** Se le pagine trovate con l'EAN danno una scheda
  povera (niente descrizione o una foto sola), si cerca anche il codice produttore che
  riportano e si fondono le pagine nuove che lo confermano. Caso 8801643654689: chironpro.com
  (francese, senza descrizione, una foto) si completa con Rexel e Sonepar (italiani, con
  descrizione e foto). I kit con un altro modello nel nome si scartano. Nel diario le ricerche
  e le pagine di questa fase sono segnate «col codice produttore».

### Changed
- Il codice produttore conferma anche senza la coda «/EU» e se compare nel nome della pagina;
  due pagine con lo stesso codice produttore sono coerenti anche se i nomi non si somigliano.
- Una marca scritta come sigla dal grossista («Produttore: SMG») è coerente col nome intero
  («Samsung») e, se il titolo porta il nome intero, si usa quello.
- I nomi tutti in maiuscolo diventano leggibili («Esterne EHS split R32 (monofase) 4.»).
- Le specifiche non ripetono più codici e marca, che hanno i loro campi («Cod. produttore»,
  «Produttore», «Réf. …»).
- Una scheda povera resta in memoria 2 giorni invece di 30: la ricerca successiva ci riprova.

### Fixed
- Foto di Sonepar scartate come segnaposto: il «default.aspx» nel percorso è il programma che
  serve le foto, non un'immagine di riserva.
- Traduzione: le parole lasciate nella lingua di partenza («Unità Samsung Bibloc extérieur»)
  si traducono da sole e si rimettono solo se il viaggio di ritorno le conferma.

## 2.7.2 — 2026-10-19

### Added
- **Registro delle ricerche** in *Impostazioni*: ogni codice cercato dai clienti con esito e
  motivo (motori che bloccano, siti che rifiutano, campi mancanti, sito d'origine), filtri,
  diario «Dettagli» ed esportazione CSV. Senza dati dei clienti: niente cliente, negozio, utente,
  nome del carico, titolo né messaggi d'errore del negozio.
- **Ricerca a giri**: dopo 5 codici cercati davvero il portale si ferma 2 minuti e riparte da
  solo (configurabile in *Impostazioni → Ricerca automatica sul web*). I codici già in memoria
  non contano; durante la pausa la barra del carico dice quando riparte e le pubblicazioni vanno
  avanti. Vale anche per la prova a lotti.

### Fixed
- Schede sbagliate dai negozi PrestaShop (es. 8801643654689 su chironpro.com): il nome e la marca
  si prendevano dal primo `itemprop` della pagina, che era un prodotto collegato (INTUIS). Ora si
  legge solo il blocco del prodotto che riporta il codice, e il nome deve somigliare al titolo
  visibile della pagina.
- L'insegna del negozio (`og:site_name`, «CHIRON Distribution») non passa più per marca: la marca
  arriva dall'etichetta «Marque / Marca / Brand» della scheda; il codice produttore anche da
  «Réf. fournisseur», «Codice produttore», «MPN».
- Le foto PrestaShop `…-large_default/…` non vengono più scartate come segnaposto.
- Traduzione: LibreTranslate a volte inventa sui termini tecnici («bibloc» → «Bilancio»). Nome e
  valori di una parola si ritraducono all'indietro e le parole che non tornano restano com'erano.
- I motori «in pausa» non contano più come nuovi blocchi nella salute della ricerca.

## 2.7.1 — 2026-10-19

### Changed
- **Pausa per i motori che bloccano.** Un motore che risponde con un CAPTCHA, «troppe richieste»
  o accesso negato non viene più richiamato a ogni domanda (era quello che teneva vivo il blocco):
  resta fuori 15 minuti, poi 30, 60… fino a 6 ore, e intanto cercano gli altri. Finita la pausa
  rientra con una domanda sola; se risponde torna normale, se blocca ancora la pausa raddoppia.
- I motori più permalosi (DuckDuckGo, Startpage, Brave, Google, Bing) si interrogano al massimo una
  volta ogni 30–60 secondi anche quando rispondono; Google CSE e Yandex tengono la cadenza normale.
  Il giro diretto su DuckDuckGo si salta se è in pausa o è stato appena interrogato.
- Se tutti i motori scelti sono in pausa il codice non si cerca a vuoto: riparte da solo a fine
  pausa e non conta fra i rinvii.
- Nel diario «Perché?» i motori in pausa compaiono in grigio con l'ora di ripresa; in
  *Impostazioni → Com'è andata la ricerca* lo stato «in pausa» con l'ora in cui riprende.
- `searxng/settings.yml`: tolti ahmia, torch e wikidata, che non servono ai codici e a ogni
  avvio riempivano il log di errori.

## 2.7.0 — 2026-10-18

### Added
- **Avviso di manutenzione** nella pagina di accesso: da *Impostazioni → Avviso di manutenzione*
  si accende con giorno, ora d'inizio, ora di fine (anche a cavallo della mezzanotte) e una nota
  facoltativa, con l'anteprima mentre si scrive. Compare sopra email e password, ben in vista, con
  giorno e orari in grassetto nella lingua di chi legge; durante i lavori diventa «Manutenzione in
  corso» e passata l'ora di fine sparisce da solo. Non compare nella pagina pubblica.
- **Verifica in due passaggi** per clienti e amministratori, dalla nuova pagina **Account** (clic
  sull'email in alto): **app di autenticazione** (codice QR o chiave da ricopiare), **codice via
  email** (sei cifre, dieci minuti, col server SMTP del `.env`) e **passkey** (impronta, volto,
  PIN del dispositivo o chiave di sicurezza). Si possono attivare tutti; all'accesso, dopo la
  password, si sceglie quello che si ha sotto mano.
- Dieci **codici di riserva** col primo modo attivato, da copiare o scaricare, ognuno buono una
  volta; si rigenerano da *Account*. Chi entra con uno di questi viene avvisato di quanti ne restano.
- Finché la verifica non è attiva, in cima al pannello una **riga piccola** invita ad attivarla, e
  un pallino accanto all'email lo ricorda; attivata, sparisce.
- In *Utenti* la colonna **2FA** con i modi attivi e **Togli 2FA** per chi ha perso telefono e
  codici. Per l'amministratore chiuso fuori: `python -m app.due_fattori togli <email>` dentro il
  container.
- Da *Account* si **cambia la password**.
- Tutto tradotto in inglese, francese e tedesco; anche l'email col codice parte nella lingua
  dell'interfaccia.

- Sicurezza della verifica: codici sbagliati contati nel database (sei ogni dieci minuti per
  utente, anche in parallelo), ogni codice e ogni sfida valgono una volta sola anche con richieste
  simultanee, al massimo sei codici via email l'ora.

### Changed
- Attivare, cambiare o spegnere un modo di verifica, togliere una passkey o rigenerare i codici
  chiede di nuovo la password (vale dieci minuti), con un limite di tentativi; il limite vale ora
  anche per il cambio della password.

### Fixed
- I limiti ai tentativi di accesso in memoria non si azzerano più tutti insieme quando le chiavi
  superano le 5000: si tengono solo i tentativi veri e si potano quelli vecchi.
- Nuove dipendenze: `webauthn` (passkey) e `segno` (codici QR), con `pyOpenSSL`, `cbor2` e
  `asn1crypto` fissati per restare compatibili con `cryptography` 44.

## 2.6.0 — 2026-10-16

### Added
- **Pagina pubblica** all'indirizzo del portale, scura e in stile prodotto: apertura col pannello
  vero, il problema (a mano contro col portale), cinque esempi che girano da soli con la schermata
  vera, come funziona in quattro passi con un codice a barre dal vivo che controlla la cifra finale,
  le funzionalità a riquadri con una registrazione del pannello, i prezzi (mensile e annuale, col
  risparmio, i mesi gratis e il confronto a barre), le domande frequenti e una chiusura con l'email.
- In alto **Accedi** e **Richiedi informazioni**: un modulo col piano (Mensile o Annuale, già scelto
  se si arriva dal pulsante di quel prezzo e scritto nella mail), nome, cognome, email e messaggio,
  spedito col server SMTP del `.env` (`SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USER`,
  `SMTP_PASSWORD`, `SMTP_FROM`, `CONTACT_TO`), con *Rispondi a* chi ha scritto. Campo trappola e
  limite di invii contro lo spam; funziona anche senza JavaScript.
- **Informativa privacy** di serie, scritta per il portale e coi dati del titolare, oppure la tua.
- Nel piede i **dati legali**. Nell'amministrazione la scheda **Pagina pubblica**: accesa o spenta,
  dati legali, prezzi e nota, chi riceve le richieste (con *Manda una mail di prova*), informativa.
- Tutto tradotto in inglese, francese e tedesco, carattere ospitato dal portale.

- **Specifiche e filtri** con TD Product Specs: le righe che un prodotto ha già, anche scritte a
  mano, restano e il portale completa le voci che mancano (con *Sempre* aggiorna anche i valori delle
  stesse voci). Dalla 1.1 del plugin il portale legge quali etichette il sito usa come filtri e
  quelle le scrive sempre, prima delle altre e oltre il limite di specifiche. Nel Magazzino la
  scheda mostra e salva le righe del plugin, quando il prodotto le ha.

### Changed
- Il pannello si apre su **/app**; la radice `/` è la pagina pubblica (spenta, porta all'accesso).
- Il selettore della lingua può stare dove la pagina lo vuole (nella pagina pubblica, nel piede).

## 2.5.0 — 2026-10-15

### Added
- Nel Magazzino, **Modifica** (o un clic sul nome) apre la **scheda completa** del prodotto com'è
  sul sito: nome, stato, marca, SKU, EAN, prezzo e scontato, scorte, categorie, foto, descrizione
  breve, descrizione e specifiche. *Salva sul sito* manda solo quello che è cambiato e allinea le
  righe di Prodotti collegate.
- **Foto**: si riordinano, si tolgono, si aggiungono con l'indirizzo o si **caricano dal
  computer** (JPG, PNG, WebP fino a 8 MB; serve `PUBLIC_URL`, perché il sito scarica il file dal
  portale).
- **Descrizioni** con un editor semplice (grassetto, corsivo, titoletti, elenchi, link) o in HTML,
  ripulite prima di scriverle sul sito. Quelle con video, contenuti incorporati o moduli restano a
  WordPress.
- **Scheda cliente** al posto di Negozio: il cliente vede anagrafica, logo, abbonamento (stato,
  piano, scadenza con i giorni che mancano, quota del mese, se può pubblicare), prodotti
  pubblicati, siti collegati e aggiornamento notturno, in sola lettura.
- In *Clienti → Modifica* l'amministratore ha tutta l'anagrafica (anche codice fiscale, SDI, CAP,
  paese, sito web) e il **logo del cliente**.
- Guida magazzino: «Modificare la scheda», con le schermate.

### Changed
- I dati del cliente e il logo li cambia solo l'amministratore: il cliente che prova a salvarli
  riceve un rifiuto.
- Categoria e prezzo al volo si cambiano con un clic sulla cella della riga.
- Il nome del cliente in alto resta su una riga.

### Fixed
- «1 prodotto pubblicato questo mese» al singolare.

## 2.4.0 — 2026-10-15

### Added
- Scheda **Magazzino**: i prodotti già sul sito per categoria (a sinistra le categorie, a destra
  i loro prodotti), con la ricerca per nome, SKU o EAN e le pagine da 15, 20, 30, 50 o 100.
- Dal Magazzino si cambiano **categoria e prezzi** di un prodotto, salvati subito sul sito.
- **Elimina** sposta il prodotto nel **cestino del sito** (lo stesso di WordPress). La vista
  **Cestino** mostra quel cestino con *Ripristina* (stato e indirizzo di prima), *Elimina per
  sempre* e *Svuota il cestino*.
- In *Siti → Modifica*, **giorni del cestino** (come `EMPTY_TRASH_DAYS` del sito, di serie 30) per
  mostrare quando WordPress svuota il cestino.
- Guida divisa in tre: **Guida prodotti**, **Guida magazzino**, **Guida categorie**, con le
  schermate.

## 2.3.0 — 2026-10-15

### Added
- Scheda **Guida**: le istruzioni tutte in un posto (caricare i prodotti in quattro passi, gli
  stati dei prodotti, le categorie), ognuna con la schermata di esempio in italiano o in inglese.
  I pulsanti 📖 di Prodotti e Categorie portano alla sezione giusta.
- Per chi entra la prima volta, in cima a Prodotti, un invito alla Guida. Chiuso una volta resta
  chiuso: lo ricorda il portale sull'utente, non il browser, quindi vale su ogni dispositivo.

### Changed
- La «Come funziona» non sta più dentro la pagina Prodotti: è nella Guida. Chi l'aveva già chiusa
  non vede nemmeno l'invito.

### Fixed
- Mentre la ricerca è in corso la riga dice «Scheda in arrivo…» invece di «Scheda non trovata».

## 2.2.0 — 2026-10-15

### Added
- **Rinomina** nella scheda Categorie: il nome si cambia sul posto (Invio salva, Esc annulla) e
  cambia sul sito e nel portale. Lo slug resta quello, così i link alla categoria non si rompono;
  un ordine non ancora salvato non si perde.
- Di nuovo le **sottocategorie**: con «Dentro» la categoria nuova nasce dentro un'altra, in fondo
  alle sue sorelle.

### Fixed
- I nomi delle categorie con «&» arrivavano da WooCommerce come «&amp;» e si vedevano così; ora sono
  in chiaro, anche nei controlli sui doppioni.

## 2.1.1 — 2026-10-15

### Changed
- **Scheda Categorie più semplice**: si aggiungono, si eliminano e si mettono in ordine le
  categorie, e basta. Una categoria nuova è sempre principale (la scelta «Dentro» non c'è più,
  anche l'API ignora la categoria madre) e va in fondo all'elenco. Le sottocategorie che il sito ha
  già si vedono ancora sotto la loro categoria.
- Tolti dalla scheda i riquadri **Menu del sito** e **Corrispondenze categoria**: CataSync non tocca
  più il menu del sito. Il mu-plugin resta facoltativo e si imposta solo dal sito (Aspetto → Menu
  categorie); le corrispondenze già salvate continuano a valere nelle importazioni.
- La conferma di eliminazione e la nota sotto l'elenco parlano di sottocategorie solo se ce ne sono.

### Fixed
- Il messaggio per il «403» del mu-plugin dava la colpa solo alla chiave REST: ora dice anche del
  firewall o del plugin di sicurezza che può bloccare l'indirizzo.

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
