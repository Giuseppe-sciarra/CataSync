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

**Magazzino** mostra i prodotti già sul sito, per categoria e con la ricerca: lì se ne modifica la
scheda intera, si cambiano al volo categoria e prezzo e si eliminano (capitolo 16). **Storico** elenca i carichi; **Categorie** gestisce le categorie del sito (creazione,
nome, eliminazione, ordine: vedi il capitolo 15) e mostra attributi globali e marche;
**Scheda cliente** mostra anagrafica, logo e abbonamento del cliente, in sola lettura: li
aggiorna l'amministratore.

**Guida** è la scheda delle istruzioni, tutte in un posto, divise in tre guide: prodotti (i
quattro passi del caricamento e gli stati), magazzino e categorie, ognuna con le schermate di
esempio (in italiano per l'interfaccia in italiano, in inglese per le altre lingue). Ci si arriva
anche dai pulsanti 📖 di Prodotti, Magazzino e Categorie, già sulla guida giusta. Chi entra per la prima volta trova in cima
a Prodotti un invito alla Guida: chiuso una volta (o aperta la Guida), il portale lo ricorda
sull'utente, quindi resta chiuso su ogni browser e dispositivo.

Le immagini della Guida stanno in `static/aiuto/` e si rifanno da un portale di prova con
dati dimostrativi: nomi inventati, siti `.example`.

## 2. La ricerca sul web

### Prima Icecat

Se in *Impostazioni → Icecat* c'è l'utenza ed è spuntato «Cerca prima su Icecat», ogni codice
**EAN** di ogni cliente si chiede prima a Icecat, con quell'unica utenza (nome utente, API access
token e Content access token del profilo su icecat.biz; i token si salvano cifrati). Icecat cerca
per EAN: i codici produttore vanno direttamente alla ricerca sul web.

- **Scheda completa** (nome, descrizione, foto): si usa quella e la ricerca sul web non parte.
  Niente motori, niente blocchi.
- **Scheda parziale**: si tiene quella di Icecat (nome, marca, codice, specifiche, testo) e la
  ricerca sul web aggiunge quello che manca, di solito le foto. La fonte risulta «Icecat + Ricerca
  sul web».
- **Non c'è**, o c'è solo con Full Icecat o fuori dal settore dell'abbonamento: parte la ricerca
  sul web. Nel diario «Perché?» Icecat è la prima riga, col motivo.
