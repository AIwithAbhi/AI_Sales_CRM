# AI Sales Intelligence Pipeline

A production-ready web application that automatically searches B2B sales prospects using AI. Upload a list of company names, and the app will find their websites, analyze their business using NVIDIA AI, score them for sales outreach, and push structured data to Airtable CRM.

It ships with a modern web UI — a **Home / landing page** that launches two tools: **Sales Search** and **Regulatory News Alerts** (built with Tailwind + a custom theme).

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Web UI +      │────▶│  Search      │────▶│   Airtable      │
│   FastAPI       │     │  Pipeline        │     │   CRM           │
│   (server.py)   │     │                  │     │                 │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
       ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
       │  Firecrawl  │ │  Firecrawl  │ │  NVIDIA     │
       │  Search     │ │  Scrape     │ │  AI API     │
       │             │ │             │ │             │
       └─────────────┘ └─────────────┘ └─────────────┘
```

## Setup Instructions

### 1. Clone and Install Dependencies

```bash
cd crm-tools-ai
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy the example environment file and fill in your API keys:

```bash
cp .env.example .env
```

Edit `.env` with your actual credentials:

```env
FIRECRAWL_API_KEY=your_firecrawl_api_key_here
NVIDIA_API_KEY=your_nvidia_api_key_here
AIRTABLE_API_KEY=your_airtable_api_key_here
AIRTABLE_BASE_ID=your_base_id_here
AIRTABLE_TABLE_NAME=Leads
```

### 3. Required API Keys

| Service | Where to Get | Free Tier |
|---------|--------------|-----------|
| **Firecrawl** | [firecrawl.dev](https://firecrawl.dev/) | Free tier available |
| **NVIDIA API** | [build.nvidia.com](https://build.nvidia.com/explore/discover) | Free tier available |
| **Airtable** | [airtable.com/create/tokens](https://airtable.com/create/tokens) | Free base available |

### 4. Airtable Setup

The app pushes **each field to its own column** (no JSON blob) and will **auto-create any missing columns** on the first push — so you only need a table with the default primary `Name` field. (Auto-creation needs an Airtable token with the `schema.bases:write` scope; otherwise create the columns manually.)

| Column | Airtable Type | Source field |
| ------ | ------------- | ------------ |
| Name | Single line text (primary) | company_name |
| Website | URL | url |
| Industry | Single line text | industry |
| Company Size | Single line text | size_estimate |
| B2B Buyer | Checkbox | b2b_buyer |
| Lead Score | Number | lead_score (1–10) |
| Status | Single select | status_tag (Hot / Warm / Cold) |
| Score Reason | Long text | score_reason |

Set `AIRTABLE_TABLE_NAME` in `.env` to your table's name (e.g. `newlead`). The `Status` options (Hot / Warm / Cold) are added automatically on write via Airtable typecast.

To remove legacy columns (Notes, Assignee, Attachments, Summary, headcount/growth fields, Enriched At), delete them in the Airtable UI or run:

```bash
python remove_airtable_fields.py
```

That script needs an Airtable token with `schema.bases:write` scope.

### 5. Run Locally

```bash
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** in your browser.

## Usage

Open the app to land on the **Home** page, then click **Launch App** (or the *Sales Search* tile) to open the tool. Use the top nav to switch between **Sales Search** and **Regulatory Alerts**, or **← Home** to return.

1. **Upload CSV**: Select a CSV with a `company_name` column (or use the first column)
2. **Search All**: Start search
3. **Monitor Progress**: Watch the progress bar until the job completes
4. **Review Results**: Open Sales Insights, ICP, and AI recommendations
5. **Push to Airtable** or **Download CSV**
6. **View Airtable** to see records in your base

## Regulatory News Alerts

A separate tab in the web UI runs AML/KYC regulatory news monitoring:

1. Open the **Regulatory Alerts** tab
2. Enter your inbox email (e.g. `sales@company.com`) and click **Send Test Email** to verify SMTP
3. Provide companies **either way**:
   - **Type a company name** directly — one, or several comma/newline separated, *or*
   - **Upload a CSV** (`company_name` column)
4. Click **Search**

For each company the app searches Firecrawl for regulatory news and analyzes every article with NVIDIA AI. It then gathers all of a company's relevant news and sends **one consolidated digest email** — instead of one email per article — containing an AI overview, prioritized sales actions, and each item's "why it matters" plus suggested talking points. Only **new** news triggers an email; already-sent items are skipped via `data/alerts_sent.json`.

### Gmail SMTP setup

In `.env`:

```env
EMAIL_SENDER=your@gmail.com
EMAIL_PASSWORD=your_16_char_app_password
EMAIL_SMTP_HOST=smtp.gmail.com
EMAIL_SMTP_PORT=587
```

Create an [App Password](https://myaccount.google.com/apppasswords) (2FA required on Google account).

## Sample Data

Use the included `sample_companies.csv` file to test the application. It contains 10 energy sector companies:

- Siemens Energy
- Vestas Wind Systems
- Schneider Electric
- ABB Group
- Orsted
- SolarEdge Technologies
- Enphase Energy
- NextEra Energy
- Brookfield Renewable
- EDF Renewables

## Deploy

Run behind any ASGI host (Railway, Render, Docker, etc.):

```bash
uvicorn server:app --host 0.0.0.0 --port $PORT
```

Set the same environment variables as in `.env`.

## Project Structure

```
AI_Sales_CRM-01/
├── server.py              # FastAPI app (entry point)
├── web/                   # Frontend (HTML, CSS, JS)
├── services/              # Business logic (process company, insights)
├── pipeline/              # Search, scrape, AI, news alerts, Airtable
├── utils/                 # Helpers, alert dedup store, UTF-8 console
├── search_pipeline.py     # Optional CLI batch script
├── requirements.txt
└── .env.example
```

## Sales Scoring Logic

The AI scores companies 1-10 based on:

- **Industry**: Energy, Technology, Manufacturing score higher
- **Size**: Companies with 51+ employees score higher
- **B2B Buyer**: Companies likely to purchase B2B software score higher

**Status Tags:**
- **Hot** (8-10): High-priority sales prospects ready for immediate outreach
- **Warm** (5-7): Potential sales opportunities worth nurturing
- **Cold** (1-4): Low-priority or poor-fit prospects

## Troubleshooting

### "Missing environment variables" error

- Ensure `.env` file exists in the project root
- Check that all 4 variables are set correctly (FIRECRAWL_API_KEY, NVIDIA_API_KEY, AIRTABLE_API_KEY, AIRTABLE_BASE_ID)
- Restart the server after changing `.env` (`uvicorn server:app --reload`)

### "No valid companies found" error
- Ensure CSV has a column named `company_name` or data in the first column
- Check that the file is valid CSV format (not Excel)

### Firecrawl rate limit exceeded
- Check Firecrawl dashboard for your plan limits
- Add delays between requests or upgrade plan

### Airtable records not appearing
- Verify the table name matches `AIRTABLE_TABLE_NAME` (e.g. `newlead`)
- Ensure the API token has **data write** permission — and **`schema.bases:write`** if you want missing columns auto-created
- Records with a matching **Name** are skipped as duplicates

## License

MIT License - See LICENSE file for details.
