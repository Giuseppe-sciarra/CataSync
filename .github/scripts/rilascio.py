#!/usr/bin/env python3
"""Crea la release di GitHub per l'ultima modifica di main (lo lancia .github/workflows/release.yml).

Numero della versione:
  - quello di VERSION.txt, se non è ancora uscito;
  - altrimenti l'ultima release + 1 sull'ultima cifra (2.0.0 → 2.0.1). Con «[minor]» o
    «[major]» in un messaggio di commit sale la cifra di mezzo (2.1.0) o la prima (3.0.0).

Note della release, nell'ordine:
  - docs/releases/<versione>.md, se c'è (note scritte a mano);
  - la voce «## <versione>» del CHANGELOG.md;
  - la voce «## Non rilasciato» (o «## Unreleased»), che prende il numero e la data;
  - l'elenco dei commit dall'ultima release, che finisce anche nel CHANGELOG.md.

Se VERSION.txt o CHANGELOG.md vanno aggiornati, il commit «Versione X.Y.Z [skip ci]» lo
fa il workflow: il numero in fondo al portale resta quello della release.

Variabili: COMMIT (il commit verificato dai test), REPO (proprietario/nome), GH_TOKEN,
NOME (titolo delle release, di serie CataSync), PROVA=1 per non pubblicare niente.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
BOT_NOME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
VOCE_APERTA = re.compile(r"^##[ \t]+\[?(?:non rilasciat[oa]|unreleased)\]?[ \t]*$", re.I | re.M)


# ------------------------------------------------------------------ funzioni pure
def numero(testo: str) -> tuple[int, int, int] | None:
    m = SEMVER.match((testo or "").strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def testo_versione(v: tuple[int, int, int]) -> str:
    return "%d.%d.%d" % v


def prossima(da_file: str, uscite: list[str], messaggi: list[str]) -> tuple[str, bool]:
    """(versione, automatica). Automatica = VERSION.txt va aggiornato."""
    nf = numero(da_file)
    if nf is None:
        raise ValueError(f"VERSION.txt non contiene un numero valido (X.Y.Z): {da_file!r}")
    gia = {numero(t) for t in uscite if numero(t)}
    ultima = max(gia) if gia else None
    if nf not in gia and (ultima is None or nf > ultima):
        return testo_versione(nf), False
    base = max(nf, ultima) if ultima else nf
    tutto = "\n".join(messaggi).lower()
    if "[major]" in tutto:
        nuova = (base[0] + 1, 0, 0)
    elif "[minor]" in tutto:
        nuova = (base[0], base[1] + 1, 0)
    else:
        nuova = (base[0], base[1], base[2] + 1)
    return testo_versione(nuova), True


def _titolo(versione: str) -> re.Pattern:
    return re.compile(r"^##[ \t]+\[?" + re.escape(versione) + r"\]?(?=[ \t]|$).*$", re.M)


def sezione(changelog: str, versione: str) -> str | None:
    """Il testo sotto «## <versione>», fino alla voce dopo."""
    m = _titolo(versione).search(changelog)
    if not m:
        return None
    resto = changelog[m.end():]
    fine = re.search(r"^##[ \t]", resto, re.M)
    corpo = (resto[:fine.start()] if fine else resto).strip()
    return corpo or None


def chiudi_voce_aperta(changelog: str, versione: str, data: str) -> tuple[str, str] | None:
    """«## Non rilasciato» → «## <versione> — <data>». Ritorna (nuovo changelog, corpo)."""
    m = VOCE_APERTA.search(changelog)
    if not m:
        return None
    nuovo = changelog[:m.start()] + f"## {versione} — {data}" + changelog[m.end():]
    corpo = sezione(nuovo, versione)
    return (nuovo, corpo) if corpo else None


def aggiungi_voce(changelog: str, versione: str, data: str, righe: list[str]) -> str:
    """Nuova voce in cima, sopra la prima «## » (sotto il titolo del file)."""
    voce = f"## {versione} — {data}\n\n" + "".join(f"- {r}\n" for r in righe) + "\n"
    m = re.search(r"^##[ \t]", changelog, re.M)
    if m:
        return changelog[:m.start()] + voce + changelog[m.start():]
    testa = changelog.rstrip("\n")
    return (testa + "\n\n" if testa else "# Changelog\n\n") + voce


def pulisci_oggetto(oggetto: str) -> str:
    """L'oggetto del commit senza i segnali per il numero ([minor], [major])."""
    return re.sub(r"\s*\[(?:minor|major)\]", "", oggetto, flags=re.I).strip()


def piede(repo: str, versione: str, precedente: str | None) -> str:
    confronto = (f"https://github.com/{repo}/compare/{precedente}...{versione}" if precedente
                 else f"https://github.com/{repo}/commits/{versione}")
    return ("\n\n**Installation:** see the [README](https://github.com/" + repo + "#installation) "
            "(Docker Compose).\n\n"
            f"**All changes:** [CHANGELOG.md](https://github.com/{repo}/blob/{versione}/CHANGELOG.md)\n\n"
            f"**Full Changelog**: {confronto}\n")


# ------------------------------------------------------------------ git e gh
def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"git {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r


def out(*args: str) -> str:
    return git(*args).stdout.strip()


def commit_da(precedente: str | None, fino: str) -> list[tuple[str, str, str]]:
    """(sha, oggetto, messaggio intero) dei commit dopo l'ultima release, senza quelli del bot."""
    intervallo = f"{precedente}..{fino}" if precedente else fino
    grezzo = out("log", "--no-merges", "--format=%H%x1f%ae%x1f%s%x1f%B%x1e", intervallo)
    voci = []
    for blocco in grezzo.split("\x1e"):
        parti = blocco.strip("\n").split("\x1f")
        if len(parti) < 4 or parti[1] == BOT_EMAIL:
            continue
        voci.append((parti[0], pulisci_oggetto(parti[2]), parti[3].strip()))
    return voci


