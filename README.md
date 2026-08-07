# FirmGen Evaluation Console

A single-service evaluator for public GitHub firmware submissions. It reads repository code, README details, public README-linked screenshots and videos, and scores each eligible submission against the seven-part rubric.

## Key rules built into this version

- A repository with no firmware build or embedded-source evidence receives **0/100** before model scoring starts.
- Genuine FirmGen evidence receives at least **15/20** for Effective Use of FirmGen.
- A mostly progressed FirmGen task list plus a visible FirmGen desktop or Doxygen report receives at least **18/20** for that criterion.
- A bare FirmGen mention, logo, or unrelated screenshot is not enough. No genuine FirmGen evidence caps the criterion at **5/20**.
- Up to **10** submissions evaluate at once. Scores are always produced; evidence flags are informational.

## Run locally first

### 1. Install prerequisites

Install Python 3.12+, Git, and FFmpeg. In VS Code, open the folder that contains `app`, `requirements.txt`, and `.env`.

### 2. Open the terminal and create the isolated environment

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, run this once, then activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### 3. Configure `.env`

The download includes a production `.env` file. Open it in VS Code and set:

```env
OPENAI_API_KEY=your_api_key_here
APP_PASSWORD=your_private_dashboard_password
SESSION_SECRET=a_long_random_secret
MOCK_OPENAI=false
```

Leave `MAX_CONCURRENT_EVALUATIONS=10` as supplied. Do not share, email, screenshot, or commit `.env`.

To generate a local session secret in PowerShell:

```powershell
[guid]::NewGuid().ToString() + [guid]::NewGuid().ToString()
```

### 4. Start the dashboard

```powershell
python -m uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000), then sign in using `APP_PASSWORD`.

### 5. Test one repository

Paste one **public GitHub repository** URL. The repository should include real firmware build/source evidence. README-linked images/videos, FirmGen task-list screenshots, Doxygen reports, and FirmGen desktop views are all examined when publicly accessible.

If a submission fails, select **Details** in the ledger to read the exact error. Stop the local server with `Ctrl+C`.

## Deploy on Railway after local testing

### 1. Create a private GitHub repository

1. Go to [github.com/new](https://github.com/new).
2. Name it `firmgen-evaluator` and choose **Private**.
3. Do not upload your populated `.env` file. It is already listed in `.gitignore`.
4. Upload the project files: `app`, `tests`, `Dockerfile`, `requirements.txt`, `railway.json`, and the other project files.

### 2. Create the Railway service

1. Sign in at [Railway](https://railway.com) with GitHub.
2. Choose **New Project** then **Deploy from GitHub repo**.
3. Select the private `firmgen-evaluator` repository.
4. Railway detects the included `Dockerfile`. Allow the initial build to complete.

### 3. Add persistent storage

1. On the Railway project canvas, right-click the evaluator service.
2. Choose **Attach Volume**.
3. Set the mount path to exactly `/data`.

This keeps the submission ledger and exports after redeploys.

### 4. Add secret variables

Open the evaluator service, select **Variables**, then add these values one at a time:

```env
OPENAI_API_KEY=your_real_key
APP_PASSWORD=your_private_dashboard_password
SESSION_SECRET=your_long_random_secret
DATA_DIR=/data
MAX_CONCURRENT_EVALUATIONS=10
MOCK_OPENAI=false
```

Do not paste the `.env` file into GitHub. Do not put the key in source code.

### 5. Publish the dashboard

1. Select the service, then **Settings**.
2. Find **Networking**.
3. Click **Generate Domain**.
4. Open the generated URL and sign in with `APP_PASSWORD`.

Railway will redeploy automatically after future GitHub commits. Railway volumes persist data at the mount path you set; Railway?s official [volume guide](https://docs.railway.com/volumes) and [GitHub deployment quick start](https://docs.railway.com/quick-start) show the same flow.

## Troubleshooting

- `OPENAI_API_KEY is not configured`: ensure `.env` is in the project root, save it, then fully restart Uvicorn.
- `model not found` or permission error: verify the API project/key has access to `gpt-5.6-sol`.
- No video analysis: install FFmpeg locally or use the supplied Docker deployment.
- `0/100`: open **View**; the repository did not meet the firmware prerequisite. Add actual firmware build/configuration and source files to the participant repository.
- GitHub download error: confirm the participant repository and README media are public.
