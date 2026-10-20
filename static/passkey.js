/* Passkey (WebAuthn) per la verifica in due passaggi: registrazione dalla pagina Account,
   uso dalla pagina di accesso. Il server manda le opzioni in JSON con i byte in base64url;
   qui diventano ArrayBuffer per il browser e la risposta torna indietro in base64url. */
(function (win) {
  'use strict';

  function aBuffer(testo) {
    const base64 = String(testo).replace(/-/g, '+').replace(/_/g, '/');
    const pieno = base64 + '==='.slice((base64.length + 3) % 4);
    const binario = atob(pieno);
    const byte = new Uint8Array(binario.length);
    for (let i = 0; i < binario.length; i++) byte[i] = binario.charCodeAt(i);
    return byte.buffer;
  }

  function aTesto(buffer) {
    if (!buffer) return null;
    const byte = new Uint8Array(buffer);
    let binario = '';
    for (let i = 0; i < byte.length; i++) binario += String.fromCharCode(byte[i]);
    return btoa(binario).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function credenziali(elenco) {
    return (elenco || []).map(c => Object.assign({}, c, { id: aBuffer(c.id) }));
  }

  function estensioni(cred) {
    try { return cred.getClientExtensionResults ? cred.getClientExtensionResults() : {}; } catch (e) { return {}; }
  }

  const Passkey = {
    // Le passkey esistono solo in un contesto sicuro: HTTPS, oppure localhost.
    supportata() {
      return !!(win.PublicKeyCredential && win.navigator.credentials && win.isSecureContext);
    },

    async crea(opzioni) {
      const publicKey = Object.assign({}, opzioni, {
        challenge: aBuffer(opzioni.challenge),
        user: Object.assign({}, opzioni.user, { id: aBuffer(opzioni.user.id) }),
        excludeCredentials: credenziali(opzioni.excludeCredentials)
      });
      const cred = await win.navigator.credentials.create({ publicKey: publicKey });
      let trasporti = [];
      try { trasporti = cred.response.getTransports ? cred.response.getTransports() : []; } catch (e) {}
      return {
        id: cred.id,
        rawId: aTesto(cred.rawId),
        type: cred.type,
        authenticatorAttachment: cred.authenticatorAttachment || undefined,
        clientExtensionResults: estensioni(cred),
        response: {
          clientDataJSON: aTesto(cred.response.clientDataJSON),
          attestationObject: aTesto(cred.response.attestationObject),
          transports: trasporti
        }
      };
    },

    async usa(opzioni) {
      const publicKey = Object.assign({}, opzioni, {
        challenge: aBuffer(opzioni.challenge),
        allowCredentials: credenziali(opzioni.allowCredentials)
      });
      const cred = await win.navigator.credentials.get({ publicKey: publicKey });
      return {
        id: cred.id,
        rawId: aTesto(cred.rawId),
        type: cred.type,
        authenticatorAttachment: cred.authenticatorAttachment || undefined,
        clientExtensionResults: estensioni(cred),
        response: {
          clientDataJSON: aTesto(cred.response.clientDataJSON),
          authenticatorData: aTesto(cred.response.authenticatorData),
          signature: aTesto(cred.response.signature),
          userHandle: aTesto(cred.response.userHandle)
        }
      };
    },

    // Gli errori del browser, in parole che si capiscono (in italiano: li traduce il catalogo).
    messaggio(errore) {
      const nome = errore && errore.name;
      if (nome === 'NotAllowedError' || nome === 'AbortError') return 'Operazione annullata o scaduta. Riprova.';
      if (nome === 'InvalidStateError') return 'Su questo dispositivo c\'è già una passkey per il tuo account.';
      if (nome === 'SecurityError') return 'Le passkey funzionano solo in HTTPS, all\'indirizzo ufficiale del portale.';
      if (nome === 'NotSupportedError') return 'Questo dispositivo non ha passkey compatibili.';
      return (errore && errore.message) || 'La passkey non ha risposto. Riprova.';
    },

    // Un nome di partenza per la passkey nuova: il dispositivo su cui la si crea.
    nomeDispositivo() {
      const ua = win.navigator.userAgent || '';
      if (/iPhone/.test(ua)) return 'iPhone';
      if (/iPad/.test(ua)) return 'iPad';
      if (/Android/.test(ua)) return 'Android';
      if (/Macintosh|Mac OS X/.test(ua)) return 'Mac';
      if (/Windows/.test(ua)) return 'Windows';
      if (/Linux/.test(ua)) return 'Linux';
      return '';
    }
  };

  win.Passkey = Passkey;
})(window);
