# Skillify

Una prima versione locale per esplorare offerte di lavoro con swipe e registrare candidature. Interfaccia in italiano, Python, SQLite, HTML/CSS/JavaScript. Non richiede librerie esterne né una connessione Internet per funzionare.

## Avvio

1. Estrai `skillify.zip` sul Desktop: ottieni la cartella `skillify`.
2. Installa Python 3.10 o successivo, se non è già disponibile.
3. Apri un terminale **dentro la cartella `skillify`**.
4. Avvia:

   ```bash
   python app.py
   ```

   Su macOS/Linux, se necessario: `python3 app.py`. Su Windows puoi usare `py -3 app.py`.

5. Apri **http://127.0.0.1:8000** nel browser. Lascia aperto il terminale mentre usi l’app.
6. Per fermare il server premi **Ctrl+C** nel terminale.

Non serve eseguire `pip install`: `requirements.txt` documenta che non ci sono dipendenze esterne. Se la porta 8000 è occupata, usa `python app.py --port 8001` e apri http://127.0.0.1:8001.

## Come usarla

- **Scopri offerte:** otto annunci di esempio al primo avvio. Puoi filtrare per categoria, modalità e testo (ruolo, azienda, località, competenze).
- **Swipe a destra / freccia destra / “Mi interessa”:** registra una candidatura. Al primo utilizzo viene richiesto il profilo; salvandolo si completa la candidatura che hai scelto.
- **Swipe a sinistra / freccia sinistra / “Passo”:** nasconde l’offerta soltanto al candidato corrente. Quando esaurisci le offerte, il pulsante “Rivedi offerte saltate” le ripristina.
- **Le candidature:** mostra tutte le candidature registrate dal tuo profilo.
- **Area aziende:** permette di pubblicare offerte, consultare i candidati con nome/email/competenze, chiudere le offerte o riaprirle immediatamente.

“L’offerta trova un candidato” è implementato come **prima candidatura ricevuta**, senza approvazione da parte dell’azienda. L’offerta si sospende per **15 minuti per tutti i candidati** e poi torna disponibile automaticamente. Per chi si è già candidato resta esclusa dal feed, evitando duplicati. Ogni candidatura successiva, dopo la riapertura, avvia un nuovo periodo di sospensione.

La sospensione è salvata nel database: resiste al riavvio del server. La disponibilità viene ricalcolata a ogni richiesta e il browser aggiorna automaticamente la vista ogni 10 secondi quando è aperta e attiva. Puoi usare “Aggiorna” per vedere subito le modifiche. Due candidature simultanee vengono gestite con una transazione SQLite: solo una è accettata durante la stessa finestra di disponibilità. Il pannello aziende conserva le candidature anche dopo la scadenza, la chiusura o la riapertura.

Per vedere lo stesso flusso da due candidati distinti, apri una finestra normale e una finestra privata, oppure due browser diversi. Schede dello stesso browser condividono il profilo. Il cookie di sessione dura 30 giorni; cancellandolo o facendolo scadere il browser riceve un nuovo profilo. Non è presente un recupero dell’identità precedente: le candidature rimangono comunque nel pannello aziende.

## Configurazione e dati

```bash
# Sospensione di 30 secondi, utile per una dimostrazione rapida
python app.py --pause-seconds 30

# Database separato, senza toccare quello principale
python app.py --db data/prova.db --port 8001
```

`data/skillify.db` viene creato automaticamente al primo avvio. Contiene offerte, profili, candidature e offerte saltate. Gli annunci iniziali sono fittizi: non provengono da portali di lavoro e non vengono importati in automatico. Non vengono inviate email o candidature a sistemi esterni. Il collegamento email nel pannello apre il tuo client di posta solo se lo selezioni.

Per una copia di sicurezza, ferma il server e copia il database. Per ricominciare da zero, ferma il server e rimuovi `data/skillify.db` ed eventuali file associati `skillify.db-wal` e `skillify.db-shm`: **questa operazione elimina i dati locali**. Al successivo avvio verranno ricreati gli otto annunci di esempio.

## Test

Dalla cartella del progetto:

```bash
python -m unittest discover -s tests -v
```

I 15 test usano database temporanei e server HTTP locali su porte libere; non modificano `data/skillify.db`. Verificano persistenza, scadenza della sospensione, concorrenza tra candidati, duplicati, offerte saltate, chiusura/riapertura, pubblicazione, validazione, sessioni, richieste HTTP e protezioni delle richieste. Un test avvia realmente `app.py`, verifica la scadenza della pausa e lo riavvia controllando la persistenza di profilo e candidatura. Il server principale non deve essere avviato per eseguirli.

Verifiche eseguite durante la creazione: tutti i 15 test superati e sintassi JavaScript controllata con `node --check static/app.js`. La verifica visiva e delle interazioni in un browser reale non è stata completata: il browser remoto dell’ambiente non può raggiungere il server locale e Chromium non è disponibile nel runtime. Il layout mobile è implementato con CSS responsive, ma non è stato verificato visivamente in questo ambiente.

## Struttura

| File/cartella | Contenuto |
| --- | --- |
| `app.py` | Server HTTP e API locali, configurazione di avvio |
| `store.py` | Database SQLite, validazione e transazioni |
| `static/index.html` | Interfaccia, profilo e moduli |
| `static/style.css` | Stile responsive per desktop e mobile |
| `static/app.js` | Swipe, filtri, candidature e pannello aziende |
| `static/favicon.svg` | Icona del browser |
| `tests/test_skillify.py` | Test automatici del database e delle API |
| `data/` | Database generato al primo avvio |
| `requirements.txt` | Nessuna dipendenza esterna |
| `.gitignore` | Esclusioni per database, cache e ambiente virtuale |

## Ambito di questa versione

È un prototipo funzionante **per uso locale**, non un servizio pubblico pronto alla distribuzione. Il server ascolta solo su `127.0.0.1`. Il pannello aziende non ha autenticazione ed è accessibile agli utenti locali dell’app: usa dati di prova. Le sessioni distinguono i browser, ma non costituiscono account verificati; l’email non viene verificata.

Prima di un utilizzo pubblico servirebbero account con autenticazione, ruoli candidati/aziende, verifica email, HTTPS, gestione dei consensi e dei dati personali, moderazione degli annunci e un server adatto alla produzione. Questa versione include cookie HttpOnly/SameSite, token CSRF, controllo dell’origine e dell’host, limiti alle richieste, escaping del testo e query parametrizzate.
