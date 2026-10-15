/* TD Sync Catalog — logica della console. */

function hub(isAdmin) {
  return {
    isAdmin: isAdmin,
    // L'admin gestisce i clienti: le schede di catalogo non le vede nemmeno.
    tab: isAdmin ? 'clients' : 'catalogo',
    busy: false,
    flash: { msg: '', kind: 'ok' },
    theme: 'auto',
    brand: null,
    sub: null,
    profile: {},
    logoBust: 0,
    edit: { images: [], attributes: [] },
    review: { on: false, ids: [], i: 0, item: null, saving: false, proposta: false, err: '' },
    // Dopo «Cerca le schede» si vede solo quell'importazione: niente elenco di
    // mezzo, niente apri-e-cerca. L'elenco resta raggiungibile con un pulsante.
    soloJob: false,
    review_labels: {},
    // Catalogo: la vista principale del cliente. Un prodotto per riga, stato
    // in parole povere, categoria e prezzo modificabili direttamente in tabella.
    catalogo: { righe: [], conteggi: {}, totale: 0, da_arricchire: 0, filtro: null, cerca: '', sel: {}, busy: false, salvando: {},
                // vista: 'ultimo' = solo i prodotti dell'ultimo carico (default: gli
                // import vecchi non stanno sempre tra i piedi), 'da_fare' = tutto
                // ciò che aspetta te, da qualunque carico, 'tutti' = tutto.
                vista: 'ultimo',
                // un carico scelto dallo Storico («Mostra nei Prodotti»)
                caricoScelto: null },
    // Legenda «Come funziona»: aperta finché il cliente non la chiude; poi resta
    // raggiungibile dal pulsante «?». La scelta si ricorda nel browser.
    // Avvisi chiusi dall'utente ('benvenuto' = l'invito alla Guida in cima a Prodotti). Li
    // tiene il server, per tutti i dispositivi; il browser ne tiene una copia solo per non
    // farli lampeggiare prima che arrivi la risposta.
    guideChiuse: (function () {
      try {
        const salvate = JSON.parse(localStorage.getItem('td_guide_chiuse') || '[]');
        if (localStorage.getItem('td_legenda') === 'chiusa' && !salvate.includes('benvenuto')) salvate.push('benvenuto');
        return salvate;
      } catch (e) { return []; }
    })(),
    bulk: { category_id: '', regular_price: '' },
    _timerRiga: {},
    ultimoJob: null,        // l'importazione appena lanciata: banner di avanzamento
    kpiStati: [
      { key: 'da_completare', label: 'Da completare' },
      { key: 'non_trovato',   label: 'Non trovati' },
      { key: 'problema',      label: 'Problemi' },
      { key: 'pronto',        label: 'Pronti' },
      { key: 'in_corso',      label: 'In ricerca' },
      { key: 'sul_sito',      label: 'Sul sito' }
    ],
    pricesOpen: false,
    pricesText: '',
    clientProviders: [],
    providerTest: null,
    providerTestGtin: '',
    presetFonti: {},
    confronto: [],
    confrontoGtin: '',
    ricerca: { motore: '', chiave: '', cx: '', url: '', motori: '', motori_predefiniti: '', lt_url: '', lt_chiave: '',
               render_url: '', motori_disponibili: {}, attiva: false, chiave_masked: '' },
    ricercaProvaGtin: '',
    ricercaProvaLingua: 'IT',
    ricercaEsito: null,
    // Lingue delle schede: i nomi restano nella loro lingua, come nei menu dei siti.
    lingueSchede: [
      { codice: 'IT', nome: 'Italiano' }, { codice: 'EN', nome: 'English' },
      { codice: 'FR', nome: 'Français' }, { codice: 'DE', nome: 'Deutsch' },
      { codice: 'ES', nome: 'Español' }
    ],
    // true quando l'utente sceglie a mano la lingua delle schede: da lì in poi
    // cambiare la lingua dell'interfaccia non la tocca più.
    linguaScelta: false,
    salute: null,
    lotto: { codici: '', lingua: 'IT', busy: false, stato: null, timer: null },
    qualityFields: [],
    editingClientId: null,
    brandForm: { name: '', logo: '' },

    // dati
    sites: [], allSites: [], clients: [], users: [], jobs: [], items: [],
    categories: [], attributes: [], brands: [], auditRows: [],
    job: null, itemFilter: null, taxError: '',

    form: {
      site_id: null, eans: '', label: '', lang: 'IT',
      product_status: 'publish', fallback_category_id: null, auto_push: false
    },
    // Categorie: creazione, ordine fra sorelle (padre -> id), trascinamento.
    catNuova: { name: '', parent: 0, busy: false },
    catRinomina: { id: null, name: '', busy: false },
    // Immagini delle istruzioni: in italiano per l'italiano, in inglese per le altre lingue.
    linguaAiuto: 'it',
    // Magazzino: i prodotti già sul sito (o nel suo cestino), per categoria, con ricerca e pagine.
    mag: {
      vista: 'prodotti', categoria: 0, q: '', pagina: 1, pagine: 1, totale: 0, prodotti: [],
      perPagina: (function () { try { const n = +localStorage.getItem('td_mag_per_pagina'); return [15, 20, 30, 50, 100].includes(n) ? n : 20; } catch (e) { return 20; } })(),
      nelCestino: 0, giorni: 30, busy: false, errore: ''
    },
    magCategorie: [],
    magModifica: { id: null, categoria: 0, altre: [], prezzo: '', scontato: '', busy: false },
    magTimer: null,
    magRichiesta: 0,
    guidaSezione: 'import',
    catOrdine: {}, catOrdineSalvato: '{}', catTrascinata: null, catBersaglio: null, catBusy: false,
    single: { gtin: '', busy: false, product: null, message: '', onSite: null, fonte: '', diagnosi: null },
    modal: { kind: null, title: '', data: {} },

    itemFilters: [
      { key: 'ready', label: 'Pronti' },
      { key: 'pushed', label: 'Sul sito' },
      { key: 'not_found', label: 'Non trovati' },
      { key: 'incomplete', label: 'Da completare' },
      { key: 'error', label: 'Errori' },
      { key: 'blocked', label: 'Bloccati' }
    ],
    policyFields: [
      { key: 'title', label: 'Nome prodotto' },
      { key: 'description', label: 'Descrizione' },
      { key: 'images', label: 'Immagini' },
      { key: 'attributes', label: 'Specifiche' },
      { key: 'brand', label: 'Marca' },
      { key: 'categories', label: 'Categorie' },
      { key: 'gtin', label: 'Codice EAN' },
      { key: 'prices', label: 'Prezzi' }
    ],

    // ------------------------------------------------------------- ciclo
    async boot() {
      this.initTheme();
      // Le schede seguono la lingua dell'interfaccia: chi lavora in inglese le
      // riceve in inglese. Finché non la sceglie a mano, cambia insieme a lei.
      this.form.lang = this.linguaUI();
      this.ricercaProvaLingua = this.form.lang;
      this.lotto.lingua = this.form.lang;
      this.linguaAiuto = this.linguaUI() === 'IT' ? 'it' : 'en';
      window.addEventListener('languagechange', () => {
        if (!this.linguaScelta) this.form.lang = this.linguaUI();
        this.linguaAiuto = this.linguaUI() === 'IT' ? 'it' : 'en';
      });
      await this.loadMe();
      await this.loadSites();
      if (this.isAdmin) { await this.loadClients(); }
      if (!this.isAdmin && this.sites.length) this.loadCatalogo();
      this.qualityFields = [['title','Nome prodotto'],['description','Descrizione'],
        ['images',"Almeno un'immagine"],
        ['attributes','Almeno una specifica'],['brand','Marca'],['mpn','Codice produttore'],
        ['category','Categoria'],['regular_price','Prezzo']];
      this.poll();
    },

    poll() {
      setInterval(() => {
        if (this.tab === 'jobs') {
          this.loadJobs(true);
          if (this.job) this.refreshJob();
        }
        if (this.tab === 'catalogo') {
          // si aggiorna da solo finché c'è qualcosa in ricerca o in invio
          const inCorso = (this.catalogo.conteggi.in_corso || 0) > 0;
          if (inCorso || this.ultimoJob) this.loadCatalogo(true);
          if (this.ultimoJob && !this.ultimoJobFinito) this.aggiornaUltimoJob();
        }
      }, 4000);
    },

    go(tab) {
      // «Importa» non è più una pagina a sé: si aggiungono i prodotti in cima
      // alla pagina Prodotti. Chi chiama go('import') finisce lì, sul riquadro.
      if (tab === 'import') {
        tab = 'catalogo';
        this.$nextTick(() => { const t = this.$refs.eans; if (t) { t.focus(); t.scrollIntoView({ behavior: 'smooth', block: 'center' }); } });
      }
      this.tab = tab;
      this.flash.msg = '';
      // Dalla barra si arriva sempre all'elenco: la modalità «solo job» la
      // riaccende openJob(id, true) subito dopo, quando serve.
      if (tab === 'jobs') { this.soloJob = false; this.loadJobs(); }
      if (tab === 'catalogo') { this.loadTaxonomy(); this.loadCatalogo(); }
      if (tab === 'categories') this.loadTaxonomy();
      if (tab === 'magazzino') this.apriMagazzino();
      if (tab === 'sites') this.loadAllSites();
      if (tab === 'users') this.loadUsers();
      if (tab === 'clients') this.loadClients();
      if (tab === 'shop') this.loadProfile();
      if (tab === 'impostazioni') { this.loadAudit(); this.loadBrand(); this.loadRicerca(); this.loadSalute(); }
    },

    // -------------------------------------------------------------- tema
    initTheme() {
      try { this.theme = localStorage.getItem('tdic-theme') || 'auto'; } catch (e) { this.theme = 'auto'; }
      this.applyTheme();
    },

    applyTheme() {
      const root = document.documentElement;
      if (this.theme === 'auto') root.removeAttribute('data-theme');
      else root.setAttribute('data-theme', this.theme);
      try {
        if (this.theme === 'auto') localStorage.removeItem('tdic-theme');
        else localStorage.setItem('tdic-theme', this.theme);
      } catch (e) {}
    },

    cycleTheme() {
      this.theme = { auto: 'light', light: 'dark', dark: 'auto' }[this.theme] || 'auto';
      this.applyTheme();
    },

    themeTitle() {
      return { auto: 'Tema: come il sistema', light: 'Tema: chiaro', dark: 'Tema: scuro' }[this.theme];
    },

    // ------------------------------------------------------------ profilo
    async loadMe() {
      try {
        const me = await this.api('/api/auth/me');
        if (me && me.authenticated) {
          this.brand = me.brand;
          this.sub = me.subscription;
          this.allineaGuide(me.guide_chiuse || []);
        }
      } catch (e) {}
    },
    // Il server comanda. Unica eccezione: la «Come funziona» chiusa in questo browser con la
    // versione di prima, che il server non sa: chi l'aveva chiusa non rivede l'invito.
    allineaGuide(dalServer) {
      let vecchia = false;
      try { vecchia = localStorage.getItem('td_legenda') === 'chiusa'; localStorage.removeItem('td_legenda'); } catch (e) {}
      this.guideChiuse = dalServer.slice();
      if (vecchia && !dalServer.includes('benvenuto')) this.ricordaGuida('benvenuto', false);
      try { localStorage.setItem('td_guide_chiuse', JSON.stringify(this.guideChiuse)); } catch (e) {}
    },
    // La Guida ha tre guide (prodotti, magazzino, categorie): ci si arriva dalla barra o dai
    // pulsanti 📖, già su quella giusta.
    apriGuida(sezione) {
      if (sezione === 'import') this.ricordaGuida('benvenuto', false);   // vista la guida, l'invito non serve più
      this.guidaSezione = sezione === 'stati' ? 'import' : sezione;
      if (this.tab !== 'guida') this.go('guida');
      this.$nextTick(() => this.vaiGuida(sezione));
    },
    vaiGuida(sezione) {
      const el = document.getElementById('guida-' + sezione);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    },
    guidaAperta(nome) {
      return !this.guideChiuse.includes(nome);
    },
    async ricordaGuida(nome, aperta) {
      const chiuse = this.guideChiuse.filter(n => n !== nome);
      if (!aperta) chiuse.push(nome);
      if (chiuse.length === this.guideChiuse.length && chiuse.every(n => this.guideChiuse.includes(n))) return;
      this.guideChiuse = chiuse;
      try { localStorage.setItem('td_guide_chiuse', JSON.stringify(chiuse)); } catch (e) {}
      try {
        await this.api('/api/auth/me/guide', { method: 'PUT', body: JSON.stringify({ guida: nome, aperta: !!aperta }) });
      } catch (e) { /* resta almeno in questo browser */ }
    },

    async loadProfile() {
      try {
        this.profile = await this.api('/api/profile');
        this.sub = this.profile.subscription;
      } catch (e) { this.fail(e.message); }
    },

    async saveProfile() {
      try {
        const body = {};
        ['legal_name','vat_number','tax_code','sdi_code','pec','address','zip_code',
         'city','province','country','phone','contact_email','website','accent_color']
          .forEach(k => { body[k] = this.profile[k] || ''; });
        this.profile = await this.api('/api/profile', { method: 'PUT', body: JSON.stringify(body) });
        this.say('Dati del negozio salvati.');
      } catch (e) { this.fail(e.message); }
    },

    async uploadLogo(event) {
      const file = event.target.files[0];
      if (!file) return;
      const body = new FormData();
      body.append('file', file);
      try {
        const res = await fetch('/api/profile/logo', { method: 'POST', body: body });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Caricamento non riuscito.');
        this.profile.logo = data.logo;
        this.logoBust = Date.now();
        if (this.brand) this.brand.logo = data.logo + '?v=' + this.logoBust;
        this.say('Logo aggiornato.');
      } catch (e) { this.fail(e.message); }
      event.target.value = '';
    },

    async removeLogo() {
      if (!confirm('Rimuovo il logo?')) return;
      await this.api('/api/profile/logo', { method: 'DELETE' });
      this.profile.logo = '';
      if (this.brand) this.brand.logo = '';
    },

    // --------------------------------------------------------------- http
    async api(path, options) {
      const opts = Object.assign({ headers: { 'Content-Type': 'application/json' } }, options || {});
      let res;
      try {
        res = await fetch(path, opts);
      } catch (e) {
        // Il fetch muore prima di una risposta: rete giù o timeout del reverse
        // proxy su un'operazione lunga. Il «NetworkError» inglese non aiuta nessuno.
        const err = new Error('Il server non ha risposto in tempo (rete o timeout del proxy). Riprova.');
        err.status = 0;
        throw err;
      }
      if (res.status === 401) { window.location.href = '/login'; return null; }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const err = new Error(this.errorText(data, res.status));
        err.status = res.status;
        throw err;
      }
      return data;
    },

    // I 422 di validazione arrivano con detail = ARRAY di oggetti. Buttarlo dentro
    // una stringa dava il famoso [object Object]: qui si traduce in italiano.
    errorText(data, status) {
      const d = data && data.detail;
      if (typeof d === 'string' && d) return d;

      if (Array.isArray(d) && d.length) {
        const etichette = {
          name: 'Nome', email: 'Email', base_url: 'Indirizzo del sito',
          client_id: 'Cliente', password: 'Password', full_name: 'Nome e cognome',
          endpoint: 'Indirizzo della chiamata', title: 'Nome prodotto',
          regular_price: 'Prezzo', monthly_quota: 'Quota mensile',
          wc_key: 'Consumer key', wc_secret: 'Consumer secret',
          role: 'Ruolo', parent: 'Categoria superiore', gtin: 'EAN'
        };
        const righe = d.slice(0, 4).map(e => {
          const campo = Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : '';
          const nome = etichette[campo] || campo || 'Campo';
          const t = e.type || '';
          if (t.indexOf('missing') !== -1) return nome + ': obbligatorio';
          if (t.indexOf('too_short') !== -1) {
            const min = e.ctx && e.ctx.min_length;
            return nome + ': troppo corto' + (min ? ' (almeno ' + min + ' caratteri)' : '');
          }
          if (t.indexOf('too_long') !== -1) return nome + ': troppo lungo';
          const msg = e.msg || '';
          // pydantic manda le email come 'value_error' con testo inglese: si
          // riconosce dal messaggio, non dal tipo.
          if (t.indexOf('email') !== -1 || msg.indexOf('email address') !== -1) {
            return nome + ': indirizzo email non valido';
          }
          if (t.indexOf('int') !== -1 || t.indexOf('float') !== -1) return nome + ': deve essere un numero';
          if (t.indexOf('url') !== -1) return nome + ': indirizzo non valido';
          return nome + ': ' + (msg || 'valore non valido');
        });
        return righe.join(' · ');
      }

      if (d && typeof d === 'object' && d.msg) return d.msg;
      return 'Errore ' + status;
    },

    // Se una modale è aperta l'errore va mostrato lì dentro, non dietro di essa.
    fail(msg) {
      if (this.modal && this.modal.kind) this.modal.error = msg;
      else this.say(msg, 'err');
    },

    say(msg, kind) {
      this.flash = { msg: msg, kind: kind || 'ok' };
      if (kind !== 'err') setTimeout(() => { this.flash.msg = ''; }, 6000);
    },

    async logout() {
      await this.api('/api/auth/logout', { method: 'POST' });
      window.location.href = '/login';
    },

    // -------------------------------------------------------------- siti
    async loadSites() {
      try {
        this.sites = await this.api('/api/sites');
        if (this.sites.length && !this.form.site_id) {
          this.form.site_id = this.sites[0].id;
          this.loadTaxonomy();
        }
      } catch (e) { this.fail(e.message); }
    },

    // Solo categorie vere: «Senza categoria» il backend la filtra già, ma la
    // cintura di sicurezza sta anche qui per i siti letti prima della modifica.
    get catValide() {
      return this.categories.filter(c => {
        const slug = (c.slug || '').toLowerCase();
        return slug !== 'uncategorized' && slug !== 'senza-categoria';
      });
    },

    get currentSite() {
      return this.sites.find(s => s.id === this.form.site_id) || null;
    },

    // ----------------------------------------------------------- lingue
    // Testi composti in JavaScript: si traduce il modello con i segnaposto
    // ({0}, {1}…) e poi si mettono i valori. Così «3 pronti · 2 sul sito»
    // esce nella lingua dell'interfaccia anche se è fatto di pezzi.
    t(testo) {
      try { return (window.I18n && window.I18n.t) ? window.I18n.t(testo) : testo; } catch (e) { return testo; }
    },
    tt(modello) {
      let out = this.t(modello);
      for (let i = 1; i < arguments.length; i++) out = out.split('{' + (i - 1) + '}').join(String(arguments[i]));
      return out;
    },

    linguaUI() {
      const l = String((window.I18n && window.I18n.locale) || 'it').toUpperCase();
      return ['IT', 'EN', 'FR', 'DE', 'ES'].includes(l) ? l : 'IT';
    },
    // La lingua fissata sul negozio vince su quella scelta qui.
    get linguaFissata() {
      return (this.currentSite && this.currentSite.content_lang) || '';
    },
    nomeLingua(codice) {
      const l = this.lingueSchede.find(x => x.codice === String(codice || '').toUpperCase());
      return l ? l.nome : (codice || '—');
    },

    onSiteChange() {
      if (this.tab === 'magazzino') { this.mag.categoria = 0; this.mag.pagina = 1; this.apriMagazzino(); }
      this.categories = []; this.attributes = []; this.brands = [];
      this.catOrdine = {}; this.catOrdineSalvato = '{}';
      this.catRinomina = { id: null, name: '', busy: false };
      this.loadTaxonomy();
    },

    async loadTaxonomy(force) {
      if (!this.form.site_id) return;
      if (this.categories.length && !force) return;
      this.taxError = '';
      try {
        const data = await this.api('/api/sites/' + this.form.site_id + '/taxonomy');
        this.categories = data.categories || [];
        this.attributes = data.attributes || [];
        this.brands = data.brands || [];
        this.preparaOrdine();
      } catch (e) {
        this.taxError = e.message;
      }
    },

    // -------------------------------------------------------- categorie
    // L'albero si disegna da catOrdine (padre -> figlie in ordine). Le categorie con una
    // madre che non è nell'elenco (per esempio dentro «Senza categoria») stanno in cima.
    padreDi(c) {
      return c && c.parent && this.catValide.some(x => x.id === c.parent) ? c.parent : 0;
    },
    categoriaPerId(id) {
      return this.catValide.find(c => c.id === id) || null;
    },
    preparaOrdine() {
      const gruppi = {};
      for (const c of this.catValide) (gruppi[this.padreDi(c)] = gruppi[this.padreDi(c)] || []).push(c);
      const nome = c => String(c.nome || c.name || '');
      const ordine = {};
      for (const p of Object.keys(gruppi)) {
        ordine[p] = gruppi[p].sort((a, b) =>
          ((a.menu_order || 0) - (b.menu_order || 0)) ||
          nome(a).localeCompare(nome(b), undefined, { numeric: true, sensitivity: 'base' }) ||
          (a.id - b.id)).map(c => c.id);
      }
      this.catOrdine = ordine;
      this.catOrdineSalvato = JSON.stringify(ordine);
    },
    get righeCategorie() {
      const per = {};
      this.catValide.forEach(c => { per[c.id] = c; });
      const righe = [];
      const visti = new Set();
      const visita = (padre, livello) => {
        const lista = (this.catOrdine[padre] || []).filter(id => per[id]);
        lista.forEach((id, i) => {
          if (visti.has(id)) return;
          visti.add(id);
          righe.push({ c: per[id], livello, primo: i === 0, ultimo: i === lista.length - 1 });
          visita(id, livello + 1);
        });
      };
      visita(0, 0);
      return righe;
    },
    get ordineCambiato() {
      return JSON.stringify(this.catOrdine) !== this.catOrdineSalvato;
    },
    spostaCategoria(c, verso) {
      const padre = this.padreDi(c);
      const lista = (this.catOrdine[padre] || []).slice();
      const i = lista.indexOf(c.id), j = i + verso;
      if (i < 0 || j < 0 || j >= lista.length) return;
      [lista[i], lista[j]] = [lista[j], lista[i]];
      this.catOrdine = Object.assign({}, this.catOrdine, { [padre]: lista });
    },
    annullaOrdine() {
      this.catOrdine = JSON.parse(this.catOrdineSalvato);
    },
    trascinaInizio(e, c) {
      this.catTrascinata = c.id;
      try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', String(c.id)); } catch (_) { /* vecchi browser */ }
    },
    // Si può lasciare solo su una sorella: l'ordine vale fra categorie dello stesso livello.
    trascinaSopra(e, c) {
      const da = this.categoriaPerId(this.catTrascinata);
      if (!da || da.id === c.id || this.padreDi(da) !== this.padreDi(c)) { this.catBersaglio = null; return; }
      e.preventDefault();
      this.catBersaglio = c.id;
    },
    trascinaRilascia(e, c) {
      e.preventDefault();
      const da = this.categoriaPerId(this.catTrascinata);
      this.catBersaglio = null;
      this.catTrascinata = null;
      if (!da || da.id === c.id || this.padreDi(da) !== this.padreDi(c)) return;
      const padre = this.padreDi(c);
      const lista = (this.catOrdine[padre] || []).slice();
      const i = lista.indexOf(da.id);
      if (i < 0) return;
      const sotto = i < lista.indexOf(c.id);   // trascinata verso il basso: va dopo la sorella
      lista.splice(i, 1);
      const j = lista.indexOf(c.id);
      lista.splice(sotto ? j + 1 : j, 0, da.id);
      this.catOrdine = Object.assign({}, this.catOrdine, { [padre]: lista });
    },
    trascinaFine() {
      this.catTrascinata = null;
      this.catBersaglio = null;
    },

    async creaCategoria() {
      const nome = this.catNuova.name.trim();
      if (!nome || this.catNuova.busy) return;
      this.catNuova.busy = true;
      try {
        const r = await this.api('/api/sites/' + this.form.site_id + '/categories', {
          method: 'POST', body: JSON.stringify({ name: nome, parent: this.catNuova.parent || 0 })
        });
        this.catNuova = { name: '', parent: this.catNuova.parent, busy: false };
        this.say(this.tt('Categoria «{0}» creata.', r.name || nome));
        await this.loadTaxonomy(true);
      } catch (e) { this.fail(e.message); }
      this.catNuova.busy = false;
    },
    // ------------------------------------------------------ magazzino
    apriMagazzino() {
      this.annullaModMag();
      this.caricaCategorieMag();
      this.caricaMag();
    },
    async caricaCategorieMag() {
      if (!this.form.site_id) return;
      try { this.magCategorie = await this.api('/api/sites/' + this.form.site_id + '/magazzino/categorie'); }
      catch (e) { this.magCategorie = []; }
    },
    // Albero come nella scheda Categorie; «Senza categoria» in fondo.
    get righeMagCat() {
      const per = {}, figlie = {};
      this.magCategorie.forEach(c => { per[c.id] = c; });
      this.magCategorie.forEach(c => {
        const padre = c.parent && per[c.parent] ? c.parent : 0;
        (figlie[padre] = figlie[padre] || []).push(c);
      });
      const ordina = l => l.sort((a, b) => (a.riserva - b.riserva) || ((a.menu_order || 0) - (b.menu_order || 0)) ||
        String(a.nome).localeCompare(String(b.nome), undefined, { sensitivity: 'base' }));
      const righe = [], visti = new Set();
      const visita = (padre, livello) => ordina(figlie[padre] || []).forEach(c => {
        if (visti.has(c.id)) return;
        visti.add(c.id);
        righe.push({ c, livello });
        visita(c.id, livello + 1);
      });
      visita(0, 0);
      return righe;
    },
    nomeCatMag(id) {
      const c = this.magCategorie.find(x => x.id === id);
      if (!c) return '';
      const parti = [c.nome];
      let p = c.parent, giri = 0;
      while (p && giri++ < 6) {
        const m = this.magCategorie.find(x => x.id === p);
        if (!m) break;
        parti.unshift(m.nome);
        p = m.parent;
      }
      return parti.join(' › ');
    },
    async caricaMag() {
      if (!this.form.site_id) return;
      const n = ++this.magRichiesta;   // vince l'ultima richiesta: chi scrive in fretta non vede risultati vecchi
      this.mag.busy = true;
      this.mag.errore = '';
      const q = new URLSearchParams({
        pagina: this.mag.pagina, per_pagina: this.mag.perPagina,
        cestino: this.mag.vista === 'cestino' ? 'true' : 'false'
      });
      if (this.mag.categoria) q.set('categoria', this.mag.categoria);
      if (this.mag.q.trim()) q.set('q', this.mag.q.trim());
      try {
        const d = await this.api('/api/sites/' + this.form.site_id + '/magazzino?' + q.toString());
        if (n !== this.magRichiesta) return;
        Object.assign(this.mag, { prodotti: d.prodotti || [], totale: d.totale || 0, pagine: d.pagine || 1,
                                  pagina: d.pagina || 1, nelCestino: d.nel_cestino || 0, giorni: d.giorni_cestino || 30 });
      } catch (e) {
        if (n !== this.magRichiesta) return;
        this.mag.prodotti = []; this.mag.totale = 0; this.mag.pagine = 1;
        this.mag.errore = e.message;
      }
      this.mag.busy = false;
    },
    vistaMag(v) {
      if (this.mag.vista === v) return;
      this.annullaModMag();
      Object.assign(this.mag, { vista: v, pagina: 1, prodotti: [] });
      this.caricaMag();
    },
    scegliCatMag(id) {
      this.annullaModMag();
      Object.assign(this.mag, { categoria: id, pagina: 1 });
      this.caricaMag();
    },
    cercaMag() {
      clearTimeout(this.magTimer);
      this.magTimer = setTimeout(() => { this.mag.pagina = 1; this.caricaMag(); }, 350);
    },
    vaiPaginaMag(n) {
      if (n < 1 || n > this.mag.pagine || n === this.mag.pagina) return;
      this.annullaModMag();
      this.mag.pagina = n;
      this.caricaMag();
    },
    cambiaPerPaginaMag() {
      try { localStorage.setItem('td_mag_per_pagina', String(this.mag.perPagina)); } catch (e) {}
      this.mag.pagina = 1;
      this.caricaMag();
    },
    // 1 … 4 5 [6] 7 8 … 20
    get pagineMag() {
      const tot = this.mag.pagine, cur = this.mag.pagina, out = [];
      for (let i = 1; i <= tot; i++) {
        if (i === 1 || i === tot || Math.abs(i - cur) <= 2) out.push(i);
        else if (out[out.length - 1] !== '…') out.push('…');
      }
      return out;
    },
    get riassuntoMag() {
      if (!this.mag.totale) return '';
      if (this.mag.totale === 1) return this.t('1 prodotto');
      const da = (this.mag.pagina - 1) * this.mag.perPagina + 1;
      const a = Math.min(this.mag.totale, da + this.mag.prodotti.length - 1);
      return this.tt('{0}–{1} di {2} prodotti', da, a, this.mag.totale);
    },
    statoMag(p) {
      return { publish: 'Pubblicato', draft: 'Bozza', pending: 'In revisione', private: 'Privato', future: 'Programmato' }[p.stato] || p.stato;
    },
    pillStatoMag(p) {
      return p.stato === 'publish' ? 'p-sul_sito' : (p.stato === 'draft' || p.stato === 'pending' ? 'p-da_completare' : 'p-in_corso');
    },
    disponibilitaMag(p) {
      if (p.disponibilita === 'outofstock') return this.t('Esaurito');
      if (p.disponibilita === 'onbackorder') return this.t('Su ordinazione');
      return p.quantita !== null && p.quantita !== undefined ? this.tt('Disponibili: {0}', p.quantita) : this.t('Disponibile');
    },
    soldi(v) {
      const n = parseFloat(String(v || '').replace(',', '.'));
      return isNaN(n) ? '' : n.toLocaleString(I18n.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    },
    prezzoPerCampo(v) {
      const n = parseFloat(String(v || '').replace(',', '.'));
      return isNaN(n) ? '' : n.toLocaleString(I18n.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: false });
    },
    modificaMag(p) {
      const ids = (p.categorie || []).map(c => c.id);
      this.magModifica = { id: p.id, categoria: ids[0] || 0, altre: ids.slice(1), busy: false,
                           prezzo: this.prezzoPerCampo(p.prezzo_pieno), scontato: this.prezzoPerCampo(p.scontato) };
    },
    annullaModMag() {
      this.magModifica = { id: null, categoria: 0, altre: [], prezzo: '', scontato: '', busy: false };
    },
    // La categoria scelta prende il posto della prima; le altre restano.
    async salvaModMag(p) {
      const m = this.magModifica;
      if (m.busy || m.id !== p.id) return;
      const corpo = {};
      const prima = (p.categorie || []).map(c => c.id);
      const dopo = [m.categoria, ...m.altre.filter(i => i !== m.categoria)].filter(Boolean);
      if (dopo.length && dopo.join(',') !== prima.join(',')) corpo.categorie = dopo;
      if (p.tipo !== 'variable') {
        if (m.prezzo.trim() !== this.prezzoPerCampo(p.prezzo_pieno)) corpo.prezzo = m.prezzo.trim();
        if (m.scontato.trim() !== this.prezzoPerCampo(p.scontato)) corpo.scontato = m.scontato.trim();
      }
      if (!Object.keys(corpo).length) { this.annullaModMag(); return; }
      m.busy = true;
      try {
        const r = await this.api('/api/sites/' + this.form.site_id + '/magazzino/' + p.id, {
          method: 'PATCH', body: JSON.stringify(corpo)
        });
        const i = this.mag.prodotti.findIndex(x => x.id === p.id);
        if (i >= 0 && r.prodotto) this.mag.prodotti.splice(i, 1, r.prodotto);
        this.annullaModMag();
        this.say(this.tt('«{0}» aggiornato sul sito.', p.nome));
        if (corpo.categorie) this.caricaCategorieMag();   // i conteggi delle categorie cambiano
      } catch (e) {
        this.magModifica.busy = false;
        this.fail(e.message);
      }
    },
    chiediCestinaMag(p) {
      this.modal = { error: null, kind: 'magcestina', title: this.t('Sposta nel cestino'), data: { p, giorni: this.mag.giorni } };
    },
    async cestinaMag() {
      const p = this.modal.data.p;
      this.mag.busy = true;
      try {
        await this.api('/api/sites/' + this.form.site_id + '/magazzino/' + p.id, { method: 'DELETE' });
        this.modal.kind = null;
        this.say(this.tt('«{0}» è nel cestino del sito.', p.nome));
        this.mag.busy = false;
        await this.caricaMag();
        this.caricaCategorieMag();
      } catch (e) { this.modal.error = e.message; this.mag.busy = false; }
    },
    async ripristinaMag(p) {
      this.mag.busy = true;
      try {
        await this.api('/api/sites/' + this.form.site_id + '/magazzino/' + p.id + '/ripristina', { method: 'POST' });
        this.say(this.tt('«{0}» è tornato sul sito.', p.nome));
        this.mag.busy = false;
        await this.caricaMag();
        this.caricaCategorieMag();
      } catch (e) { this.mag.busy = false; this.fail(e.message); }
    },
    chiediPerSempreMag(p) {
      this.modal = { error: null, kind: 'magpersempre', title: this.t('Elimina per sempre'), data: { p } };
    },
    async perSempreMag() {
      const p = this.modal.data.p;
      this.mag.busy = true;
      try {
        await this.api('/api/sites/' + this.form.site_id + '/magazzino/' + p.id + '/per-sempre', { method: 'DELETE' });
        this.modal.kind = null;
        this.say(this.tt('«{0}» eliminato per sempre.', p.nome));
        this.mag.busy = false;
        await this.caricaMag();
      } catch (e) { this.modal.error = e.message; this.mag.busy = false; }
    },
    chiediSvuotaMag() {
      this.modal = { error: null, kind: 'magsvuota', title: this.t('Svuota il cestino'), data: { quanti: this.mag.nelCestino } };
    },
    async svuotaMag() {
      this.mag.busy = true;
      try {
        const r = await this.api('/api/sites/' + this.form.site_id + '/magazzino/cestino/svuota', { method: 'POST' });
        this.modal.kind = null;
        this.say(r.eliminati === 1 ? this.t('Cestino svuotato: un prodotto eliminato per sempre.')
                                   : this.tt('Cestino svuotato: {0} prodotti eliminati per sempre.', r.eliminati || 0));
        this.mag.busy = false;
        await this.caricaMag();
      } catch (e) { this.modal.error = e.message; this.mag.busy = false; }
    },

    // Rinomina sul posto: il nuovo nome va sul sito e si aggiorna qui senza rileggere tutto,
    // così un ordine non ancora salvato non si perde.
    iniziaRinomina(c) {
      this.catRinomina = { id: c.id, name: c.nome || c.name || '', busy: false };
    },
    annullaRinomina() {
      this.catRinomina = { id: null, name: '', busy: false };
    },
    async salvaRinomina() {
      const id = this.catRinomina.id;
      const nome = this.catRinomina.name.replace(/\s+/g, ' ').trim();
      const c = this.categories.find(x => x.id === id);
      if (!id || !nome || this.catRinomina.busy) return;
      if (c && nome === (c.nome || c.name)) { this.annullaRinomina(); return; }
      this.catRinomina.busy = true;
      try {
        const r = await this.api('/api/sites/' + this.form.site_id + '/categories/' + id, {
          method: 'PATCH', body: JSON.stringify({ name: nome })
        });
        if (c) { c.nome = r.name || nome; this.rifaiPercorsi(); }
        this.annullaRinomina();
        this.say(this.tt('Categoria rinominata in «{0}».', r.name || nome));
      } catch (e) {
        this.catRinomina.busy = false;
        this.fail(e.message);
      }
    },
    // «Madre › Figlia», come lo scrive il server: serve alle tendine delle categorie.
    rifaiPercorsi() {
      const per = {};
      this.categories.forEach(c => { per[c.id] = c; });
      for (const c of this.categories) {
        const parti = [c.nome || ''];
        let padre = c.parent, giri = 0;
        while (padre && per[padre] && giri < 6) {
          parti.unshift(per[padre].nome || '');
          padre = per[padre].parent;
          giri++;
        }
        c.name = parti.filter(Boolean).join(' › ');
      }
    },
    chiediEliminaCategoria(c) {
      const figlie = this.catValide.filter(x => x.parent === c.id).length;
      this.modal = {
        error: null, kind: 'catdel', title: this.t('Elimina categoria'),
        data: { id: c.id, nome: c.nome || c.name, count: c.count || 0, figlie }
      };
    },
    async eliminaCategoria() {
      const d = this.modal.data;
      this.catBusy = true;
      try {
        const r = await this.api('/api/sites/' + this.form.site_id + '/categories/' + d.id, { method: 'DELETE' });
        this.modal.kind = null;
        this.say(this.tt('Categoria «{0}» eliminata.', r.name || d.nome));
        await this.loadTaxonomy(true);
      } catch (e) { this.modal.error = e.message; }
      this.catBusy = false;
    },
    async salvaOrdine() {
      this.catBusy = true;
      try {
        const ids = this.righeCategorie.map(r => r.c.id);
        const r = await this.api('/api/sites/' + this.form.site_id + '/categories/order', {
          method: 'PUT', body: JSON.stringify({ ids })
        });
        this.say(r.cambiate ? this.t('Ordine salvato sul sito.') : this.t("L'ordine sul sito era già questo."));
        await this.loadTaxonomy(true);
      } catch (e) { this.fail(e.message); }
      this.catBusy = false;
    },

    // ---------------------------------------------------------- importa
    // Stessa logica di app/codici.py: un codice per riga; le righe di soli
    // numeri possono contenere più EAN separati da virgole o spazi. EAN = cifre
    // con checksum giusto, tutto il resto (≥4 caratteri) è codice produttore.
    _checksumGtin(d) {
      if (!/^\d+$/.test(d) || ![8, 12, 13, 14].includes(d.length)) return false;
      let somma = 0;
      const corpo = d.slice(0, -1).split('').reverse();
      corpo.forEach((ch, i) => { somma += Number(ch) * (i % 2 === 0 ? 3 : 1); });
      return (10 - somma % 10) % 10 === Number(d.slice(-1));
    },
    _codiciIncollati() {
      const out = { ean: new Set(), mpn: new Set(), interni: new Set() };
      (this.form.eans || '').replace(/\r/g, '').split('\n').forEach(riga => {
        riga = riga.trim();
        if (!riga) return;
        let pezzi;
        if (/^[\d\s,;'.]+$/.test(riga) && /[,;\s]/.test(riga)) pezzi = riga.split(/[,;\s]+/);
        else pezzi = riga.split(';');
        pezzi.forEach(p => {
          const t = (p || '').replace(/\s+/g, ' ').trim().replace(/^['"]|['"]$/g, '');
          if (!t) return;
          const cifre = t.replace(/\D/g, '');
          if (/^[\d\s'.]+$/.test(t) && this._checksumGtin(cifre)) {
            if (cifre.length === 13 && Number(cifre.slice(0, 2)) >= 20 && Number(cifre.slice(0, 2)) <= 29) {
              out.interni.add(cifre);
              return;
            }
            out.ean.add(cifre); return;
          }
          const c = t.slice(0, 32);
          if (c.length >= 4 && (/\d/.test(c) || (c.match(/[A-Za-z]/g) || []).length >= 4)) out.mpn.add(c.toLowerCase());
        });
      });
      return out;
    },
    get eanCount() {
      const c = this._codiciIncollati();
      return c.ean.size + c.mpn.size;
    },
    get riepilogoCodici() {
      const c = this._codiciIncollati();
      if (!c.ean.size && !c.mpn.size) {
        if (c.interni.size) return this.tt('{0} codici 20-29 non ricercabili: sembrano codici interni del grossista', c.interni.size);
        return '';
      }
      const parti = [];
      if (c.ean.size) parti.push(c.ean.size + ' EAN');
      if (c.mpn.size) parti.push(this.tt(c.mpn.size === 1 ? '{0} codice produttore' : '{0} codici produttore', c.mpn.size));
      let testo = this.tt('{0} riconosciuti', parti.join(' + '));
      if (c.interni.size) testo += ' · ' + this.tt('{0} codici interni 20-29 esclusi', c.interni.size);
      return testo;
    },

    async uploadCsv(event) {
      const file = event.target.files[0];
      if (!file) return;
      const body = new FormData();
      body.append('file', file);
      try {
        const res = await fetch('/api/jobs/upload?site_id=' + this.form.site_id, { method: 'POST', body: body });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Lettura del CSV non riuscita.');
        this.form.eans = data.eans.join('\n');
        this.say(this.tt('{0} codici letti dal file.', data.count));
      } catch (e) { this.fail(e.message); }
      event.target.value = '';
    },

    async createJob() {
      this.busy = true;
      try {
        const corpo = Object.assign({}, this.form, { lang: this.linguaFissata || this.form.lang });
        const data = await this.api('/api/jobs', { method: 'POST', body: JSON.stringify(corpo) });
        this.form.eans = ''; this.form.label = '';
        let msg = this.tt('Cerco le schede di {0} prodotti.', data.total);
        if (data.invalid && data.invalid.length) msg += ' ' + this.tt('{0} codici scartati perché non ricercabili o non validi.', data.invalid.length);
        if (data.gia_sul_sito) msg += ' ' + this.tt('{0} sono già sul sito e verranno aggiornati.', data.gia_sul_sito);
        this.say(msg);
        // Si atterra sul Catalogo: banner di avanzamento in alto, righe che si
        // riempiono da sole, riepilogo chiaro a fine ricerca. Niente card tecniche.
        this.ultimoJob = data;
        this.catalogo.filtro = null;
        this.catalogo.sel = {};
        this.catalogo.vista = 'ultimo';
        if (this.tab !== 'catalogo') this.go('catalogo');
        await this.loadCatalogo(true);
      } catch (e) { this.fail(e.message); this.loadMe(); }
      this.busy = false;
    },

    async testEan() {
      this.single.busy = true; this.single.product = null; this.single.message = ''; this.single.onSite = null;
      this.single.fonte = ''; this.single.diagnosi = null;
      try {
        const data = await this.api('/api/lookup?site_id=' + this.form.site_id +
                                    '&gtin=' + encodeURIComponent(this.single.gtin) +
                                    '&lang=' + (this.linguaFissata || this.form.lang));
        this.single.diagnosi = data.diagnosi || null;
        if (!data.found) { this.single.message = data.message; }
        else { this.single.product = data.product; this.single.onSite = data.on_site; this.single.fonte = data.fonte || ''; }
      } catch (e) { this.single.message = e.message; }
      this.single.busy = false;
    },

    // -------------------------------------------------------- catalogo
    async loadCatalogo(silent) {
      if (!this.form.site_id) return;
      if (!silent) this.catalogo.busy = true;
      try {
        const d = await this.api('/api/sites/' + this.form.site_id + '/catalogo');
        // Non sovrascrivo le righe che l'utente sta modificando adesso.
        const inModifica = new Set(Object.keys(this._timerRiga).map(Number));
        const vecchie = {};
        this.catalogo.righe.forEach(r => { vecchie[r.item_id] = r; });
        this.catalogo.righe = (d.righe || []).map(r => {
          if (inModifica.has(r.item_id) && vecchie[r.item_id]) return vecchie[r.item_id];
          // categoria mancante = '' (non null): così la select mostra «— scegli —»
          // invece della prima voce a caso, che faceva credere la categoria impostata.
          if (!r.category_id) r.category_id = '';
          return r;
        });
        this.catalogo.conteggi = d.conteggi || {};
        this.catalogo.totale = d.totale || 0;
        this.catalogo.da_arricchire = d.da_arricchire || 0;
      } catch (e) { if (!silent) this.fail(e.message); }
      this.catalogo.busy = false;
    },

    // L'ultimo carico: il job con id più alto fra le righe (o quello appena lanciato).
    get ultimoJobId() {
      if (this.ultimoJob && this.ultimoJob.id) return this.ultimoJob.id;
      return this.catalogo.righe.reduce((m, r) => Math.max(m, r.job_id || 0), 0) || null;
    },
    get righeVista() {
      const v = this.catalogo.vista;
      const q = (this.catalogo.cerca || '').trim();
      // con una ricerca attiva si cerca ovunque, altrimenti la vista decide
      if (q || v === 'tutti') return this.catalogo.righe;
      if (v === 'da_fare') return this.catalogo.righe.filter(r => ['da_completare', 'non_trovato', 'problema'].includes(r.stato));
      if (v === 'carico' && this.catalogo.caricoScelto) {
        const id = this.catalogo.caricoScelto.id;
        return this.catalogo.righe.filter(r => r.job_id === id);
      }
      const u = this.ultimoJobId;
      return u ? this.catalogo.righe.filter(r => r.job_id === u) : this.catalogo.righe;
    },
    get conteggiVista() {
      const c = {};
      this.righeVista.forEach(r => { c[r.stato] = (c[r.stato] || 0) + 1; });
      return c;
    },
    get righeFiltrate() {
      const q = (this.catalogo.cerca || '').trim().toLowerCase();
      return this.righeVista.filter(r => {
        if (this.catalogo.filtro && r.stato !== this.catalogo.filtro) return false;
        if (!q) return true;
        return (r.title || '').toLowerCase().includes(q) || (r.brand || '').toLowerCase().includes(q) || (r.gtin || '').includes(q);
      });
    },
    etichettaVista() {
      const v = this.catalogo.vista;
      if (v === 'tutti') return this.tt('{0} prodotti caricati in tutto', this.catalogo.totale);
      if (v === 'da_fare') return this.tt('{0} prodotti che aspettano te', this.righeVista.length);
      if (v === 'carico' && this.catalogo.caricoScelto) {
        return this.tt('{0} prodotti del carico «{1}» del {2}', this.righeVista.length,
                       this.catalogo.caricoScelto.label, this.fmtDate(this.catalogo.caricoScelto.created_at));
      }
      return this.tt('{0} prodotti dell\'ultimo carico', this.righeVista.length);
    },
    // Dallo Storico: si torna alla tabella dei Prodotti filtrata su quel carico,
    // invece di aprire la card tecnica sotto la lista.
    mostraCarico(j) {
      this.catalogo.caricoScelto = { id: j.id, label: j.label, created_at: j.created_at };
      this.catalogo.vista = 'carico';
      this.catalogo.filtro = null;
      this.catalogo.sel = {};
      this.go('catalogo');
    },

    get numSel() { return Object.values(this.catalogo.sel).filter(Boolean).length; },
    get numEliminabiliSel() {
      const sel = this.catalogo.sel || {};
      return this.catalogo.righe.filter(r => sel[r.item_id] && r.can_delete).length;
    },
    get tuttiSel() {
      const vis = this.righeFiltrate.filter(r => r.stato !== 'in_corso');
      return vis.length > 0 && vis.every(r => this.catalogo.sel[r.item_id]);
    },
    selTutti(on) {
      const sel = {};
      if (on) this.righeFiltrate.forEach(r => { if (r.stato !== 'in_corso') sel[r.item_id] = true; });
      this.catalogo.sel = sel;
    },

    statoLabel(r) {
      const base = { sul_sito: 'Sul sito', pronto: 'Pronto da pubblicare', da_completare: 'Da completare',
                     non_trovato: 'Non trovato', in_corso: 'In ricerca…', problema: 'Problema' }[r.stato] || r.stato;
      if (r.stato === 'da_completare') {
        const nomi = { regular_price: 'prezzo', category: 'categoria', title: 'nome',
                       description: 'descrizione', images: 'foto', attributes: 'specifiche',
                       brand: 'marca', mpn: 'codice produttore' };
        const fonte = (r.missing_source || []).map(k => this.t(nomi[k] || k));
        const utente = (r.missing_user || []).map(k => this.t(nomi[k] || k));
        if (fonte.length) return this.tt('Scheda incompleta: {0}', fonte.join(', '));
        if (utente.length) return this.tt('Da compilare: {0}', utente.join(', '));
      }
      return base;
    },

    // Salvataggio in riga: piccolo ritardo per non chiamare il server a ogni
    // tasto; alla fine si rilegge lo stato della riga dal catalogo.
    salvaRiga(r) {
      clearTimeout(this._timerRiga[r.item_id]);
      this._timerRiga[r.item_id] = setTimeout(async () => {
        this.catalogo.salvando[r.item_id] = true;
        try {
          await this.api('/api/sites/' + this.form.site_id + '/catalogo/bulk', {
            method: 'POST',
            body: JSON.stringify({ item_ids: [r.item_id], category_id: r.category_id || null,
                                   regular_price: r.regular_price || '', sale_price: r.sale_price || '' })
          });
        } catch (e) { this.fail(e.message); }
        delete this._timerRiga[r.item_id];
        this.catalogo.salvando[r.item_id] = false;
        this.loadCatalogo(true);
      }, 600);
    },

    async applicaBulk(cosa) {
      const ids = Object.keys(this.catalogo.sel).filter(k => this.catalogo.sel[k]).map(Number);
      if (!ids.length) return;
      const body = { item_ids: ids };
      if (cosa === 'categoria') body.category_id = this.bulk.category_id;
      if (cosa === 'prezzo') body.regular_price = this.bulk.regular_price;
      try {
        const d = await this.api('/api/sites/' + this.form.site_id + '/catalogo/bulk', { method: 'POST', body: JSON.stringify(body) });
        this.say(this.tt('{0} prodotti aggiornati.', d.aggiornate));
        this.catalogo.sel = {};
        if (cosa === 'prezzo') this.bulk.regular_price = '';
        await this.loadCatalogo(true);
      } catch (e) { this.fail(e.message); }
    },

    async eliminaRiga(r) {
      if (!r || !r.can_delete) return;
      const nome = r.title || r.gtin || this.t('questo prodotto');
      if (!window.confirm(this.tt('Eliminare «{0}» dal catalogo?', nome) + '\n\n' + this.t('Non verrà cancellato nulla da WooCommerce.'))) return;
      try {
        const d = await this.api('/api/items/' + r.item_id, { method: 'DELETE' });
        this.catalogo.sel[r.item_id] = false;
        this.say('Prodotto eliminato dal catalogo.');
        await this.loadCatalogo(true);
      } catch (e) { this.fail(e.message); }
    },

    async eliminaSelezionati() {
      const ids = this.catalogo.righe
        .filter(r => this.catalogo.sel[r.item_id] && r.can_delete)
        .map(r => r.item_id);
      if (!ids.length) { this.say('Tra i selezionati non ci sono schede eliminabili.', 'warn'); return; }
      if (!window.confirm(this.tt('Eliminare dal catalogo {0} prodotti selezionati?', ids.length) + '\n\n' + this.t('Non verrà cancellato nulla da WooCommerce.'))) return;
      try {
        const d = await this.api('/api/sites/' + this.form.site_id + '/catalogo/delete', {
          method: 'POST', body: JSON.stringify({ item_ids: ids })
        });
        this.catalogo.sel = {};
        this.say(this.tt('{0} prodotti eliminati dal catalogo.', d.prodotti || ids.length));
        await this.loadCatalogo(true);
        if (this.job) await this.refreshJob();
      } catch (e) { this.fail(e.message); }
    },

    async eliminaItemJob(it) {
      if (!it || !it.can_delete) return;
      const nome = it.title || it.gtin || this.t('questo prodotto');
      if (!window.confirm(this.tt('Eliminare «{0}» dal catalogo?', nome) + '\n\n' + this.t('Non verrà cancellato nulla da WooCommerce.'))) return;
      try {
        await this.api('/api/items/' + it.id, { method: 'DELETE' });
        this.say('Prodotto eliminato dal catalogo.');
        await this.refreshJob();
        await this.loadCatalogo(true);
      } catch (e) { this.fail(e.message); }
    },

    async pubblicaTutto() {
      try {
        const d = await this.api('/api/sites/' + this.form.site_id + '/catalogo/pubblica', { method: 'POST' });
        this.say(d.pronte ? this.tt('Invio sul sito {0} prodotti: ci vuole qualche minuto.', d.pronte) : 'Niente di pronto da pubblicare.');
        await this.loadCatalogo(true);
      } catch (e) { this.fail(e.message); this.loadMe(); }
    },

    // Pubblica una riga sola: compare solo sulle righe «pronte». Mentre va, la
    // riga mostra «pubblico…» e il pulsante non si può ripremere.
    async pubblicaRiga(r) {
      if (r.stato !== 'pronto' || this.catalogo.salvando[r.item_id]) return;
      this.catalogo.salvando[r.item_id] = 'pubblico…';
      try {
        const d = await this.api('/api/items/' + r.item_id + '/pubblica', { method: 'POST' });
        if (d.stato === 'pushed') this.say(this.tt('«{0}» è sul sito.', r.title || r.gtin));
        else this.fail(this.tt('Non sono riuscito a pubblicare «{0}»: {1}', r.title || r.gtin, d.errore || this.t('riprova fra poco.')));
      } catch (e) { this.fail(e.message); if (e.status === 402) this.loadMe(); }
      this.catalogo.salvando[r.item_id] = false;
      await this.loadCatalogo(true);
    },

    async riprovaRiga(r) {
      try {
        await this.api('/api/items/' + r.item_id + '/retry', { method: 'POST' });
        this.say(this.tt('Ricerco «{0}» da capo.', r.gtin));
        await this.loadCatalogo(true);
      } catch (e) { this.fail(e.message); }
    },

    async riprovaSchedeIncomplete() {
      try {
        const d = await this.api('/api/sites/' + this.form.site_id + '/catalogo/retry-missing', { method: 'POST' });
        this.say(d.requeued ? this.tt('Ricerco da capo {0} schede con dati mancanti.', d.requeued) : 'Non ci sono schede da ricercare di nuovo.');
        await this.loadCatalogo(true);
      } catch (e) { this.fail(e.message); }
    },

    // banner dell'importazione appena lanciata
    get ultimoJobFinito() {
      if (!this.ultimoJob) return false;
      const c = this.ultimoJob.counts || {};
      return (c.pending || 0) + (c.fetching || 0) === 0 && this.ultimoJob.status !== 'queued' && this.ultimoJob.status !== 'running';
    },
    async aggiornaUltimoJob() {
      try { this.ultimoJob = await this.api('/api/jobs/' + this.ultimoJob.id); } catch (e) { if (e.status === 404) this.ultimoJob = null; }
    },
    riepilogoRicerca(j) {
      const c = j.counts || {};
      const fatti = (j.total || 0) - (c.pending || 0) - (c.fetching || 0);
      return this.tt('{0} di {1} controllati', fatti, j.total);
    },
    riepilogoFinale(j) {
      const c = j.counts || {};
      const parti = [];
      if (c.ready) parti.push(this.tt('{0} pronti da pubblicare', c.ready));
      if (c.pushed) parti.push(this.tt('{0} già sul sito', c.pushed));
      if (c.incomplete) parti.push(this.tt('{0} da completare', c.incomplete));
      if (c.not_found) parti.push(this.tt('{0} non trovati: «Perché?» ti dice il motivo', c.not_found));
      if (c.error || c.blocked) parti.push(this.tt('{0} con problemi', (c.error || 0) + (c.blocked || 0)));
      if (c.pending) parti.push(this.tt('{0} in attesa: i motori di ricerca ci hanno rallentati, riprovo da solo', c.pending));
      return parti.length ? parti.join(' · ') + '.' : this.t('Nessun prodotto lavorato.');
    },

    // ------------------------------------------------------------- job
    async loadJobs(silent) {
      try {
        this.jobs = await this.api('/api/jobs');
      } catch (e) { if (!silent) this.fail(e.message); }
    },

    async openJob(id, solo) {
      this.soloJob = !!solo;
      this.review.proposta = false;
      this.job = await this.api('/api/jobs/' + id);
      this.itemFilter = null;
      await this.loadItems();
      // La card si apre sotto la lista: la si porta a schermo, sennò sembra che
      // il tasto non abbia fatto niente.
      this.$nextTick(() => { const el = document.getElementById('job-card'); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); });
      // Se la ricerca è già finita (o era istantanea) la revisione parte subito:
      // il cliente non deve capire dove premere.
      const c = this.job.counts || {};
      const cercando = (c.pending || 0) + (c.fetching || 0);
      if (solo && !cercando && this.daRivedere > 0 && !this.review.on) {
        this.review.proposta = true;
        this.startReview();
      }
    },

    async refreshJob() {
      if (!this.job) return;
      // Appena la ricerca finisce si entra da soli nella revisione: è il passo
      // successivo in ogni caso, farlo cercare è un clic buttato.
      const prima = this.job.counts || {};
      const cercando = (prima.pending || 0) + (prima.fetching || 0);
      let aggiornato;
      try {
        aggiornato = await this.api('/api/jobs/' + this.job.id);
      } catch (e) {
        // Fine dell'eliminazione: il backend cancella il job dal database, quindi
        // la richiesta torna 404. Non è un errore: si chiude e si torna all'elenco.
        if (e.status === 404) {
          const eraPurge = this.job.status === 'purging';
          this.job = null; this.items = []; this.soloJob = false;
          this.say(eraPurge
            ? 'Eliminazione completata: prodotti tolti dal sito e importazione rimossa.'
            : 'Questa importazione non esiste più.');
          this.loadJobs();
        }
        return; // qualunque altro errore: si riprova al prossimo giro del poll
      }
      this.job = aggiornato;
      this.loadItems();
      const ora = this.job.counts || {};
      const finito = (ora.pending || 0) + (ora.fetching || 0) === 0;
      if (cercando > 0 && finito && !this.review.on && !this.review.proposta && this.daRivedere > 0) {
        this.review.proposta = true;
        this.startReview();
      }
    },

    async loadItems() {
      if (this.review.on && !this.job) this.review.on = false;
      if (!this.job) return;
      let url = '/api/jobs/' + this.job.id + '/items?limit=200';
      if (this.itemFilter) url += '&status_filter=' + this.itemFilter;
      this.items = await this.api(url);
    },

    get hasReady() {
      return this.job && (this.job.counts.ready || 0) > 0;
    },

    async publishJob() {
      try {
        await this.api('/api/jobs/' + this.job.id + '/publish', { method: 'POST' });
        this.say('Pubblicazione avviata. Le schede vengono mandate sul sito una alla volta.');
      } catch (e) {
        this.fail(e.message);
        this.loadMe();
        return;
      }
      this.refreshJob();
    },

    async askPurge() {
      try {
        const info = await this.api('/api/jobs/' + this.job.id + '/purge-preview');
        this.modal = { error: null, kind: 'purge', title: this.t('Elimina importazione'), data: info };
      } catch (e) { this.say(e.message, 'err'); }
    },

    async doPurge(anche_dal_sito) {
      const id = this.job.id;
      try {
        if (anche_dal_sito) {
          const r = await this.api('/api/jobs/' + id + '/purge', { method: 'POST' });
          this.modal.kind = null;
          this.say(this.tt('{0} prodotti in eliminazione dal sito.', r.marcati));
          this.refreshJob();
        } else {
          await this.api('/api/jobs/' + id, { method: 'DELETE' });
          this.modal.kind = null;
          this.job = null; this.items = [];
          this.say('Importazione eliminata dal portale. Il sito non è stato toccato.');
          this.loadJobs();
        }
      } catch (e) { this.fail(e.message); }
    },

    async retryJob() {
      const r = await this.api('/api/jobs/' + this.job.id + '/retry', { method: 'POST' });
      this.say(this.tt('{0} righe rimesse in coda.', r.requeued));
      this.refreshJob();
    },

    // Quante schede aspettano ancora una revisione: pronte, da completare, non trovate.
    get daRivedere() {
      if (!this.job || !this.job.counts) return 0;
      const c = this.job.counts;
      return (c.ready || 0) + (c.incomplete || 0) + (c.not_found || 0);
    },

    // ------------------------------------------------- revisione guidata
    async startReview() {
      // Servono le categorie del sito per poter scegliere dove va ogni prodotto.
      if (!this.categories.length) await this.loadTaxonomy();
      const tutte = await this.api('/api/jobs/' + this.job.id + '/items');
      const daVedere = ['ready', 'incomplete', 'not_found'];
      this.review.ids = tutte.filter(i => daVedere.indexOf(i.status) !== -1).map(i => i.id);
      if (!this.review.ids.length) { this.say('Non c\'è niente da rivedere.', 'warn'); return; }
      this.review.i = 0;
      this.review.on = true;
      await this.loadReviewItem();
    },

    async loadReviewItem() {
      const id = this.review.ids[this.review.i];
      this.review.err = '';
      const d = await this.api('/api/items/' + id + '/preview');
      const pr = d.product || {};
      this.review.item = { id: d.id, gtin: d.gtin, status: d.status, source: d.source, issues: d.issues || [], can_delete: !!d.can_delete };
      this.review.labels = d.field_labels || {};
      this.edit = {
        title: pr.title || '',
        brand: pr.brand || '',
        mpn: pr.mpn || '',
        description: pr.description || '',
        short_description: '',
        category_name: (pr.category && pr.category.name) || '',
        category_id: d.category_id || '',
        images: (pr.images || []).slice(),
        attributes: (pr.attributes || []).map(a => ({ name: a.name, value: a.value })),
        regular_price: pr.regular_price || '',
        sale_price: pr.sale_price || ''
      };
      // Il cursore va dove serve: il prezzo è l'unica cosa che devi digitare.
      this.$nextTick(() => { if (this.$refs.prezzo) this.$refs.prezzo.focus(); });
    },

    async reviewSave() {
      // Si può salvare anche una scheda ancora incompleta. Categoria e prezzo
      // restano obbligatori solo per PUBBLICARE, non per correggere nome/foto/testo:
      // così non si è costretti a fare avanti-indietro fra modale e catalogo.
      this.review.err = '';
      this.review.saving = true;
      try {
        await this.api('/api/items/' + this.review.item.id, {
          method: 'PUT', body: JSON.stringify(this.edit)
        });
      } catch (e) {
        this.review.saving = false;
        this.say(e.message, 'err');
        return;
      }
      this.review.saving = false;
      await this.reviewNext(true);
    },

    async eliminaReviewItem() {
      const it = this.review.item;
      if (!it || !it.can_delete) return;
      if (!window.confirm('Eliminare questa scheda dal catalogo?\n\nNon verrà cancellato nulla da WooCommerce.')) return;
      try {
        await this.api('/api/items/' + it.id, { method: 'DELETE' });
        this.review.ids.splice(this.review.i, 1);
        await this.loadCatalogo(true);
        await this.refreshJob();
        if (!this.review.ids.length) {
          this.review.on = false;
          this.review.item = null;
          this.say('Scheda eliminata. Revisione completata.');
          return;
        }
        if (this.review.i >= this.review.ids.length) this.review.i = this.review.ids.length - 1;
        await this.loadReviewItem();
        this.say('Scheda eliminata dal catalogo.');
      } catch (e) { this.fail(e.message); }
    },

    async reviewNext(pubblica) {
      if (this.review.i + 1 >= this.review.ids.length) {
        this.review.on = false;
        await this.refreshJob();
        if (pubblica && this.hasReady && !(this.sub && !this.sub.allowed)) {
          await this.publishJob();   // ultimo passo compreso nel pulsante
        } else {
          this.say('Revisione finita.');
        }
        return;
      }
      this.review.i += 1;
      await this.loadReviewItem();
    },

    async reviewPrev() {
      if (this.review.i === 0) return;
      this.review.i -= 1;
      await this.loadReviewItem();
    },

    // ------------------------------------------------ compilazione manuale
    async editItem(itemId) {
      if (!this.categories.length) await this.loadTaxonomy();
      const d = await this.api('/api/items/' + itemId + '/preview');
      const p = d.product || {};
      this.edit = {
        title: p.title || '',
        brand: p.brand || '',
        mpn: p.mpn || '',
        description: p.description || '',
        short_description: '',
        category_name: (p.category && p.category.name) || '',
        category_id: d.category_id || '',
        images: (p.images || []).slice(),
        attributes: (p.attributes || []).map(a => ({ name: a.name, value: a.value })),
        regular_price: p.regular_price || '',
        sale_price: p.sale_price || ''
      };
      this.modal = {
        error: null,
        kind: 'edit',
        title: this.t(d.status === 'not_found' ? 'Compila la scheda' : 'Correggi la scheda'),
        data: { id: d.id, gtin: d.gtin, status: d.status, source: d.source,
                issues: d.issues || [], field_labels: d.field_labels || {} }
      };
    },

    async saveItem() {
      // Anche qui il salvataggio parziale è ammesso: il backend tiene la scheda
      // in “da completare” finché non ha tutto ciò che serve alla pubblicazione.
      try {
        const r = await this.api('/api/items/' + this.modal.data.id, {
          method: 'PUT', body: JSON.stringify(this.edit)
        });
        this.modal.data.issues = r.issues;
        if (this.tab === 'catalogo') this.loadCatalogo(true);
        if (r.issues.length) {
          this.say('Salvata, ma manca ancora qualcosa: ' +
                   r.issues.map(i => this.modal.data.field_labels[i] || i).join(', '), 'warn');
        } else {
          this.say('Scheda completa, pronta per il sito.');
          this.modal.kind = null;
        }
        this.refreshJob();
      } catch (e) { this.fail(e.message); }
    },

    // ------------------------------------------------------------- prezzi
    async openPrices() {
      this.pricesOpen = !this.pricesOpen;
      if (this.pricesOpen && !this.pricesText) {
        const r = await this.api('/api/jobs/' + this.job.id + '/prices/template');
        this.pricesText = r.text;
      }
    },

    async savePrices() {
      try {
        const r = await this.api('/api/jobs/' + this.job.id + '/prices', {
          method: 'POST', body: JSON.stringify({ text: this.pricesText })
        });
        let msg = this.tt('{0} righe aggiornate.', r.updated);
        if (r.unknown.length) msg += ' ' + this.tt('{0} codici non erano in questa importazione.', r.unknown.length);
        this.say(msg);
        this.refreshJob();
      } catch (e) { this.fail(e.message); }
    },

    // ------------------------------------------------------------- fonti
    providerKind(k) {
      return { web: 'Ricerca sul web', http_json: 'Servizio JSON', manual: 'Manuale' }[k] || k;
    },

    async confrontaFonti() {
      if (!this.confrontoGtin) { this.fail('Metti un EAN da confrontare.'); return; }
      this.confronto = [];
      this.busy = true;
      try {
        const r = await this.api('/api/admin/clients/' + this.editingClientId +
          '/providers/confronta?gtin=' + encodeURIComponent(this.confrontoGtin), { method: 'POST' });
        this.confronto = r.esiti;
      } catch (e) { this.fail(e.message); }
      this.busy = false;
    },

    async loadProviders(clientId) {
      try {
        const r = await this.api('/api/admin/clients/' + clientId + '/providers');
        this.clientProviders = r.providers;
        this.qualityFields = r.fields;
        this.presetFonti = r.preset || {};
      } catch (e) { this.clientProviders = []; }
    },

    // Una fonte è «attiva» se il cliente ne ha già una di quel tipo.
    fonteAttiva(chiave) {
      const preset = this.presetFonti[chiave];
      if (!preset) return null;
      return this.clientProviders.find(p =>
        p.kind === preset.kind && (preset.kind !== 'http_json' || p.name === preset.name)
      ) || null;
    },

    aggiungiFonte(chiave) {
      const preset = this.presetFonti[chiave];
      if (!preset) return;
      this.providerTest = null;
      this.modal = {
        error: null,
        kind: 'provider',
        title: this.t('Attiva') + ' ' + this.t(preset.name),
        data: {
          client_id: this.editingClientId,
          name: preset.name,
          kind: preset.kind,
          priority: preset.priority,
          descrizione: preset.descrizione || '',
          is_active: true,
          lang: '',
          username: '',
          secret: '',
          endpoint: preset.endpoint || '',
          auth_mode: preset.auth_mode || 'none',
          auth_name: preset.auth_name || '',
          root_path: preset.root_path || '',
          field_map_text: JSON.stringify(preset.field_map || {}, null, 2)
        }
      };
    },

    addProvider() {
      this.providerTest = null;
      this.modal = {
        error: null,
        kind: 'provider',
        title: this.t('Nuova fonte'),
        data: {
          client_id: this.editingClientId, name: '', kind: 'http_json', priority: 20,
          is_active: true, lang: '', username: '', secret: '', endpoint: '',
          auth_mode: 'none', auth_name: '', root_path: '',
          field_map_text: JSON.stringify({
            title: 'name',
            brand: 'brand',
            mpn: 'code',
            description: 'description',
            category: 'category.name',
            images: { path: 'assets.images[]', url: 'url' },
            attributes: { path: 'features[]', name: 'name', value: 'value', unit: 'unit' },
            documents: { path: 'assets.documents[]', url: 'url', label: 'title' }
          }, null, 2)
        }
      };
    },

    editProvider(pr) {
      this.providerTest = null;
      const preset = Object.values(this.presetFonti).find(p => p.kind === pr.kind && p.name === pr.name);
      this.modal = {
        error: null,
        kind: 'provider',
        title: pr.name,
        data: Object.assign({}, pr, {
          secret: '',
          descrizione: (preset && preset.descrizione) || '',
          field_map_text: JSON.stringify(pr.field_map || {}, null, 2)
        })
      };
    },

    providerBody() {
      const d = this.modal.data;
      let map = null;
      if (d.kind === 'http_json') {
        try { map = JSON.parse(d.field_map_text || '{}'); }
        catch (e) { throw new Error('La mappa dei campi non è JSON valido.'); }
      }
      return JSON.stringify({
        name: d.name, kind: d.kind, priority: d.priority || 20,
        is_active: d.is_active !== false, lang: d.lang || '',
        username: d.username || '', secret: d.secret ? d.secret : null,
        endpoint: d.endpoint || '', auth_mode: d.auth_mode || 'none',
        auth_name: d.auth_name || '', root_path: d.root_path || '',
        field_map: map, notes: d.notes || ''
      });
    },

    async saveProvider() {
      try {
        const body = this.providerBody();
        if (this.modal.data.id) {
          await this.api('/api/admin/providers/' + this.modal.data.id, { method: 'PUT', body: body });
        } else {
          const created = await this.api('/api/admin/clients/' + this.editingClientId + '/providers',
                                         { method: 'POST', body: body });
          this.modal.data.id = created.id;
        }
        this.say('Fonte salvata.');
        this.loadProviders(this.editingClientId);
      } catch (e) { this.fail(e.message); }
    },

    async deleteProvider() {
      if (!confirm('Disattivo la fonte ' + this.modal.data.name + ' per questo cliente?')) return;
      await this.api('/api/admin/providers/' + this.modal.data.id, { method: 'DELETE' });
      this.modal.kind = null;
      this.loadProviders(this.editingClientId);
    },

    async testProvider() {
      if (!this.providerTestGtin) { this.fail('Metti un EAN di prova.'); return; }
      // Un errore della prova precedente non deve restare lì a confondere:
      // in vetrina c'era il banner rosso vecchio sopra l'esito verde nuovo.
      this.modal.error = null;
      this.providerTest = null;
      try {
        await this.saveProvider();
        if (!this.modal.data.id) return;
        this.providerTest = await this.api('/api/admin/providers/' + this.modal.data.id +
          '/test?gtin=' + encodeURIComponent(this.providerTestGtin), { method: 'POST' });
      } catch (e) { this.providerTest = { ok: false, error: e.message }; }
    },

    // -------------------------------------------------- campi obbligatori
    isRequired(field) {
      return (this.modal.data.required_fields || []).includes(field);
    },

    toggleRequired(field) {
      const cur = this.modal.data.required_fields || [];
      this.modal.data.required_fields = cur.includes(field)
        ? cur.filter(f => f !== field)
        : cur.concat(field);
    },

    async preview(itemId) {
      const data = await this.api('/api/items/' + itemId + '/preview');
      this.modal = { kind: 'preview', title: data.product.title || data.gtin, data: data };
    },

    // ------------------------------------------------------------- admin
    async loadClients() { this.clients = (await this.api('/api/admin/clients')).clients; },
    async loadAllSites() { this.allSites = await this.api('/api/admin/sites'); },
    async loadUsers() { this.users = await this.api('/api/admin/users'); },
    async loadAudit() { this.auditRows = await this.api('/api/admin/audit?limit=60'); },

    ricercaServe(campo) {
      const m = this.ricerca.motori_disponibili && this.ricerca.motori_disponibili[this.ricerca.motore];
      return !!(m && m.campi && m.campi.indexOf(campo) !== -1);
    },

    async loadRicerca() {
      try {
        const r = await this.api('/api/admin/ricerca');
        this.ricerca = Object.assign({ chiave: '' }, r);
      } catch (e) {}
    },

    async salvaRicerca() {
      try {
        const r = await this.api('/api/admin/ricerca', {
          method: 'PUT',
          body: JSON.stringify({
            motore: this.ricerca.motore || '',
            chiave: this.ricerca.chiave ? this.ricerca.chiave : null,
            cx: this.ricerca.cx || '',
            url: this.ricerca.url || '',
            motori: this.ricerca.motori || '',
            lt_url: this.ricerca.lt_url || '',
            lt_chiave: this.ricerca.lt_chiave ? this.ricerca.lt_chiave : null,
            render_url: this.ricerca.render_url || ''
          })
        });
        this.ricerca = Object.assign({ chiave: '' }, r);
        this.say(r.attiva ? 'Ricerca automatica attiva.' : 'Ricerca automatica spenta.');
      } catch (e) { this.say(e.message, 'err'); }
    },

    // Svuota la memoria della ricerca. Solo i buchi di base; con tutto=true anche
    // le schede web trovate, che al prossimo giro si riscaricano da capo.
    async svuotaCacheRicerca(tutto) {
      if (tutto && !confirm('Buttare anche le schede web già trovate? Al prossimo giro si riscaricano da capo.')) return;
      try {
        const r = await this.api('/api/admin/ricerca/cache' + (tutto ? '?tutto=true' : ''), { method: 'DELETE' });
        this.say(r.cancellate
          ? r.cancellate + (tutto ? ' righe di cache web eliminate.' : ' tentativi a vuoto eliminati: le prossime ricerche ripartono da zero.')
          : 'Non c\'era niente da svuotare.');
      } catch (e) { this.say(e.message, 'err'); }
    },

    async provaRicerca() {
      if (!this.ricercaProvaGtin) { this.say('Metti un EAN di prova.', 'warn'); return; }
      this.ricercaEsito = null;
      this.busy = true;
      try {
        await this.salvaRicerca();
        this.ricercaEsito = await this.api('/api/admin/ricerca/prova?gtin=' +
          encodeURIComponent(this.ricercaProvaGtin) + '&lang=' + this.ricercaProvaLingua, { method: 'POST' });
        this.loadSalute();
      } catch (e) { this.ricercaEsito = { ok: false, error: e.message }; }
      this.busy = false;
    },

    async loadBrand() {
      try { this.brandForm = await this.api('/api/admin/brand'); } catch (e) {}
    },

    async saveBrand() {
      try {
        this.brandForm = await this.api('/api/admin/brand', {
          method: 'PUT', body: JSON.stringify({ name: this.brandForm.name })
        });
        this.say('Nome salvato. Ricarica la pagina per vederlo in alto.');
      } catch (e) { this.say(e.message, 'err'); }
    },

    async uploadBrandLogo(event) {
      const file = event.target.files[0];
      if (!file) return;
      const body = new FormData();
      body.append('file', file);
      try {
        const res = await fetch('/api/admin/brand/logo', { method: 'POST', body: body });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Caricamento non riuscito.');
        this.brandForm = data;
        this.logoBust = Date.now();
        this.say('Logo caricato. Ricarica la pagina per vederlo in alto.');
      } catch (e) { this.say(e.message, 'err'); }
      event.target.value = '';
    },

    async removeBrandLogo() {
      if (!confirm('Rimuovo il logo del portale?')) return;
      this.brandForm = await this.api('/api/admin/brand/logo', { method: 'DELETE' });
    },

    // La favicon caricata si vede subito nella scheda, senza ricaricare la pagina.
    async uploadBrandFavicon(event) {
      const file = event.target.files[0];
      if (!file) return;
      const body = new FormData();
      body.append('file', file);
      try {
        const res = await fetch('/api/admin/brand/favicon', { method: 'POST', body: body });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Caricamento non riuscito.');
        this.brandForm = data;
        this.applicaFavicon(data);
        this.say(data.avviso ? this.t('Favicon caricata.') + ' ' + this.t(data.avviso) : 'Favicon caricata.',
                 data.avviso ? 'warn' : 'ok');
      } catch (e) { this.say(e.message, 'err'); }
      event.target.value = '';
    },

    async removeBrandFavicon() {
      if (!confirm('Rimuovo la favicon e torno a quella di serie?')) return;
      try {
        this.brandForm = await this.api('/api/admin/brand/favicon', { method: 'DELETE' });
        this.applicaFavicon(this.brandForm);
        this.say('Favicon tolta: si torna a quella di serie.');
      } catch (e) { this.say(e.message, 'err'); }
    },

    applicaFavicon(marca) {
      document.querySelectorAll('link[rel~="icon"], link[rel="apple-touch-icon"]').forEach(l => l.remove());
      const aggiungi = (rel, href, tipo, misure) => {
        const l = document.createElement('link');
        l.rel = rel; l.href = href;
        if (tipo) l.type = tipo;
        if (misure) l.sizes = misure;
        document.head.appendChild(l);
      };
      if (marca && marca.favicon) {
        aggiungi('icon', marca.favicon, marca.favicon_type);
        if (marca.favicon_raster) aggiungi('apple-touch-icon', marca.favicon);
      } else {
        const v = '?v=' + Date.now();
        aggiungi('icon', '/static/favicon.svg' + v, 'image/svg+xml');
        aggiungi('icon', '/static/favicon-32.png' + v, 'image/png', '32x32');
        aggiungi('apple-touch-icon', '/static/apple-touch-icon.png' + v);
      }
    },

    editClient(c) {
      this.editingClientId = c ? c.id : null;
      this.clientProviders = [];
      if (c) this.loadProviders(c.id);
      this.modal = {
        error: null,
        kind: 'client',
        title: c ? c.name : this.t('Nuovo cliente'),
        data: c
          ? Object.assign({}, c, c.profile || {}, {
              content_lang: c.content_lang || 'IT',
              subscription_until_day: c.subscription_until ? c.subscription_until.slice(0, 10) : ''
            })
          : { name: '', notes: '', is_active: true, monthly_quota: 0, content_lang: this.linguaUI(),
              subscription_status: 'trial', plan_name: 'Prova', subscription_until_day: '',
              legal_name: '', vat_number: '', address: '', city: '', province: '',
              contact_email: '', pec: '', phone: '' }
      };
    },

    editSite(s) {
      this.modal = {
        error: null,
        kind: 'site',
        title: s ? s.name : this.t('Nuovo sito'),
        data: s ? Object.assign({ write_policy: {}, resync_policy: {}, content_lang: '' }, s, { wc_key: '', wc_secret: '' })
                : { client_id: this.clients.length ? this.clients[0].id : null, name: '', base_url: '',
                    is_active: true, default_status: 'publish', wc_key: '', wc_secret: '', specs_meta: true,
                    content_lang: '',
                    resync_enabled: false, resync_hour: 3, resync_minute: 0, resync_days: '',
                    resync_scope: 'hub', resync_batch: 200, resync_min_age_days: 25, specs_limit: 6, giorni_cestino: 30,
                    required_fields: ['title', 'description', 'images', 'category', 'regular_price'],
                    resync_policy: { title: 'skip', description: 'overwrite', short_description: 'overwrite',
                                     images: 'fill', attributes: 'overwrite', brand: 'fill',
                                     categories: 'skip', gtin: 'skip', prices: 'skip' },
                    write_policy: { title: 'fill', description: 'fill', short_description: 'fill',
                                    images: 'fill', attributes: 'overwrite', brand: 'fill',
                                    categories: 'fill', gtin: 'overwrite', prices: 'overwrite' } }
      };
      if (!this.modal.data.resync_policy || !Object.keys(this.modal.data.resync_policy).length) {
        this.modal.data.resync_policy = { title: 'skip', description: 'overwrite', short_description: 'overwrite',
                                          images: 'fill', attributes: 'overwrite', brand: 'fill',
                                          categories: 'skip', gtin: 'skip', prices: 'skip' };
      }
      if (!this.modal.data.write_policy || !Object.keys(this.modal.data.write_policy).length) {
        this.modal.data.write_policy = { title: 'fill', description: 'fill', short_description: 'fill',
                                         images: 'fill', attributes: 'overwrite', brand: 'fill',
                                         categories: 'fill', gtin: 'overwrite', prices: 'overwrite' };
      }
    },

    editUser(u) {
      this.modal = {
        error: null,
        kind: 'user',
        title: u ? u.email : this.t('Nuovo utente'),
        data: u
          ? Object.assign({}, u, { password: '', client_id: u.client_id || '' })
          : { email: '', full_name: '', role: 'client', client_id: '', is_active: true, password: '' }
      };
    },

    async saveModal() {
      const d = this.modal.data;
      this.modal.error = null;
      try {
        if (this.modal.kind === 'edit') { await this.saveItem(); return; }
        if (this.modal.kind === 'provider') { await this.saveProvider(); return; }
        if (this.modal.kind === 'client') {
          const body = JSON.stringify({
            name: d.name, notes: d.notes || '', is_active: d.is_active,
            content_lang: d.content_lang || 'IT',
            subscription_status: d.subscription_status || 'trial',
            plan_name: d.plan_name || '',
            subscription_until: d.subscription_until_day ? d.subscription_until_day + 'T00:00:00+00:00' : null,
            monthly_quota: d.monthly_quota || 0,
            legal_name: d.legal_name || '', vat_number: d.vat_number || '',
            contact_email: d.contact_email || '', pec: d.pec || '', phone: d.phone || '',
            tax_code: d.tax_code || '', sdi_code: d.sdi_code || '', pec: d.pec || '',
            address: d.address || '', zip_code: d.zip_code || '', city: d.city || '',
            province: d.province || '', country: d.country || 'Italia',
            phone: d.phone || '', contact_email: d.contact_email || '',
            website: d.website || '', accent_color: d.accent_color || ''
          });
          if (d.id) await this.api('/api/admin/clients/' + d.id, { method: 'PUT', body: body });
          else await this.api('/api/admin/clients', { method: 'POST', body: body });
          this.modal.kind = null;
          this.loadClients();
          this.say('Cliente salvato.');

        } else if (this.modal.kind === 'site') {
          const body = JSON.stringify({
            client_id: d.client_id, name: d.name, base_url: d.base_url,
            is_active: d.is_active !== false, default_status: d.default_status,
            wc_key: d.wc_key ? d.wc_key : null, wc_secret: d.wc_secret ? d.wc_secret : null,
            write_policy: d.write_policy,
            required_fields: d.required_fields || null,
            specs_meta: d.specs_meta !== false,
            specs_limit: d.specs_limit === undefined ? 6 : d.specs_limit,
            giorni_cestino: d.giorni_cestino || 30,
            content_lang: d.content_lang || '',
            resync_enabled: !!d.resync_enabled,
            resync_hour: d.resync_hour ?? 3,
            resync_minute: d.resync_minute ?? 0,
            resync_days: d.resync_days || '',
            resync_scope: d.resync_scope || 'hub',
            resync_batch: d.resync_batch ?? 200,
            resync_min_age_days: d.resync_min_age_days ?? 25,
            resync_policy: d.resync_policy
          });
          if (d.id) {
            await this.api('/api/admin/sites/' + d.id, { method: 'PUT', body: body });
            this.modal.kind = null;
            this.say('Sito salvato.');
            this.loadSites();
          } else {
            const created = await this.api('/api/admin/sites', { method: 'POST', body: body });
            this.modal.data = Object.assign({}, this.modal.data, created);
            this.say('Sito creato. Premi Verifica per controllare che le chiavi funzionino.');
          }
          this.loadAllSites();

        } else if (this.modal.kind === 'user') {
          const email = (d.email || '').trim();
          if (!email || email.indexOf('@') === -1 || email.indexOf('.') === -1) {
            this.fail('Metti un indirizzo email valido, tipo nome@dominio.it');
            return;
          }
          if (d.password && d.password.length < 10) {
            this.fail('La password deve avere almeno 10 caratteri.');
            return;
          }
          const body = JSON.stringify({
            email: email, full_name: d.full_name || '', role: d.role,
            client_id: d.client_id || null, is_active: d.is_active !== false,
            password: d.password ? d.password : null
          });
          if (d.id) {
            await this.api('/api/admin/users/' + d.id, { method: 'PUT', body: body });
            this.modal.kind = null;
            this.say('Utente salvato.');
          } else {
            const created = await this.api('/api/admin/users', { method: 'POST', body: body });
            // Se l'hai scritta tu la sai già: si mostra solo quando l'ha fatta il portale.
            this.modal.data.generated_password = d.password ? '' : created.password;
            this.modal.data.id = created.id;
            this.say('Utente creato. Copia la password: non si rivede.');
          }
          this.loadUsers();
        }
      } catch (e) { this.fail(e.message); }
    },

    isResyncDay(i) {
      return (this.modal.data.resync_days || '').includes(String(i));
    },

    toggleResyncDay(i) {
      const cur = (this.modal.data.resync_days || '').split('');
      const key = String(i);
      const next = cur.includes(key) ? cur.filter(c => c !== key) : cur.concat(key);
      this.modal.data.resync_days = next.sort().join('');
    },

    resyncLabel(site) {
      if (!site || !site.resync_enabled) return 'disattivato';
      const hh = String(site.resync_hour).padStart(2, '0');
      const mm = String(site.resync_minute || 0).padStart(2, '0');
      const names = ['Lun','Mar','Mer','Gio','Ven','Sab','Dom'];
      const days = (site.resync_days || '').split('').map(d => names[+d]).filter(Boolean);
      return this.tt('{0} alle {1}', days.length ? days.map(d => this.t(d)).join(', ') : this.t('tutte le notti'), hh + ':' + mm);
    },

    async resyncNow(s) {
      if (!confirm('Lancio subito l\'aggiornamento per ' + s.name + '?')) return;
      try {
        const r = await this.api('/api/admin/sites/' + s.id + '/resync-now', { method: 'POST' });
        this.say(r.message, r.ok ? 'ok' : 'warn');
        this.loadAllSites();
      } catch (e) { this.fail(e.message); }
    },

    async pingSite(s) {
      const r = await this.api('/api/admin/sites/' + s.id + '/ping', { method: 'POST' });
      this.say(r.ok ? 'Sito raggiunto correttamente.' : r.error, r.ok ? 'ok' : 'err');
      this.loadAllSites();
    },


    generaPassword() {
      const alfabeto = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
      const numeri = new Uint32Array(16);
      window.crypto.getRandomValues(numeri);
      this.modal.data.password = Array.from(numeri, n => alfabeto[n % alfabeto.length]).join('');
    },

    async resetPwd(u) {
      if (!confirm('Genero una nuova password per ' + u.email + '?')) return;
      const r = await this.api('/api/admin/users/' + u.id + '/reset-password', { method: 'POST' });
      window.prompt('Nuova password per ' + r.email + ':', r.password);
    },


    // ------------------------------------------------- perché non si trova
    causaTesto(causa) {
      return {
        trovato: 'Scheda trovata.',
        motori_bloccati: 'Motori di ricerca bloccati: il codice non è stato cercato davvero. Riprovo da solo più tardi.',
        motori_irraggiungibili: 'Il motore di ricerca non risponde: controlla che SearXNG sia acceso.',
        sconosciuto: 'Nessun sito indicizzato riporta questo codice: probabilmente non è pubblicato online. Prova col codice produttore o compila a mano.',
        siti_bloccati: 'Il codice c\'è, ma i siti che lo pubblicano rifiutano la lettura automatica.',
        non_confermato: 'Ho trovato pagine simili, ma nessuna dimostra che sia proprio questo prodotto.',
        solo_somiglianza: 'Una sola pagina somiglia per nome e nessuna riporta il codice: troppo poco per fidarsi.',
        ricerca_spenta: 'La ricerca automatica non è attiva.',
        codice_interno: 'Codice interno del grossista (prefisso 20-29): usa l\'EAN del produttore o il codice produttore.'
      }[causa] || causa || '';
    },

    // La stessa causa, corta: sta sotto il nome del prodotto in tabella.
    causaBreve(causa) {
      return {
        trovato: 'Trovata',
        motori_bloccati: 'Motori di ricerca bloccati: riprovo più tardi',
        motori_irraggiungibili: 'Motore di ricerca spento o irraggiungibile',
        sconosciuto: 'Nessun sito pubblica questo codice',
        siti_bloccati: 'I siti che lo pubblicano non si lasciano leggere',
        non_confermato: 'Nessuna pagina conferma che sia lui',
        solo_somiglianza: 'Trovato solo per somiglianza del nome',
        ricerca_spenta: 'Ricerca automatica spenta',
        codice_interno: 'Codice interno del grossista'
      }[causa] || causa || '';
    },

    esitoTesto(esito) {
      return {
        confermata: 'confermata', bloccata: 'protezione anti-bot', robots: 'vietato da robots.txt',
        http: 'errore del sito', rete: 'non risponde', senza_dati: 'senza dati leggibili',
        altro_prodotto: 'altro prodotto', non_conferma: 'non conferma il codice',
        incoerente: 'non coerente con gli altri siti', documento: 'documento PDF',
        elenco: 'pagina di categoria o di ricerca'
      }[esito] || esito || '';
    },

    fmtOra(iso) {
      if (!iso) return '';
      return new Date(iso).toLocaleTimeString(I18n.locale, { hour: '2-digit', minute: '2-digit' });
    },

    async apriPerche(itemId, titolo) {
      try {
        const d = await this.api('/api/items/' + itemId + '/diagnosi');
        this.modal = { error: null, kind: 'perche', title: this.tt('Perché: {0}', titolo || d.gtin),
                       data: { diagnosi: d.diagnosi || {}, riprova_alle: d.riprova_alle } };
      } catch (e) { this.fail(e.message); }
    },

    apriDiario(diagnosi, codice) {
      this.modal = { error: null, kind: 'perche', title: this.tt('Perché: {0}', codice), data: { diagnosi: diagnosi || {} } };
    },

    // Il diario di una ricerca, leggibile: la causa in parole povere, le
    // ricerche fatte (con i motori che hanno risposto e quelli che ci hanno
    // bloccati) e i siti aperti con l'esito di ognuno.
    renderDiagnosi(d) {
      if (!d) return '';
      const e = this.esc.bind(this);
      let html = '';
      if (d.causa) {
        html += '<div class="diario-causa c-' + e(d.causa) + '">' + e(this.causaTesto(d.causa)) + '</div>';
      }
      const ricerche = d.ricerche || [];
      if (ricerche.length) {
        html += '<h3 class="diario-h">Ricerche</h3><table class="diario"><thead><tr><th>Domanda</th>' +
                '<th class="right">Risultati</th><th class="right">Col codice</th><th>Motori</th></tr></thead><tbody>';
        ricerche.forEach(r => {
          const ok = (r.ok || []).map(m => '<span class="chip ok" translate="no">' + e(m) + '</span>').join(' ');
          const ko = (r.ko || []).map(k => '<span class="chip ko"><span translate="no">' + e(k[0]) + '</span> · ' +
                                         e(k[1] || '') + '</span>').join(' ');
          html += '<tr><td><span class="mono" translate="no">' + e(r.q || '—') + '</span>' +
                  (r.via && r.via !== 'searxng' ? ' <span class="hint" translate="no">(' + e(r.via) + ')</span>' : '') +
                  (r.lingua ? ' <span class="hint">· ' + e(r.lingua === '*' ? this.t('tutte le lingue') : this.t(this.nomeLingua(r.lingua))) + '</span>' : '') +
                  (r.errore ? '<div class="hint err">' + e(r.errore) + '</div>' : '') + '</td>' +
                  '<td class="right num">' + e(r.risultati == null ? '—' : r.risultati) + '</td>' +
                  '<td class="right num">' + e(r.ancorati == null ? '—' : r.ancorati) + '</td>' +
                  '<td>' + ok + ' ' + ko + '</td></tr>';
        });
        html += '</tbody></table>';
      }
      const pagine = d.pagine || [];
      if (pagine.length) {
        html += '<h3 class="diario-h">Siti aperti</h3><table class="diario"><thead><tr><th>Sito</th><th>Esito</th>' +
                '<th>Dettaglio</th></tr></thead><tbody>';
        pagine.forEach(p => {
          html += '<tr><td><a class="mono" translate="no" href="' + e(p.url) + '" target="_blank" rel="noopener">' +
                  e(p.dominio) + '</a></td><td><span class="esito es-' + e(p.esito) + '">' + e(this.esitoTesto(p.esito)) +
                  '</span></td><td class="hint">' + e(p.dettaglio || '') +
                  (p.browser && !/browser/.test(p.dettaglio || '') ? ' · col browser' : '') +
                  (p.segue ? '<div><a translate="no" class="mono" href="' + e(p.segue) + '" target="_blank" rel="noopener">→ ' +
                             e(p.segue.replace(/^https?:\/\/[^/]+/, '').slice(0, 70)) + '</a> <span>apro la scheda del prodotto</span></div>' : '') +
                  '</td></tr>';
        });
        html += '</tbody></table>';
      } else if (d.causa && d.causa !== 'trovato' && ricerche.length) {
        html += '<p class="hint">Nessun sito da aprire: fra i risultati dei motori non c\'era nessuna pagina col codice o col modello.</p>';
      }
      const fine = [];
      if (d.quando) fine.push(this.fmtDate(d.quando));
      if (d.lingua) fine.push(this.nomeLingua(d.lingua));
      if (d.secondi != null) fine.push(d.secondi + ' s');
      if (fine.length) html += '<p class="hint diario-fine">' + e(fine.join(' · ')) + '</p>';
      return html;
    },

    // ------------------------------------------------ salute e prove a lotti
    async loadSalute() {
      try { this.salute = await this.api('/api/admin/ricerca/salute'); } catch (e) { this.salute = null; }
    },

    async avviaLotto() {
      if (!this.lotto.codici.trim()) return;
      this.lotto.busy = true;
      try {
        this.lotto.stato = await this.api('/api/admin/ricerca/lotto', {
          method: 'POST', body: JSON.stringify({ codici: this.lotto.codici, lang: this.lotto.lingua })
        });
        clearInterval(this.lotto.timer);
        this.lotto.timer = setInterval(() => this.aggiornaLotto(), 3000);
      } catch (e) { this.fail(e.message); this.lotto.busy = false; }
    },

    async aggiornaLotto() {
      if (!this.lotto.stato) return;
      try {
        this.lotto.stato = await this.api('/api/admin/ricerca/lotto/' + this.lotto.stato.id);
      } catch (e) {
        clearInterval(this.lotto.timer); this.lotto.busy = false; this.fail(e.message); return;
      }
      if (this.lotto.stato.finito) {
        clearInterval(this.lotto.timer);
        this.lotto.busy = false;
        this.loadSalute();
      }
    },

    // ------------------------------------------------------------ render
    // Le descrizioni arrivano da pagine di terzi: il server le pulisce già, qui
    // si ripulisce comunque (schede salvate prima, fonti JSON dei connettori).
    // DOMParser crea un documento inerte: niente script eseguiti, niente
    // immagini caricate mentre lo si attraversa.
    pulisciHtml(html) {
      const doc = new DOMParser().parseFromString('<body>' + String(html == null ? '' : html) + '</body>', 'text/html');
      const ok = new Set(['P', 'BR', 'UL', 'OL', 'LI', 'STRONG', 'B', 'EM', 'I', 'U', 'H2', 'H3', 'H4', 'H5',
                          'TABLE', 'THEAD', 'TBODY', 'TFOOT', 'TR', 'TH', 'TD', 'SUP', 'SUB', 'BLOCKQUOTE']);
      const via = new Set(['SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'SVG', 'MATH', 'FORM', 'INPUT',
                           'BUTTON', 'SELECT', 'TEXTAREA', 'NOSCRIPT', 'TEMPLATE', 'LINK', 'META', 'IMG',
                           'VIDEO', 'AUDIO', 'CANVAS', 'FRAME', 'FRAMESET', 'BASE', 'MAP', 'AREA']);
      const pulisci = (nodo) => {
        for (const figlio of Array.from(nodo.childNodes)) {
          if (figlio.nodeType === 1) {
            const tag = figlio.tagName.toUpperCase();
            if (via.has(tag)) { figlio.remove(); continue; }
            pulisci(figlio);
            if (!ok.has(tag)) { figlio.replaceWith(...Array.from(figlio.childNodes)); continue; }
            for (const a of Array.from(figlio.attributes)) figlio.removeAttribute(a.name);
          } else if (figlio.nodeType !== 3) {
            figlio.remove();
          }
        }
      };
      pulisci(doc.body);
      return doc.body.innerHTML;
    },

    esc(v) {
      return String(v == null ? '' : v)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    },

    renderProduct(p) {
      if (!p) return '';
      const e = this.esc.bind(this);
      let html = '';

      if (p.warnings && p.warnings.length) {
        html += '<div class="notice n-warn">' + p.warnings.map(e).join('<br>') + '</div>';
      }

      html += '<dl class="kv">' +
        '<dt>EAN</dt><dd class="gtin">' + e(p.gtin) + '</dd>' +
        '<dt>Marca</dt><dd translate="no">' + e(p.brand || '—') + '</dd>' +
        '<dt>Codice produttore</dt><dd class="mono">' + e(p.mpn || '—') + '</dd>' +
        '<dt>Categoria della fonte</dt><dd translate="no">' + e((p.category && p.category.name) || '—') + '</dd>' +
        (p.lingua ? '<dt>Lingua della scheda</dt><dd translate="no">' + e(this.nomeLingua(p.lingua)) + '</dd>' : '') +
        (p.origine_dominio ? '<dt>Trovata su</dt><dd class="mono" translate="no">' + e(p.origine_dominio) + '</dd>' : '') +
        '</dl>';

      // I contenuti della scheda restano come sono: il traduttore dell'interfaccia
      // non deve toccarli (la lingua delle schede è un'altra scelta).
      html += '<h3 style="margin:16px 0 6px">Nome</h3><div translate="no">' + e(p.title || '—') + '</div>';

      if (p.images && p.images.length) {
        html += '<h3 style="margin:16px 0 6px">' + e(this.t('Immagini')) + ' (' + p.images.length + ')</h3><div class="thumbs">';
        p.images.slice(0, 8).forEach(u => { html += '<img src="' + e(u) + '" alt="" loading="lazy">'; });
        html += '</div>';
      }

      if (p.description) {
        html += '<h3 style="margin:16px 0 6px">Descrizione</h3><div translate="no">' + this.pulisciHtml(p.description) + '</div>';
      }

      if (p.attributes && p.attributes.length) {
        html += '<h3 style="margin:16px 0 6px">' + e(this.t('Specifiche')) + ' (' + p.attributes.length + ')</h3><div class="specs" translate="no"><table><tbody>';
        p.attributes.forEach(a => {
          html += '<tr><td style="width:45%">' + e(a.name) + '</td><td>' + e(a.value) + '</td></tr>';
        });
        html += '</tbody></table></div>';
      }

      if (p.documents && p.documents.length) {
        html += '<h3 style="margin:16px 0 6px">Documenti</h3><ul style="margin:0;padding-left:18px" translate="no">';
        p.documents.slice(0, 10).forEach(d => {
          html += '<li><a href="' + e(d.url) + '" target="_blank" rel="noopener">' + e(d.label || d.type || d.url) + '</a></li>';
        });
        html += '</ul>';
      }
      return html;
    },

    // Una barra piena, divisa in proporzione. Con le stanghette per EAN, un'unica
    // scheda dava un trattino di 5 pixel dentro un riquadro largo: sembrava rotto.
    barcode(job) {
      const order = ['pushed', 'ready', 'incomplete', 'pending', 'not_found', 'blocked', 'error'];
      const totale = job.total || 1;
      let html = '';
      order.forEach(stato => {
        const n = job.counts[stato] || 0;
        if (!n) return;
        const perc = (n * 100 / totale).toFixed(2);
        html += '<i class="s-' + stato + '" style="width:' + perc + '%" title="' +
                n + ' ' + this.itemLabel(stato).toLowerCase() + '"></i>';
      });
      return html || '<i class="s-pending" style="width:100%"></i>';
    },

    // La frase che dice cosa deve fare adesso, senza doverlo dedurre dai numeri.
    prossimoPasso(job) {
      if (!job) return '';
      const c = job.counts || {};
      if (job.status === 'purging') return 'Aspetta che finisca, poi sparisce da sola.';
      const cercando = (c.pending || 0) + (c.fetching || 0);
      if (cercando) return this.tt('Aspetta: {0} schede ancora da cercare. Puoi anche chiudere la pagina.', cercando);
      const daVedere = (c.ready || 0) + (c.incomplete || 0) + (c.not_found || 0);
      if (daVedere) return this.tt('Premi «Rivedi una a una»: {0} schede aspettano categoria e prezzo.', daVedere);
      if (c.blocked) return 'Abbonamento non attivo: le schede sono pronte ma non partono.';
      if (c.error) return this.tt('{0} schede in errore: premi «Riprova quelli mancanti».', c.error);
      if (c.pushed) return this.tt('Tutto sul sito: {0} prodotti. Non devi fare altro.', c.pushed);
      return '';
    },

    // Che cosa sta succedendo, in una frase.
    fase(job) {
      if (!job) return '';
      if (job.status === 'purging') return 'Eliminazione dal sito in corso';
      const c = job.counts || {};
      if ((c.pending || 0) + (c.fetching || 0) > 0) return 'Fase 1 · cerco le schede';
      if ((c.pushing || 0) > 0) return 'Fase 3 · pubblico sul sito';
      const daFare = (c.ready || 0) + (c.incomplete || 0) + (c.not_found || 0);
      if (daFare > 0) return 'Fase 2 · rivedi le schede e metti i prezzi';
      if ((c.pushed || 0) > 0) return 'Finita · tutto sul sito';
      return 'Finita';
    },

    countsLabel(job) {
      const c = job.counts || {};
      const parts = [];
      if (c.pushed) parts.push(this.tt('{0} sul sito', c.pushed));
      if (c.ready) parts.push(this.tt('{0} pronti', c.ready));
      if (c.incomplete) parts.push(this.tt('{0} da completare', c.incomplete));
      if (c.pending) parts.push(this.tt('{0} in coda', c.pending));
      if (c.not_found) parts.push(this.tt('{0} non trovati', c.not_found));
      if (c.blocked) parts.push(this.tt('{0} bloccati', c.blocked));
      if (c.error) parts.push(this.tt('{0} errori', c.error));
      return parts.join(' · ') || this.t('in attesa');
    },

    jobBadge(s) {
      return { queued: 'b-wait', running: 'b-run', done: 'b-ok', paused: 'b-warn', error: 'b-err' }[s] || 'b-wait';
    },
    jobLabel(s) {
      return { queued: 'In coda', running: 'In corso', done: 'Completata', paused: 'Sospesa', error: 'Errore' }[s] || s;
    },
    itemBadge(s) {
      return { pending: 'b-wait', fetching: 'b-run', ready: 'b-run', pushing: 'b-run',
               pushed: 'b-ok', not_found: 'b-warn', error: 'b-err', skipped: 'b-wait',
               blocked: 'b-err', incomplete: 'b-warn',
               to_delete: 'b-warn', deleted: 'b-wait' }[s] || 'b-wait';
    },
    itemLabel(s) {
      return { pending: 'In coda', fetching: 'Cerco', ready: 'Pronto', pushing: 'Invio',
               pushed: 'Sul sito', not_found: 'Non trovato', error: 'Errore', skipped: 'Saltato',
               blocked: 'Bloccato', incomplete: 'Da completare',
               to_delete: 'Da eliminare', deleted: 'Eliminato' }[s] || s;
    },

    // Al cliente finale i dettagli tecnici («HTTP 400», «cache», nomi delle fonti)
    // non dicono niente e spaventano: si traducono in cosa fare adesso.
    erroreGentile(it) {
      if (it.status === 'not_found') {
        return 'Scheda non trovata: «Perché?» ti dice cosa è successo. Puoi compilarla a mano dalla revisione.';
      }
      if (it.status === 'error') {
        return 'La ricerca non è andata a buon fine: premi «Riprova quelli mancanti» o compila a mano.';
      }
      if (it.status === 'blocked') return it.error || 'Scheda bloccata.';
      return it.error || '';
    },

    fmtDay(iso) {
      if (!iso) return '';
      return new Date(iso).toLocaleDateString(I18n.locale, { day: '2-digit', month: '2-digit', year: 'numeric' });
    },

    fmtDate(iso) {
      if (!iso) return '';
      const d = new Date(iso);
      return d.toLocaleString(I18n.locale, { day: '2-digit', month: '2-digit', year: '2-digit',
                                         hour: '2-digit', minute: '2-digit' });
    }
  };
}
