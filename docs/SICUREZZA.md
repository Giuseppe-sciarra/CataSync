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

