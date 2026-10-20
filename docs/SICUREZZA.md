# Sicurezza — audit e scelte di progetto

Rilevato il 20/07/2026 sulla v1.0, correzioni applicate nella v1.1. Aggiornato l'11/10/2026 per la v2.0
(ricerca sul web come unica fonte: contenuti e indirizzi che arrivano da siti di terzi).

---

## 1. Isolamento fra clienti — verificato

Ogni rotta che un cliente può chiamare passa da uno di questi due controlli:

- `_visible_site()` — carica il sito e, se chi chiede non è amministratore, confronta
  `site.client_id` con `user.client_id`. Diverso → 403.
- `_visible_job()` — risale al sito del job e richiama il controllo sopra.

Non esiste una rotta cliente che accetti un id senza passare da lì. `GET /api/sites` e
`GET /api/jobs` filtrano in SQL sul `client_id` dell'utente, quindi nemmeno enumerando
si vede altro.

Prove eseguite con un utente del cliente A contro le risorse del cliente B:

| Tentativo | Esito |
|---|---|
| Elenco siti | mostra solo i propri |
| `GET /api/sites/{altrui}/taxonomy` | 403 |
| `POST /api/sites/{altrui}/mapping` | 403 |
| `GET /api/lookup?site_id={altrui}` | 403 |
| `POST /api/jobs` su sito altrui | 403 |
| `GET /api/profile?client_id={altrui}` | 403 |
| `PUT /api/profile?client_id={altrui}` | 403 |
| `POST /api/profile/logo?client_id={altrui}` | 403 |
| `GET /media/logo/{altrui}` | 404 |
| Qualunque rotta `/api/admin/*` | 403 |

**Un cliente vede solo il proprio negozio.** Confermato.

---

## 2. Problemi trovati e corretti

**Enumerazione degli account tramite i tempi di risposta.** Il login usciva subito se
l'email non esisteva, senza calcolare l'hash: misurando i millisecondi si capiva quali
indirizzi erano registrati. Ora viene comunque verificato un hash fittizio, così le due
risposte costano uguale.

**Nessun limite ai tentativi di accesso.** Si potevano provare password all'infinito. Ora
c'è una finestra scorrevole di 8 tentativi ogni 5 minuti, contata sia sull'indirizzo di
rete sia sull'account: il primo contatore ferma chi prova mille password, il secondo chi
prova la stessa email da più indirizzi. Oltre il limite, 429.

**Richieste da altri siti.** Il cookie di sessione era `SameSite=lax`, adeguato ma non
massimo. Ora è `SameSite=strict` e c'è un controllo di provenienza su tutte le richieste
che scrivono: se `Origin` o `Referer` non corrispondono al portale, 403.

**Intestazioni di sicurezza assenti.** Aggiunte `X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy`, `Permissions-Policy`, e `Cache-Control: no-store` sulle risposte API.

**Documentazione delle API esposta.** `/api/docs` era raggiungibile e mostrava l'intera
superficie del portale. Disattivata.

**Indirizzo del sito non validato.** Si poteva salvare un sito con schema strano o con
utente e password dentro l'URL. Ora si accettano solo `http` e `https`, senza credenziali
incorporate.

**Caricamenti senza limiti.** Il CSV veniva letto tutto in memoria: ora ha un tetto di 5 MB.
Il logo ha un tetto di 2 MB, tipo verificato, nome del file generato dall'id del cliente e
mai preso da quello caricato. Gli SVG con `<script>`, `javascript:`, `<foreignObject>`,
gestori di eventi (`onload=` & c.) o contenuti incorporati vengono rifiutati: un SVG è
codice, non un'immagine. Dalla v2.0 loghi e favicon SVG si servono anche con
`Content-Security-Policy: … sandbox`, così aperti da soli non eseguono niente. La favicon
caricabile riconosce il formato dai byte (PNG, ICO, SVG, WebP, JPG), non dal nome del file.

**Il logo era un file pubblico.** Ora passa da una rotta autenticata che verifica chi
sta chiedendo cosa.

**Superficie sul sito del cliente azzerata.** Nella v1.2 il plugin companion è stato tolto:
si usano le REST API di WooCommerce. Un pezzo di codice in meno da mantenere su ogni sito,
e un endpoint in meno esposto. Le chiavi REST hanno permessi che il cliente vede e revoca
dal proprio pannello quando vuole.

**Nessun avviso sulle chiavi.** Se `SECRET_KEY` restava quella di esempio o `FERNET_KEY`
non era impostata, nessuno se ne accorgeva. Ora il container lo scrive nei log all'avvio.

---

## 3. Quello che era già a posto

- Password con PBKDF2-SHA256, 240.000 iterazioni, sale per utente.
- Chiavi REST dei siti, chiavi dei motori di ricerca e di LibreTranslate cifrate a riposo
  con Fernet.
- Cookie di sessione firmato, `HttpOnly`, `Secure` in produzione.
- Query sempre tramite SQLAlchemy: nessuna concatenazione.
- I segreti non compaiono mai nelle risposte: solo mascherati, e in chiaro una volta sola
  al momento della generazione.
