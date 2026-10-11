# Third-party components

CataSync is released under AGPL-3.0-or-later. It uses the following third-party components,
each under its own licence.

## Portal (Python, installed by pip)

| Component | Licence |
|-----------|---------|
| [FastAPI](https://fastapi.tiangolo.com) / [Starlette](https://www.starlette.io) | MIT / BSD-3-Clause |
| [Uvicorn](https://www.uvicorn.org) | BSD-3-Clause |
| [SQLAlchemy](https://www.sqlalchemy.org) | MIT |
| [Psycopg 3](https://www.psycopg.org) (PostgreSQL driver) | LGPL-3.0 |
| [HTTPX](https://www.python-httpx.org) | BSD-3-Clause |
| [h2](https://github.com/python-hyper/h2) / hpack / hyperframe (HTTP/2) | MIT |
| [Pydantic](https://docs.pydantic.dev) / [email-validator](https://github.com/JoshData/python-email-validator) | MIT / Unlicense |
| [Jinja2](https://jinja.palletsprojects.com) | BSD-3-Clause |
| [python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0 |
| [itsdangerous](https://itsdangerous.palletsprojects.com) | BSD-3-Clause |
| [cryptography](https://cryptography.io) | Apache-2.0 OR BSD-3-Clause |

## Bundled assets

| Asset | Licence |
|-------|---------|
| [Alpine.js](https://alpinejs.dev) (`static/alpine.min.js`) | MIT |
| Interface, translations (`static/i18n`), icons, favicon and stylesheets | part of CataSync, AGPL-3.0 |

The interface is served by the application itself: the portal makes no requests to external CDNs.

## Services in `docker-compose.yml`

These run as separate containers from their official images; CataSync talks to them over HTTP
and does not redistribute them.

| Service | Role | Licence |
|---------|------|---------|
| [PostgreSQL](https://www.postgresql.org) 17 (`postgres:17-alpine`) | database | PostgreSQL License |
| [SearXNG](https://github.com/searxng/searxng) (`searxng/searxng`) | metasearch engine used to find product pages | AGPL-3.0 |
| [LibreTranslate](https://github.com/LibreTranslate/LibreTranslate) (`libretranslate/libretranslate`) | translation into the language of the product sheets — optional | AGPL-3.0 |
| [Browserless](https://github.com/browserless/browserless) (`browserless/chrome`) | headless Chrome for pages built with JavaScript — optional | check the licence of the image you deploy: recent Browserless releases are SSPL-1.0 or a commercial licence |
| [Python](https://www.python.org) 3.12 (`python:3.12-slim`) | base image of the portal | PSF License |

## Content read from the web

Product names, descriptions, specifications and photos found by the search belong to their
authors (manufacturers, distributors, shops). CataSync reads only the structured data that pages
publish for search engines, respects `robots.txt` and does not bypass anti-bot protections; using
that content on a shop remains the responsibility of whoever publishes it.
