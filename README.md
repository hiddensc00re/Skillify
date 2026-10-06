# Skillify

App con offerte di lavoro da swipare, account candidati e aziende, prenotazioni esclusive e test con allegati. Versione online: https://skillify-pi.vercel.app/

## Flusso della versione 0.3

1. Crea un account **candidato** oppure **azienda**. Ogni email identifica un solo account con un ruolo. Le password devono avere almeno 12 caratteri; vengono salvate con hash scrypt.
2. L’azienda pubblica un’offerta con istruzioni del test e, se servono, materiali allegati. Il pannello mostra soltanto le offerte e le candidature appartenenti all’azienda connessa.
3. Il primo swipe a destra **prenota in esclusiva** l’offerta e la nasconde subito a tutti. Una transazione impedisce due prenotazioni contemporanee.
4. Nel **Carrello e test**, il candidato scrive la motivazione, indica la disponibilità e conferma di voler svolgere la prova. Deve poi premere **Avvia test** entro 15 minuti dallo swipe. Confermare non estende la scadenza.
5. Se il test non viene avviato in tempo, il server libera l’offerta e il feed torna a mostrarla. La prenotazione scaduta resta nello storico. La scadenza è verificata lato server alla richiesta del feed, del carrello o del pannello: non richiede che il candidato resti online, né un processo cron.
6. Avviato il test, la prenotazione non scade più automaticamente. L’ambiente presenta le istruzioni, i materiali da scaricare, una risposta scritta e il caricamento degli elaborati. Il candidato preme **Consegna test all’azienda**.
7. L’azienda valuta il lavoro. **Test non superato · rimetti annuncio nel feed** conserva l’esito e riapre l’offerta. **Seleziona candidato · chiudi definitivamente** conclude l’offerta: non può tornare nel feed per scadenza o riapertura.

Gli allegati sono privati, salvati nel database PostgreSQL e scaricabili soltanto dal candidato della prova o dall’azienda proprietaria. Massimo **5 file da 2 MB ciascuno** per i materiali e per la consegna; i caricamenti sono singoli. Non vengono eseguiti file o codice sul server. Le bozze testuali non consegnate non sono salvate automaticamente: mantenere aperto il test fino alla consegna. Gli annunci iniziali sono esempi; non vengono inviate email o candidature a servizi esterni. Non è implementata la verifica email o il recupero password.

## Avvio locale

Python 3.12 o successivo:

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m flask --app web run --host 127.0.0.1 --port 8000
```

Apri http://127.0.0.1:8000 e crea gli account dai due pulsanti di accesso. Senza `DATABASE_URL`, il backend web usa `data/skillify.db` con SQLite. Puoi impostare `PAUSE_SECONDS=30` per una prova rapida. Le variabili di `.env.example` vanno esportate nell’ambiente; il codice non carica automaticamente quel file.

## Deploy Vercel

Il punto d’ingresso è `web:app` in `pyproject.toml`; preset **Flask**, cartella radice `./`, comandi di build e output automatici. L’interfaccia si trova in `public/`. Importare il repository GitHub oppure caricare uno ZIP del sorgente tramite Vercel Drop. Il progetto corrente è stato pubblicato manualmente: i commit GitHub non avviano automaticamente un deploy. Per aggiornare lo stesso progetto, apri la sua pagina Vercel e carica il nuovo archivio dal menu di aggiornamento, oppure usa la CLI Vercel con il progetto già collegato.

| Variabile | Significato |
| --- | --- |
| `DATABASE_URL` | PostgreSQL dedicato, con TLS |
| `ADMIN_PASSWORD` | Segreto di almeno 32 caratteri per gestire gli annunci demo preesistenti |
| `PAUSE_SECONDS` | Tempo per confermare e avviare il test; predefinito 900 |
| `SEED_DEMO_JOBS` | 1 per inizializzare gli annunci di esempio, 0 per partire senza annunci |
| `APP_URL` | URL canonico facoltativo per limitare gli host accettati |

Le variabili devono essere presenti nell’ambiente del deploy. Dopo una modifica, effettuare un redeploy. Sul cloud il backend rifiuta di usare SQLite temporaneo; configurazioni incomplete producono 503. `/api/health` deve rispondere `{"status":"ok","storage":"postgresql"}` e `/api/bootstrap` deve indicare la versione `0.3.0`.

La migrazione aggiunge tabelle e colonne senza cancellare offerte, vecchie candidature o profili. Le vecchie candidature restano nella tabella storica `applications`; il nuovo flusso usa `reservations`. Gli annunci senza proprietario sono gestibili attraverso **Accedi azienda → Gestisci gli annunci demo con la password precedente**; gli account azienda ordinari vedono solo i propri annunci. Il segreto demo non è un account candidato né un account azienda. Le sessioni account durano 30 giorni e gli accessi demo un’ora; logout e nuovo login ruotano la sessione. Le richieste di modifica richiedono CSRF e origine coerente, con cookie HttpOnly/Secure sul cloud.

## Test

```bash
python -m unittest discover -s tests -v
node --check public/static/app.js
```

La suite comprende i 15 test della demo originale, 7 test HTTP Flask e 6 test del nuovo flusso: timeout, conferma senza estensione, test avviato senza scadenza, selezione definitiva, esito negativo e ripubblicazione, login da un altro browser, rotazione CSRF, separazione ruoli/aziende, allegati privati, persistenza e primo swipe concorrente.

Il test opzionale PostgreSQL richiede `SKILLIFY_TEST_DATABASE_URL` su un database dedicato ai test. Prima di dichiarare il deploy verificato, eseguire anche il flusso sulla vera applicazione cloud: SQLite locale non prova il comportamento PostgreSQL remoto.

## Struttura

| File | Funzione |
| --- | --- |
| `web.py` | API Flask, accessi e autorizzazioni |
| `hiring.py` | Account, prenotazioni, test, esiti e allegati |
| `store.py` | Persistenza condivisa e compatibilità della demo originale |
| `postgres.py` | Query parametrizzate su PostgreSQL |
| `public/index.html` | Interfaccia con login, carrello e ambiente test |
| `public/static/app.js`, `style.css` | Interazioni e layout responsive |
| `tests/` | Test automatici con dati sintetici |
| `requirements.txt` | Flask e psycopg |
| `pyproject.toml`, `vercel.json` | Configurazione del deploy |

## Demo originale

`python app.py` conserva il precedente server locale con i file in `static/`. È una demo separata: non implementa il nuovo flusso di account e test. Per usare le nuove funzionalità avviare `web.py` con Flask come indicato sopra. Non eseguire la demo originale contro il database di produzione.