- Le chiavi REST dei siti sono visibili al solo amministratore, mascherate anche lì.
  Verificato che l'area cliente non le restituisca in nessun campo di nessuna rotta.

---

## 4. Cosa resta a carico dell'infrastruttura

- **HTTPS obbligatorio sui siti dei clienti.** Le chiavi WooCommerce viaggiano in Basic
  auth, e se il server mangia l'intestazione finiscono nell'indirizzo: in chiaro si
  leggono. Senza HTTPS non si collega un sito.
- **Il portale non va esposto direttamente.** Nel compose la porta è legata a `127.0.0.1`.
- **`FERNET_KEY` va salvata da qualche parte.** Se la perdi, le chiavi REST dei siti e
  quelle dei motori di ricerca diventano illeggibili e vanno reinserite tutte.
- **`ADMIN_PASSWORD` va svuotata dopo il primo avvio.**
- **Il rate limit è in memoria.** Se un giorno il portale gira su più repliche, va
  spostato su Redis, altrimenti ogni replica conta per conto suo.
- **Backup**: `pg_dump` (o il volume `pgdata`) per il database, più il volume `/data` per loghi, immagini di categoria e cache.

---

## 5. Nessuna rotta anonima

Tolta la creazione delle categorie è sparito anche l'unico endpoint senza autenticazione
(`/media/cat/{token}`, che serviva a far scaricare a WooCommerce le immagini caricate).
Ora ogni rotta del portale richiede una sessione, tranne la pagina di accesso e il logo e la
favicon dell'agenzia che quella pagina deve mostrare (`/media/portale/logo`,
`/media/portale/favicon`, `/favicon.ico`).
Le rotte del secondo passaggio dell'accesso (`/api/auth/2fa/verifica`, `/2fa/email/invia`,
`/2fa/passkey/opzioni`, `/2fa/passkey/verifica`, `/2fa/annulla`) non chiedono la sessione
completa ma quella «in attesa», che nasce solo dopo la password giusta e dura dieci minuti.

## 6. Note sul modello di rischio

Chi ruba le chiavi REST di un sito ha su quel sito i permessi che quelle chiavi hanno, non
di più, e nulla sugli altri siti. Si revocano dal pannello WooCommerce del cliente e si
sostituiscono nella scheda del sito: nessun intervento sul portale.

Le chiavi sono cifrate a riposo con Fernet e non tornano mai in chiaro nelle risposte: il
consumer secret si vede solo mascherato.

Un amministratore del portale può vedere tutto: è per costruzione, non è un difetto. Il
registro attività tiene traccia di chi ha fatto cosa, incluso l'indirizzo di rete al login.

---

## 7. Contenuti e indirizzi che arrivano da siti di terzi (v2.0)

Con la ricerca sul web come fonte, il portale legge pagine che non controlla nessuno: vanno
trattate come input ostile.