- **EAN non collegato**: Icecat risponde «The GTIN can not be found» quando nel suo catalogo
  nessun prodotto porta quell'EAN. A volte il prodotto c'è, ma senza quell'EAN: se la ricerca sul
  web trova marca e codice produttore (dalle pagine che riportano l'EAN), si richiede a Icecat per
  marca + codice («Samsung Electronics» si prova anche come «Samsung»). Se lo trova, la scheda
  Icecat diventa la base e il web riempie i buchi; nel diario: «scheda Icecat trovata col codice
  produttore …».
- **Lingua**: si chiede la scheda nella lingua del cliente; se nome, descrizione o specifiche ci
  sono solo in inglese si prendono in inglese e si traducono con LibreTranslate, con lo stesso
  controllo d'andata e ritorno della ricerca sul web.
- **Memoria**: le schede Icecat restano in archivio per sempre (`CACHE_DAYS=0`, di serie; un numero =
  giorni), i «non c'è» 3 giorni (`ICECAT_MISSING_DAYS`). «Riprova dati» le butta insieme a quelle web.
- **Prova un codice su Icecat** (EAN, oppure marca e codice produttore) nella stessa scheda delle Impostazioni: cosa arriva, quante foto e
  specifiche, se c'è stato da tradurre, o perché non c'è. Utile per capire la copertura prima di
  passare a Full Icecat. Anche la *prova a lotti* e il *confronto fonti* del cliente passano
  prima da Icecat.

Open Icecat (gratis) copre i marchi che pagano Icecat per essere pubblicati; Full Icecat tutti
quelli dell'abbonamento. Se un'utenza sola possa servire tutti i clienti lo decidono le
condizioni di Icecat: va chiesto a loro.

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
   Una marca scritta come sigla dal grossista («Produttore: SMG») vale come il nome intero
   («Samsung») se ne è la sigla.
7. **Seconda lettura col browser**: una pagina giusta ma povera (senza descrizione o con una
   foto sola) si riapre con Chrome (il container `browser`, da indicare in *Browser per le
   pagine fatte di JavaScript*: `http://browser:3000`) e si tiene la lettura più ricca: tante
   schede caricano descrizione e galleria col JavaScript, dentro le linguette. Le pagine che ci
   hanno respinti (403, verifiche anti-bot) non si riaprono.
8. **Completamento col codice produttore**: se le pagine trovate con l'EAN danno una scheda
   povera (niente descrizione vera o una foto sola), si fa una seconda ricerca col codice
   produttore che quelle pagine riportano («Réf. fournisseur AE040RXEDEG/EU») e si aggiungono
   le pagine nuove che lo confermano: è così che il distributore francese senza descrizione
   viene completato da Rexel e Sonepar. Le pagine di un kit («AE040RXEDEG/EU + modulo
   interno») si scartano: la loro descrizione parlerebbe d'altro. Nel diario queste ricerche
   e pagine sono segnate «col codice produttore». Se in quel momento i motori bloccano, la
   scheda povera resta in memoria solo 2 giorni invece di 30, così la ricerca successiva ci
   riprova.
9. **Foto**: si scaricano e si misurano; via segnaposto, loghi, icone e immagini troppo
   piccole.
10. **Traduzione** nella lingua delle schede di quello che arriva in un'altra lingua. Le parole
   che il traduttore storpia («bibloc» → «Bilancio») restano com'erano, quelle che lascia
   nella lingua di partenza («extérieur») si traducono da sole.

I nomi scritti tutti in maiuscolo («ESTERNE EHS SPLIT R32 (MONOFASE) 4.») si riscrivono in
forma da negozio («Esterne EHS split R32 (monofase) 4.»); codici, misure e sigle corte
restano come sono.

Le schede trovate restano in archivio (tabella `ean_cache`, per codice e lingua: solo testi,
specifiche e indirizzi delle foto, mai i file) per sempre con `CACHE_DAYS=0`, di serie; un numero
sono i giorni dopo i quali si ricercano. Un codice già cercato da un cliente qualunque è pronto per
tutti gli altri. «Riprova» ricerca davvero; togliere un prodotto dal catalogo butta solo le schede
parziali o «non trovato», quelle complete restano. I prezzi **non arrivano mai** dalle pagine.

### Cataloghi diretti
Quando i motori non danno pagine col codice, o sono tutti bloccati, in coda ai candidati vanno i
**cataloghi** (`web_search.CATALOGHI`, `ARCHIVI_EAN`): indirizzi con l'EAN dentro, nessun motore in
mezzo. Tre tipi:
- **archivi EAN** (go-upc, barcodelookup, buycott, ean-search, eandata, upcitemdb…): l'indirizzo è
  già la scheda, con l'EAN dichiarato; si provano per primi perché costano una pagina sola;
- **comparatori, negozi e marketplace** per paese (idealo, geizhals, billiger, ledenicheur, eBay,
  ManoMano, trovaprezzi, MediaWorld, Unieuro, Leroy Merlin, Cdiscount, Fnac, PcComponentes, Currys,
  Newegg, grossisti di termoidraulica…): la pagina di ricerca è un elenco, si segue il primo link
  fatto come una scheda e la scheda vale solo se dichiara l'EAN;
- **vetrine** (Google Shopping, Bing Shopping): i link portano ai negozi e diventano candidati come
  quelli di un motore.

Chi risponde 403 o CAPTCHA va in pausa come un motore (15 minuti, poi il doppio, fino a 6 ore) e
compare in *Impostazioni → motori* con via «catalogo». Le pagine di catalogo hanno un budget loro
(14 per codice) e non rubano posti ai siti trovati dai motori. Coi motori bloccati e i cataloghi a
vuoto il codice si rimanda come prima, non finisce fra i «non trovato».

Alcuni cataloghi (eBay, ManoMano, trovaprezzi, kelkoo, ePrice, pricerunner, Walmart) **vietano la
ricerca ai programmi nel robots.txt**: di serie il portale li rispetta e non li apre. In
*Impostazioni → Cataloghi diretti* c'è l'interruttore **«Leggi i cataloghi anche se robots.txt lo
vieta»** (spento di serie): acceso, si aprono anche quelli, sempre col ritmo di una persona e le
pause automatiche. È una scelta di chi gestisce il portale, non del programma. Amazon resta fuori
in ogni caso: non dichiara l'EAN in pagina (la scheda non si confermerebbe mai) e il suo anti-bot
brucia l'indirizzo del server per tutti i clienti. Il controllo sugli indirizzi interni resta sempre.

### Banche dati libere
Prima della ricerca sul web, se Icecat non ha l'EAN o la scheda è incompleta, si chiede l'identità
del prodotto a banche dati pubbliche gratuite e senza chiavi (`app/banche_dati.py`): **UPCitemdb**
(piano trial: 100 richieste al giorno per IP, oltre risponde 429 e si va avanti senza segnare il
buco) e **Open Food / Beauty / Pet Food / Products Facts**. Danno nome, marca, codice produttore e
qualche foto; la descrizione si prende solo se è nella lingua della scheda. Con marca e codice
produttore in mano la ricerca sul web va a segno anche quando i motori col solo EAN non trovano
niente: in quel caso si cerca direttamente il codice produttore e le pagine si confermano su quello.
Nel diario della ricerca compaiono dopo Icecat («upcitemdb», «openfoodfacts»… o «banche dati
libere: non c'è nelle banche dati»).

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

**Pausa per motore.** Un motore che risponde con un blocco non si richiama subito: ogni domanda
in più allungherebbe il blocco e terrebbe l'indirizzo del server nella sua lista nera. Resta in
pausa 15 minuti, poi 30, 60, 120… fino a 6 ore, e intanto cercano gli altri (di solito Google CSE
e Yandex reggono). A fine pausa rientra con una domanda sola: se risponde torna normale, se
blocca ancora la pausa raddoppia. DuckDuckGo, Startpage, Brave, Google e Bing, anche quando
rispondono, si interrogano al massimo una volta ogni 30–60 secondi. Se sono in pausa tutti i
motori scelti il codice non si cerca a vuoto: riparte a fine pausa senza contare un rinvio. Le
pause si azzerano quando il portale riparte.

**Ricerca a giri.** I codici non partono tutti di fila: dopo 5 codici cercati davvero il portale
si ferma 2 minuti e poi riprende da solo (si cambiano in *Impostazioni → Ricerca automatica sul
web*, «Codici per giro» e «Pausa fra un giro e l'altro»). Un carico da 50 codici ci mette circa
mezz'ora, ma ai motori arriva il ritmo di una persona invece di una raffica, che è quello che fa
scattare i CAPTCHA. Contano solo i codici che arrivano ai motori: quelli già in memoria no.
Durante la pausa la barra del carico dice a che ora riparte; le pubblicazioni sul negozio intanto
vanno avanti. Vale anche per la prova a lotti. Quanti codici incolla il cliente non cambia nulla:
conta il ritmo con cui partono le domande, non la lunghezza dell'elenco.

### Impostazioni → Ricerca automatica sul web

- **Motore**: SearXNG (consigliato), DuckDuckGo senza chiave (solo per provare), Brave Search,
  Google Programmable Search o SerpAPI con le loro API a pagamento.
- **Riserva: chiave Brave Search API** (solo SearXNG, facoltativa): quando i motori di SearXNG
  bloccano o non danno almeno due risultati col codice, la stessa domanda va all'API di Brave,
  che è un servizio a pagamento con accordo e non blocca mai (5 $ ogni mille ricerche, 5 $ di
  credito gratis al mese; la chiave si crea su api-dashboard.search.brave.com). Un codice ne usa
  da una a due, anche nella ricerca col codice produttore. Con più clienti è la differenza fra
  «a volte i motori bloccano» e «funziona sempre». Senza chiave non cambia niente.
- Con un motore a pagamento (Brave, SerpAPI, Google) **giri e pause non si applicano**: servono
  solo ai motori letti senza accordo. L'API ufficiale di Google è chiusa ai nuovi clienti e si
  spegne il 1° gennaio 2027.
- **Motori da interrogare** (solo SearXNG): vuoto = quelli che sui codici funzionano —
  `google cse, yandex, google, startpage, duckduckgo, brave`. Valgono anche se nel
  `settings.yml` sono spenti. Bing, Qwant e Mojeek sui numeri lunghi rispondono a caso
  (partite di football, canzoni, giochi): restano fuori.
- **LibreTranslate**: indirizzo del container (`http://libretranslate:5000`) ed eventuale chiave.
- **Browser**: indirizzo di browserless (`http://browser:3000`), per le pagine JavaScript.
- **Prova un codice**: la scheda trovata e il diario completo.
- **Com'è andata la ricerca**: stato dei motori (chi risponde, chi ci blocca e perché), le
  cause dei mancati degli ultimi carichi, i siti che rifiutano la lettura.
- **Registro delle ricerche**: ogni codice cercato dai clienti, dal più recente, con l'esito
  (trovato, trovato a metà, non trovato, rimandato, errore) e il motivo: motori che bloccano,
  siti che rifiutano la lettura, campi che mancano, sito da cui è arrivata la scheda. **Non**
  dice chi l'ha cercato: niente cliente, negozio, utente, nome del carico, titolo o messaggi di
  errore del negozio (possono contenere il suo indirizzo). Filtri (di serie «Solo i problemi»),
  «Dettagli» per il diario completo, **CSV** per il foglio di calcolo (fino a 5000 righe). «Trovato
  a metà» vuol dire che la ricerca non ha portato nome, descrizione, foto, specifiche, marca o
  codice produttore: prezzo e categoria li mette il cliente e non contano. I riallineamenti
  automatici non compaiono, solo le ricerche dei carichi.
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

Le schede di Icecat arrivano con la licenza di Icecat e dei marchi che le pubblicano: per i
contenuti con diritti chiari è la fonte da preferire. Foto e descrizioni sono opere protette di chi le ha fatte. I produttori le pubblicano perché
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

**Scheda cliente.** Ogni cliente ha la sua scheda con ragione sociale, partita IVA, codice
fiscale, SDI, PEC, sede, contatti e logo. Li compila l'amministratore in *Clienti → Modifica*
(anche il logo, che si salva subito); il cliente li vede nella scheda **Scheda cliente** ma non li
può cambiare. Nella stessa scheda il cliente trova l'abbonamento (stato, piano, scadenza con i
giorni che mancano, quota del mese con la barra, se può pubblicare e, se no, perché), quanti
prodotti ha pubblicato in tutto, da quando è cliente, i siti collegati e l'aggiornamento notturno.
Sono dati interni al portale: non finiscono sul sito.

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
  aggiornamenti dello stesso prodotto. Le schede in archivio (`CACHE_DAYS`, di serie per sempre) si
  riprendono da lì, senza nuove ricerche.

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

**Specifiche già sul prodotto.** Le righe di TD Product Specs che un prodotto ha già, anche
scritte a mano, non si perdono: con *solo se vuoto* sulle specifiche si aggiungono in coda le
voci che mancano (confrontate come fa il plugin: maiuscole, accenti, spazi doppi e due punti
finali non contano); con *sempre* si aggiornano anche i valori delle stesse voci; le voci che il
portale non conosce restano comunque. Se non c'è niente da aggiungere il prodotto non si riscrive.

**Filtri dalle specifiche.** TD Product Specs 1.1 trasforma in filtri le etichette scelte in
*Prodotti → Filtri specifiche* (termini di un attributo WooCommerce, oppure le Marche) e lo dice
su `GET /wp-json/wc-td-specs/v1/filtri`, con le stesse chiavi REST. Il portale lo legge (ogni
cinque minuti al massimo) e quelle voci le scrive sempre, prima delle altre e in più rispetto a
*Quante specifiche scrivere*, così il prodotto compare sotto il filtro. Plugin assente o più
vecchio: elenco vuoto, tutto come prima. I filtri li costruisce il plugin sul sito, anche dove il
portale non c'è.

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
  banche_dati.py banche dati pubbliche gratuite per EAN (UPCitemdb, Open*Facts)
  posta.py       posta in uscita (SMTP del .env): codici di verifica
  due_fattori.py verifica in due passaggi: TOTP, codice via email, passkey, codici di riserva
  manutenzione.py avviso di manutenzione nella pagina di accesso
  web.py         template Jinja condivisi
  routers/       auth, admin, client, magazzino
templates/       accesso, pannello (dashboard.html)
static/          CSS, Alpine, logica UI, passkey.js, favicon, traduzioni dell'interfaccia (i18n/)
searxng/         settings.yml del motore di ricerca
wordpress/       mu-plugin facoltativo per il menu delle categorie (catasync-category-menu.php)
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
| Categorie: creare, rinominare, eliminare | `POST /products/categories`, `PUT` / `DELETE /products/categories/{id}` |
| Magazzino: elenco e ricerca | `GET /products` con `status=any` (o `trash`), `category`, `search` + `search_fields[]=name,sku,global_unique_id`, `_fields` |
| Magazzino: categoria e prezzi | `PUT /products/{id}` (`categories`, `regular_price`, `sale_price`) |
| Magazzino: scheda completa | `GET /products/{id}`; `PUT /products/{id}` (`name`, `status`, `sku`, `global_unique_id`, prezzi, scorte, `categories`, `images` con `id` o `src`, `short_description`, `description`, `attributes`, `brands`, meta `_td_product_specs`) |
| Filtri dalle specifiche (TD Product Specs 1.1, facoltativo) | `GET /wp-json/wc-td-specs/v1/filtri`: le etichette che il sito usa come filtri |
| Magazzino: nel cestino, ripristino | `DELETE /products/{id}` senza `force`; `PUT /products/{id}` con `status` e `slug` di prima |
| Magazzino: per sempre, svuota | `DELETE /products/{id}?force=true`; `POST /products/batch` (`delete`, a lotti da cento) |
| Ordine delle categorie | `POST /products/categories/batch` (`menu_order`, a lotti da cento) |

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

Nella scheda **Categorie** ci sono le categorie del negozio, nell'ordine in cui le mostra il sito:
è l'ordine di *Prodotti → Categorie* in WooCommerce (il campo `menu_order`).

- **Crea**: nome e, se serve, la categoria madre («Dentro»). La nuova va in fondo al suo livello.
  Due categorie con lo stesso nome allo stesso livello non si possono avere.
- **Rinomina**: il nome si cambia sul posto (Invio salva, Esc annulla) e cambia sul sito e nel
  portale. L'indirizzo della categoria (slug) resta quello di prima, così i link non si rompono.
  Rinominare non tocca un ordine non ancora salvato.
- **Ordine**: si trascina una categoria sopra o sotto una sorella, oppure si usano le frecce, e si
  preme **Salva l'ordine**. Vale fra sorelle: una sottocategoria si sposta fra le sottocategorie
  della stessa madre. Finché l'ordine non è salvato, creare ed eliminare sono fermi.
- **Elimina**: prima una finestra dice quanti prodotti (e quante sottocategorie, se ce ne sono) ci
  sono dentro. I prodotti restano sul sito (WooCommerce mette in «Senza categoria» quelli che non
  hanno altre categorie), le sottocategorie salgono di un livello. Nel portale le
  righe che la usavano e non sono ancora pubblicate tornano da completare, e i carichi che l'avevano
  come categoria di riserva la perdono.
- «Senza categoria» non compare e non si elimina: è la categoria di riserva di WooCommerce.

Le categorie create dal sito finiscono dove le mette WooCommerce: senza un ordine proprio stanno in
cima, finché non le sposti.

Creare, eliminare e ordinare scrive sul sito: serve l'account attivo, come per pubblicare (la
quota dei prodotti qui non conta). L'utente della chiave REST deve poter gestire le categorie dei
prodotti (amministratore o gestore del negozio), altrimenti il sito risponde 403.

I nomi con «&» (per esempio «Pompe & circolatori») si vedono e si confrontano come sono scritti:
WooCommerce li manda come «&amp;», il portale li rimette in chiaro.

### Il menu del sito (facoltativo)

CataSync non tocca mai il menu del sito. Se le categorie nel menu le vuoi, c'è il mu-plugin
`wordpress/catasync-category-menu.php` («CataSync – Menu delle categorie»): le mette sotto una voce
del menu e le tiene allineate. Va in `wp-content/mu-plugins/` e parte da solo; senza, il menu resta
com'è.

- **Dove**: sul sito, in *Aspetto → Menu categorie*: si sceglie la voce (per esempio «Prodotti» nel
  menu principale), si spunta **Attivo** e si salva. Una voce che è già una categoria non si può
  scegliere.
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

Il plugin si accorge da solo delle categorie create, eliminate o riordinate da CataSync: il portale
non lo chiama. Le stesse impostazioni si leggono e si cambiano anche via REST su
`/wp-json/wc-catasync/v1/menu` (il prefisso `wc-` fa accettare a WooCommerce le chiavi REST del
negozio): serve un utente che può modificare i menu, amministratore o gestore del negozio; senza
chiave le rotte non rispondono.

## 16. Magazzino

Nella scheda **Magazzino** ci sono i prodotti che sono già sul sito, letti ogni volta da
WooCommerce: niente copie nel portale che possano restare indietro.

- **Categorie a sinistra**: un clic e a destra compaiono i prodotti della categoria e delle sue
  sottocategorie; «Tutti i prodotti» li mostra tutti. C'è anche «Senza categoria», se ha prodotti.
- **Ricerca** per nome, SKU o EAN, anche dentro la categoria scelta. Con un WooCommerce vecchio,
  che non conosce `search_fields`, si cerca solo nel nome e nel testo.
- **Pagine** da 15, 20, 30, 50 o 100 prodotti: la scelta resta nel browser.
- **Modifica** (o un clic sul nome) apre la **scheda completa**, letta in quel momento dal sito:
  nome, stato (pubblicato, bozza, in revisione, privato), marca, SKU, EAN (con il controllo della
  cifra finale), prezzo e scontato, scorte (quantità o disponibilità), categorie (se ne aggiungono
  e se ne tolgono, almeno una resta), foto, descrizione breve, descrizione e specifiche. *Salva sul
  sito* manda solo quello che è cambiato. Uno SKU o un EAN già usati da un altro prodotto vengono
  rifiutati da WooCommerce e il portale lo dice in chiaro.
  - **Foto**: la prima è la principale; si riordinano con le frecce, si tolgono (restano nella
    libreria media), si aggiungono con l'indirizzo o con *Carica dal computer* (JPG, PNG, WebP fino
    a 8 MB). Il file caricato resta per due giorni in `uploads/foto-temporanee/` e si legge da
    `/media/foto/<nome>`: è il sito a scaricarlo da lì e a metterlo nella sua libreria, quindi
    serve `PUBLIC_URL` raggiungibile dal sito. Senza `PUBLIC_URL` si aggiungono solo con
    l'indirizzo.
  - **Descrizioni**: editor semplice (grassetto, corsivo, sottolineato, titoletti, elenchi, link)
    o HTML. Prima di scrivere sul sito l'HTML viene ripulito: niente script, iframe, moduli,
    gestori di eventi o link `javascript:`. Una descrizione che ha già video, iframe o moduli non si
    apre nell'editor (si rovinerebbe): si cambia da WordPress. Gli shortcode (`[...]`) restano come
    testo. Il testo salvato da WordPress senza `<p>`, a righe vuote, nell'editor diventa a paragrafi;
    aprire la scheda e chiuderla senza scrivere non cambia niente.
  - **Specifiche**: gli attributi del prodotto (più valori separati da `|`), e se il sito ha la
    tabella delle specifiche anche il meta `_td_product_specs`. Gli attributi globali del sito e
    quelli usati per le varianti si vedono ma restano come sono. Se il prodotto ha già le righe di
    TD Product Specs (anche scritte a mano) l'editor mostra quelle, cioè quello che il negozio
    mostra e filtra; salvando, una riga non toccata tiene il suo valore com'era (link e grassetto
    compresi) e a un prodotto con le specifiche solo nel plugin non si creano attributi WooCommerce.
  - Le righe di Prodotti collegate a quel prodotto si allineano (nome, marca, prezzi, categoria),
    così una nuova pubblicazione non rimette i dati vecchi.
