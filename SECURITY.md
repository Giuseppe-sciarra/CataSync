# Security policy

🇮🇹 [Versione italiana più sotto](#italiano)

## Supported versions

Only the latest release receives security fixes. If you run an older version, update first and
check whether the problem is still there.

| Version | Supported |
|---------|-----------|
| 2.0.x   | ✅        |
| < 2.0   | ❌        |

## Reporting a vulnerability

**Please do not open a public issue, discussion or pull request for a security problem.**
Report it privately instead:

👉 **[Report a vulnerability](https://github.com/Giuseppe-sciarra/CataSync/security/advisories/new)**
(GitHub private vulnerability reporting — only the maintainer can see it)

A useful report contains the version (`VERSION.txt`, also shown at the bottom of every page),
what an attacker can do and what they need first (a client account, an admin account, a
malicious product page found by the search…), steps to reproduce and, if relevant, your
deployment details (reverse proxy, database, browser).

You get an acknowledgement as soon as possible. The problem is fixed in a new release, listed in
the [CHANGELOG](CHANGELOG.md); once the fix is out a security advisory is published, crediting you
unless you prefer to stay anonymous. Please give a reasonable time to ship the fix before
disclosing details publicly.

## Scope

**In scope** — the code in this repository:

- login, sessions, roles (admin / client) and the isolation between clients
- the handling of third-party content read by the web search: HTML in descriptions, names,
  images, links (stored XSS), and the addresses the portal opens (SSRF towards your network)
- uploads (logos, favicon, CSV) and the WooCommerce REST keys stored by the portal
- the REST API used by the interface

**Out of scope:** problems in your own deployment rather than in the code (reverse proxy or TLS
configuration, an exposed or weak `.env`, a portal reachable from the internet without HTTPS),
vulnerabilities of WordPress/WooCommerce themselves, of SearXNG, LibreTranslate or Browserless
(report them to their projects), and the policies of the sites the search reads.

## What this release does not contain

No production database, no real `.env`, no uploaded files, no saved keys. Blank secrets in
`.env.example` must be set before deployment; the application creates its own schema in a new
database. Design notes and the security review are in [docs/SICUREZZA.md](docs/SICUREZZA.md)
(Italian).

---

<a id="italiano"></a>
## 🇮🇹 Italiano

**Non aprire issue, discussioni o pull request pubbliche per un problema di sicurezza.**
Segnalalo in privato:
👉 **[Segnala una vulnerabilità](https://github.com/Giuseppe-sciarra/CataSync/security/advisories/new)**
(segnalazione privata di GitHub: la vede solo il manutentore).

Riceve correzioni solo l'ultima versione (oggi la 2.0.x). In una segnalazione utile ci sono la
versione (`VERSION.txt`, scritta anche in fondo a ogni pagina), cosa può fare chi attacca e cosa
gli serve prima (un account cliente, un account amministratore, una pagina prodotto costruita
apposta e trovata dalla ricerca…), i passi per riprodurre il problema e, se servono, i dettagli
dell'installazione.

Rientrano: accesso, sessioni, ruoli e isolamento fra clienti; i contenuti di terzi letti dalla
ricerca sul web (HTML nelle descrizioni, nomi, foto, link) e gli indirizzi che il portale apre;
i caricamenti (loghi, favicon, CSV) e le chiavi REST di WooCommerce custodite dal portale.
Non rientrano i problemi della tua installazione (proxy, TLS, `.env` esposto o debole), quelli di
WordPress/WooCommerce, SearXNG, LibreTranslate o Browserless, né le regole dei siti letti.

Questa versione non contiene database, `.env` veri, file caricati o chiavi salvate. Le scelte di
sicurezza e la revisione del codice sono in [docs/SICUREZZA.md](docs/SICUREZZA.md).
