# How to run the Weather-Advisory Support Bot

## What you need
- **Python 3.11 or newer.** Check by running `python --version` (on macOS or Linux, `python3 --version`). If you don't have it, install it from https://www.python.org/downloads/. On Windows, tick **"Add Python to PATH"** during setup.
- **Internet access.** The bot fetches live weather from Open-Meteo, which is free and needs no account or key.
- **An OpenAI API key is optional.** The bot works fully without one.

## 1. Unzip
Extract `weather-advisory-bot.zip`, then open a terminal in the extracted `weather-advisory-bot` folder.

## 2. Create a virtual environment and install packages (one time only)

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

**macOS / Linux:**
```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## 3. Optional: configure settings
You can skip this step.

To use an OpenAI key, copy `.env.example` to `.env` and set `OPENAI_API_KEY=` to your key. The `.env` file stays on your computer.

## 4. Start the backend (Terminal 1)
**Windows:**
```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```
**macOS / Linux:**
```bash
.venv/bin/python -m uvicorn app.main:app --port 8000
```
Wait for `Uvicorn running on http://127.0.0.1:8000`, and keep this terminal open. You can check it by opening http://127.0.0.1:8000, which redirects to the API docs.

## 5. Start the chat interface (Terminal 2)
Open a second terminal in the same folder.

**Windows:**
```powershell
.\.venv\Scripts\python -m streamlit run frontend/streamlit_app.py
```
**macOS / Linux:**
```bash
.venv/bin/python -m streamlit run frontend/streamlit_app.py
```
If Streamlit asks for an email address, leave it blank and press **Enter**. The chat opens at **http://localhost:8501**.

## 6. Try it
- "Is it safe to cycle in Bhopal today?"
- then "What about this evening?" (the bot remembers the activity and city)
- "Can I take my children to the park in Indore this afternoon?"
- "Is tomorrow a good day for a picnic in Pune?"

Each answer cites the SOP it used. Expand **"Weather data used"** to see the exact values.

## 7. Run the tests (optional)
**Windows:**
```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python evals/run_evals.py --live
```
**macOS / Linux:**
```bash
.venv/bin/python -m pytest -q
.venv/bin/python evals/run_evals.py --live
```
To also run the tests against the real Open-Meteo API:
- Windows: `$env:RUN_LIVE="1"; .\.venv\Scripts\python -m pytest -q -m live`
- macOS/Linux: `RUN_LIVE=1 .venv/bin/python -m pytest -q -m live`

## Troubleshooting
| Problem | Fix |
|---|---|
| `uvicorn` / `streamlit` "not recognized" | Use the full `.venv\Scripts\python -m ...` commands shown above. |
| `{"detail":"Not Found"}` in the browser | That's the backend, not the chat. Open http://localhost:8501 instead. |
| Chat says it "could not reach the advisory backend" | Terminal 1 (the backend) must still be running. |
| Port already in use | Use another port, e.g. `--port 8001`. Then set `BACKEND_URL=http://localhost:8001` before starting Streamlit. |
| "couldn't retrieve live weather" | Check your internet connection or firewall. Open-Meteo must be reachable. |
| PowerShell blocks `Activate.ps1` | You don't need to activate the environment; the commands above work without it. |

To stop the bot, press **Ctrl+C** in both terminals.