def release_esiste(repo: str, tag: str) -> bool:
    if os.getenv("PROVA") == "1" and os.getenv("PROVA_RELEASE_ESISTE") is not None:
        return os.getenv("PROVA_RELEASE_ESISTE") == "1"
    try:
        r = subprocess.run(["gh", "release", "view", tag, "--repo", repo], capture_output=True, text=True)
    except FileNotFoundError:
        sys.exit("manca il comando gh (GitHub CLI)")
    return r.returncode == 0


def pubblica_release(repo: str, tag: str, titolo: str, note: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(note)
    comando = ["gh", "release", "create", tag, "--repo", repo, "--title", titolo,
               "--notes-file", f.name, "--verify-tag"]
    if os.getenv("PROVA") == "1":
        print("PROVA:", " ".join(comando))
        print("---- note ----\n" + note + "---- fine note ----")
        return
    subprocess.run(comando, check=True)


def note_di(radice: Path, versione: str, changelog: str, voci, repo: str, precedente: str | None) -> str:
    scritte = radice / "docs" / "releases" / f"{versione}.md"
    if scritte.is_file():
        corpo = scritte.read_text(encoding="utf-8").strip()
    else:
        corpo = sezione(changelog, versione) or "\n".join(f"- {o} ({s[:7]})" for s, o, _ in voci) \
            or "- Manutenzione."
    return corpo + piede(repo, versione, precedente)


def main() -> int:
    radice = Path(out("rev-parse", "--show-toplevel"))
    repo = os.environ["REPO"]
    nome = os.getenv("NOME", "CataSync")
    commit = os.getenv("COMMIT") or out("rev-parse", "HEAD")
    commit = out("rev-parse", commit)

    git("fetch", "--force", "--tags", "origin", "+refs/heads/main:refs/remotes/origin/main")
    punta = out("rev-parse", "refs/remotes/origin/main")
    if punta != commit:
        print(f"main è andata avanti ({punta[:7]}): la release la fa il giro di quel commit.")
        return 0

    uscite = [t for t in out("tag", "--list").splitlines() if numero(t)]
    sul_commit = [t for t in out("tag", "--points-at", commit).splitlines() if numero(t)]
    ordinate = sorted(uscite, key=numero)

    if sul_commit:
        tag = max(sul_commit, key=numero)
        if release_esiste(repo, tag):
            print(f"{tag} è già uscita: niente da fare.")
            return 0
        prima = [t for t in ordinate if numero(t) < numero(tag)]
        precedente = prima[-1] if prima else None
        changelog = (radice / "CHANGELOG.md").read_text(encoding="utf-8") if (radice / "CHANGELOG.md").exists() else ""
        note = note_di(radice, tag, changelog, commit_da(precedente, commit), repo, precedente)
        pubblica_release(repo, tag, f"{nome} {tag}", note)
        print(f"Release {tag} creata sul tag che c'era già.")
        return 0

    precedente = ordinate[-1] if ordinate else None
    voci = commit_da(precedente, commit)
    file_versione = radice / "VERSION.txt"
    da_file = file_versione.read_text(encoding="utf-8").strip() if file_versione.exists() else "0.0.0"
    versione, automatica = prossima(da_file, uscite, [m for _, _, m in voci])
    data = _dt.date.today().isoformat()

    percorso_cl = radice / "CHANGELOG.md"
    changelog = percorso_cl.read_text(encoding="utf-8") if percorso_cl.exists() else ""
    nuovo_cl = changelog
    if not sezione(changelog, versione):
        aperta = chiudi_voce_aperta(changelog, versione, data)
        if aperta:
            nuovo_cl = aperta[0]
        else:
            righe = [o for _, o, _ in voci] or ["Manutenzione."]
            nuovo_cl = aggiungi_voce(changelog, versione, data, righe)

    cambiati = []
    if automatica:
        file_versione.write_text(versione + "\n", encoding="utf-8")
        cambiati.append("VERSION.txt")
    if nuovo_cl != changelog:
        percorso_cl.write_text(nuovo_cl, encoding="utf-8")
        cambiati.append("CHANGELOG.md")

    git("config", "user.name", BOT_NOME)
    git("config", "user.email", BOT_EMAIL)
    if cambiati:
        git("add", *cambiati)
        git("commit", "-m", f"Versione {versione} [skip ci]",
            "-m", "Numero e changelog aggiornati dal workflow delle release.")
    bersaglio = out("rev-parse", "HEAD")
    git("tag", "-a", versione, "-m", f"{nome} {versione}", bersaglio)

    if os.getenv("PROVA") == "1" and os.getenv("PROVA_PUSH") != "1":
        print(f"PROVA: push di {versione} saltato ({', '.join(cambiati) or 'nessun file cambiato'})")
    else:
        destinazioni = (["HEAD:refs/heads/main"] if cambiati else []) + [f"refs/tags/{versione}"]
        r = git("push", "--atomic", "origin", *destinazioni, check=False)
        if r.returncode:
            errore = (r.stderr or r.stdout).strip()
            if re.search(r"non-fast-forward|fetch first|stale info|cannot lock ref", errore):
                print("main è cambiata mentre preparavo la release: ci pensa il giro successivo.\n" + errore)
                return 0
            sys.exit("push non riuscito:\n" + errore)

    note = note_di(radice, versione, nuovo_cl, voci, repo, precedente)
    pubblica_release(repo, versione, f"{nome} {versione}", note)
    print(f"Release {versione} pubblicata" + (f" (aggiornati: {', '.join(cambiati)})" if cambiati else "") + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