**HTML attivo nelle descrizioni — corretto.** Una descrizione dichiarata in JSON-LD può
contenere `<script>`, `<img onerror=…>`, `<iframe>`. Prima finiva così com'era sia nella
console del portale (codice eseguito nella sessione dell'amministratore) sia su WooCommerce:
con chiavi REST di un amministratore WordPress, che può scrivere HTML libero, sarebbe stato
codice eseguito dai visitatori del negozio del cliente. Ora (`app/html_sicuro.py`):

- nelle descrizioni restano solo paragrafi, elenchi, grassetti, titoletti e tabelle, **senza
  attributi**; script, stili, iframe, form, immagini e link spariscono (dei link resta il testo);
- nome, marca, codice produttore, categoria e specifiche diventano testo puro;
- la pulizia avviene in tre punti: quando la scheda si legge dal sito, quando qualcuno la
  corregge a mano (un utente cliente non può iniettare codice che l'amministratore poi apre)
  e subito prima di scrivere su WooCommerce (vale anche per le schede salvate prima);
- l'interfaccia ripulisce comunque le descrizioni prima di mostrarle, con un documento
  inerte (`DOMParser`): nessuno script eseguito, nessuna immagine caricata.

**Richieste verso la rete interna (SSRF) — corretto.** Gli indirizzi da aprire arrivano dai
risultati dei motori e dalle pagine stesse (link, foto). Una pagina furba poteva indicare
`http://192.168.1.1/`, `http://db:5432/` o il pannello di Proxmox, e il portale ci andava.
Ora pagine, `robots.txt`, foto e browser aprono **solo indirizzi pubblici**: il nome si
risolve e ogni indirizzo deve essere globale (niente reti private, loopback, link-local,
100.64/10, nomi corti da container o `.local`). Il controllo vale anche sui rimandi (hook
sulle richieste httpx). Restano fuori dal controllo, perché interni per scelta e impostati
dall'amministratore: SearXNG, LibreTranslate, browserless, i siti WooCommerce e i connettori
JSON.

Limite noto: fra la risoluzione del nome e la connessione un DNS ostile potrebbe cambiare
risposta (DNS rebinding). Per chiudere anche quello, il container del portale non deve
poter raggiungere la LAN: rete Docker dedicata o regole sul firewall di Proxmox.

**Lettura amichevole.** `robots.txt` rispettato (token `tdsynccatalog` e `*`), una richiesta
ogni secondo e mezzo per sito, pagine oltre 4 MB troncate, al massimo 16 tentativi e 8
pagine lette per prodotto. Le verifiche anti-bot (Cloudflare, Akamai, DataDome, Imperva,
CAPTCHA, 403/429) non si aggirano: la pagina si salta e il motivo finisce nel diario. Il
browser serve solo per le pagine fatte di JavaScript e per i guasti del server.


---

## 8. Verifica in due passaggi e avviso di manutenzione (v2.7)

**Accesso in due tempi.** Con la verifica attiva, la password giusta non apre la sessione: il
cookie ricorda solo chi sta facendo il secondo passaggio (`mfa_uid`, dieci minuti) e
`current_user()` non lo considera entrato. La sessione vera nasce solo dopo il secondo
passaggio. Codici provati: al massimo sei ogni dieci minuti per utente, contati **nel
database** e prenotati prima del controllo (`UPDATE … WHERE errori_2fa < 6`), così il tetto
tiene anche con richieste in parallelo, da indirizzi diversi o dopo un riavvio. Ogni errore va
nel registro attività con l'indirizzo di rete (`2fa_errato`).

**Codici usati una volta sola, anche in parallelo.** Intervallo TOTP, codice via email, codice di
riserva e sfida della passkey si consumano con un UPDATE condizionato (confronta e scambia): di due
richieste partite nello stesso istante col medesimo codice ne passa una.

**App di autenticazione (TOTP, RFC 6238).** Segreto da 160 bit cifrato a riposo con Fernet;
diventa buono solo quando l'utente lo conferma col primo codice. Si accettano l'intervallo
corrente, quello prima e quello dopo, e mai due volte lo stesso (`totp_ultimo_passo`): un
codice letto da sopra la spalla non si riusa. Il QR si genera nel portale (segno), senza
servizi esterni.

**Codice via email.** Sei cifre casuali (`secrets`), valide dieci minuti, cinque tentativi, un
invio ogni 30 secondi e al massimo sei l'ora per utente. Nel database c'è solo l'impronta HMAC-SHA256 legata a `SECRET_KEY` e
all'utente, mai il codice; la sessione (cookie firmato ma leggibile) non contiene niente del
codice.

**Passkey (WebAuthn).** Verifica crittografica con py_webauthn: origine, `rp_id`, sfida, firma e
contatore. La sfida sta nel database e si cancella appena letta, così una risposta catturata non
si rigioca. `rp_id` e origine attesi vengono da `PUBLIC_URL`, come il controllo di provenienza:
una pagina di phishing su un altro dominio non ottiene firme valide. L'identificativo utente
passato all'autenticatore è un'impronta, non l'email né l'id.

**Codici di riserva.** Dieci, da 10 caratteri senza simboli ambigui (circa 49 bit l'uno), si
vedono una volta sola; nel database ci sono solo le impronte HMAC, e ognuno si cancella appena
usato. Spento l'ultimo modo, spariscono anche loro.

**Operazioni delicate.** Attivare o cambiare un modo (nuovo QR, codice via email, passkey),
spegnerlo, togliere una passkey o rigenerare i codici chiede di nuovo la password, con il suo
limite di tentativi; riscritta, vale dieci minuti (`conferma_t` nella sessione firmata). Così con
una sessione rubata non si toglie l'account al titolare né lo si lega a un altro telefono.
L'amministratore può togliere la verifica a un utente (*Utenti → Togli 2FA*, nel registro come
`user_2fa_reset`); per l'amministratore stesso c'è solo la riga di comando dentro il container.

**Limiti noti.**
- I contatori della *password* restano in memoria. Prima un'ondata di richieste inventate (oltre
  5000 chiavi) li azzerava tutti; ora si tengono solo i tentativi veri e si potano quelli vecchi.
  Il contatore per indirizzo usa l'IP che arriva dal proxy: con `--forwarded-allow-ips "*"` (il
  Dockerfile) si fida di qualunque `X-Forwarded-For`. Meglio mettere lì l'IP del reverse proxy.
- Le sessioni già aperte non si chiudono quando si attiva la verifica: valgono fino alla scadenza
  (`SESSION_MAX_AGE`).
- I codici di riserva e quelli via email sono impronte legate a `SECRET_KEY`: cambiandola, i
  codici di riserva non valgono più. I segreti TOTP sono cifrati con `FERNET_KEY` (se manca, con
  una chiave derivata da `SECRET_KEY`): persa quella, le app vanno riconfigurate. In entrambi i
  casi si rientra togliendo la verifica (*Utenti → Togli 2FA* o la riga di comando).

**Avviso di manutenzione.** La nota scritta dall'amministratore si mostra come testo (Jinja la
sfugge e la pagina la scrive con `textContent`), mai come HTML. L'avviso compare solo nella
pagina di accesso e non rivela niente oltre a quello che l'amministratore ha scritto.

