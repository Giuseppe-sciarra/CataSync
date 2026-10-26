/* Pagina pubblica: la testata che si stacca dal fondo, la schermata dell'eroe che si raddrizza
   scorrendo, gli esempi che girano da soli, il codice a barre dal vivo, la luce che segue il
   puntatore sulle tessere e le entrate delle sezioni. Senza questo file la pagina è comunque completa. */
(function () {
  'use strict';

  const limita = (x, a, b) => Math.min(b, Math.max(a, x));

  // ------------------------------------------------------------ testata e schermata dell'eroe
  const testa = document.querySelector('[data-testa]');
  const inclina = document.querySelector('[data-inclina]');
  let inAttesa = false;
  function alloScorrere() {
    inAttesa = false;
    if (testa) testa.classList.toggle('is-scorsa', window.scrollY > 8);
    if (inclina) {
      const alto = inclina.getBoundingClientRect().top;
      const vh = window.innerHeight || 800;
      const t = limita((alto - vh * 0.12) / (vh * 0.62), 0, 1);
      inclina.style.setProperty('--inclina', (t * 16).toFixed(2) + 'deg');
      inclina.style.setProperty('--scala', (1 - t * 0.05).toFixed(4));
    }
  }
  window.addEventListener('scroll', () => { if (!inAttesa) { inAttesa = true; requestAnimationFrame(alloScorrere); } }, { passive: true });
  window.addEventListener('resize', alloScorrere);
  alloScorrere();

  // ------------------------------------------------------------ EAN-13 a moduli
  // Le stesse tabelle di app/vetrina.py: 95 moduli, pieni (1) o vuoti (0).
  const L = ['0001101', '0011001', '0010011', '0111101', '0100011', '0110001', '0101111', '0111011', '0110111', '0001011'];
  const G = ['0100111', '0110011', '0011011', '0100001', '0011101', '0111001', '0000101', '0010001', '0001001', '0010111'];
  const R = L.map(c => c.replace(/./g, b => (b === '0' ? '1' : '0')));
  const PARITA = ['LLLLLL', 'LLGLGG', 'LLGGLG', 'LLGGGL', 'LGLLGG', 'LGGLLG', 'LGGGLL', 'LGLGLG', 'LGLGGL', 'LGGLGL'];

  function moduli(cifre) {
    const d = (cifre + '0000000000000').slice(0, 13);
    let s = '101';
    for (let i = 0; i < 6; i++) s += (PARITA[+d[0]][i] === 'L' ? L : G)[+d[i + 1]];
    s += '01010';
    for (let i = 7; i < 13; i++) s += R[+d[i]];
    return s + '101';
  }
  // Cifra di controllo GTIN: pesi 3 e 1 a partire da destra.
  function controllo(corpo) {
    let somma = 0;
    for (let i = 0; i < corpo.length; i++) somma += +corpo[corpo.length - 1 - i] * (i % 2 === 0 ? 3 : 1);
    return (10 - (somma % 10)) % 10;
  }

  const svg = document.querySelector('[data-codice-vivo]');
  const cifreBox = document.querySelector('[data-cifre]');
  const campo = document.getElementById('prova-ean');
  const esito = document.getElementById('prova-esito');
  const prova = campo ? campo.closest('.v-prova') : null;
  let mostrato = '';

  function disegna(codice) {
    if (!svg || codice === mostrato) return;
    const m = moduli(codice);
    svg.querySelectorAll('rect').forEach((r, i) => r.classList.toggle('on', m[i] === '1'));
    if (cifreBox) {
      const celle = cifreBox.querySelectorAll('.v-cifre-prima, .v-cifre-gruppo span');
      celle.forEach((c, i) => {
        const nuova = codice[i] || '0';
        if (c.textContent !== nuova) {
          c.textContent = nuova;
          c.classList.remove('is-nuova'); void c.offsetWidth; c.classList.add('is-nuova');
        }
      });
    }
    mostrato = codice;
  }

  // ------------------------------------------------------------ il campo «Prova con un codice»
  let toccato = false;
  function scrivi(testo) { if (esito && esito.textContent !== testo) esito.textContent = testo; }
  function leggiCampo() {
    const cifre = campo.value.replace(/\D/g, '');
    if (prova) prova.classList.remove('is-ok');
    if (!cifre) { scrivi('Il codice qui accanto cambia mentre scrivi.'); return; }
    toccato = true;
    if (cifre.length === 8 && controllo(cifre.slice(0, 7)) === +cifre[7]) {
      scrivi('EAN-8 valido: va bene anche questo.');
      if (prova) prova.classList.add('is-ok');
      return;
    }
    if (cifre.length > 13) { scrivi('Un EAN ha 13 cifre.'); return; }
    if (cifre.length < 12) {
      const mancano = 13 - cifre.length;
      scrivi(mancano === 1 ? 'Ancora 1 cifra.' : 'Ancora ' + mancano + ' cifre.');
      disegna((cifre + '000000000000').slice(0, 12) + '0');
      return;
    }
    const giusta = controllo(cifre.slice(0, 12));
    if (cifre.length === 12) {
      scrivi("Manca l'ultima cifra: è " + giusta + '.');
      disegna(cifre + giusta);
      return;
    }
    disegna(cifre);
    if (+cifre[12] === giusta) {
      scrivi('EAN valido: da qui parte la scheda.');
      if (prova) prova.classList.add('is-ok');
    } else {
      scrivi("L'ultima cifra dovrebbe essere " + giusta + ': controlla il codice.');
    }
  }
  if (campo) {
    campo.addEventListener('input', leggiCampo);
    campo.addEventListener('focus', () => { toccato = true; });
  }

  // Finché nessuno scrive, il codice passa da un prodotto all'altro dei dati dimostrativi.
  const giro = ['8034110000034', '8034110000027', '8034110000010', '8034110000058', '8034110000041'];
  let passo = 0, codiceVisibile = false;
  if (svg) {
    mostrato = giro[0];
    new IntersectionObserver(e => { codiceVisibile = e[0].isIntersecting; }).observe(svg);
    setInterval(() => {
      if (toccato || document.hidden || !codiceVisibile) return;
      passo = (passo + 1) % giro.length;
      disegna(giro[passo]);
    }, 3600);
  }

  // ------------------------------------------------------------ esempi: linguette che girano da sole
  const esempi = document.querySelector('[data-esempi]');
  if (esempi) {
    const corpo = esempi.querySelector('.v-esempi-corpo');
    const linguette = [...esempi.querySelectorAll('[role="tab"]')];
    const DURATA = 7000;
    let attuale = 0, fermo = false, visibile = false, timer = null;
    corpo.style.setProperty('--durata', DURATA + 'ms');

    const scegli = (j, fuoco) => {
      attuale = j;
      linguette.forEach((b, i) => {
        const si = i === j;
        b.setAttribute('aria-selected', si ? 'true' : 'false');
        b.tabIndex = si ? 0 : -1;
      });
      esempi.querySelectorAll('[data-pannello-esempio]').forEach(p => {
        const si = p.dataset.pannelloEsempio === linguette[j].dataset.esempio;
        p.hidden = !si;
        p.classList.toggle('is-attivo', si);
      });
      if (fuoco) linguette[j].focus();
      const elenco = linguette[j].parentElement;
      if (elenco.scrollWidth > elenco.clientWidth) {
        elenco.scrollTo({ left: linguette[j].offsetLeft - elenco.offsetLeft - 16, behavior: 'smooth' });
      }
      riparti();
    };
    // La barretta sotto la linguetta scorre; quando arriva in fondo si passa all'esempio dopo.
    function riparti() {
      clearTimeout(timer);
      corpo.classList.remove('is-gira');
      if (fermo || !visibile || document.hidden) return;
      void corpo.offsetWidth;
      corpo.classList.add('is-gira');
      timer = setTimeout(() => scegli((attuale + 1) % linguette.length, false), DURATA);
    }
    const ferma = () => { fermo = true; riparti(); };

    linguette.forEach((btn, i) => {
      btn.addEventListener('click', () => { ferma(); scegli(i, false); });
      btn.addEventListener('keydown', e => {
        let j = null;
        if (e.key === 'ArrowDown' || e.key === 'ArrowRight') j = (i + 1) % linguette.length;
        if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') j = (i - 1 + linguette.length) % linguette.length;
        if (e.key === 'Home') j = 0;
        if (e.key === 'End') j = linguette.length - 1;
        if (j !== null) { e.preventDefault(); ferma(); scegli(j, true); }
      });
    });
    new IntersectionObserver(e => { visibile = e[0].isIntersecting; riparti(); }, { threshold: 0.35 }).observe(corpo);
    document.addEventListener('visibilitychange', riparti);
  }

  // ------------------------------------------------------------ la luce che segue il puntatore
  document.querySelectorAll('.v-tessera').forEach(t => {
    t.addEventListener('pointermove', e => {
      const r = t.getBoundingClientRect();
      t.style.setProperty('--mx', (e.clientX - r.left) + 'px');
      t.style.setProperty('--my', (e.clientY - r.top) + 'px');
    });
  });

  // ------------------------------------------------------------ schermate nella lingua giusta
  // Ci sono in italiano e in inglese: le altre lingue usano quelle inglesi.
  function linguaImmagini() {
    const lingua = (window.I18n && window.I18n.locale) || document.documentElement.lang || 'it';
    const cartella = lingua === 'it' ? 'it' : 'en';
    document.querySelectorAll('img[data-img-lingua]').forEach(img => {
      const voluto = '/static/vetrina/img/' + cartella + '/' + img.dataset.imgLingua;
      if (img.getAttribute('src') !== voluto) img.setAttribute('src', voluto);
    });
  }
  // La registrazione del pannello: nella lingua dell'interfaccia, ferma quando non si vede.
  const video = document.querySelector('video[data-video-lingua]');
  let videoVisibile = false;
  function aggiornaVideo() {
    if (!video) return;
    const deveAndare = videoVisibile && !document.hidden;
    if (deveAndare && video.paused) video.play().catch(() => {});
    if (!deveAndare && !video.paused) video.pause();
  }
  function linguaVideo() {
    if (!video) return;
    const lingua = (window.I18n && window.I18n.locale) || 'it';
    const cartella = lingua === 'it' ? 'it' : 'en';
    const sorgente = video.querySelector('source');
    const voluto = '/static/vetrina/video/scheda-' + cartella + '.mp4';
    if (sorgente.getAttribute('src') !== voluto) {
      sorgente.setAttribute('src', voluto);
      video.setAttribute('poster', '/static/vetrina/video/scheda-' + cartella + '.webp');
      video.load();
      aggiornaVideo();
    }
  }
  if (video) {
    new IntersectionObserver(e => { videoVisibile = e[0].isIntersecting; aggiornaVideo(); }, { threshold: 0.2 }).observe(video.closest('.v-finestra') || video);
    document.addEventListener('visibilitychange', aggiornaVideo);
  }
  linguaImmagini();
  linguaVideo();
  window.addEventListener('languagechange', () => { linguaImmagini(); linguaVideo(); });

  // ------------------------------------------------------------ entrate delle sezioni
  const daMostrare = document.querySelectorAll('[data-reveal]');
  const apri = el => el.classList.add('is-in');
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(voci => {
      for (const v of voci) if (v.isIntersecting) { apri(v.target); io.unobserve(v.target); }
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.06 });
    daMostrare.forEach(el => io.observe(el));
  } else {
    daMostrare.forEach(apri);
  }
})();