- **Categoria e prezzo al volo**: un clic sulla categoria o sul prezzo nella riga apre la modifica
  veloce, salvata subito sul sito. Se il prodotto sta in più categorie cambia la prima e le altre
  restano. Lo scontato dev'essere
  sotto il pieno, come in Prodotti. I prodotti con varianti hanno i prezzi sulle varianti, che si
  cambiano dal sito. Le righe del portale collegate a quel prodotto si allineano: una nuova
  pubblicazione non rimette il prezzo vecchio.
- **Elimina** chiede conferma e sposta il prodotto nel **cestino del sito**, lo stesso di
  WordPress (*Prodotti → Cestino*): sparisce subito dal negozio. Il portale si segna com'era
  (pubblicato, bozza…) per rimetterlo uguale.
- **Cestino**: è il cestino del sito, quindi ci sono anche i prodotti cestinati da WordPress.
  *Ripristina* rimette lo stato di prima (pubblicato se il prodotto l'ha cestinato WordPress) e
  l'indirizzo di prima, togliendo il `__trashed` che WordPress aggiunge allo slug. *Elimina per
  sempre* e *Svuota il cestino* lo cancellano davvero; le immagini restano nella libreria media.
  Nel portale, le righe di Prodotti legate a un prodotto eliminato per sempre spariscono, così
  nessuno lo ripubblica per sbaglio.
- **Giorni del cestino**: WordPress svuota il cestino da solo dopo `EMPTY_TRASH_DAYS` giorni (nel
  `wp-config.php` del sito, di serie 30). Il portale non lo può leggere: in *Siti → Modifica* si
  mette lo stesso numero, che serve a mostrare quando si svuota.

Modificare ed eliminare scrive sul sito: servono l'account attivo e una chiave REST di un
amministratore o gestore del negozio, come per le categorie.

## 17. Account e verifica in due passaggi

Ogni utente, cliente o amministratore, ha la sua pagina **Account**: si apre con un clic
sull'email in alto a destra. Lì attiva la verifica in due passaggi e cambia la password.

**I tre modi.** Si possono attivare tutti; all'accesso, dopo la password, il portale propone per
prima la passkey, poi l'app, poi l'email, e si passa dall'uno all'altro con un clic.

- **Passkey**: impronta, volto o PIN del dispositivo, oppure una chiave di sicurezza USB o NFC. Se
  ne registra una per dispositivo, con un nome per riconoscerla. Vogliono HTTPS e sono legate al
  dominio di `PUBLIC_URL`.
- **App di autenticazione**: si inquadra il QR (o si ricopia la chiave) con Google o Microsoft
  Authenticator, 1Password, Aegis…, e si conferma col primo codice. Un codice vale 30 secondi e
  una volta sola.
- **Codice via email**: sei cifre, dieci minuti, spedite col server SMTP del `.env` nella lingua
  dell'interfaccia. Senza posta configurata non si attiva.

**Codici di riserva.** Arrivano col primo modo attivato: dieci, si vedono una volta (*Copia* o
*Scarica*), ognuno vale una volta. Dalla pagina di accesso: *Hai perso il telefono? Usa un codice
di riserva*. Chi entra così viene avvisato di quanti gliene restano. *Genera nuovi codici*
invalida i vecchi.

**La password.** Attivare, cambiare o spegnere un modo chiede di nuovo la password: scritta una
volta, per dieci minuti non la richiede. Nel secondo passaggio dell'accesso si possono sbagliare
sei codici ogni dieci minuti, poi bisogna aspettare.

**Il promemoria.** Finché un utente non ha attivato niente, in cima al pannello c'è una riga
piccola che lo invita a farlo, e accanto all'email un pallino giallo. Attivato un modo,
spariscono. Non blocca niente: è un invito.

**Chi ha perso tutto.** L'amministratore, in *Utenti*, vede nella colonna *2FA* i modi attivi di
ognuno e con *Togli 2FA* li spegne: l'utente rientra con la password e la riattiva. Se a restare
fuori è l'amministratore:

```bash
docker exec -it catasync-hub python -m app.due_fattori togli admin@example.com
```

## 18. Avviso di manutenzione

In *Impostazioni → Avviso di manutenzione*: la casella *Mostra l'avviso nella pagina di
accesso*, il giorno, l'ora d'inizio e quella di fine (nel fuso `TZ` del portale; se la fine viene
prima dell'inizio vale il giorno dopo) e una nota facoltativa, che si mostra così come la scrivi.
L'anteprima si aggiorna mentre scrivi.

Chi apre la pagina di accesso vede il riquadro sopra email e password: «Manutenzione
programmata» con giorno e orari in grassetto, scritti nella sua lingua; durante i lavori diventa
«Manutenzione in corso» e passata l'ora di fine sparisce da solo, anche se la casella resta
accesa. Non si salva acceso un avviso già scaduto.

